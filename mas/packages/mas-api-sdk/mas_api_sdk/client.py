"""Small async client for the generated orchestrator contract."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

from .generated import OPERATIONS, ApiOperation

if TYPE_CHECKING:
    from collections.abc import Mapping


class ApiError(RuntimeError):
    """An orchestrator request returned a non-success HTTP status."""

    def __init__(self, *, operation_id: str, status_code: int, detail: Any) -> None:
        self.operation_id = operation_id
        self.status_code = status_code
        self.detail = detail
        super().__init__(
            f"orchestrator operation {operation_id!r} failed with HTTP "
            f"{status_code}: {detail}"
        )


class OperationNotFoundError(KeyError):
    """The generated contract does not contain the requested operation ID."""


class OrchestratorClient:
    """Execute generated OpenAPI operations against the AIAT control plane.

    The client deliberately accepts an operation ID rather than exposing a
    handwritten duplicate of the 200+ endpoint surface. The checked-in
    generated metadata validates path/query/body names and keeps callers
    aligned with the runtime contract.
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        self._default_headers = dict(default_headers or {})

    @property
    def client(self) -> httpx.AsyncClient:
        """Expose the underlying transport for advanced callers/tests."""
        return self._client

    async def __aenter__(self) -> OrchestratorClient:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def operation(operation_id: str) -> ApiOperation:
        try:
            return OPERATIONS[operation_id]
        except KeyError as exc:
            raise OperationNotFoundError(operation_id) from exc

    async def request(
        self,
        operation_id: str,
        *,
        path_params: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Execute one operation after validating generated parameter names."""
        operation = self.operation(operation_id)
        path_values = dict(path_params or {})
        query_values = dict(query or {})
        expected_path = set(operation.path_params)
        expected_query = set(operation.query_params)
        missing = sorted(expected_path - path_values.keys())
        unknown_path = sorted(path_values.keys() - expected_path)
        unknown_query = sorted(query_values.keys() - expected_query)
        if missing:
            raise ValueError(f"{operation_id}: missing path parameters: {', '.join(missing)}")
        if unknown_path:
            raise ValueError(f"{operation_id}: unknown path parameters: {', '.join(unknown_path)}")
        if unknown_query:
            raise ValueError(f"{operation_id}: unknown query parameters: {', '.join(unknown_query)}")

        path = operation.path
        for name in operation.path_params:
            path = path.replace("{" + name + "}", quote(str(path_values[name]), safe=""))

        request_headers = dict(self._default_headers)
        if self.api_key:
            request_headers.setdefault("X-API-Key", self.api_key)
        request_headers.update(headers or {})
        request_headers.setdefault("Accept", "application/json")
        response = await self._client.request(
            operation.method,
            path,
            params={key: value for key, value in query_values.items() if value is not None},
            json=json_body,
            headers=request_headers,
        )
        if response.is_error:
            try:
                detail: Any = response.json()
            except ValueError:
                detail = response.text
            raise ApiError(
                operation_id=operation_id,
                status_code=response.status_code,
                detail=detail,
            )
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text


__all__ = ["ApiError", "OperationNotFoundError", "OrchestratorClient"]
