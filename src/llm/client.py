import logging
import asyncio
from typing import AsyncGenerator, Optional

from google import genai
from google.genai import types

from groq import AsyncGroq
from openai import AsyncOpenAI

from src import config
from src.llm.key_manager import key_manager

logger = logging.getLogger(__name__)

# ── Cached SDK clients ─────────────────────────────────────────────────────────
# Building a new client per call adds connection setup to every LLM call.
# Cache one client per (provider, api_key) — key rotation still works because
# the rotated key produces a new cache entry.

_groq_clients: dict[str, AsyncGroq] = {}
_gemini_clients: dict[str, genai.Client] = {}
_local_client: Optional[AsyncOpenAI] = None


def _get_groq_client() -> AsyncGroq:
    api_key = key_manager.get_current_key("groq")
    if api_key not in _groq_clients:
        _groq_clients[api_key] = AsyncGroq(api_key=api_key)
    return _groq_clients[api_key]


def _get_gemini_client() -> genai.Client:
    api_key = key_manager.get_current_key("gemini")
    if api_key not in _gemini_clients:
        _gemini_clients[api_key] = genai.Client(api_key=api_key)
    return _gemini_clients[api_key]


def _get_local_client() -> Optional[AsyncOpenAI]:
    """OpenAI-compatible local model endpoint (e.g. LM Studio / vLLM via ngrok).

    Opt-in via LOCAL_LLM_URL. Never used unless explicitly configured — a
    hardcoded always-first local endpoint previously routed the entire
    pipeline through a 2B model when up, and added a connection-timeout tax
    to every call when down.
    """
    global _local_client
    if not config.LOCAL_LLM_URL:
        return None
    if _local_client is None:
        _local_client = AsyncOpenAI(
            base_url=f"{config.LOCAL_LLM_URL.rstrip('/')}/v1",
            api_key=config.LOCAL_LLM_API_KEY or "local-key",
            timeout=config.LOCAL_LLM_TIMEOUT,
        )
    return _local_client


def _use_local(llm_mode: Optional[str]) -> bool:
    return bool(config.LOCAL_LLM_URL) and (llm_mode or "").lower() == "local"


def _is_rate_limit(exc: Exception) -> bool:
    err_str = str(exc)
    return "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "rate limit" in err_str.lower()


# ── Public API ─────────────────────────────────────────────────────────────────

async def call_llm(
    system_prompt: str,
    user_message: str,
    temperature: float = 0.0,
    max_tokens: int = 1000,
    provider_override: str = None,
    llm_mode: str = None,
) -> str:
    provider = provider_override or config.LLM_PROVIDER

    # ── Optional local model (only when the user chose it in settings) ──
    if _use_local(llm_mode):
        try:
            return await _call_local(system_prompt, user_message, temperature, max_tokens)
        except Exception as e:
            logger.warning(f"Local LLM failed: {e}. Falling back to {provider}...")

    max_retries = 3
    for attempt in range(max_retries):
        try:
            if provider == "groq":
                return await _call_groq(system_prompt, user_message, temperature, max_tokens)
            else:
                return await _call_gemini(system_prompt, user_message, temperature, max_tokens)
        except Exception as e:
            if _is_rate_limit(e):
                logger.warning(f"Rate limit hit on {provider}. Rotating key... (Attempt {attempt+1}/{max_retries})")
                key_manager.rotate_key(provider)
                await asyncio.sleep(2)
            else:
                raise e
    raise Exception(f"Failed to call {provider} after {max_retries} attempts due to rate limits.")


async def stream_llm(
    system_prompt: str,
    user_message: str,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    provider_override: str = None,
    enable_tools: bool = False,
    llm_mode: str = None,
) -> AsyncGenerator[str, None]:
    provider = provider_override or config.LLM_PROVIDER

    # ── Optional local model (only when the user chose it in settings) ──
    if _use_local(llm_mode):
        try:
            async for chunk in _stream_local(system_prompt, user_message, temperature, max_tokens):
                yield chunk
            return
        except Exception as e:
            logger.warning(f"Local LLM stream failed: {e}. Falling back to {provider}...")

    max_retries = 3
    for attempt in range(max_retries):
        try:
            if provider == "groq":
                async for chunk in _stream_groq(system_prompt, user_message, temperature, max_tokens, enable_tools):
                    yield chunk
            else:
                async for chunk in _stream_gemini(system_prompt, user_message, temperature, max_tokens, enable_tools):
                    yield chunk
            return
        except Exception as e:
            if _is_rate_limit(e):
                logger.warning(f"Rate limit hit on {provider} (stream). Rotating key... (Attempt {attempt+1}/{max_retries})")
                key_manager.rotate_key(provider)
                await asyncio.sleep(2)
            else:
                raise e
    raise Exception(f"Failed to stream {provider} after {max_retries} attempts due to rate limits.")


