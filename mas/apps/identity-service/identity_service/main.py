"""Identity-service ASGI application."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .config import IdentitySettings, get_settings
from .providers.factory import build_provider_pair
from .routes import router
from .service import IdentityService
from .store import IdentityStore, InMemoryIdentityStore, PostgresIdentityStore


def create_app(*, settings: IdentitySettings | None = None, store: IdentityStore | None = None) -> FastAPI:
    settings = settings or get_settings()
    if store is None:
        if settings.database_dsn:
            store = PostgresIdentityStore(settings.database_dsn, content_encryption_key=settings.identity_content_encryption_key)
        elif settings.is_production:
            raise RuntimeError("identity database credentials are required in production")
        else:
            store = InMemoryIdentityStore()
    inbound_provider, outbound_provider = build_provider_pair(settings)
    service = IdentityService(
        settings=settings,
        store=store,
        inbound_provider=inbound_provider,
        outbound_provider=outbound_provider,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.identity_store = store
        app.state.identity_service = service
        for client_id, public_key in settings.client_public_keys.items():
            registration = await store.ensure_client_registration(
                client_id=client_id, public_key=public_key,
                scopes=sorted(settings.client_scopes.get(client_id, frozenset())),
            )
            configured_scopes = settings.client_scopes.get(client_id, frozenset())
            registered_scopes = frozenset(registration.get("scopes") or [])
            if (
                registration.get("public_key") != public_key
                or registration.get("state") != "ACTIVE"
                or registered_scopes != configured_scopes
            ):
                # Environment changes never silently rotate a durable key or
                # widen/narrow its authority. An operator must reconcile the
                # registration explicitly, which makes stale privileges fail
                # closed instead of surviving a configuration reduction.
                raise RuntimeError(
                    f"identity client registration mismatch or revocation: {client_id}"
                )
        yield
        await store.close()

    app = FastAPI(title="AIAT identity-service", version="1.0.0", lifespan=lifespan, docs_url=None if settings.is_production else "/docs", redoc_url=None)
    app.include_router(router)

    @app.get("/healthz")
    async def healthz() -> dict:
        return await service.health()

    @app.get("/readyz")
    async def readyz() -> dict:
        if not await store.healthcheck():
            raise HTTPException(503, "identity database is unavailable")
        return {
            "status": "ready",
            "database": "configured" if settings.database_dsn else "memory-development",
        }

    return app


app = create_app()
