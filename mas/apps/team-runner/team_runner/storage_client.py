"""Control-plane storage adapter for deployed team runners.

Team runners deliberately do not receive database or object-storage
credentials.  This adapter keeps the small storage surface used by
``AgentBase`` and ``ExecutiveAgent`` while sending typed, allow-listed
operations to the orchestrator API over the worker/CEO control-plane key.

The local ``PGBOUNCER_DSN`` path remains available to isolated development
fixtures, but Compose deployments use this client so the database and MinIO
networks stay private to the control plane.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import httpx


def _json_value(value: Any) -> Any:
    """Convert UUID/datetime values recursively for the JSON API."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


class ControlPlaneStorageClient:
    """Small typed client for team-runner checkpoint/review persistence."""

    def __init__(
        self,
        *,
        orchestrator_url: str,
        api_key: str,
        team_id: str,
        timeout: float = 15.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key is required for control-plane storage")
        if not team_id.strip():
            raise ValueError("team_id is required for control-plane storage")
        self.team_id = team_id.strip()
        self._client = httpx.AsyncClient(
            base_url=orchestrator_url.rstrip("/"),
            headers={
                "X-API-Key": api_key,
                "X-AIAT-Team-ID": self.team_id,
            },
            timeout=timeout,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def health_check(self) -> None:
        """Fail runner startup when the durable control-plane path is down."""
        result = await self._request("storage_health")
        if not isinstance(result, dict) or result.get("status") != "ok":
            raise RuntimeError("control-plane storage health check failed")

    async def _request(self, operation: str, **payload: Any) -> Any:
        response = await self._client.post(
            f"/internal/team-runners/{self.team_id}/storage",
            json={"operation": operation, "payload": _json_value(payload)},
        )
        if response.is_error:
            detail: Any
            try:
                decoded = response.json()
                detail = (
                    decoded.get("detail", response.text)
                    if isinstance(decoded, dict)
                    else decoded
                )
            except ValueError:
                detail = response.text
            raise RuntimeError(
                f"control-plane storage {operation} failed ({response.status_code}): {detail}"
            )
        return response.json()

    # ------------------------------------------------------------------
    # CheckpointStore-compatible surface
    # ------------------------------------------------------------------

    async def save(
        self,
        *,
        agent_id: str,
        team_id: str,
        task_message_id: str,
        iteration: int,
        messages_json: list[dict],
        project_id: UUID | None = None,
        tool_results_json: list[dict] | None = None,
        budget_state_json: dict | None = None,
        task_envelope_json: dict,
        checkpoint_id: UUID | None = None,
    ) -> UUID:
        result = await self._request(
            "checkpoint_save",
            agent_id=agent_id,
            team_id=team_id,
            task_message_id=task_message_id,
            iteration=iteration,
            messages_json=messages_json,
            project_id=project_id,
            tool_results_json=tool_results_json,
            budget_state_json=budget_state_json,
            task_envelope_json=task_envelope_json,
            checkpoint_id=checkpoint_id,
        )
        return UUID(str(result["checkpoint_id"]))

    async def load(
        self,
        agent_id: str,
        task_message_id: str | None = None,
        team_id: str | None = None,
    ) -> dict[str, Any] | None:
        result = await self._request(
            "checkpoint_load",
            agent_id=agent_id,
            task_message_id=task_message_id,
            team_id=team_id or self.team_id,
        )
        return result if isinstance(result, dict) else None

    async def load_latest_for_team_agents(self, team_id: str) -> list[dict[str, Any]]:
        result = await self._request("checkpoint_latest", team_id=team_id)
        return result if isinstance(result, list) else []

    async def delete(
        self,
        agent_id: str,
        task_message_id: str,
        team_id: str | None = None,
    ) -> bool:
        result = await self._request(
            "checkpoint_delete",
            agent_id=agent_id,
            task_message_id=task_message_id,
            team_id=team_id or self.team_id,
        )
        return bool(result.get("deleted")) if isinstance(result, dict) else False

    # ------------------------------------------------------------------
    # AgentStorage-compatible usage/review surface
    # ------------------------------------------------------------------

    async def record_project_usage(self, **kwargs: Any) -> dict[str, Any] | None:
        result = await self._request("usage_record", **kwargs)
        return result if isinstance(result, dict) else None

    async def get_document(self, document_id: UUID) -> dict[str, Any] | None:
        result = await self._request("document_get", document_id=document_id)
        return result if isinstance(result, dict) else None

    async def create_document(self, **kwargs: Any) -> dict[str, Any]:
        result = await self._request("document_create", **kwargs)
        if not isinstance(result, dict):
            raise RuntimeError("control-plane returned an invalid document")
        return result

    async def update_document_status(self, document_id: UUID, *, status: str) -> None:
        await self._request(
            "document_update_status",
            document_id=document_id,
            status=status,
        )

    async def create_review_session(self, **kwargs: Any) -> dict[str, Any]:
        result = await self._request("review_create", **kwargs)
        if not isinstance(result, dict):
            raise RuntimeError("control-plane returned an invalid review session")
        return result

    async def get_review_session(self, session_id: UUID) -> dict[str, Any] | None:
        result = await self._request("review_get", session_id=session_id)
        return result if isinstance(result, dict) else None

    async def update_review_session(self, session_id: UUID, **kwargs: Any) -> None:
        await self._request(
            "review_update",
            session_id=session_id,
            updates=kwargs,
        )

    async def add_review_comment(self, **kwargs: Any) -> dict[str, Any]:
        result = await self._request("review_comment_add", **kwargs)
        if not isinstance(result, dict):
            raise RuntimeError("control-plane returned an invalid review comment")
        return result

    async def get_review_comments(self, session_id: UUID) -> list[dict[str, Any]]:
        result = await self._request("review_comments_get", session_id=session_id)
        return result if isinstance(result, list) else []

    async def list_review_sessions(
        self,
        project_id: UUID,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        result = await self._request(
            "review_list",
            project_id=project_id,
            limit=limit,
        )
        return result if isinstance(result, list) else []
