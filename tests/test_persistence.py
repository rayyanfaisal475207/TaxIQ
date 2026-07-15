"""
Chat persistence — the bug class that made conversations vanish.

Guards:
  * Sessions must ALWAYS be created with an owner. Ownerless rows are invisible
    in the sidebar (filtered by user_id) and 403 on reopen.
  * REST inserts must supply client-side UUIDs — the Supabase tables have no
    server-side defaults for message_id / users.id / file_id / call_id, so an
    omitted key means a NOT NULL violation and the write is silently lost.
  * Both gateway backends must implement the same protocol surface, since the
    app switches between them by network (direct at the office, REST at home).
"""
import uuid
from unittest.mock import MagicMock

import pytest

from src.data_gateway.base import DataGateway
from src.data_gateway.direct_backend import DirectGateway
from src.data_gateway.rest_backend import RestGateway
from src.database.pipeline_logger import PgPipelineLogger


# ── Session ownership ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_saved_session_is_visible_to_its_owner(gateway, user_id, session_id):
    """A session created for a user must come back from the sidebar query."""
    await gateway.create_session(session_id, user_id, "Dividend WHT", None)

    listed = await gateway.get_sessions_for_user(user_id)

    assert [s["session_id"] for s in listed] == [session_id]


@pytest.mark.asyncio
async def test_history_round_trips(gateway, user_id, session_id):
    """Messages saved during a chat must load back in order."""
    await gateway.create_session(session_id, user_id, "T", None)
    await gateway.save_message(session_id, "user", "What is the WHT rate?")
    await gateway.save_message(session_id, "assistant", "15% for filers.")

    history = await gateway.get_session_history(session_id)

    assert [(m["role"], m["content"]) for m in history] == [
        ("user", "What is the WHT rate?"),
        ("assistant", "15% for filers."),
    ]


@pytest.mark.asyncio
async def test_conversation_store_creates_session_with_owner(patched_gateway, user_id, session_id):
    """
    Regression: save_history used to create the session without a user_id when
    no row existed yet, orphaning every conversation.
    """
    from src.memory.conversation import async_save_history

    await async_save_history(session_id, "hello", "hi there", user_id)

    session = await patched_gateway.get_session(session_id)
    assert session is not None
    assert session["user_id"] == user_id, "session was created without an owner"


@pytest.mark.asyncio
async def test_load_history_rejects_other_users_session(patched_gateway, user_id, session_id):
    """History must not leak across users."""
    from src.memory.conversation import async_load_history

    await patched_gateway.create_session(session_id, str(uuid.uuid4()), "someone else's", None)
    await patched_gateway.save_message(session_id, "user", "secret")

    history = await async_load_history(session_id, user_id)

    assert history == []


class _FakeDb:
    """Minimal async-session double for exercising PgPipelineLogger."""

    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.committed = False

    async def execute(self, _stmt):
        existing = self.existing

        class _Result:
            def scalars(self):
                class _Scalars:
                    def first(self_inner):
                        return existing
                return _Scalars()
        return _Result()

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_pipeline_logger_creates_session_with_owner(monkeypatch, user_id, session_id):
    """
    Regression (the original root cause): the pipeline logger ran FIRST and
    inserted Session(session_id=...) with no user_id. save_history then saw a
    row already existed and never set the owner — so user_id stayed NULL forever.
    """
    from contextlib import asynccontextmanager

    db = _FakeDb(existing=None)

    @asynccontextmanager
    async def _fake_session():
        yield db

    monkeypatch.setattr("src.database.postgres.get_session", _fake_session)

    await PgPipelineLogger().upsert_session(session_id, user_id=user_id)

    assert db.added, "no session row was created"
    assert db.added[0].user_id == uuid.UUID(user_id), "session created without an owner"


@pytest.mark.asyncio
async def test_pipeline_logger_backfills_legacy_null_owner(monkeypatch, user_id, session_id):
    """Legacy ownerless sessions self-heal on the next message."""
    from contextlib import asynccontextmanager

    class _LegacySession:
        def __init__(self):
            self.session_id = uuid.UUID(session_id)
            self.user_id = None
            self.updated_at = None

    legacy = _LegacySession()
    db = _FakeDb(existing=legacy)

    @asynccontextmanager
    async def _fake_session():
        yield db

    monkeypatch.setattr("src.database.postgres.get_session", _fake_session)

    await PgPipelineLogger().upsert_session(session_id, user_id=user_id)

    assert legacy.user_id == uuid.UUID(user_id), "legacy NULL owner was not backfilled"


# ── REST backend: client-side UUIDs (Supabase has no defaults) ─────────────────

