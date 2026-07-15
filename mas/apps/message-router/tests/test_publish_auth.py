"""Regression coverage for router HTTP publication authentication."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from message_router.routes_publish import router


@asynccontextmanager
async def _lifespan(app: FastAPI):  # noqa: ARG001
    yield


def test_publish_rejects_anonymous_callers() -> None:
    app = FastAPI(lifespan=_lifespan)
    app.include_router(router)

    with TestClient(app) as client:
        response = client.post("/messages/publish", json={})

    assert response.status_code == 401

