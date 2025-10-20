import logging.config  # <-- ADD THIS
from .logging_config import LOGGING_CONFIG  # <-- ADD THIS

# Load the logging configuration DICTIONARY
logging.config.dictConfig(LOGGING_CONFIG)  # <-- ADD THIS
logger = logging.getLogger(__name__)
# Now, import everything else
from fastapi import FastAPI
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
    app_state["rag_service"].conn.close()
    logger.info("--- Application Shutting Down ---") # <-- Add log

app = FastAPI(lifespan=lifespan)

class DispatchRequest(BaseModel):
    text: str

@app.post("/dispatch")
async def handle_dispatch(request: DispatchRequest):
    """Single endpoint for Siri to call."""
    logger.info(f"Received request from Siri: '{request.text}'")
    
    service: RAGService = app_state["rag_service"]
    
    try:
        intent_data = service.classify_intent(request.text)
        intent = intent_data.get("intent")
        
        if intent == "add":
            service.add(intent_data["item_name"], intent_data["location"])
            response_text = f"{intent_data['item_name']}を{intent_data['location']}にしまいました。"
        
        elif intent == "delete":
            service.delete(intent_data["item_name"])
            response_text = f"{intent_data['item_name']}を削除しました。"
        
        elif intent == "query":
            response_text = service.ask(intent_data["item_name"])
            
        # --- ADD THIS CONDITION ---
        elif intent == "unknown":
            logger.warning(f"LLM returned 'unknown' intent. Raw text was: {request.text}")
            response_text = "すみません、よくわかりませんでした。"
        # --- END ADDITION ---
            
        else:
            logger.warning(f"Could not determine intent. Data: {intent_data}")
            response_text = "すみません、よくわかりませんでした。"
            
    except Exception as e:
        logger.error(f"An error occurred processing request: {e}", exc_info=True)
        response_text = "エラーが発生しました。"
    
    logger.info(f"Sending response to Siri: '{response_text}'")
    return {"answer": response_text}
