import logging
import json
from fastapi import BackgroundTasks
from openai import OpenAI

# 設定と定数
from .config import settings
from . import prompts
from . import constants

# 外部連携モジュール
from .notion_client import NotionShoppingListClient
from .database import InventoryDAO  # <--- 追加: DB操作の委譲先
from .estimation import ExpirationEstimator  # <--- 追加: 賞味期限推定
from shelf_aware.estimation import EstimationResult  # Enumをインポート

logger = logging.getLogger(__name__)


# --- Helper Functions ---
def extract_json_block(text: str) -> str | None:
    """Finds the first and last curly brace to extract a JSON block."""
    start_index = text.find("{")
    end_index = text.rfind("}")

    if start_index == -1 or end_index == -1 or end_index < start_index:
        logger.error(f"Could not find JSON block in LLM response: {text}")
        return None

    return text[start_index : end_index + 1]


# --- LLM Client Setup ---
# Intent分類用 (EmbeddingモデルはInventoryDAO内で管理されるためここでは不要)
llm_client = OpenAI(
    api_key=settings.LLM_API_KEY,
    base_url=settings.LLM_API_BASE,
)

# --- Background Task Function ---
# 依存関係(dao, estimator)を持つため、今回はサービスクラス内のメソッドとして呼び出す形をとる


async def run_estimation_task(
    item_name: str, estimator: ExpirationEstimator, dao: InventoryDAO
):
    """賞味期限推定を実行し、結果に応じてDBを更新する"""
    logger.info(f"⏳ Estimating expiration for: {item_name}")
    try:
        # 結果セットを取得
        result_packet = await estimator.estimate_expiration(item_name, dao)
        status = result_packet["status"]
        data = result_packet["data"]

        if status == EstimationResult.SUCCESS and data:
            # 成功: 日付を更新 (is_estimated -> 1)
            dao.update_expiry(item_name, data["expiry_date"])
            logger.info(
                f"✅ Expiry Updated: {item_name} -> {data['expiry_date']} (約{data['days_offset']}日)"
            )

        elif status == EstimationResult.NON_FOOD:
            # 食品ではない: 対象外マーク (is_estimated -> 2)
            dao.mark_as_non_food(item_name)
            logger.info(f"🚫 Marked as Non-Food: {item_name}")

        else:
            # スキップ/エラー: ログだけ出して何もしない（次回リトライ対象のまま）
            logger.info(
                f"⏭️ Skipped expiration update for: {item_name} (Status: {status})"
            )

    except Exception as e:
        logger.error(f"❌ Estimation task failed for {item_name}: {e}")


class RAGService:
    def __init__(self):
        # 全てのデータ操作はDAO経由で行うことで、SQLiteとChromaの整合性を保つ
        self.dao = InventoryDAO()
        self.list_client = NotionShoppingListClient()
        self.estimator = ExpirationEstimator()
        logger.info("RAGService initialized with DAO and Estimator.")

    def classify_intent(self, text: str) -> dict:
        """Uses the LLM to classify the user's intent."""
        logger.debug(f"Classifying intent for: '{text}'")
        system_prompt = prompts.INTENT_CLASSIFICATION_SYSTEM_PROMPT
        combined_prompt = f"{system_prompt}\n\n---\n\nユーザーの発言:\n{text}"

        try:
            response = llm_client.chat.completions.create(
                model=settings.LLM_MODEL,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": combined_prompt}],
                temperature=0.1,
            )
            raw_response_text = response.choices[0].message.content
            logger.debug(f"Raw LLM response (for intent): {raw_response_text}")

            json_block = extract_json_block(raw_response_text)
            if not json_block:
                return {"intent": "unknown", "item_name": "unknown"}

            return json.loads(json_block)
        except Exception as e:
            logger.error(f"Intent classification failed: {e}", exc_info=True)
            return {"intent": "unknown", "item_name": "unknown"}

    def add(self, item_name: str, location: str, background_tasks: BackgroundTasks):
        """Adds item to SQLite & Chroma (via DAO), then triggers Estimation & Notion sync."""
        logger.info(f"Adding/updating item: '{item_name}' at '{location}'")

        # 1. DB更新 (SQLite + ChromaDB) - DAOに一任
        self.dao.add_or_update_item(item_name, location)
        logger.debug(f"DAO add_or_update complete for '{item_name}'")

        # 2. Notion同期 (非同期)
        if self.list_client.is_active():
            logger.info(f"Scheduling Notion check for '{item_name}'")
            background_tasks.add_task(self.list_client.remove_item, item_name)

        # 3. 賞味期限推定 (非同期) - 新機能
        #    依存オブジェクト(estimator, dao)を渡して実行
        background_tasks.add_task(
            run_estimation_task, item_name, self.estimator, self.dao
        )

    def delete(self, item_name: str, background_tasks: BackgroundTasks):
        """Deletes item from SQLite & Chroma (via DAO), then syncs Notion."""
        logger.info(f"Deleting item: '{item_name}'")

        # 1. DB削除 (SQLite + ChromaDB)
        self.dao.delete_item(item_name)
        logger.debug(f"DAO delete complete for '{item_name}'")

        # 2. Notion同期 (非同期)
        if self.list_client.is_active():
            try:
                logger.info(f"Scheduling Notion addition for '{item_name}'")
                background_tasks.add_task(self.list_client.add_item, item_name)
            except Exception as e:
                logger.error(f"Error scheduling Notion addition: {e}")

    def ask(self, item_name: str) -> str:
        """Asks where an item is using ChromaDB (accessed via DAO)."""
        logger.debug(f"Querying for: '{item_name}'")

        # DAOが保持しているcollectionを使って検索
        results = self.dao.collection.query(
            query_texts=[item_name], n_results=1, include=["distances", "metadatas"]
        )
        logger.debug(f"ChromaDB results: {results}")

        if not results["ids"] or not results["ids"][0]:
            return f"「{item_name}」に関する情報は見つかりませんでした。"

        # 類似度チェック
        distance = results["distances"][0][0]
        similarity = 1 - distance
        threshold = constants.SIMILARITY_THRESHOLD

        if similarity < threshold:
            logger.warning(
                f"Similarity {similarity:.2f} < threshold {threshold} for '{item_name}'"
            )
            return f"「{item_name}」に関する情報は見つかりませんでした。"

        location = results["metadatas"][0][0]["location"]
        found_item = results["ids"][0][0]

        # 将来的にはここで「賞味期限」も返答に含めることが可能
        # expiry = self.dao.get_item(found_item).get('expiry_date') ...

        return f"「{found_item}」は{location}にあります。"
