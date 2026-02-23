# src/shelf_aware/database.py
import logging
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from sentence_transformers import SentenceTransformer

from shelf_aware.config import settings
from shelf_aware.constants import CHROMA_COLLECTION_NAME, CHROMA_DB_DIR, SQLITE_DB_PATH

logger = logging.getLogger(__name__)


class ChromaEmbeddingWrapper(EmbeddingFunction):
    """
    SentenceTransformerをChromaDBで使えるようにするラッパー。
    config.pyで定義されたモデルを使用する。
    """

    def __init__(self, model_name: str):
        # ここで intfloat/multilingual-e5-small がロードされます
        self.model = SentenceTransformer(model_name)

    def __call__(self, input: Documents) -> Embeddings:
        # テキストリストをベクトルリストに変換
        return self.model.encode(input).tolist()


class InventoryDAO:
    def __init__(self):
        self.conn = sqlite3.connect(str(SQLITE_DB_PATH), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        # 複数コンテナ(rag-api, dashboard)からの同時アクセスを安全にする設定
        self.conn.execute("PRAGMA journal_mode=WAL;")  # WALモードで並行読み書きを許可
        self.conn.execute("PRAGMA busy_timeout=5000;")  # ロック競合時に5秒待機してリトライ

        # テーブル作成とスキーマ更新を初期化時に実行
        self._create_table()
        self._migrate_schema()

        self.chroma_client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
        # 設定ファイル(settings)からモデル名を取得して初期化
        self.embedding_fn = ChromaEmbeddingWrapper(settings.EMBEDDING_MODEL)

        # embedding_functionを明示的に渡すことで、デフォルト(MiniLM)のDLを防ぐ
        self.collection = self.chroma_client.get_or_create_collection(
            name=CHROMA_COLLECTION_NAME, embedding_function=self.embedding_fn
        )

    def _create_table(self):
        """新規作成用"""
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY,
                    location TEXT NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    expiry_date TEXT,               -- 推定された賞味期限 (YYYY-MM-DD)
                    is_estimated INTEGER DEFAULT 0  -- 1: AI推定, 0: 手動
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS api_usage (
                    period TEXT PRIMARY KEY,  -- 'YYYY-MM' 形式
                    service TEXT NOT NULL,    -- 'brave_search' 等
                    count INTEGER DEFAULT 0,
                    updated_at TIMESTAMP
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_chroma_deletions (
                    item_id TEXT PRIMARY KEY,
                    created_at TIMESTAMP NOT NULL
                )
            """)

    def _migrate_schema(self):
        """
        [Schema Migration]
        既存のDBに対して、足りないカラムがあればALTER TABLEで追加する。
        これにより、既存データを消さずに機能拡張が可能。
        """
        cursor = self.conn.execute("PRAGMA table_info(items)")
        columns = [row["name"] for row in cursor.fetchall()]

        with self.conn:
            if "expiry_date" not in columns:
                self.conn.execute("ALTER TABLE items ADD COLUMN expiry_date TEXT")
            if "is_estimated" not in columns:
                self.conn.execute("ALTER TABLE items ADD COLUMN is_estimated INTEGER DEFAULT 0")

    def update_expiry(self, item_id: str, expiry_date: str, is_estimated: bool = True):
        """
        [New] 賞味期限情報の更新
        """
        with self.conn:
            self.conn.execute(
                """
                UPDATE items
                SET expiry_date = ?, is_estimated = ?
                WHERE id = ?
            """,
                (expiry_date, 1 if is_estimated else 0, item_id),
            )

    def add_or_update_item(self, name: str, location: str):
        """
        アイテムの追加または場所の更新（さらにシンプルに）
        """
        now = datetime.now().isoformat()

        # 1. SQLite: パラメータは3つだけで完結
        with self.conn:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO items (id, location, updated_at)
                VALUES (?, ?, ?)
            """,
                (name, location, now),
            )

        # 2. ChromaDB: ID(name)で上書き
        self.collection.upsert(
            ids=[name], metadatas=[{"location": location, "updated_at": now}], documents=[f"{name}は{location}にある"]
        )

    def get_all_items(self, sort_by_date: bool = True):
        """ダッシュボード用の全件取得 (SQLiteから高速取得)"""
        order = "DESC" if sort_by_date else "ASC"
        cursor = self.conn.execute(f"SELECT * FROM items ORDER BY updated_at {order}")
        return [dict(row) for row in cursor.fetchall()]

    def delete_item(self, item_id: str):
        """両方のDBから削除 (一貫性を維持) - ダッシュボード等の同期処理用"""
        self.delete_item_from_sqlite(item_id)
        self.delete_item_from_chroma(item_id)

    def delete_item_from_sqlite(self, item_id: str):
        """SQLiteからのみ削除 (高速・同期処理向け)"""
        with self.conn:
            self.conn.execute("DELETE FROM items WHERE id = ?", (item_id,))

    def delete_item_from_chroma(self, item_id: str):
        """ChromaDBからのみ削除 (バックグラウンド処理向け)"""
        self.collection.delete(ids=[item_id])

    def add_pending_deletion(self, item_id: str):
        """ChromaDB削除のpendingレコードを追加"""
        now = datetime.now().isoformat()
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO pending_chroma_deletions (item_id, created_at) VALUES (?, ?)",
                (item_id, now),
            )

    def remove_pending_deletion(self, item_id: str):
        """ChromaDB削除成功後にpendingレコードを削除"""
        with self.conn:
            self.conn.execute("DELETE FROM pending_chroma_deletions WHERE item_id = ?", (item_id,))

    def get_pending_deletions(self) -> List[str]:
        """未処理のChromaDB削除対象を全件取得"""
        cursor = self.conn.execute("SELECT item_id FROM pending_chroma_deletions")
        return [row["item_id"] for row in cursor.fetchall()]

    def delete_item_from_chroma_with_cleanup(self, item_id: str):
        """ChromaDB削除 + pending解消 (BackgroundTask用)"""
        try:
            self.collection.delete(ids=[item_id])
            self.remove_pending_deletion(item_id)
            logger.info(f"ChromaDB delete complete for '{item_id}'")
        except Exception as e:
            logger.error(f"ChromaDB delete failed for '{item_id}', will retry on next startup: {e}")

    def process_pending_deletions(self) -> int:
        """未処理の削除を一括リトライ（起動時に呼ぶ）"""
        pending = self.get_pending_deletions()
        if not pending:
            return 0
        logger.info(f"🔄 Retrying {len(pending)} pending ChromaDB deletions: {pending}")
        success_count = 0
        for item_id in pending:
            try:
                self.collection.delete(ids=[item_id])
                self.remove_pending_deletion(item_id)
                success_count += 1
                logger.info(f"  ✅ Retry success: '{item_id}'")
            except Exception as e:
                logger.error(f"  ❌ Retry failed: '{item_id}': {e}")
        return success_count

    def sync_existing_chroma_data(self):
        """【重要】既存のChromaDBデータをSQLiteにインポートする一回限りのスクリプト"""
        # 既存のデータを取得
        # existing_data = self.collection.get()
        # # ID, Metadatas, DocumentsをループしてSQLiteにINSERT... (後述)
        pass

    def check_and_increment_usage(self, service_name: str, limit: int) -> bool:
        """
        指定したサービスの今月の使用回数をチェックし、上限未満ならインクリメントする。
        Return: True(実行可), False(上限到達)
        """
        current_month = datetime.now().strftime("%Y-%m")
        key = f"{service_name}:{current_month}"

        with self.conn:
            # 現在のカウントを取得（なければ作成）
            cursor = self.conn.execute("SELECT count FROM api_usage WHERE period = ?", (key,))
            row = cursor.fetchone()

            if row:
                current_count = row["count"]
            else:
                current_count = 0
                self.conn.execute(
                    "INSERT INTO api_usage (period, service, count, updated_at) VALUES (?, ?, 0, ?)",
                    (key, service_name, datetime.now()),
                )

            # 上限チェック
            if current_count >= limit:
                return False

            # インクリメント
            self.conn.execute(
                "UPDATE api_usage SET count = count + 1, updated_at = ? WHERE period = ?", (datetime.now(), key)
            )
            return True

    def get_current_usage(self, service_name: str) -> int:
        """現在の使用回数を確認（ログ用）"""
        current_month = datetime.now().strftime("%Y-%m")
        key = f"{service_name}:{current_month}"
        cursor = self.conn.execute("SELECT count FROM api_usage WHERE period = ?", (key,))
        row = cursor.fetchone()
        return row["count"] if row else 0

    def get_items_for_backfill(self, limit: int = 5) -> List[Dict]:
        """
        推定がまだ行われていないアイテムを取得する。
        is_estimated = 0 のものを対象とする。
        """
        cursor = self.conn.execute(
            """
            SELECT * FROM items
            WHERE is_estimated = 0
            LIMIT ?
        """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_as_non_food(self, item_id: str):
        """
        「推定したけど食品じゃなかった」としてマークする。
        is_estimated = 2 (対象外) とする。
        """
        with self.conn:
            self.conn.execute(
                """
                UPDATE items
                SET is_estimated = 2
                WHERE id = ?
            """,
                (item_id,),
            )

    def update_item_state(self, item_id: str, expiry_date: Optional[str], is_estimated: int):
        """
        ダッシュボードからの手動編集用。
        指定されたIDの賞味期限とステータスだけを安全に更新する。
        """
        # 現在時刻
        now = datetime.now().isoformat()

        with self.conn:
            self.conn.execute(
                """
                UPDATE items
                SET expiry_date = ?, is_estimated = ?, updated_at = ?
                WHERE id = ?
            """,
                (expiry_date, is_estimated, now, item_id),
            )

            # ChromaDB側のメタデータも更新（整合性維持のため）
            # データがない場合のエラーを避けるため、try-exceptなどはあえて入れず、
            # IDが存在すれば更新、なければ無視されるupsertを利用しても良いが、
            # ここではシンプルにSQLiteマスターで運用し、検索用indexの更新は必須ではない（検索対象はテキストなので）
            # 必要であれば以下を追加：
            try:
                self.collection.update(ids=[item_id], metadatas=[{"updated_at": now}])

            except Exception as e:
                print(e)
                pass
