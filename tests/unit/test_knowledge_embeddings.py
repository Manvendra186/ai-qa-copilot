"""S5.2 (embedding seam) unit tests — fake-server suite + guarded DB round-trip.

Covers (build bible §19 S5.2, exit: "fake-embedding unit tests green;
lexical path unchanged"):

- `EmbeddingProvider` protocol + `OpenAICompatibleEmbeddingProvider` via
  `httpx.MockTransport`: success parse/order, 501/503 →
  `EmbeddingUnavailable` (the graceful lexical-fallback signal), other HTTP
  errors + malformed payloads → `EmbeddingError` (fail loud), transport
  retry, input guards;
- `cosine_similarity` + `vector_search` — deterministic ranking;
- `hybrid_search` — vector mode, graceful fallback to the **unchanged**
  S5.1 lexical result on 501/503/unreachable, no-provider / no-vectors
  lexical mode, top-k ≤ 5 cap, validation;
- `persist` — `embeddings`-table round-trip (upsert idempotency, dimension
  + non-finite guards); skipped when the dev database or its tables are
  unavailable, always rolled back so it leaves no dev-DB trace.
"""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Callable, Generator

import httpx
import pytest
import sqlalchemy as sa
from qa_copilot_knowledge import (
    EmbeddingError,
    EmbeddingProvider,
    EmbeddingUnavailable,
    HybridSearchResult,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIndex,
    KnowledgeSourceType,
    OpenAICompatibleEmbeddingProvider,
    cosine_similarity,
    embed_and_store,
    hybrid_search,
    load_document_embeddings,
    store_document_embedding,
    vector_search,
)
from qa_copilot_repository import db, models
from sqlalchemy.orm import Session

MODEL = "fake-embedder"
BASE_URL = "http://embeddings.test/v1"
DIM = models.VECTOR_DIM
Handler = Callable[[httpx.Request], httpx.Response]


def _vec(*coords: float) -> list[float]:
    """A deterministic VECTOR_DIM vector with *coords* at those positions."""
    out = [0.0] * DIM
    for i, value in enumerate(coords):
        out[i] = value
    return out


def _payload(*vectors: list[float]) -> dict[str, object]:
    """A well-formed OpenAI-compatible `/embeddings` response body."""
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": list(v)} for i, v in enumerate(vectors)
        ],
        "model": MODEL,
        "usage": {"prompt_tokens": 3, "total_tokens": 3},
    }


def _provider(handler: Handler) -> OpenAICompatibleEmbeddingProvider:
    return OpenAICompatibleEmbeddingProvider(
        BASE_URL, MODEL, transport=httpx.MockTransport(handler)
    )


def _ok_handler(*vectors: list[float]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/embeddings")
        body = json.loads(request.content)
        assert body["model"] == MODEL
        return httpx.Response(200, json=_payload(*vectors))

    return handler


class _IncompleteProvider:
    """Has `embed` but no `model` property — must NOT satisfy the protocol."""

    def embed(self, texts: list[str]) -> list[object]:
        raise NotImplementedError


def test_real_provider_satisfies_protocol() -> None:
    assert isinstance(_provider(_ok_handler(_vec(1.0))), EmbeddingProvider)


def test_protocol_requires_model_property() -> None:
    assert not isinstance(_IncompleteProvider(), EmbeddingProvider)


def test_embed_orders_results_by_input_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"object": "embedding", "index": 1, "embedding": _vec(1.0, 1.0)},
                    {"object": "embedding", "index": 0, "embedding": _vec(1.0)},
                ],
                "model": "server-side-name",
            },
        )

    provider = _provider(handler)
    results = provider.embed(["alpha", "beta"])
    assert [r.index for r in results] == [0, 1]
    assert results[0].vector == _vec(1.0)
    assert results[1].vector == _vec(1.0, 1.0)
    assert results[0].model == "server-side-name"


def test_embed_multiple_texts_sends_batch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["input"] == ["one", "two", "three"]
        return httpx.Response(200, json=_payload(_vec(1.0), _vec(2.0), _vec(3.0)))

    provider = _provider(handler)
    results = provider.embed(["one", "two", "three"])
    assert [r.vector[0] for r in results] == [1.0, 2.0, 3.0]


def test_embed_empty_texts_raises_value_error() -> None:
    provider = _provider(_ok_handler(_vec(1.0)))
    with pytest.raises(ValueError):
        provider.embed([])


def test_blank_base_url_rejected() -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleEmbeddingProvider("  ", MODEL)


def test_blank_model_rejected() -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleEmbeddingProvider(BASE_URL, " ")


