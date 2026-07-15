# ============================================================
# Router — LLM Call 2: Does This Query Need Retrieval?
#
# PURPOSE:
# Not every question requires searching the document store.
# "Hello! How are you?" doesn't need retrieval.
# "What is the bleeding risk of aspirin?" definitely does.
#
# Routing correctly has two benefits:
# 1. Speed: skipping retrieval makes conversational responses instant
# 2. Quality: retrieving documents for a general question can inject
#    irrelevant context that confuses the final response
#
# THE PROMPT STRATEGY (FEW-SHOT):
# The router prompt includes 10 example Q→YES/NO pairs.
# This "few-shot prompting" dramatically improves accuracy compared to
# just describing the rules in words. The examples serve as calibration
# data embedded directly in the prompt.
#
# OUTPUT FORMAT:
# Strictly JSON conforming to the schema in the prompt.
# ============================================================

import logging
import json
from pathlib import Path

from src.llm.client import call_llm

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "prompts" / "router.txt"
_SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")


async def route_query(rewritten_query: str) -> dict:
    """
    Decide the route and output format for the query.

    Args:
        rewritten_query: The standalone query from the query rewriter.

    Returns:
        Dict: {"route": str, "output_format": str, "confidence": str, "reason": str}
    """
    response = await call_llm(
        system_prompt=_SYSTEM_PROMPT,
        user_message=rewritten_query,
        temperature=0.0,
        # 100 tokens could truncate the JSON mid-"reason", forcing the
        # fallback RAG route on perfectly routable queries.
        max_tokens=250,
        provider_override="groq",
    )

    try:
        import re
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if match:
            cleaned = match.group(0)
        else:
            cleaned = response
        result = json.loads(cleaned.strip())
        
        # Ensure default values if LLM misses them
        route = result.get("route", "RAG").upper()
        if route not in ["DIRECT", "RAG", "WEB", "SQL"]:
            route = "RAG"
            
        output_format = result.get("output_format", "chat").lower()
        if output_format not in ["chat", "file_xlsx", "file_docx", "file_pdf"]:
            output_format = "chat"
            
        return {
            "route": route,
            "output_format": output_format,
            "target_year": result.get("target_year", None),
            "confidence": result.get("confidence", "high").lower(),
            "reason": result.get("reason", "No reason provided")
        }
    except Exception as e:
        logger.error("Router failed to parse JSON: %s. Raw response: %s", e, response)
        return {
            "route": "RAG",
            "output_format": "chat",
            "confidence": "low",
            "reason": "Failed to parse router output, defaulting to RAG"
        }

