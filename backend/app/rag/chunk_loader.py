from pathlib import Path
from typing import Any, Dict, List
import json

from app.rag.rag_config import get_rag_settings


def _load_json_list(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(
            f"RCA chunks file not found: {path}. "
            "Run: python -m app.rag.excel_parser, then python -m app.rag.rca_rule_chunker"
        )

    with path.open("r", encoding="utf-8") as f:
        chunks = json.load(f)

    if not isinstance(chunks, list):
        raise ValueError("RCA chunks JSON must contain a list of chunks.")

    return chunks


def load_rca_chunks(path: str | Path | None = None, include_custom: bool = True) -> List[Dict[str, Any]]:
    settings = get_rag_settings()
    chunk_path = Path(path) if path else Path(settings["chunks_path"])
    chunks = _load_json_list(chunk_path)

    if include_custom and path is None:
        try:
            from app.rag.custom_rule_service import get_custom_chunks

            custom_chunks = get_custom_chunks()
            existing_ids = {str(chunk.get("chunk_id")) for chunk in chunks}
            for chunk in custom_chunks:
                chunk_id = str(chunk.get("chunk_id"))
                if chunk_id and chunk_id not in existing_ids:
                    chunks.append(chunk)
                    existing_ids.add(chunk_id)
        except Exception:
            # Reindexing original Excel rules should not fail just because the
            # optional custom rule JSON file is missing or temporarily invalid.
            pass

    return chunks
