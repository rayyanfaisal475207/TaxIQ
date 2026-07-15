"""
Orchestrator — the full pipeline, driven end to end with a fake LLM and a fake
gateway. No network, no database.

Guards the behaviour that broke in production:
  * the session is created WITH its owner, and the exchange is persisted
  * user settings (language, context) reach EVERY answer path, not just RAG
  * a DIRECT-routed request can still produce a file ("make me a PDF of X")
  * file-generation failures are surfaced as error events, never swallowed
  * per-token events are not written to the step log (that blocked the loop)
"""
import pytest

import src.pipeline.orchestrator as orch


@pytest.fixture
def run_pipeline(monkeypatch, patched_gateway):
    """
    Drive process_query with every external dependency faked.
    Returns (events, gateway) for assertions.
    """
    async def _run(message="What is the WHT rate on dividends?",
                   route='{"route": "DIRECT", "output_format": "chat"}',
                   answer="The rate is 15 percent.",
                   user_profile=None,
                   project_id=None,
                   session_id="11111111-1111-1111-1111-111111111111",
                   user_id="22222222-2222-2222-2222-222222222222"):

        async def fake_call_llm(system_prompt, user_message, **kwargs):
            # Order matters: match the most specific prompt first (the file
            # structurer prompt also mentions "title").
            sp = (system_prompt or "").lower()
            if "structurer" in sp:
                return ('{"title": "WHT Rates", "sections": [{"type": "table", '
                        '"headers": ["Section", "Rate"], "rows": [["150", "15%"]]}]}')
            if "routing engine" in sp:
                return route
            if "rewrit" in sp or "search-query" in sp:
                return message
            if "evaluat" in sp:
                return '{"relevant": true, "reason": "covered"}'
            if "generate a short, descriptive title" in sp:
                return "Dividend WHT"
            return answer

        async def fake_stream_llm(system_prompt, user_message, **kwargs):
            fake_stream_llm.last_system = system_prompt
            fake_stream_llm.last_kwargs = kwargs
            for token in answer.split(" "):
                yield token + " "

        fake_stream_llm.last_system = ""
        fake_stream_llm.last_kwargs = {}

        # LLM boundaries
        monkeypatch.setattr(orch, "call_llm", fake_call_llm)
        monkeypatch.setattr(orch, "stream_llm", fake_stream_llm)
        monkeypatch.setattr("src.pipeline.router.call_llm", fake_call_llm)
        monkeypatch.setattr("src.pipeline.query_rewriter.call_llm", fake_call_llm)
        monkeypatch.setattr("src.pipeline.evaluator.call_llm", fake_call_llm)
        monkeypatch.setattr("src.pipeline.title_generator.call_llm", fake_call_llm)
        monkeypatch.setattr("src.pipeline.file_structurer.call_llm", fake_call_llm)

        # Retrieval boundaries
        async def fake_embed(_text, **kwargs):
            return [0.1] * 8

        async def fake_expand(_q, n=2):
            return []

        chunks = [
            {"id": "c1", "text": "Dividends are taxed at 15%.",
             "metadata": {"source": "ITO.pdf"}, "rrf_score": 0.9}
        ]
        patched_gateway.chunks = chunks

        async def fake_query_similar(_query, _embedding, top_k=10, where=None):
            return chunks

        monkeypatch.setattr(orch, "embed_text", fake_embed)
        monkeypatch.setattr(orch, "query_similar", fake_query_similar)
        monkeypatch.setattr("src.pipeline.query_expander.expand_query", fake_expand)

        # SQLite audit log — irrelevant here, and it would touch disk
        for fn in ("upsert_session", "create_query", "log_step", "log_llm_call",
                   "log_retrieved_docs", "update_retrieved_docs_relevance", "update_query"):
            monkeypatch.setattr(orch.pipeline_logger, fn,
                                lambda *a, **k: 1, raising=False)

        events = []
        async for event in orch.process_query(
            session_id, message, project_id=project_id,
            user_profile=user_profile, user_id=user_id,
        ):
            events.append(event)

        _run.stream = fake_stream_llm
        return events, patched_gateway

    return _run


def _text_of(events):
    return "".join(e["detail"] for e in events
                   if e["step"] == "response" and e["status"] == "streaming")


# ── Persistence ───────────────────────────────────────────────────────────────

async def test_pipeline_creates_the_session_with_its_owner(run_pipeline):
    events, gateway = await run_pipeline()

    session = await gateway.get_session("11111111-1111-1111-1111-111111111111")
    assert session is not None
    assert session["user_id"] == "22222222-2222-2222-2222-222222222222"


async def test_pipeline_persists_the_exchange(run_pipeline):
    events, gateway = await run_pipeline(message="hello", answer="hi there")

    history = await gateway.get_session_history("11111111-1111-1111-1111-111111111111")
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["content"] == "hello"
    assert "hi there" in history[1]["content"]
    assert any(e["step"] == "memory" and e["status"] == "done" for e in events)


