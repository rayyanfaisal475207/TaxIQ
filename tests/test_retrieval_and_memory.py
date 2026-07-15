"""
Retrieval primitives (RRF, BM25, chunking) and conversation memory.

These are pure functions — no mocking needed, so they're the cheapest place to
pin down behaviour the rest of the pipeline depends on.
"""
import pytest

from src.retrieval.reranker import reciprocal_rank_fusion, rerank_results
from src.retrieval.bm25_retriever import retrieve_bm25
from src.ingestion.chunker import split_text_into_chunks
from src.memory.conversation import (
    format_history_for_prompt,
    _truncate_to_token_budget,
)


def _chunk(chunk_id, text="some text"):
    return {"id": chunk_id, "text": text, "metadata": {"source": "doc.pdf"}}


# ── Reciprocal Rank Fusion ────────────────────────────────────────────────────

def test_rrf_ranks_a_document_found_by_both_retrievers_first():
    """
    The whole point of RRF: agreement across signals beats a single strong hit.
    'a' is the top semantic hit but invisible to BM25; 'b' places well in both,
    so 'b' must win.
    """
    semantic = [_chunk("a"), _chunk("b"), _chunk("c")]
    bm25 = [_chunk("b")]

    fused = reciprocal_rank_fusion([semantic, bm25], top_k=3)

    assert fused[0]["id"] == "b", "the doc ranked by BOTH retrievers should win"


def test_rrf_is_order_stable_for_tied_documents():
    """A fully symmetric input ties on score; ranking must still be deterministic."""
    semantic = [_chunk("a"), _chunk("b"), _chunk("c")]
    bm25 = [_chunk("c"), _chunk("b"), _chunk("a")]

    first = reciprocal_rank_fusion([semantic, bm25], top_k=3)
    second = reciprocal_rank_fusion([semantic, bm25], top_k=3)

    assert [c["id"] for c in first] == [c["id"] for c in second]


def test_rrf_deduplicates_documents():
    semantic = [_chunk("a"), _chunk("b")]
    bm25 = [_chunk("a"), _chunk("b")]

    fused = reciprocal_rank_fusion([semantic, bm25], top_k=10)

    assert len(fused) == 2
    assert {c["id"] for c in fused} == {"a", "b"}


def test_rrf_attaches_a_score():
    fused = reciprocal_rank_fusion([[_chunk("a")], [_chunk("a")]], top_k=1)
    assert fused[0]["rrf_score"] > 0


def test_rrf_respects_top_k():
    semantic = [_chunk(str(i)) for i in range(10)]
    assert len(reciprocal_rank_fusion([semantic], top_k=3)) == 3


def test_rrf_handles_one_empty_retriever():
    """BM25 returns nothing on a query with no lexical overlap — must not crash."""
    fused = reciprocal_rank_fusion([[_chunk("a")], []], top_k=5)
    assert [c["id"] for c in fused] == ["a"]


def test_rrf_handles_both_empty():
    assert reciprocal_rank_fusion([[], []], top_k=5) == []


def test_rerank_results_is_the_orchestrator_facing_wrapper():
    """The orchestrator calls this two-list wrapper, not RRF directly."""
    reranked = rerank_results([_chunk("a"), _chunk("b")], [_chunk("b")], top_k=1)

    assert len(reranked) == 1
    assert reranked[0]["id"] == "b"


# ── BM25 ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def corpus():
    return [
        _chunk("1", "The capital gains tax on property disposal is calculated as follows"),
        _chunk("2", "Withholding tax on dividends paid to filers is fifteen percent"),
        _chunk("3", "Sales tax registration threshold is annual turnover of ten million"),
        _chunk("4", "Penalty for late filing of a return under section 182 of the ordinance"),
    ]


def test_bm25_ranks_lexically_matching_documents_higher(corpus):
    results = retrieve_bm25("dividends withholding filers", corpus, top_k=4)

    assert results[0]["id"] == "2"


