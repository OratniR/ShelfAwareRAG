import sys
from pathlib import Path
from datetime import datetime
import logging

# プロジェクトルートをpathに追加して自作モジュールをインポート可能にする
# (scripts/ から src/ を参照するため)
sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

from shelf_aware.database import InventoryDAO

# ログ設定: 運用状況が分かりやすいようフォーマットを微調整
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def migrate():
    logger.info("🚀 データの移行を開始します (Schema: item_id as name)...")
    
    # DAOの初期化 (constants.pyのパス設定を自動利用)
    dao = InventoryDAO()
    
    # 1. ChromaDBから全データを取得
    try:
        results = dao.collection.get()
    except Exception as e:
        logger.error(f"❌ ChromaDBからのデータ取得に失敗しました: {e}")
        return

    ids = results.get('ids', [])  # これが「アイテム名」そのもの
    metadatas = results.get('metadatas', [])
    
    if not ids:
        logger.info("ℹ️ 移行対象のデータは見つかりませんでした。")
        return

    logger.info(f"📦 {len(ids)} 件のアイテムを処理中...")

    migrated_count = 0
    skipped_count = 0

    # 2. 1件ずつSQLiteの状態を確認しながら移行
    for i in range(len(ids)):
        item_name_as_id = ids[i]  # 以前のidsリストに入っていた名前をIDとして扱う
        meta = metadatas[i]
        
        # メタデータから場所を抽出
        location = meta.get('location', '不明な場所')
        
        # 既にSQLiteに存在するかチェック
        # (daoにexecute_queryメソッドがない場合を想定し、直接 conn を使用する書き方にします)
        cursor = dao.conn.execute("SELECT 1 FROM items WHERE id = ?", (item_name_as_id,))
        exists = cursor.fetchone()
        
        if not exists:
            # 移行時の更新日時として現在時刻をセット
            now = datetime.now().isoformat()
            
            # 新しいスキーマに合わせて INSERT
            # id (name), location, updated_at の3カラムのみ
            with dao.conn:
                dao.conn.execute(
                    "INSERT INTO items (id, location, updated_at) VALUES (?, ?, ?)",
                    (item_name_as_id, location, now)
                )
            migrated_count += 1
            logger.info(f"✅ 移行完了: {item_name_as_id}")
        else:
            skipped_count += 1
            logger.debug(f"⏭️ スキップ (既存在): {item_name_as_id}")

    logger.info("------------------------------------------")
    logger.info(f"📊 移行結果報告:")
    logger.info(f"   - 新規移行: {migrated_count} 件")
    logger.info(f"   - スキップ: {skipped_count} 件")
    logger.info("✨ 移行作業が正常に終了しました。")

if __name__ == "__main__":
    migrate()