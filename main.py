# main.py
from fastapi import FastAPI
from .rag_core import RAGService  # Your RAG logic
from pydantic import BaseModel

class DispatchRequest(BaseModel):
    text: str  # The Japanese text from Siri

app = FastAPI()
# Load your RAG service (and its models) once on startup
rag_service = RAGService()

@app.post("/dispatch")
async def handle_dispatch(request: DispatchRequest):
    """
    Single endpoint for Siri. It receives text and routes
    it to the correct RAG service action.
    """
    
    # 1. Classify the user's intent (add, query, or delete)
    intent, data = rag_service.classify_intent(request.text)
    
    # 2. Route to the correct action
    if intent == "query":
        answer = rag_service.ask(data["item_name"])
        return {"answer": answer}
        
    elif intent == "add":
        rag_service.add(
            item_name=data["item_name"], 
            location=data["location"]
        )
        return {"answer": f"{data['item_name']}を{data['location']}にしまいました。"}
        
    elif intent == "delete":
        rag_service.delete(data["item_name"])
        return {"answer": f"{data['item_name']}を空にしました。"}

    else:
        return {"answer": "すみません、よくわかりませんでした。"}