async def test_pipeline_derives_user_id_from_the_profile(run_pipeline):
    """The chat endpoint passes a profile; user_id must still land on the session."""
    events, gateway = await run_pipeline(
        user_id=None,
        user_profile={"id": "33333333-3333-3333-3333-333333333333",
                      "context_text": "", "preferred_language": "English", "llm_mode": "cloud"},
    )

    session = await gateway.get_session("11111111-1111-1111-1111-111111111111")
    assert session["user_id"] == "33333333-3333-3333-3333-333333333333"


async def test_streaming_tokens_are_not_written_to_the_step_log(run_pipeline):
    """
    Regression: every token was logged as a pipeline step — hundreds of blocking
    writes per answer, stalling the stream.
    """
    events, gateway = await run_pipeline(answer="one two three four five")

    assert not any(s["status"] == "streaming" for s in gateway.steps)


# ── Personalization reaches every path ────────────────────────────────────────

async def test_direct_answers_honour_the_language_setting(run_pipeline):
    """Regression: language/context applied only to the RAG path."""
    await run_pipeline(
        route='{"route": "DIRECT", "output_format": "chat"}',
        user_profile={"id": "u", "context_text": "I run a textile SME",
                      "preferred_language": "Urdu", "llm_mode": "cloud"},
    )

    system_prompt = run_pipeline.stream.last_system
    assert "Urdu" in system_prompt
    assert "textile SME" in system_prompt


async def test_rag_answers_honour_the_language_setting(run_pipeline):
    await run_pipeline(
        route='{"route": "RAG", "output_format": "chat"}',
        message="What is the WHT rate on dividends under section 150?",
        user_profile={"id": "u", "context_text": "I run a textile SME",
                      "preferred_language": "Urdu", "llm_mode": "cloud"},
    )

    system_prompt = run_pipeline.stream.last_system
    assert "Urdu" in system_prompt
    assert "textile SME" in system_prompt


async def test_llm_mode_setting_is_passed_to_the_client(run_pipeline):
    """The llm_mode setting used to save but never be read by anything."""
    await run_pipeline(
        user_profile={"id": "u", "context_text": "", "preferred_language": "English",
                      "llm_mode": "local"},
    )

    assert run_pipeline.stream.last_kwargs.get("llm_mode") == "local"


async def test_project_context_is_injected(run_pipeline, patched_gateway):
    project = await patched_gateway.create_project(
        {"user_id": "u", "name": "Textile Co", "domain_context": "Client exports towels"}
    )
    await patched_gateway.upsert_project_memory(project["id"], "Prior year turnover was 50M")

    await run_pipeline(project_id=project["id"])

    system_prompt = run_pipeline.stream.last_system
    assert "Client exports towels" in system_prompt
    assert "Prior year turnover was 50M" in system_prompt


# ── File generation ───────────────────────────────────────────────────────────

async def test_direct_route_can_still_produce_a_file(run_pipeline):
    """
    Regression: the DIRECT path returned before the file-generation block, so
    "make me a PDF of X" produced no file and no error.
    """
    events, gateway = await run_pipeline(
        route='{"route": "DIRECT", "output_format": "file_xlsx"}',
        message="Make me an excel of the dividend rates",
    )

    done = [e for e in events if e["step"] == "file_generation" and e["status"] == "done"]
    assert done, "DIRECT route produced no file"
    assert gateway.files, "no file record was persisted"

    import os
    for record in gateway.files.values():
        assert record["file_name"].endswith(".xlsx"), "download name must carry its extension"
        os.remove(record["storage_path"])


async def test_generated_file_is_owned_by_the_user(run_pipeline):
    events, gateway = await run_pipeline(
        route='{"route": "DIRECT", "output_format": "file_pdf"}',
    )

    import os
    record = next(iter(gateway.files.values()))
    assert record["user_id"] == "22222222-2222-2222-2222-222222222222"
    os.remove(record["storage_path"])


async def test_file_generation_failure_is_surfaced_not_swallowed(run_pipeline, monkeypatch):
    """
    Regression: builder crashes were logged server-side and the user just saw
    an answer with no file and no explanation.
    """
    def _boom(_payload):
        raise RuntimeError("reportlab exploded")

    monkeypatch.setattr(orch, "build_pdf", _boom)

    events, _ = await run_pipeline(route='{"route": "DIRECT", "output_format": "file_pdf"}')

    errors = [e for e in events if e["step"] == "file_generation" and e["status"] == "error"]
    assert errors, "file generation failed silently"
    assert "reportlab exploded" in errors[0]["detail"]


# ── Routing ───────────────────────────────────────────────────────────────────

async def test_rag_route_retrieves_and_answers(run_pipeline):
    events, _ = await run_pipeline(
        route='{"route": "RAG", "output_format": "chat"}',
        message="What is the WHT rate on dividends under section 150?",
        answer="Fifteen percent.",
    )

    steps = {e["step"] for e in events}
    assert {"retrieval", "reranker", "evaluator", "response"} <= steps
    assert "Fifteen" in _text_of(events)


async def test_greeting_short_circuits_retrieval(run_pipeline):
    """Greetings must not pay for retrieval — it's pure latency."""
    events, _ = await run_pipeline(message="hello", answer="Hi! How can I help?")

    skipped = {e["step"] for e in events if e["status"] == "skipped"}
    assert "retrieval" in skipped
