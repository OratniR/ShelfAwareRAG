import logging
import json
import chromadb
from openai import OpenAI
from sentence_transformers import SentenceTransformer
from openai import OpenAI
from .config import settings
from chromadb.api.types import EmbeddingFunction, Documents, Embeddings
import re
import logging 
from . import prompts

logger = logging.getLogger(__name__)
def extract_json_block(text: str) -> str | None:
    """
    Finds the first and last curly brace to extract a JSON block.
    """
    start_index = text.find('{')
    end_index = text.rfind('}')
    
    if start_index == -1 or end_index == -1 or end_index < start_index:
        logger.error(f"Could not find JSON block in LLM response: {text}")
        return None
    
    return text[start_index:end_index + 1]


# ... Model loading code (no changes needed) ...
print("Loading embedding model...")
embed_model = SentenceTransformer(settings.EMBEDDING_MODEL)
print("Embedding model loaded.")
# Connect to the LLM server
llm_client = OpenAI(
    api_key=settings.LLM_API_KEY,
    base_url=settings.LLM_API_BASE,
)

class ChromaEmbeddingWrapper(EmbeddingFunction):
    # ... (no changes to this class) ...
    def __init__(self, sentence_transformer_model):
        self.model = sentence_transformer_model

    def __call__(self, input: Documents) -> Embeddings:
        return self.model.encode(input).tolist()

    def name(self) -> str:
        return settings.EMBEDDING_MODEL

# ... Database Setup code (no changes needed) ...
chroma_embed_wrapper = ChromaEmbeddingWrapper(embed_model)
client = chromadb.PersistentClient(path="data/chroma_db")
collection = client.get_or_create_collection(
    name="inventory",
    embedding_function=chroma_embed_wrapper
)

# --- RAG Service Class (Add logs here) ---
class RAGService:
    def __init__(self):
        logger.info("RAGService initialized.") # <-- Add log

    def classify_intent(self, text: str) -> dict:
        """Uses the LLM to classify the user's intent."""
        logger.debug(f"Classifying intent for: '{text}'")
        system_prompt = prompts.INTENT_CLASSIFICATION_SYSTEM_PROMPT
        
        # Combine system instructions and user text into a single user message
        combined_prompt = f"{system_prompt}\n\n---\n\nユーザーの発言:\n{text}"

        response = llm_client.chat.completions.create(
            model=settings.LLM_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "user", "content": combined_prompt} 
            ],
            temperature=0.1
        )
        
        # 1. Get the raw text from the LLM
        raw_response_text = response.choices[0].message.content
        logger.debug(f"Raw LLM response (for intent): {raw_response_text}") # <-- This log is key!

        # 2. Extract the JSON block
        json_block = extract_json_block(raw_response_text)
        
        if not json_block:
            logger.error("Failed to extract JSON, returning 'unknown' intent.")
            return {"intent": "unknown", "item_name": "unknown"}

        # 3. Try to parse the *extracted* block
        try:
            return json.loads(json_block)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse extracted JSON: {e}")
            logger.error(f"Extracted block was: {json_block}")
            return {"intent": "unknown", "item_name": "unknown"}
        


    def add(self, item_name: str, location: str):
        """Adds or updates an item in the database using upsert."""
        logger.info(f"Adding/updating item: '{item_name}' at '{location}'")
        collection.upsert(
            ids=[item_name],
            metadatas=[{"location": location}],
            documents=[f"{item_name}は{location}にある"]
        )
        logger.debug(f"Upsert complete for '{item_name}'")

    def delete(self, item_name: str):
        """Deletes an item from the database by its ID."""
        logger.info(f"Deleting item: '{item_name}'")
        collection.delete(ids=[item_name])
        logger.debug(f"Delete complete for '{item_name}'")

    def ask(self, item_name: str) -> str:
        """Asks the RAG system where an item is."""
        logger.debug(f"Querying for: '{item_name}'")
        
        results = collection.query(
            query_texts=[item_name],
            n_results=1
        )
        logger.debug(f"ChromaDB results: {results}") # <-- Add log

        if not results["ids"][0]:
            logger.warning(f"No results found for '{item_name}'")
            return f"「{item_name}」に関する情報は見つかりませんでした。"

        location = results["metadatas"][0][0]["location"]
        found_item = results["ids"][0][0]
        context = f"- {found_item}は{location}にある。"
        logger.debug(f"Generated context: {context}") # <-- Add log

        # ... (prompt setup and LLM call) ...
        prompt = prompts.RAG_QUERY_SYSTEM_PROMPT.format(
            context=context,
            item_name=item_name
        )
        
        response = llm_client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        answer = response.choices[0].message.content
        logger.debug(f"LLM generated final answer: {answer}") # <-- Add log
        return answer