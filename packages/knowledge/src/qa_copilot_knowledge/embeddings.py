"""Embedding seam (build bible §19 S5.2) — the upgrade path past lexical search.

The local LLM endpoint is completion-only (``POST /v1/embeddings`` → 501,
§19 S5.0 note), so retrieval stays the deterministic lexical baseline (S5.1).
This module is the pluggable seam the step prescribes:

- :class:`EmbeddingProvider` — the protocol every embedding source
  implements (tests fake it; S5.3 wires a real endpoint into the API);
- :class:`OpenAICompatibleEmbeddingProvider` — ``POST {base}/embeddings``
  (LM Studio / llama.cpp / Ollama / any OpenAI-compatible server) with the
  §31.1 reliability conventions (timeout, one retry on transport errors);
- :class:`EmbeddingUnavailable` — the **graceful fallback** signal: the
  endpoint does not implement embeddings (501), is unavailable (503), or is
  unreachable. Callers catch it and keep the lexical path unchanged;
- :class:`EmbeddingError` — any other fault (other HTTP status, malformed
  payload) — fails loud, never silently degrades (§9, §31.1);
- :func:`cosine_similarity` — the deterministic vector-scoring primitive.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import httpx
from pydantic import Field
from qa_copilot_domain import DomainModel

#: §31.1 reliability — built-in defaults (overridable at construction).
DEFAULT_EMBED_TIMEOUT_S = 60.0
DEFAULT_EMBED_CONNECT_TIMEOUT_S = 10.0
#: One retry on transport errors, mirroring ``qa_copilot_ai.gateway`` (§31.1).
DEFAULT_MAX_RETRIES = 1

#: HTTP statuses meaning "no embeddings right now" — the graceful set
#: (build bible §19 S5.2: "graceful lexical fallback when endpoint
#: unavailable (501)"; 503 added — the standard unavailable code).
UNAVAILABLE_STATUSES: frozenset[int] = frozenset({501, 503})


class EmbeddingError(RuntimeError):
    """An embedding fault that is NOT a graceful fallback — fail loud.

    Carries the HTTP ``status`` when the failure was an HTTP error (mirrors
    ``qa_copilot_ai.gateway.LLMError``). A silent model-swap is forbidden
    (§31.1), so callers must not catch this to keep going.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class EmbeddingUnavailable(EmbeddingError):
    """The endpoint cannot serve embeddings right now — graceful fallback.

    501 (not implemented — the local completion-only endpoint's exact
    behavior, §19 S5.0), 503 (unavailable), or unreachable after retries.
    Catch this to keep the deterministic lexical path (S5.1) unchanged.
    """


class EmbeddingVector(DomainModel):
    """One embedded text: its position in the input batch and its vector."""

    index: int = Field(ge=0)
    vector: list[float] = Field(min_length=1)
    #: The model id the server reports (audit + reports); None if absent.
    model: str | None = None


@runtime_checkable
class EmbeddingProvider(Protocol):
    """The S5.2 seam: anything that can turn texts into vectors.

    Implementations raise :class:`EmbeddingUnavailable` when the backing
    endpoint cannot produce embeddings (so callers fall back to the lexical
    path) and :class:`EmbeddingError` for any other fault.
    """

    @property
    def model(self) -> str:
        """The model id behind this provider (audit + reports)."""
        ...

    def embed(self, texts: Sequence[str]) -> list[EmbeddingVector]:
        """Embed *texts*; ``result[i]`` corresponds to ``texts[i]``."""
        ...


def parse_embedding_response(payload: object, *, expected_count: int) -> list[EmbeddingVector]:
    """Parse + validate an OpenAI-compatible ``/embeddings`` payload.

    Fail-loud (:class:`EmbeddingError`) on any malformed shape — a broken
    payload must never silently degrade retrieval (§9 fail loud, §31.1 no
    silent model-swap).
    """
    if not isinstance(payload, dict):
        raise EmbeddingError(f"embedding payload must be an object (got {type(payload).__name__})")
    data = payload.get("data")
    if not isinstance(data, list):
        raise EmbeddingError("embedding payload has no 'data' list")
    by_index: dict[int, list[float]] = {}
    for entry in data:
        if not isinstance(entry, dict):
            raise EmbeddingError(f"embedding entry must be an object (got {type(entry).__name__})")
        index = entry.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise EmbeddingError(f"embedding entry has a non-integer 'index' ({index!r})")
        raw_vector = entry.get("embedding")
        if not isinstance(raw_vector, list) or not raw_vector:
            raise EmbeddingError("embedding entry has no non-empty 'embedding' list")
        vector: list[float] = []
        for value in raw_vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EmbeddingError(f"embedding contains a non-numeric value ({value!r})")
            number = float(value)
            if not math.isfinite(number):
                raise EmbeddingError("embedding contains a non-finite value (NaN/inf)")
            vector.append(number)
        if index in by_index:
            raise EmbeddingError(f"duplicate embedding index {index}")
        by_index[index] = vector
    if set(by_index) != set(range(expected_count)):
        raise EmbeddingError(
            f"embedding indexes {sorted(by_index)} do not cover the "
            f"{expected_count} requested text(s) (0..{expected_count - 1})"
        )
    raw_model = payload.get("model")
    model = raw_model if isinstance(raw_model, str) and raw_model else None
    return [
        EmbeddingVector(index=i, vector=by_index[i], model=model) for i in range(expected_count)
    ]


