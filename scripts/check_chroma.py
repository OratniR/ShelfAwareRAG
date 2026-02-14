import sys
from pathlib import Path

import chromadb

# プロジェクトルートをpathに追加
sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
from shelf_aware.constants import CHROMA_COLLECTION_NAME, CHROMA_DB_DIR


def check():
    print("--- Debug Information ---")
    print(f"1. Looking for ChromaDB at: {CHROMA_DB_DIR}")
    print(f"   (Exists: {CHROMA_DB_DIR.exists()})")

    if not CHROMA_DB_DIR.exists():
        print("❌ ERROR: 指定されたディレクトリが存在しません。")
        return

    # 直接クライアントを立てて中身を調査
    client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))

    collections = client.list_collections()
    print(f"2. Found Collections: {[c.name for c in collections]}")

    if not collections:
        print("❌ ERROR: コレクションが一つも見つかりません。")
        return

    # 指定されたコレクション名で中身を確認
    try:
        col = client.get_collection(name=CHROMA_COLLECTION_NAME)
        count = col.count()
        print(f"3. Collection '{CHROMA_COLLECTION_NAME}' exists.")
        print(f"4. Item Count in '{CHROMA_COLLECTION_NAME}': {count}")

        if count > 0:
            sample = col.get(limit=1)
            print(f"5. Sample Data ID: {sample['ids']}")
    except Exception as e:
        print(f"❌ ERROR: コレクション '{CHROMA_COLLECTION_NAME}' の取得に失敗しました: {e}")


if __name__ == "__main__":
    check()
