# ============================================================
# Conversation Memory — Load and Save Session History
#
# WHY KEEP CONVERSATION HISTORY?
# Without memory, every message is treated as the first in a conversation.
# "What about the side effects?" would be meaningless without knowing
# the user just asked about aspirin. Memory makes the chatbot coherent.
#
# STORAGE FORMAT:
# JSON files per session, stored in the memory directory.
# File naming: {session_id}.json
# The session_id is provided by the frontend (typically a UUID).
#
# TOKEN BUDGET MANAGEMENT:
# Long conversations can exceed the LLM's context window. We keep
# only the most recent messages that fit within MAX_HISTORY_TOKENS.
# We drop from the OLDEST end, never the newest — the recent context
# is always most relevant.
#
# SECURITY NOTE:
# session_id is used as a filename. A malicious user could pass
# session_id = "../../etc/passwd" (path traversal attack).
# We sanitize session_id by allowing only alphanumeric, hyphen, underscore.
# ============================================================

import json
import logging
import re
from pathlib import Path
from typing import TypedDict

from src import config

logger = logging.getLogger(__name__)

# Messages in OpenAI/Anthropic API format
class Message(TypedDict):
    role: str     # "user" or "assistant"
    content: str  # The message text


def _get_session_path(session_id: str) -> Path:
    """
    Sanitize session_id and return the path to its JSON file.

    Only alphanumeric characters, hyphens, and underscores are allowed.
    This prevents path traversal attacks (e.g. session_id = "../../etc/passwd").
    """
    # Strip everything except safe characters
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id)
    if not safe_id:
        safe_id = "default_session"
    return config.MEMORY_DIR / f"{safe_id}.json"


def load_history(session_id: str) -> list[Message]:
    """
    Load the conversation history for a session from disk.

    Args:
        session_id: Unique identifier for the conversation session.

    Returns:
        List of Message dicts in chronological order.
        Returns empty list if no history exists yet (first message in session).
    """
    session_path = _get_session_path(session_id)

    if not session_path.exists():
        logger.debug("No history found for session '%s' (new session)", session_id)
        return []

    try:
        data = json.loads(session_path.read_text(encoding="utf-8"))
        history: list[Message] = data.get("messages", [])
        logger.debug(
            "Loaded %d messages for session '%s'", len(history), session_id
        )
        return history

    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning(
            "Corrupted history for session '%s': %s. Starting fresh.",
            session_id, exc
        )
        return []


def save_history(
    session_id: str,
    user_message: str,
    assistant_response: str,
) -> None:
    """
    Append a user+assistant exchange to the session history and save to disk.

    Automatically truncates history to stay within the token budget.

    Args:
        session_id:          The session identifier.
        user_message:        What the user sent.
        assistant_response:  What the assistant replied.
    """
    # Load existing history
    history = load_history(session_id)

    # Append the new exchange
    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": assistant_response})

    # Truncate to token budget (remove oldest messages from the front)
    history = _truncate_to_token_budget(history, config.MAX_HISTORY_TOKENS)

    # Write to disk
    session_path = _get_session_path(session_id)
    config.MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    session_path.write_text(
        json.dumps({"session_id": session_id, "messages": history}, indent=2),
        encoding="utf-8",
    )
    logger.debug(
        "Saved history for session '%s' (%d messages total)",
        session_id, len(history)
    )


def format_history_for_prompt(history: list[Message]) -> str:
    """
    Format conversation history as a readable string for insertion into prompts.

    Example output:
        User: Tell me about aspirin.
        Assistant: Aspirin is a nonsteroidal anti-inflammatory drug...
        User: What about the side effects?

    Args:
        history: List of Message dicts.

    Returns:
        Formatted string, or empty string if history is empty.
    """
    if not history:
        return ""

    lines: list[str] = []
    role_labels = {"user": "User", "assistant": "Assistant", "system": "System"}
    for msg in history:
        role = role_labels.get(msg["role"], "Assistant")
        lines.append(f"{role}: {msg['content']}")

    return "\n".join(lines)