def _rest_gateway_with_mock_client(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")

    captured = {}

    def _fake_create_client(url, key):
        client = MagicMock()

        def table(name):
            tbl = MagicMock()

            def insert(data):
                captured.setdefault(name, []).append(data)
                result = MagicMock()
                result.execute = MagicMock(return_value=MagicMock(
                    data=[data if isinstance(data, dict) else data[0]]
                ))
                return result

            tbl.insert = insert
            return tbl

        client.table = table
        return client

    monkeypatch.setattr("src.data_gateway.rest_backend.create_client", _fake_create_client)
    return RestGateway(), captured


@pytest.mark.asyncio
async def test_rest_save_message_supplies_message_id(monkeypatch, session_id):
    """
    Regression: messages.message_id is NOT NULL with no default. Omitting it
    meant EVERY message insert failed — chats were never saved in REST mode.
    """
    gw, captured = _rest_gateway_with_mock_client(monkeypatch)

    await gw.save_message(session_id, "user", "hello")

    row = captured["messages"][0]
    assert uuid.UUID(row["message_id"])  # present and a valid UUID
    assert row["session_id"] == session_id


@pytest.mark.asyncio
async def test_rest_create_user_supplies_id(monkeypatch):
    """Regression: users.id is NOT NULL with no default — registration 500'd."""
    gw, captured = _rest_gateway_with_mock_client(monkeypatch)

    await gw.create_user({"email": "a@b.com", "password_hash": "x"})

    row = captured["users"][0]
    assert uuid.UUID(row["id"])
    assert row["plan"] == "free"
    assert row["is_admin"] is False


@pytest.mark.asyncio
async def test_rest_log_generated_file_supplies_file_id(monkeypatch, session_id, user_id):
    """Regression: generated_files.file_id is NOT NULL with no default."""
    gw, captured = _rest_gateway_with_mock_client(monkeypatch)

    file_id = await gw.log_generated_file({
        "session_id": session_id, "user_id": user_id, "file_type": "pdf",
        "file_name": "Report.pdf", "file_size_bytes": 10, "storage_path": "/tmp/x.pdf",
    })

    assert uuid.UUID(str(file_id))
    assert uuid.UUID(captured["generated_files"][0]["file_id"])


@pytest.mark.asyncio
async def test_rest_query_similar_chunks_normalizes_row_shape(monkeypatch):
    """
    Regression: the match_documents RPC returns {chunk_id, chunk_text,
    source_file, similarity}, but BM25/reranker/evaluator expect
    {id, text, metadata, rrf_score}. The mismatch crashed RAG with KeyError('text').
    """
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")

    def _fake_create_client(url, key):
        client = MagicMock()
        rpc_result = MagicMock()
        rpc_result.execute = MagicMock(return_value=MagicMock(data=[{
            "chunk_id": "c1",
            "chunk_text": "Dividend income is taxed at 15%.",
            "source_file": "ITO_2001.pdf",
            "similarity": 0.87,
        }]))
        client.rpc = MagicMock(return_value=rpc_result)
        return client

    monkeypatch.setattr("src.data_gateway.rest_backend.create_client", _fake_create_client)

    chunks = await RestGateway().query_similar_chunks("dividends", [0.1, 0.2], top_k=5)

    assert chunks[0]["id"] == "c1"
    assert chunks[0]["text"] == "Dividend income is taxed at 15%."
    assert chunks[0]["metadata"]["source"] == "ITO_2001.pdf"
    assert chunks[0]["rrf_score"] == pytest.approx(0.87)


# ── Backend parity ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("backend", [DirectGateway, RestGateway])
def test_backend_implements_full_gateway_protocol(backend):
    """
    Both backends are live (direct at the office, REST at home). A method added
    to one but not the other is a crash waiting for a change of network.
    """
    required = [
        name for name in vars(DataGateway)
        if not name.startswith("_") and callable(getattr(DataGateway, name, None))
    ]

    missing = [name for name in required if not hasattr(backend, name)]

    assert not missing, f"{backend.__name__} is missing: {missing}"


# ── Direct-backend SQL correctness (regressions found during live IPv6 testing) ──
# These guard bugs the fake-gateway suite can't see, since it never touches SQL.
# They exercise pure/static logic only — no live DB — so they stay network-free.

def test_direct_naive_utc_strips_timezone():
    """
    Regression: pipeline_runs / pipeline_steps / error_logs are
    `timestamp without time zone`. Binding a tz-aware datetime raised
    "can't subtract offset-naive and offset-aware datetimes" under asyncpg,
    breaking every dashboard latency/usage/error query on the direct backend.
    """
    from src.data_gateway.direct_backend import DirectGateway

    dt = DirectGateway._naive_utc("2026-06-15T07:57:11.104980+00:00")
    assert dt.tzinfo is None
    assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 6, 15, 7)

    # A "Z" suffix must also parse to a naive datetime.
    dt2 = DirectGateway._naive_utc("2026-06-15T07:57:11Z")
    assert dt2.tzinfo is None


def test_direct_query_casts_embedding_to_vector():
    """
    Regression: the hybrid-search SQL must cast the embedding param to ::vector.
    Bound as a bare string the HNSW index scan ran ~740ms; with the cast it
    drops to ~1ms. Assert the cast is present so it can't silently regress.
    """
    import inspect
    from src.data_gateway.direct_backend import DirectGateway

    src = inspect.getsource(DirectGateway.query_similar_chunks)
    assert "(:query_embedding)::vector" in src, "embedding param must be cast to ::vector for the HNSW index"


def test_direct_mcp_calls_uses_real_columns():
    """
    Regression: get_mcp_calls referenced started_at / completed_at /
    error_message, none of which exist on mcp_tool_calls — it raised on every
    call. It must use created_at / duration_ms / output_summary.
    """
    import inspect
    from src.data_gateway.direct_backend import DirectGateway

    src = inspect.getsource(DirectGateway.get_mcp_calls)
    # Check attribute ACCESS, not comment text (the comment names the old columns).
    assert "McpToolCall.created_at" in src
    assert "c.started_at" not in src
    assert "c.completed_at" not in src
    assert "c.error_message" not in src