def test_negative_max_retries_rejected() -> None:
    with pytest.raises(ValueError):
        OpenAICompatibleEmbeddingProvider(BASE_URL, MODEL, max_retries=-1)


# --- error mapping -----------------------------------------------------------


@pytest.mark.parametrize("status", [501, 503])
def test_501_and_503_raise_unavailable(status: int) -> None:
    provider = _provider(lambda _r: httpx.Response(status, json={"error": "nope"}))
    try:
        provider.embed(["x"])
    except EmbeddingUnavailable as exc:
        assert exc.status == status
    else:
        pytest.fail(f"expected EmbeddingUnavailable for HTTP {status}")


@pytest.mark.parametrize("status", [400, 404, 500])
def test_other_http_errors_fail_loud(status: int) -> None:
    provider = _provider(lambda _r: httpx.Response(status, text="boom"))
    try:
        provider.embed(["x"])
    except EmbeddingUnavailable:
        pytest.fail(f"HTTP {status} must fail loud, not degrade gracefully")
    except EmbeddingError as exc:
        assert exc.status == status
    else:
        pytest.fail(f"expected EmbeddingError for HTTP {status}")


def test_non_json_success_body_fails_loud() -> None:
    provider = _provider(lambda _r: httpx.Response(200, text="not json"))
    with pytest.raises(EmbeddingError):
        provider.embed(["x"])


MALFORMED_PAYLOADS: list[object] = [
    "a bare string",
    42,
    None,
    {},
    {"data": "not a list"},
    {"data": [42]},
    {"data": [{"embedding": [1.0]}]},
    {"data": [{"index": "0", "embedding": [1.0]}]},
    {"data": [{"index": True, "embedding": [1.0]}]},
    {"data": [{"index": 0}]},
    {"data": [{"index": 0, "embedding": []}]},
    {"data": [{"index": 0, "embedding": [1.0, "x"]}]},
    {"data": [{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [2.0]}]},
    {"data": [{"index": 1, "embedding": [1.0]}]},
]


@pytest.mark.parametrize("payload", MALFORMED_PAYLOADS)
def test_malformed_payload_fails_loud(payload: object) -> None:
    provider = _provider(lambda _r: httpx.Response(200, json=payload))
    with pytest.raises(EmbeddingError):
        provider.embed(["hello"])


def test_nan_embedding_fails_loud() -> None:
    # httpx refuses to JSON-encode NaN, so the body is served as raw text.
    provider = _provider(
        lambda _r: httpx.Response(200, text='{"data": [{"index": 0, "embedding": [NaN]}]}')
    )
    with pytest.raises(EmbeddingError, match="non-finite"):
        provider.embed(["hello"])


# --- retry -------------------------------------------------------------------


def test_transport_error_retries_then_succeeds() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json=_payload(_vec(1.0)))

    provider = _provider(handler)
    results = provider.embed(["x"])
    assert calls == 2
    assert results[0].vector == _vec(1.0)


def test_transport_error_exhausts_retries_into_unavailable() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connection refused")

    provider = OpenAICompatibleEmbeddingProvider(
        BASE_URL, MODEL, max_retries=2, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(EmbeddingUnavailable):
        provider.embed(["x"])
    assert calls == 3


# --- cosine_similarity -------------------------------------------------------


def test_cosine_known_values() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([1.0, 0.0], [1.0, 1.0]) == pytest.approx(math.sqrt(2.0) / 2.0)
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_dimension_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="dimension mismatch"):
        cosine_similarity([1.0], [1.0, 0.0])


# --- vector_search -----------------------------------------------------------


def _chunk(chunk_id: str, ref: str) -> KnowledgeChunk:
    content = f"content of {chunk_id}"
    return KnowledgeChunk(
        id=chunk_id,
        document_ref=ref,
        source_type=KnowledgeSourceType.DOCUMENT,
        title=chunk_id,
        content=content,
        chunk_index=0,
        char_count=len(content),
    )


def test_vector_search_ranks_by_cosine_and_skips_unvectorized() -> None:
    chunks = [_chunk("c1", "d1"), _chunk("c2", "d2"), _chunk("c3", "d3")]
    vectors = {"c1": _vec(1.0, 1.0), "c3": _vec(0.0, 1.0)}  # c2 has no vector
    hits = vector_search(chunks, vectors, _vec(1.0))
    assert [h.chunk.id for h in hits] == ["c1", "c3"]
    assert hits[0].score == pytest.approx(math.sqrt(2.0) / 2.0)
    assert hits[0].matched_terms == []


