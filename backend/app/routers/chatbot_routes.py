from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.ai_chatbot_service import handle_chatbot_message, friendly_chatbot_error_message


router = APIRouter(prefix="/api/chatbot", tags=["AI Chatbot"])


class ChatbotMessageRequest(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = None
    form_params: Optional[Dict[str, Any]] = None


class ChatbotMessageResponse(BaseModel):
    reply: str
    context: Dict[str, Any]


@router.post("/message", response_model=ChatbotMessageResponse)
def chatbot_message(request: ChatbotMessageRequest):
    try:
        if not request.message or not request.message.strip():
            raise HTTPException(status_code=400, detail="Message is required.")

        result = handle_chatbot_message(
            message=request.message,
            context=request.context or {},
            form_params=request.form_params or {},
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=friendly_chatbot_error_message(exc))
