import logging
import json
from pathlib import Path

from src.llm.client import call_llm
from src.data_gateway import get_gateway

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "citation_validator.txt"
_SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")

async def validate_citations(
    session_id: str,
    response_text: str,
    documents_text: str,
    target_date: int = None,
    top_chunks: list[dict] = None
) -> list[str]:
    """
    Validate the generated response against retrieved chunks.
    
    This is designed to run asynchronously after the stream finishes,
    to flag any hallucinations without blocking the UX.
    
    Returns a list of unverified sentences.
    """
    unverified = []

    # Deterministic temporal check
    if target_date and top_chunks:
        invalid_docs = []
        for chunk in top_chunks:
            meta = chunk.get("metadata", {})
            ef = meta.get("effective_from")
            et = meta.get("effective_to")
            
            if ef and ef > target_date:
                invalid_docs.append(meta.get("source", "Unknown"))
            elif et and et < target_date:
                invalid_docs.append(meta.get("source", "Unknown"))
                
        if invalid_docs:
            unverified.append(f"Response relies on temporally invalid documents for target date {target_date}: {', '.join(set(invalid_docs))}")

    user_message = f"SOURCES:\n{documents_text}\n\nRESPONSE:\n{response_text}"
    
    try:
        # We use standard call_llm (which uses config.LLM_PROVIDER).
        # To reduce cost in production, this could be hardcoded to a smaller/cheaper model like LLaMA-3-8B or GPT-4o-mini.
        response = await call_llm(
            system_prompt=_SYSTEM_PROMPT,
            user_message=user_message,
            temperature=0.0,
            max_tokens=500,
        )
        
        cleaned = response.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
            
        result = json.loads(cleaned.strip())
        llm_unverified = result.get("unverified_sentences", [])
        unverified.extend(llm_unverified)
        
        if unverified:
            logger.warning("Citation Validator found %d unverified claims.", len(unverified))
        else:
            logger.info("Citation Validator verified all claims.")
            
        # Update the database
        await _update_message_citations(session_id, response_text, unverified)
            
        return unverified
        
    except Exception as e:
        logger.error("Citation Validator failed: %s", e)
        return []

async def _update_message_citations(session_id: str, response_text: str, unverified: list[str]):
    """Update the most recent assistant message in this session with citation validation results."""
    try:
        gateway = await get_gateway()
        await gateway.update_message_citations(session_id, response_text, unverified)
        logger.info("Updated message citations in database")
    except Exception as e:
        logger.error("Failed to update message citations in DB: %s", e)