def test_bm25_attaches_a_score(corpus):
    results = retrieve_bm25("dividends", corpus, top_k=1)

    assert results[0]["bm25_score"] > 0


def test_bm25_drops_documents_with_no_lexical_overlap(corpus):
    """Zero-score docs are filtered out — they contribute nothing to fusion."""
    results = retrieve_bm25("dividends", corpus, top_k=4)

    assert [r["id"] for r in results] == ["2"]


def test_bm25_on_empty_corpus_returns_nothing():
    assert retrieve_bm25("anything", [], top_k=5) == []


def test_bm25_contributes_nothing_on_a_tiny_corpus():
    """
    Documents a real BM25Okapi quirk: on a corpus of 2-3 docs, IDF for a term
    present in half of them goes negative and is clamped to zero, so every score
    is 0 and the >0 filter drops everything. Harmless in production (the
    candidate pool is TOP_K_RETRIEVAL chunks), but it means BM25 silently adds
    no signal if that pool ever shrinks — RRF then rides on semantic alone.
    """
    tiny = [
        _chunk("1", "capital gains tax on property"),
        _chunk("2", "withholding tax on dividends for filers"),
    ]

    assert retrieve_bm25("dividends filers", tiny, top_k=2) == []


# ── Chunking ──────────────────────────────────────────────────────────────────

def test_chunking_respects_the_size_limit():
    text = "word " * 500

    chunks = split_text_into_chunks(text, chunk_size=100, chunk_overlap=20)

    assert chunks
    assert all(len(c) <= 100 for c in chunks)


def test_chunking_never_cuts_mid_word():
    text = "supercalifragilistic " * 30

    chunks = split_text_into_chunks(text, chunk_size=50, chunk_overlap=10)

    for chunk in chunks:
        assert not chunk.startswith("ic "), "a word was split across chunks"


def test_short_text_stays_a_single_chunk():
    assert split_text_into_chunks("A short clause.", chunk_size=512) == ["A short clause."]


def test_empty_text_yields_no_chunks():
    assert split_text_into_chunks("", chunk_size=512) == []


# ── Conversation memory ───────────────────────────────────────────────────────

def test_history_is_formatted_with_speaker_labels():
    history = [
        {"role": "user", "content": "What is the WHT rate?"},
        {"role": "assistant", "content": "15% for filers."},
    ]

    formatted = format_history_for_prompt(history)

    assert "User: What is the WHT rate?" in formatted
    assert "Assistant: 15% for filers." in formatted


def test_injected_system_context_is_labelled_system_not_assistant():
    """
    Regression: project context is injected as a system message, but the
    formatter labelled every non-user message "Assistant" — so the model read
    the project brief as something it had said itself.
    """
    history = [{"role": "system", "content": "Client exports towels"}]

    formatted = format_history_for_prompt(history)

    assert formatted.startswith("System:")


def test_empty_history_formats_to_empty_string():
    assert format_history_for_prompt([]) == ""


def test_token_budget_drops_the_oldest_turns_first():
    history = [
        {"role": "user", "content": "old question " * 100},
        {"role": "assistant", "content": "old answer " * 100},
        {"role": "user", "content": "recent question"},
        {"role": "assistant", "content": "recent answer"},
    ]

    trimmed = _truncate_to_token_budget(history, max_tokens=50)

    assert trimmed[-1]["content"] == "recent answer", "the newest turn must survive"
    assert len(trimmed) < len(history)


def test_token_budget_keeps_history_under_the_limit_when_possible():
    history = [{"role": "user", "content": "x" * 4000},
               {"role": "assistant", "content": "y" * 4000},
               {"role": "user", "content": "short"},
               {"role": "assistant", "content": "short"}]

    trimmed = _truncate_to_token_budget(history, max_tokens=100)

    assert sum(len(m["content"]) for m in trimmed) // 4 <= 100


def test_short_history_is_untouched():
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert _truncate_to_token_budget(history, max_tokens=1000) == history
