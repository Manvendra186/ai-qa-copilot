"""Minimal FastAPI app (sample repo for scanner tests)."""

from fastapi import FastAPI

app = FastAPI(title="python-api sample")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness endpoint."""
    return {"status": "ok"}


@app.get("/api/v1/items")
def list_items() -> list[dict[str, str]]:
    """Return the seeded item list."""
    return [{"id": "1", "name": "widget", "status": "active"}]
