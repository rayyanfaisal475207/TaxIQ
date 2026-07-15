import logging
from typing import Optional

from src.llm.client import call_llm

logger = logging.getLogger(__name__)

async def update_project_memory(project_id: str, new_messages: list[dict], gateway) -> Optional[str]:
    """
    Updates the rolling summary of a project based on new messages.

    EXPLICIT CONFLICT POLICY:
    If a new fact directly contradicts an existing fact in the summary:
    - Prioritize the newer fact.
    - Append a note indicating it replaced a prior value.
    If they are about different aspects, append the new fact.
    """
    try:
        # 1. Fetch existing memory (works on both REST and direct gateways)
        memory_row = await gateway.get_project_memory(project_id)
        existing_memory = memory_row.get("summary_text", "") if memory_row else ""

        if not new_messages:
            return existing_memory

        # 2. Extract facts from new messages
        chat_text = "\n".join([f"{msg.get('role', 'user').upper()}: {msg.get('content', '')}" for msg in new_messages])

        prompt = f"""You are an intelligent summarizer maintaining a rolling memory of facts for a tax consulting project.

CURRENT MEMORY SUMMARY:
{existing_memory if existing_memory else 'No existing memory.'}

NEW CHAT EXCERPT:
{chat_text}

TASK:
Update the CURRENT MEMORY SUMMARY with any new, relevant factual information established in the NEW CHAT EXCERPT.
Strictly adhere to this conflict policy:
If a new fact directly contradicts an existing fact in the summary, prioritize the newer fact but append a note indicating it replaced a prior value. If they are about different aspects, append the new fact.

Return ONLY the updated comprehensive memory summary text, with no preamble.
"""

        updated_memory = await call_llm(prompt, "Update memory.", temperature=0.1)
        if not updated_memory or not updated_memory.strip():
            return existing_memory

        # 3. Save back to DB
        await gateway.upsert_project_memory(project_id, updated_memory.strip())
        return updated_memory

    except Exception as e:
        logger.error(f"Failed to update project memory: {e}")
        return None
