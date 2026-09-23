import logging.config
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI
from langfuse import get_client, observe
from pydantic import BaseModel

from .logging_config import LOGGING_CONFIG
from .rag_core import RAGService
from .scheduler import BackfillScheduler

# Load Logging Config
logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)


app_state = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("--- Application Starting Up ---")

    # RAGService内でDAO, Estimator, NotionShoppingListClientが初期化される
    app_state["rag_service"] = RAGService()
    service: RAGService = app_state["rag_service"]

    # 前回起動時に失敗したChromaDB削除をリトライ
    retried = service.dao.process_pending_deletions()
    if retried > 0:
        logger.info(f"Retried {retried} pending ChromaDB deletions on startup")

    # 日次バックフィル（未処理・失敗アイテムの再推定）を起動する。
    # DAO/EstimatorはRAGServiceと共有する（Pi 5でSentenceTransformerを二重ロードしないため）。
    try:
        backfill = BackfillScheduler(service.dao, service.estimator)
        backfill.start()
        app_state["backfill_scheduler"] = backfill
    except Exception as e:
        # バックフィルが起動しなくてもAPI自体は動かす
        logger.error(f"Failed to start Backfill Scheduler: {e}", exc_info=True)

    logger.info("RAG Service loaded. Application is ready.")
    yield

    # スケジューラー停止
    backfill = app_state.get("backfill_scheduler")
    if backfill is not None:
        backfill.shutdown()

    # Langfuse: 未送信のイベントをflush
    try:
        get_client().flush()
    except Exception:
        pass
    logger.info("--- Application Shutting Down ---")


app = FastAPI(lifespan=lifespan)


def sanitize_dict_keys(d: dict) -> dict:
    """Removes leading/trailing spaces from keys."""
    return {k.strip(): v for k, v in d.items()}


@app.get("/")
async def read_root():
    return {"Hello": "World"}


class DispatchRequest(BaseModel):
    text: str


@app.post("/dispatch")
@observe()
async def handle_dispatch(request: DispatchRequest, background_tasks: BackgroundTasks):
    """
    Siriからのエントリポイント。
    Intent分類 -> RAGServiceのメソッド呼び出しを行う。
    """
    logger.info(f"Received request: '{request.text}'")
    service: RAGService = app_state["rag_service"]
    response_text = "すみません、エラーが発生しました。"

    try:
        # 1. Intent Classification
        raw_intent_data = service.classify_intent(request.text)
        intent_data = sanitize_dict_keys(raw_intent_data)
        intent = intent_data.get("intent")

        logger.info(f"Classified Intent: {intent}, Data: {intent_data}")

        # 2. Routing
        if intent == "add":
            item_name = intent_data.get("item_name")
            location = intent_data.get("location")
            if item_name and location:
                # ここで background_tasks を渡すことで、賞味期限推定が裏で走る
                service.add(item_name, location, background_tasks)
                response_text = f"{item_name}を{location}にしまいました。"
            else:
                response_text = "アイテム名か場所が聞き取れませんでした。"

        elif intent == "delete":
            item_name = intent_data.get("item_name")
            if item_name:
                service.delete(item_name, background_tasks)
                response_text = f"{item_name}の削除を受け付けました。"
            else:
                response_text = "どのアイテムを削除するか分かりませんでした。"

        elif intent == "query":
            item_name = intent_data.get("item_name")
            if item_name:
                response_text = service.ask(item_name)
            else:
                response_text = "何を探しているか分かりませんでした。"

        elif intent == "unknown":
            response_text = "すみません、よくわかりませんでした。"

        else:
            logger.warning(f"Unexpected intent: {intent}")
            response_text = "すみません、意図が理解できませんでした。"

    except Exception as e:
        logger.error(f"Dispatch Error: {e}", exc_info=True)
        response_text = "内部エラーが発生しました。"

    return {"answer": response_text}
