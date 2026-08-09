from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.rag.indexer import index_rca_chunks
from app.rag.rca_pipeline import run_rca_feedback_pipeline


router = APIRouter(prefix="/rag", tags=["RAG / RCA"])


class RcaFeedbackRequest(BaseModel):
    scoring_result: Dict[str, Any]
    peer_confirmation_available: bool = False


@router.post("/reindex")
def reindex_rca_knowledge_base(recreate: bool = True):
    try:
        return index_rca_chunks(recreate=recreate)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/feedback")
def generate_rca_feedback(request: RcaFeedbackRequest):
    try:
        return run_rca_feedback_pipeline(
            scoring_result=request.scoring_result,
            peer_confirmation_available=request.peer_confirmation_available,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