# ── Local (OpenAI-compatible) ─────────────────────────────────────────────────

async def _call_local(system_prompt: str, user_message: str, temperature: float, max_tokens: int) -> str:
    client = _get_local_client()
    response = await client.chat.completions.create(
        model=config.LOCAL_LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
    )
    content = response.choices[0].message.content
    if content is None:
        raise ValueError("Local LLM returned empty content")
    return content


async def _stream_local(system_prompt: str, user_message: str, temperature: float, max_tokens: int):
    client = _get_local_client()
    stream = await client.chat.completions.create(
        model=config.LOCAL_LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True
    )

    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


# ── Gemini ────────────────────────────────────────────────────────────────────

async def _call_gemini(system_prompt: str, user_message: str, temperature: float, max_tokens: int) -> str:
    client = _get_gemini_client()
    response = await client.aio.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            max_output_tokens=max_tokens,
        ),
    )
    return response.text


async def _stream_gemini(system_prompt: str, user_message: str, temperature: float, max_tokens: int, enable_tools: bool = False):
    # Fully async streaming — the previous implementation iterated a
    # synchronous generator inside the async function, blocking the event
    # loop (and every other request) between chunks.
    client = _get_gemini_client()
    stream = await client.aio.models.generate_content_stream(
        model=config.GEMINI_MODEL,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            max_output_tokens=max_tokens,
        ),
    )
    async for chunk in stream:
        if chunk.text:
            yield chunk.text


async def call_gemini_with_search(user_message: str, max_tokens: int = 1500) -> tuple[str, list[dict]]:
    """
    Calls Gemini using the Google Search tool.
    Returns a tuple of (generated_text, list_of_sources).
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            client = _get_gemini_client()
            response = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=user_message,
                config=types.GenerateContentConfig(
                    tools=[{'google_search': {}}],
                    temperature=0.3,
                    max_output_tokens=max_tokens
                )
            )

            text = response.text or ""
            sources = []

            # Extract URLs from grounding metadata
            if response.candidates and response.candidates[0].grounding_metadata:
                metadata = response.candidates[0].grounding_metadata
                if hasattr(metadata, "grounding_chunks") and metadata.grounding_chunks:
                    for chunk in metadata.grounding_chunks:
                        if hasattr(chunk, "web") and chunk.web:
                            sources.append({
                                "title": getattr(chunk.web, "title", "Google Search Result"),
                                "url": getattr(chunk.web, "uri", getattr(chunk.web, "url", ""))
                            })

            return text, sources

        except Exception as e:
            if _is_rate_limit(e):
                logger.warning(f"Rate limit hit on Gemini Search. Rotating key... (Attempt {attempt+1}/{max_retries})")
                key_manager.rotate_key("gemini")
                await asyncio.sleep(2)
            else:
                raise e

    raise Exception(f"Failed to use Gemini Search after {max_retries} attempts.")


# ── Groq ──────────────────────────────────────────────────────────────────────

async def _call_groq(system_prompt: str, user_message: str, temperature: float, max_tokens: int) -> str:
    client = _get_groq_client()
    response = await client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        model=config.GROQ_MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content


async def _stream_groq(system_prompt: str, user_message: str, temperature: float, max_tokens: int, enable_tools: bool = False):
    from src.llm.tools import TOOL_DEFINITIONS, execute_tool

    client = _get_groq_client()

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ]
    tools = TOOL_DEFINITIONS if enable_tools else None

    stream = await client.chat.completions.create(
        messages=messages,
        model=config.GROQ_MODEL,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
        tools=tools,
        tool_choice="auto" if enable_tools else "none"
    )

    tool_calls = {}
    async for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in tool_calls:
                    tool_calls[idx] = {"id": tc.id, "name": tc.function.name, "arguments": ""}
                if tc.function.arguments:
                    tool_calls[idx]["arguments"] += tc.function.arguments
        elif delta.content:
            yield delta.content

    if tool_calls:
        # Append the assistant's tool calls to messages
        messages.append({
            "role": "assistant",
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"]
                    }
                } for tc in tool_calls.values()
            ]
        })

        # Execute tools and append tool results
        for tc in tool_calls.values():
            result = await execute_tool(tc["name"], tc["arguments"])
            messages.append({
                "tool_call_id": tc["id"],
                "role": "tool",
                "name": tc["name"],
                "content": result,
            })
            # Yield event dict to caller (orchestrator)
            yield {"tool_call": tc["name"], "args": tc["arguments"], "result": result}

        # Second stream call with tool results
        stream2 = await client.chat.completions.create(
            messages=messages,
            model=config.GROQ_MODEL,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True
        )
        async for chunk in stream2:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
