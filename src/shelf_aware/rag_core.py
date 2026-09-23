import json
import logging

from fastapi import BackgroundTasks
from langfuse import get_client, observe
from langfuse.openai import OpenAI

from shelf_aware.estimation import (  # Enumをインポート
    EstimationResult,
    apply_estimation_outcome,
    update_langfuse,
)

from . import constants, prompts

# 設定と定数
from .config import settings
from .constants import STATUS_FINALIZED
from .database import InventoryDAO  # <--- 追加: DB操作の委譲先
from .estimation import ExpirationEstimator  # <--- 追加: 賞味期限推定

# 外部連携モジュール
from .notion_handler import NotionShoppingListClient

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


def current_trace_id() -> str | None:
    """現在のLangfuseトレースIDを返す（取得できなければNone）。"""
    try:
        return get_client().get_current_trace_id()
    except Exception:
        return None


# --- LLM Client Setup ---
# Intent分類用 (EmbeddingモデルはInventoryDAO内で管理されるためここでは不要)
llm_client = OpenAI(
    api_key=settings.LLM_API_KEY,
    base_url=settings.LLM_API_BASE,
)

# --- Background Task Function ---
# 依存関係(dao, estimator)を持つため、今回はサービスクラス内のメソッドとして呼び出す形をとる


@observe(name="run_estimation_task", capture_input=False, capture_output=False)
async def run_estimation_task(
    item_name: str,
    estimator: ExpirationEstimator,
    dao: InventoryDAO,
    dispatch_trace_id: str | None = None,
):
    """
    賞味期限推定を実行し、結果に応じてDBを更新する。

    - 失敗は is_estimated=3 として記録される（未処理のまま放置しない）。
    - estimator/dao はLangfuseに記録しない（BraveのAPIキーとDB接続がトレースに載るため）。
    """
    logger.info(f"⏳ Estimating expiration for: {item_name}")
    update_langfuse("span", input={"item_name": item_name, "dispatch_trace_id": dispatch_trace_id})

    try:
        # 既に推定が確定しているアイテムは再推定しない（Piの限られたAPIクォータの節約）
        current = dao.get_item(item_name)
        if current and current.get("is_estimated") in STATUS_FINALIZED:
            logger.info(f"⏭️ Already finalized (status={current['is_estimated']}): {item_name}")
            update_langfuse("span", output={"status": "already_finalized", "is_estimated": current["is_estimated"]})
            return

        # 結果セットを取得
        outcome = await estimator.estimate_expiration(item_name, dao)

        # 結果をDBへ反映（dispatch / バックフィル / 手動スクリプトで共通の処理）
        apply_estimation_outcome(dao, item_name, outcome)

        update_langfuse("span", output=outcome.as_dict())
        if outcome.status == EstimationResult.ERROR:
            update_langfuse("span", level="ERROR", status_message=(outcome.reason or "estimation_error")[:300])

    except Exception as e:
        logger.error(f"❌ Estimation task failed for {item_name}: {e}", exc_info=True)
        update_langfuse("span", level="ERROR", status_message=str(e)[:300])


class RAGService:
    def __init__(self):
        # 全てのデータ操作はDAO経由で行うことで、SQLiteとChromaの整合性を保つ
        self.dao = InventoryDAO()
        self.list_client = NotionShoppingListClient()
        self.estimator = ExpirationEstimator()
        logger.info("RAGService initialized with DAO and Estimator.")

    @observe()
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
                max_tokens=30,
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

    @observe()
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
        #    BackgroundTaskは別トレースになるため、元の /dispatch のトレースIDを渡して追跡可能にする
        background_tasks.add_task(run_estimation_task, item_name, self.estimator, self.dao, current_trace_id())

    @observe()
    def delete(self, item_name: str, background_tasks: BackgroundTasks):
        """Deletes item from SQLite (sync) & Chroma (async via BackgroundTasks), then syncs Notion."""
        logger.info(f"Deleting item: '{item_name}'")

        # 1. Pending登録 (同期) — ChromaDB削除の受付証
        self.dao.add_pending_deletion(item_name)

        # 2. SQLite削除 (同期・高速)
        self.dao.delete_item_from_sqlite(item_name)
        logger.debug(f"SQLite delete complete for '{item_name}'")

        # 3. ChromaDB削除 + pending解消 (非同期・重い処理)
        background_tasks.add_task(self.dao.delete_item_from_chroma_with_cleanup, item_name)

        # 4. Notion同期 (非同期)
        if self.list_client.is_active():
            try:
                logger.info(f"Scheduling Notion addition for '{item_name}'")
                background_tasks.add_task(self.list_client.add_item, item_name)
            except Exception as e:
                logger.error(f"Error scheduling Notion addition: {e}")

    @observe()
    def ask(self, item_name: str) -> str:
        """Asks where an item is using ChromaDB (accessed via DAO)."""
        logger.debug(f"Querying for: '{item_name}'")

        # DAOが保持しているcollectionを使って検索
        results = self.dao.collection.query(query_texts=[item_name], n_results=1, include=["distances", "metadatas"])
        logger.debug(f"ChromaDB results: {results}")

        if not results["ids"] or not results["ids"][0]:
            return f"「{item_name}」に関する情報は見つかりませんでした。"

        # 類似度チェック
        distance = results["distances"][0][0]
        similarity = 1 - distance
        threshold = constants.SIMILARITY_THRESHOLD

        if similarity < threshold:
            logger.warning(f"Similarity {similarity:.2f} < threshold {threshold} for '{item_name}'")
            return f"「{item_name}」に関する情報は見つかりませんでした。"

        location = results["metadatas"][0][0]["location"]
        found_item = results["ids"][0][0]

        # 将来的にはここで「賞味期限」も返答に含めることが可能
        # expiry = self.dao.get_item(found_item).get('expiry_date') ...

        return f"「{found_item}」は{location}にあります。"