def _truncate_to_token_budget(
    history: list[Message],
    max_tokens: int,
) -> list[Message]:
    """
    Remove the oldest messages until the history fits within max_tokens.

    Token estimation: ~4 characters per token (rough but fast approximation).
    We always remove in pairs (user + assistant) to keep the conversation coherent.

    Args:
        history:    Full list of messages.
        max_tokens: Maximum allowed token count.

    Returns:
        Trimmed history list (always ends with the most recent messages).
    """
    def estimate_tokens(msgs: list[Message]) -> int:
        total_chars = sum(len(m["content"]) for m in msgs)
        return total_chars // 4  # ~4 chars per token

    while len(history) > 2 and estimate_tokens(history) > max_tokens:
        # Remove the oldest user+assistant pair (first two elements)
        history = history[2:]
        logger.debug("Truncated oldest message pair from history (budget: %d tokens)", max_tokens)

    return history


def delete_history(session_id: str) -> bool:
    """
    Delete the session history file.

    Returns:
        True if the file existed and was deleted, False if it didn't exist.
    """
    session_path = _get_session_path(session_id)
    if session_path.exists():
        session_path.unlink()
        logger.info("Deleted history for session '%s'", session_id)
        return True
    return False


# ============================================================
# PostgreSQL Conversation Store — Async Backend
#
# Mirrors the JSON-file API above but reads/writes from the
# PostgreSQL `sessions` and `messages` tables via async
# SQLAlchemy.  All methods are fully async and use the shared
# connection pool managed by src.database.postgres.
#
# The module-level convenience functions at the bottom
# (async_load_history, async_save_history, async_delete_history)
# automatically select the right backend:
#   • PostgreSQL when DATABASE_URL is set
#   • JSON files otherwise (the original code above)
# ============================================================

import uuid

from sqlalchemy import select, delete, func as sa_func

from src.data_gateway import get_gateway


class PgConversationStore:
    """
    Async conversation store backed by PostgreSQL.

    Each method mirrors its synchronous JSON-file counterpart but
    operates on the ``sessions`` and ``messages`` tables instead.
    """

    # ── Load History ──────────────────────────────────────────

    async def load_history(self, session_id: str, user_id: str = None) -> list[Message]:
        gateway = await get_gateway()
        session_obj = await gateway.get_session(session_id)
        if not session_obj:
            return []
        if user_id and session_obj["user_id"] != user_id:
            return []
            
        history = await gateway.get_session_history(session_id)
        return [{"role": m["role"], "content": m["content"]} for m in history]

    # ── Save History ──────────────────────────────────────────

    async def save_history(
        self,
        session_id: str,
        user_message: str,
        assistant_response: str,
        user_id: str = None,
        title: str = None,
        project_id: str = None,
    ) -> None:
        gateway = await get_gateway()
        session_obj = await gateway.get_session(session_id)
        if not session_obj:
            await gateway.create_session(session_id, user_id, title or "New Conversation", project_id)
        
        await gateway.save_message(session_id, "user", user_message)
        await gateway.save_message(session_id, "assistant", assistant_response)

    # ── Delete History ────────────────────────────────────────

    async def delete_history(self, session_id: str, user_id: str = None) -> bool:
        gateway = await get_gateway()
        session_obj = await gateway.get_session(session_id)
        if not session_obj:
            return False
        if user_id and session_obj["user_id"] != user_id:
            return False
            
        await gateway.delete_session(session_id)
        return True

    # ── List Sessions ─────────────────────────────────────────

    async def get_sessions(self, user_id: str) -> list[dict]:
        gateway = await get_gateway()
        return await gateway.get_sessions_for_user(user_id)


# ── Backend Switch — used by the orchestrator ─────────────────────────────────

_pg_store = PgConversationStore()

async def async_load_history(session_id: str, user_id: str = None) -> list[Message]:
    """Load history from backend."""
    return await _pg_store.load_history(session_id, user_id)

async def async_save_history(
    session_id: str,
    user_message: str,
    assistant_response: str,
    user_id: str = None,
    project_id: str = None
) -> None:
    """Save history to backend."""
    await _pg_store.save_history(session_id, user_message, assistant_response, user_id, project_id=project_id)

async def async_delete_history(session_id: str, user_id: str = None) -> bool:
    """Delete history from backend."""
    return await _pg_store.delete_history(session_id, user_id)

async def async_get_sessions(user_id: str) -> list[dict]:
    """Get all sessions for a user."""
    return await _pg_store.get_sessions(user_id)
