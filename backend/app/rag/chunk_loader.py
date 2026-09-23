from pathlib import Path
from typing import Any, Dict, List
import json

from app.rag.rag_config import get_rag_settings


def _load_json_list(path: Path) -> List[Dict[str, Any]]:
    """Load an explicit JSON seed/backup file.

    Normal runtime indexing no longer uses these files as the source of truth;
    they remain useful for one-time migration and offline regeneration.
    """

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
    """Load RCA chunks for indexing.

    With no explicit path, Supabase-backed Analysis rules are authoritative and
    both original and custom chunks are rebuilt from there. Passing ``path`` is
    reserved for migration/offline tooling that intentionally reads a JSON file.
    """

    if path is not None:
        return _load_json_list(Path(path))

    from app.rag.custom_rule_service import get_all_chunks_for_indexing, load_base_chunks

    if include_custom:
        return get_all_chunks_for_indexing()
    return load_base_chunks()
