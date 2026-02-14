# tests/test_notion.py (ファイル名も test_ で始めるのが一般的)
import logging
import os
import sys

import pytest

from shelf_aware.config import settings
from shelf_aware.notion_handler import NotionShoppingListClient

current_path = os.getcwd()

# --- ロギング設定 (pytestは自身のロギング機構も持つが、個別設定も可能) ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)
# -----------------------------
logger.debug(f"Pytest is running at {current_path}")


# --- pytestで実行されるテスト関数 ---
def test_notion_connection_and_operations():
    """Notionへの接続と基本的な追加・削除操作をテストします。"""
    logger.info("--- Notion Client Test Start ---")

    # 設定確認 (pytestでは @pytest.mark.skipif を使うこともできる)
    if not settings.NOTION_API_KEY or not settings.NOTION_DATASOURCE_ID:
        pytest.skip("Notion API Key or Database ID not configured. Skipping test.")  # テストをスキップ

    logger.info(f"Using Notion Datasource ID: {settings.NOTION_DATASOURCE_ID}")

    # NotionClientを初期化
    client = NotionShoppingListClient()

    # クライアントがアクティブかチェック (アサーションを使う)
    assert client.is_active(), "Notion client failed to initialize."

    # --- テスト項目 ---
    test_item_add = "pytestテストアイテム追加"
    test_item_remove = "pytestテストアイテム追加"

    # 1. アイテム追加/チェック解除テスト
    logger.info(f"Attempting to add/uncheck item: '{test_item_add}'")
    client.add_item(test_item_add)
    # ここで実際にNotionに追加されたかを確認するアサーションを追加する
    page = client._find_item_page(test_item_add)
    assert page is not None
    assert page["properties"][settings.NOTION_CHECKBOX_PROPERTY_NAME]["checkbox"] is False
    logger.info("Add/uncheck operation attempted.")

    # 2. アイテム削除/チェックテスト
    #    事前にNotionに "pytestテストアイテム削除してチェック" を追加しておく
    logger.info(f"Attempting to remove/check item: '{test_item_remove}'")
    client.remove_item(test_item_remove)
    logger.info("Remove/check operation attempted.")

    # 3. 存在しないアイテムの削除/チェックテスト (エラーにならないことの確認)
    logger.info("Attempting to remove/check a non-existent item: '存在しないアイテム'")
    try:
        client.remove_item("存在しないアイテム")
    except Exception as e:
        pytest.fail(f"Removing non-existent item raised an exception: {e}")
    logger.info("Non-existent item removal attempted successfully (no error).")

    logger.info("--- Notion Client Test End ---")
