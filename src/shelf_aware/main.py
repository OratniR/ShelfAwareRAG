import logging.config  # <-- ADD THIS
from .logging_config import LOGGING_CONFIG  # <-- ADD THIS

# Load the logging configuration DICTIONARY
logging.config.dictConfig(LOGGING_CONFIG)  # <-- ADD THIS
logger = logging.getLogger(__name__)
# Now, import everything else
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from contextlib import asynccontextmanager
from .rag_core import RAGService

# This dictionary will hold our services
app_state = {}

# ... app_state and lifespan code (no changes needed) ...
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("--- Application Starting Up ---") # <-- Add log
    print("Loading RAG Service...") # This print is still fine
    app_state["rag_service"] = RAGService()
    logger.info("RAG Service loaded.") # <-- Add log
    print("Service loaded. Application is ready.")
    yield
    logger.info("--- Application Shutting Down ---") # <-- Add log

app = FastAPI(lifespan=lifespan)

def sanitize_dict_keys(d: dict) -> dict:
    """Removes leading/trailing spaces from keys in a dictionary."""
    sanitized = {}
    for key, value in d.items():
        sanitized_key = key.strip() # Remove leading/trailing spaces
        # You could add more aggressive cleaning like removing all spaces:
        # sanitized_key = "".join(key.split()) 
        sanitized[sanitized_key] = value
    return sanitized

@app.get("/")
async def read_root():
    logger.info("Root endpoint was hit!")
    return {"Hello": "World"}


class DispatchRequest(BaseModel):
    text: str

@app.post("/dispatch")
async def handle_dispatch(request: DispatchRequest, background_tasks: BackgroundTasks):
    """Single endpoint for Siri to call."""
    logger.info(f"Received request from Siri: '{request.text}'")

    service: RAGService = app_state["rag_service"]

    try:
        # 1. Classify intent
        raw_intent_data = service.classify_intent(request.text)
        logger.info(f"Raw LLM classified intent data: {raw_intent_data}")

        # --- ADD THIS LINE TO FIX KEYS ---
        intent_data = sanitize_dict_keys(raw_intent_data)
        if raw_intent_data != intent_data:
            logger.warning(f"Sanitized LLM keys. Original: {raw_intent_data}, Sanitized: {intent_data}")
        # --- END ADDITION ---

        intent = intent_data.get("intent")

        # 2. Route to the correct action
        if intent == "add":
            # Check if location is present before accessing
            item_name = intent_data.get("item_name")
            location = intent_data.get("location")
            if item_name and location:
                service.add(item_name, location, background_tasks)
                response_text = f"{item_name}を{location}にしまいました。"
            else:
                logger.error(f"Missing 'item_name' or 'location' for add intent. Data: {intent_data}")
                response_text = "すみません、アイテム名か場所がわかりませんでした。"

        elif intent == "delete":
            item_name = intent_data.get("item_name")
            if item_name:
                service.delete(item_name, background_tasks)
                response_text = f"{item_name}を削除しました。"
            else:
                logger.error(f"Missing 'item_name' for delete intent. Data: {intent_data}")
                response_text = "すみません、どのアイテムかわかりませんでした。"

        elif intent == "query":
            item_name = intent_data.get("item_name")
            if item_name:
                response_text = service.ask(item_name)
            else:
                logger.error(f"Missing 'item_name' for query intent. Data: {intent_data}")
                response_text = "すみません、どのアイテムかわかりませんでした。"

        elif intent == "unknown":
            logger.warning(f"LLM returned 'unknown' intent. Raw text was: {request.text}")
            response_text = "すみません、よくわかりませんでした。"

        else: # Should not happen if LLM returns valid intent
            logger.warning(f"Unexpected intent value. Data: {intent_data}")
            response_text = "すみません、よくわかりませんでした。"

    except Exception as e:
        # Log the full error including traceback
        logger.error(f"An error occurred processing request: {e}", exc_info=True)
        response_text = "エラーが発生しました。"

    logger.info(f"Sending response to Siri: '{response_text}'")
    return {"answer": response_text}