def test_vector_search_ties_break_on_chunk_id() -> None:
    chunks = [_chunk("c2", "d2"), _chunk("c1", "d1")]
    hits = vector_search(chunks, {"c1": _vec(1.0), "c2": _vec(1.0)}, _vec(1.0))
    assert [h.chunk.id for h in hits] == ["c1", "c2"]
    assert all(h.score == pytest.approx(1.0) for h in hits)


def test_vector_search_empty_when_no_vectors() -> None:
    assert vector_search([_chunk("c1", "d")], {}, _vec(1.0)) == []


# --- hybrid_search -----------------------------------------------------------


def _corpus(n: int = 3) -> KnowledgeIndex:
    docs = [
        KnowledgeDocument(
            source_type=KnowledgeSourceType.DOCUMENT,
            source_ref=f"doc-{i}",
            title=f"document {i}",
            content=f"the content number {i} is about alpha beta",
        )
        for i in range(n)
    ]
    return KnowledgeIndex(docs)


def test_hybrid_vector_mode_ranks_by_cosine() -> None:
    index = _corpus()
    vectors = {
        index.chunks[0].id: _vec(1.0, 1.0),
        index.chunks[1].id: _vec(0.0, 1.0),
        index.chunks[2].id: _vec(-1.0),
    }
    provider = _provider(_ok_handler(_vec(1.0)))
    result = hybrid_search(index, "query", provider=provider, vectors=vectors)
    assert result.mode == "vector"
    assert result.provider == MODEL
    assert [h.chunk.id for h in result.result.hits] == [
        index.chunks[0].id,
        index.chunks[1].id,
        index.chunks[2].id,
    ]
    assert result.result.total_candidates == 3
    assert result.result.truncated is False


def test_hybrid_vector_mode_works_without_lexical_overlap() -> None:
    index = _corpus()
    provider = _provider(_ok_handler(_vec(1.0)))
    result = hybrid_search(
        index,
        "zzz qqq",  # no lexical overlap with the corpus
        provider=provider,
        vectors={index.chunks[0].id: _vec(1.0)},
    )
    assert result.mode == "vector"
    assert [h.chunk.id for h in result.result.hits] == [index.chunks[0].id]


def _hybrid_501_result(index: KnowledgeIndex) -> HybridSearchResult:
    """hybrid_search against a 501 endpoint — must fall back to lexical."""
    provider = _provider(lambda _r: httpx.Response(501, json={"error": "no"}))
    return hybrid_search(
        index,
        "content alpha",
        provider=provider,
        vectors={index.chunks[0].id: _vec(1.0)},
    )


def test_hybrid_501_falls_back_to_unchanged_lexical_result() -> None:
    index = _corpus()
    result = _hybrid_501_result(index)
    assert result.mode == "lexical"
    assert result.provider is None
    # S5.2 exit criterion: the lexical path is unchanged.
    assert result.result == index.search("content alpha")
    assert result.result.hits  # non-empty — the lexical path still finds hits


def test_hybrid_503_falls_back_to_lexical() -> None:
    index = _corpus()
    provider = _provider(lambda _r: httpx.Response(503, text="unavailable"))
    result = hybrid_search(
        index,
        "content alpha",
        provider=provider,
        vectors={index.chunks[0].id: _vec(1.0)},
    )
    assert result.mode == "lexical"


def test_hybrid_unreachable_endpoint_falls_back_to_lexical() -> None:
    index = _corpus()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = _provider(handler)
    result = hybrid_search(
        index,
        "content alpha",
        provider=provider,
        vectors={index.chunks[0].id: _vec(1.0)},
    )
    assert result.mode == "lexical"
    assert result.result == index.search("content alpha")


def test_hybrid_no_provider_is_lexical() -> None:
    index = _corpus()
    result = hybrid_search(index, "content alpha")
    assert result.mode == "lexical"
    assert result.result == index.search("content alpha")


def test_hybrid_no_vectors_is_lexical() -> None:
    index = _corpus()
    provider = _provider(_ok_handler(_vec(1.0)))
    result = hybrid_search(index, "content alpha", provider=provider, vectors={})
    assert result.mode == "lexical"


def test_hybrid_unknown_vector_keys_fall_back_to_lexical() -> None:
    index = _corpus()
    provider = _provider(_ok_handler(_vec(1.0)))
    result = hybrid_search(index, "content alpha", provider=provider, vectors={"nope": _vec(1.0)})
    assert result.mode == "lexical"


def test_hybrid_top_k_capped_at_five() -> None:
    index = _corpus(6)
    vectors = {chunk.id: _vec(1.0) for chunk in index.chunks}
    provider = _provider(_ok_handler(_vec(1.0)))
    result = hybrid_search(index, "q", provider=provider, vectors=vectors, top_k=50)
    assert len(result.result.hits) == 5
    assert result.result.truncated is True
    assert result.result.total_candidates == 6


