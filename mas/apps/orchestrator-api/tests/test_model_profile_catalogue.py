"""API coverage for the runtime/model-profile reconciliation catalogue."""

from __future__ import annotations

from uuid import UUID

import pytest


class _CatalogueStorage:
    async def list_model_profiles(self):
        return [
            {
                "logical_profile_id": "profile-catalogue-test",
                "purpose": "catalogue route test",
                "approved_provider_ids": ["test-provider"],
                "required_capabilities": [],
                "fallback_profile_ids": [],
                "status": "approved",
                "owner": "aiat",
                "versions": [
                    {
                        "version": "1.0.0",
                        "provider_id": "test-provider",
                        "exact_model_id": "not-in-runtime-registry",
                        "status": "approved",
                        "constraints_json": {},
                        "provider_settings": {},
                    }
                ],
            }
        ]


class _ExecutiveStorage(_CatalogueStorage):
    company_id = UUID("00000000-0000-4000-a000-000000000801")
    project_id = UUID("00000000-0000-4000-a000-000000000802")

    async def list_projects(self, *, limit: int, offset: int = 0):
        return [{"id": self.project_id, "company_id": self.company_id, "state": "IN_PROGRESS"}]

    async def get_project_usage(self, project_id: UUID):
        return {"available": True, "llm_calls": 2, "total_cost_usd": 0.75}

    async def list_worker_runs(self, *, limit: int, offset: int = 0):
        return [{"id": "run-1", "project_id": self.project_id, "state": "SUCCEEDED"}]

    async def list_companies(self, *, status: str | None = None):
        return [{"id": self.company_id}]

    async def get_company(self, company_id: UUID):
        return {"id": company_id}

    async def list_company_budgets(self, company_id: UUID):
        return [{"budget_key": "max_cost_usd"}]

    async def get_budget_state(self, company_id: UUID, budget_key: str):
        return {
            "configured": True,
            "company_id": company_id,
            "budget_key": budget_key,
            "limit": 10,
            "used": 1,
            "available": 9,
        }

    async def list_budget_reservations(self, *, company_id: UUID, limit: int):
        return [{"state": "COMMITTED", "company_id": company_id}]


@pytest.mark.anyio
async def test_model_profile_catalogue_route_reconciles_persisted_rows(client) -> None:
    from orchestrator_api.main import app

    app.state.storage = _CatalogueStorage()

    response = await client.get("/model-profiles/catalogue")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "aiat.model-profile-catalogue.v1"
    assert body["profile_count"] == 1
    assert body["profile_version_count"] == 1
    assert body["covered_profile_version_count"] == 0
    assert body["findings"][0]["code"] == "PROFILE_MODEL_NOT_REGISTERED"


@pytest.mark.anyio
async def test_executive_reconciliation_route_uses_durable_sources(client) -> None:
    from orchestrator_api.main import app

    app.state.storage = _ExecutiveStorage()

    response = await client.get("/executive/reconciliation")

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "aiat.executive-reconciliation.v1"
    assert body["projects"]["usage"]["total_cost_usd"] == 0.75
    assert body["delivery"]["successful_run_count"] == 1
    assert body["budgets"]["used_usd"] == 1.0
    assert body["views"]["cfo"]["spend_usd"] == 0.75
    assert body["views"]["cto"]["successful_worker_runs"] == 1
    assert body["views"]["ceo"]["total_projects"] == 1
    assert any(item["code"] == "MODEL_PROFILE_COVERAGE_PENDING" for item in body["findings"])


@pytest.mark.anyio
async def test_executive_role_view_route_projects_one_canonical_view(client) -> None:
    from orchestrator_api.main import app

    app.state.storage = _ExecutiveStorage()

    response = await client.get(
        "/executive/views/cfo",
        params={"company_id": str(_ExecutiveStorage.company_id)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "aiat.executive-views.v1"
    assert body["role"] == "cfo"
    assert body["company_id"] == str(_ExecutiveStorage.company_id)
    assert body["view"]["spend_usd"] == 0.75
    assert body["coverage"]["project_count"] == 1
