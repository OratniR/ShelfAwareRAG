import sqlite3
import uuid
from datetime import datetime
from typing import List, Dict, Optional
import chromadb
from shelf_aware.constants import SQLITE_DB_PATH, CHROMA_DB_DIR, CHROMA_COLLECTION_NAME

class InventoryDAO:
    def __init__(self):
        # SQLite初期化
        self.conn = sqlite3.connect(str(SQLITE_DB_PATH), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_table()
        
        self.chroma_client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
        self.collection = self.chroma_client.get_or_create_collection(
            name=CHROMA_COLLECTION_NAME
        )

    def _create_table(self):
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY, -- これが「アイテム名」を兼ねる
                    location TEXT NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    expiry_date TIMESTAMP    -- Phase 2で活用
                )
            """)

    def add_or_update_item(self, name: str, location: str):
        """
        アイテムの追加または場所の更新（さらにシンプルに）
        """
        now = datetime.now().isoformat()
        
        # 1. SQLite: パラメータは3つだけで完結
        with self.conn:
            self.conn.execute("""
                INSERT OR REPLACE INTO items (id, location, updated_at)
                VALUES (?, ?, ?)
            """, (name, location, now))
        
        # 2. ChromaDB: ID(name)で上書き
        self.collection.upsert(
            ids=[name],
            metadatas=[{"location": location, "updated_at": now}],
            documents=[f"{name}は{location}にある"]
        )


    def get_all_items(self, sort_by_date: bool = True):
        """ダッシュボード用の全件取得 (SQLiteから高速取得)"""
        order = "DESC" if sort_by_date else "ASC"
        cursor = self.conn.execute(f"SELECT * FROM items ORDER BY updated_at {order}")
        return [dict(row) for row in cursor.fetchall()]

    def delete_item(self, item_id: str):
        """両方のDBから削除 (一貫性を維持)"""
        with self.conn:
            self.conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        self.collection.delete(ids=[item_id])

    def sync_existing_chroma_data(self):
        """【重要】既存のChromaDBデータをSQLiteにインポートする一回限りのスクリプト"""
        # 既存のデータを取得
        existing_data = self.collection.get()
        # ID, Metadatas, DocumentsをループしてSQLiteにINSERT... (後述)
        pass