def test_hybrid_query_and_top_k_validation() -> None:
    index = _corpus()
    with pytest.raises(ValueError):
        hybrid_search(index, "   ")
    with pytest.raises(ValueError):
        hybrid_search(index, "q", top_k=0)


def test_hybrid_dimension_mismatch_fails_loud() -> None:
    index = _corpus()
    provider = _provider(_ok_handler([0.1, 0.2, 0.3]))  # 3-D query vector
    with pytest.raises(ValueError, match="dimension mismatch"):
        hybrid_search(index, "q", provider=provider, vectors={index.chunks[0].id: _vec(1.0)})


# --- persist: embeddings table (live dev DB, rolled back) -------------------


def _engine_or_skip() -> sa.Engine | None:
    engine = sa.create_engine(db.get_database_url(), pool_pre_ping=True)
    try:
        with engine.connect():
            pass
    except sa.exc.OperationalError:
        engine.dispose()
        return None
    return engine


def _skip_if_no_embeddings_table(session: Session) -> None:
    has_tables: object
    try:
        has_tables = bool(
            session.scalar(sa.text("SELECT to_regclass('public.embeddings') IS NOT NULL"))
        )
    except sa.exc.SQLAlchemyError:
        pytest.skip("embeddings schema not migrated")
    if not has_tables:
        pytest.skip("embeddings table not migrated")


@pytest.fixture()
def doc_row() -> Generator[tuple[Session, models.KnowledgeDocument], None, None]:
    """A stored knowledge document in a transaction that is rolled back."""
    engine = _engine_or_skip()
    if engine is None:
        pytest.skip("dev database unreachable")
    session = db.make_session_factory(engine)()
    _skip_if_no_embeddings_table(session)
    org_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    conn = session.connection()
    conn.execute(
        sa.insert(models.Organization),
        {"id": org_id, "name": "s52-embeddings", "plan": "dev"},
    )
    conn.execute(
        sa.insert(models.Project),
        {
            "id": project_id,
            "organization_id": org_id,
            "name": "s52-embeddings",
            "settings": {},
        },
    )
    conn.execute(
        sa.insert(models.KnowledgeDocument),
        {
            "id": doc_id,
            "project_id": project_id,
            "source_type": "standard",
            "source_ref": "s52-embeddings",
            "content": "S5.2 persistence test document",
            "metadata": {},
        },
    )
    document = session.get(models.KnowledgeDocument, doc_id)
    assert document is not None
    try:
        yield session, document
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _dim_vector(seed: float) -> list[float]:
    """A deterministic VECTOR_DIM vector (finite, non-zero)."""
    return [seed * (i + 1) for i in range(models.VECTOR_DIM)]


def test_store_and_load_round_trip_and_upsert(
    doc_row: tuple[Session, models.KnowledgeDocument],
) -> None:
    session, document = doc_row
    first = _dim_vector(0.1)
    row = store_document_embedding(session, document, first)
    assert row.knowledge_document_id == document.id
    assert list(row.vector) == first
    assert load_document_embeddings(session, [document.id]) == {document.id: first}
    second = _dim_vector(0.2)
    updated = store_document_embedding(session, document, second)
    assert updated.id == row.id  # same row — idempotent upsert
    assert list(updated.vector) == second
    assert len(load_document_embeddings(session, [document.id])) == 1


def test_store_wrong_dimension_fails_loud(
    doc_row: tuple[Session, models.KnowledgeDocument],
) -> None:
    session, document = doc_row
    with pytest.raises(EmbeddingError, match="dimension"):
        store_document_embedding(session, document, [0.1, 0.2, 0.3])
    assert load_document_embeddings(session, [document.id]) == {}


def test_store_non_finite_fails_loud(
    doc_row: tuple[Session, models.KnowledgeDocument],
) -> None:
    session, document = doc_row
    with pytest.raises(EmbeddingError, match="non-finite"):
        store_document_embedding(session, document, [float("nan")] * models.VECTOR_DIM)
    assert load_document_embeddings(session, [document.id]) == {}


def test_embed_and_store_uses_provider(
    doc_row: tuple[Session, models.KnowledgeDocument],
) -> None:
    session, document = doc_row
    vector = _dim_vector(0.3)
    provider = _provider(lambda _r: httpx.Response(200, json=_payload(vector)))
    row = embed_and_store(session, provider, document, "some text to embed")
    assert list(row.vector) == vector