class OpenAICompatibleEmbeddingProvider:
    """``POST {base_url}/embeddings`` over a synchronous httpx client.

    - *base_url* is the OpenAI-compatible root (same shape as ``LLM_BASE_URL``,
      e.g. ``http://localhost:8080/v1``) — the provider appends ``/embeddings``.
    - Timeout + one retry on transport errors mirror
      :class:`qa_copilot_ai.gateway.LLMGateway` (§31.1 reliability).
    - 501/503 or an unreachable endpoint → :class:`EmbeddingUnavailable`
      (the graceful-lexical-fallback signal, §19 S5.2).
    - Any other 4xx/5xx or a malformed payload → :class:`EmbeddingError`
      (fail loud — never a silent model-swap, §31.1).
    - ``transport`` injection enables the fake-server unit tests
      (``httpx.MockTransport`` — the S4.2 CLI-test pattern).
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_s: float = DEFAULT_EMBED_TIMEOUT_S,
        connect_timeout_s: float = DEFAULT_EMBED_CONNECT_TIMEOUT_S,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url must be non-blank")
        if not model.strip():
            raise ValueError("model must be non-blank")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self._base_url = base_url
        self._model = model
        self._max_retries = max_retries
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_s, connect=connect_timeout_s),
            transport=transport,
        )

    @property
    def base_url(self) -> str:
        """The configured OpenAI-compatible root (audit + reports)."""
        return self._base_url

    @property
    def model(self) -> str:
        """The configured model id (audit + reports)."""
        return self._model

    def embed(self, texts: Sequence[str]) -> list[EmbeddingVector]:
        """Embed *texts*; ``results[i]`` corresponds to ``texts[i]``.

        Raises:
            ValueError: *texts* is empty.
            EmbeddingUnavailable: endpoint 501/503, or unreachable after
                retries — the graceful lexical-fallback signal.
            EmbeddingError: any other HTTP status or a malformed payload.
        """
        if not texts:
            raise ValueError("texts must be non-empty")
        body: dict[str, object] = {"model": self._model, "input": list(texts)}
        payload = self._post(body)
        return parse_embedding_response(payload, expected_count=len(texts))

    def close(self) -> None:
        """Close the underlying HTTP client (idempotent)."""
        self._client.close()

    def __enter__(self) -> OpenAICompatibleEmbeddingProvider:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _post(self, body: dict[str, object]) -> object:
        """POST with §31.1 retry: one retry on transport errors only."""
        client = self._client
        for attempt in range(self._max_retries + 1):
            try:
                response = client.post("/embeddings", json=body)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise EmbeddingUnavailable(
                        f"embedding endpoint unreachable at {self._base_url} after "
                        f"{attempt + 1} attempt(s): {exc}"
                    ) from exc
                continue  # retry, then the loop ends and raises below
            if response.status_code in UNAVAILABLE_STATUSES:
                raise EmbeddingUnavailable(
                    f"embedding endpoint unavailable (HTTP {response.status_code}) "
                    f"at {self._base_url} — falling back to lexical retrieval",
                    status=response.status_code,
                )
            if response.status_code >= 400:
                raise EmbeddingError(
                    f"embedding endpoint HTTP {response.status_code}: {response.text[:300]}",
                    status=response.status_code,
                )
            try:
                return response.json()
            except ValueError as exc:
                raise EmbeddingError("embedding endpoint returned a non-JSON body") from exc
        raise EmbeddingUnavailable(
            f"embedding endpoint unreachable at {self._base_url} after "
            f"exhausting {self._max_retries + 1} attempt(s)"
        )


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Deterministic cosine similarity; ``0.0`` when either vector is all zeros.

    Raises:
        ValueError: dimension mismatch (a model mismatch — fail loud, never
            a silent fallback).
    """
    if len(a) != len(b):
        raise ValueError(f"dimension mismatch: {len(a)} != {len(b)}")
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / math.sqrt(norm_a * norm_b)


__all__ = [
    "DEFAULT_EMBED_CONNECT_TIMEOUT_S",
    "DEFAULT_EMBED_TIMEOUT_S",
    "DEFAULT_MAX_RETRIES",
    "EmbeddingError",
    "EmbeddingProvider",
    "EmbeddingUnavailable",
    "EmbeddingVector",
    "OpenAICompatibleEmbeddingProvider",
    "UNAVAILABLE_STATUSES",
    "cosine_similarity",
    "parse_embedding_response",
]
