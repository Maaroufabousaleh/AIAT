"""Opt-in disposable PostgreSQL contract test; never uses an AIAT data volume."""
import asyncio
import os
import secrets
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from tool_service.usage import ProjectUsageWriter
from tool_service.config import Settings
from tool_service.registry import ToolRegistry
from tool_service.tools.opencode_workspace import OpenCodeWorkspaceWriteTool
from mas_core.protocols.enums import AgentRole
from mas_core.protocols.tool import ToolRequest


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get("AIAT_AUDIT_PG_TEST") != "1", reason="opt-in disposable PostgreSQL test")
async def test_audit_intent_and_recovery_in_postgres(tmp_path, monkeypatch):
    docker = ["docker", "-H", "unix:///run/aiat-docker/docker.sock"]
    image = subprocess.check_output(docker + ["inspect", "--format", "{{.Image}}", "mas-postgres-1"], text=True).strip()
    name = "aiat-audit-test-" + secrets.token_hex(8)
    password = secrets.token_urlsafe(32)
    env = dict(os.environ, POSTGRES_PASSWORD=password)
    started = False
    pool = None
    try:
        subprocess.run(docker + ["run", "-d", "--rm", "--name", name,
                        "--tmpfs", "/var/lib/postgresql/data:rw,size=128m",
                        "-p", "127.0.0.1::5432", "-e", "POSTGRES_PASSWORD", image],
                       env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        started = True
        port_text = subprocess.check_output(docker + ["port", name, "5432/tcp"], text=True).strip()
        port = int(port_text.rsplit(":", 1)[1])
        for _ in range(60):
            try:
                pool = await asyncpg.create_pool(host="127.0.0.1", port=port, user="postgres", password=password, database="postgres", min_size=1, max_size=2)
                break
            except (OSError, asyncpg.PostgresError):
                await asyncio.sleep(0.5)
        assert pool is not None, "disposable PostgreSQL did not start"
        mas_root = Path(__file__).resolve().parents[3]
        migration_env = dict(os.environ, PGBOUNCER_DSN=f"postgresql://postgres:{password}@127.0.0.1:{port}/postgres")
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=mas_root,
                       env=migration_env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        project, run, key, company, worker = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
        await pool.execute("INSERT INTO companies(id,slug,name,created_by) VALUES($1,$2,$3,$4)", company, str(company), "test", "test")
        await pool.execute("INSERT INTO projects(id,name,state,created_by,company_id) VALUES($1,'test','INIT','test',$2)", project, company)
        await pool.execute("INSERT INTO worker_registry(id,name,adapter_type) VALUES($1,$2,'test')", worker, str(worker))
        await pool.execute("INSERT INTO worker_runs(id,idempotency_key,worker_id,project_id,task_type,state,request_json,claim_owner,attempt_count,lease_expires_at) VALUES($1,$2,$3,$4,'test','RUNNING','{}','host',1,now()+interval '5 minutes')", run, str(run), worker, project)
        writer = ProjectUsageWriter(pool)
        assert await writer.worker_tool_effects.validate_worker_run_claim(run_id=run, claim_owner="host", attempt_count=1, worker_id=str(worker), project_id=str(project))
        assert not await writer.worker_tool_effects.validate_worker_run_claim(run_id=run, claim_owner="host", attempt_count=1, worker_id=str(uuid4()), project_id=str(project))
        assert not await writer.worker_tool_effects.validate_worker_run_claim(run_id=run, claim_owner="host", attempt_count=1, worker_id=str(worker), project_id=str(uuid4()))
        assert not await writer.worker_tool_effects.validate_worker_run_claim(run_id=run, claim_owner="host", attempt_count=2, worker_id=str(worker), project_id=str(project))
        effect_request_id = uuid4()
        effect = await writer.worker_tool_effects.begin_worker_tool_effect(
            run_id=run, request_id=effect_request_id, idempotency_key="schema-test",
            tool_name="opencode.workspace_write", request_sha256="a" * 64,
            claim_owner="host", attempt_count=1, worker_id=str(worker), project_id=str(project))
        assert effect["disposition"] == "NEW"
        assert await writer.worker_tool_effects.validate_dispatch_effect(run_id=run, request_id=effect_request_id, tool_name="opencode.workspace_write")
        assert not await writer.worker_tool_effects.validate_dispatch_effect(run_id=run, request_id=uuid4(), tool_name="opencode.workspace_write")
        args = dict(project_id=project, run_id=run, actor="worker", tool_name="file_write", idempotency_key=key)
        assert await writer.record_required_tool_audit(**args, status="intent")
        assert not await writer.record_required_tool_audit(**args, status="intent")
        assert len(await writer.list_required_tool_audit_recovery()) == 1
        monkeypatch.setenv("OPENCODE_WORKSPACE_ROOT", str(tmp_path))
        registry = ToolRegistry(Settings(), usage_storage=writer)
        registry.register(OpenCodeWorkspaceWriteTool())
        registry.grant_tool(str(worker), "opencode.workspace_write")
        tool_request = ToolRequest(caller_id=str(worker), caller_role=AgentRole.WORKER,
            project_id=str(project), worker_run_id=run, idempotency_key=uuid4(),
            tool_name="opencode.workspace_write", tool_kwargs={"workspace_run_id": str(run), "path": "real.txt", "content": "verified"})
        response = await registry.execute(tool_request)
        assert response.success
        assert (tmp_path / str(run) / "real.txt").read_text() == "verified"
        rows = await pool.fetch("SELECT status FROM project_usage_events WHERE idempotency_key LIKE $1 ORDER BY status", f"worker-tool-audit:{run}:{tool_request.idempotency_key}:%")
        assert {r["status"] for r in rows} == {"intent", "success"}
        retry = await registry.execute(tool_request)
        assert retry.error_code == "AUDIT_INTENT_UNAVAILABLE"
        denied_request = tool_request.model_copy(update={"caller_id": str(uuid4()), "idempotency_key": uuid4()})
        denied = await registry.execute(denied_request)
        assert denied.error_code == "FORBIDDEN"
        assert await pool.fetchval("SELECT status FROM project_usage_events WHERE idempotency_key=$1", f"worker-tool-audit:{run}:{denied_request.idempotency_key}:forbidden") == "forbidden"
        await pool.execute("ALTER TABLE project_usage_events RENAME TO temporarily_unavailable_usage_events")
        failed_request = tool_request.model_copy(update={"idempotency_key": uuid4(), "tool_kwargs": {"workspace_run_id": str(run), "path": "not-written.txt", "content": "no"}})
        try:
            failed = await registry.execute(failed_request)
            assert failed.error_code == "AUDIT_INTENT_UNAVAILABLE"
            assert not (tmp_path / str(run) / "not-written.txt").exists()
        finally:
            await pool.execute("ALTER TABLE temporarily_unavailable_usage_events RENAME TO project_usage_events")
        assert await writer.record_required_tool_audit(**args, status="uncertain")
        assert len(await writer.list_required_tool_audit_recovery()) == 1
        assert not await writer.record_required_tool_audit(**args, status="success")
        other = dict(args, idempotency_key=uuid4())
        assert await writer.record_required_tool_audit(**other, status="intent")
        assert await writer.record_required_tool_audit(**other, status="success")
        assert len(await writer.list_required_tool_audit_recovery()) == 1
    finally:
        if pool is not None:
            await pool.close()
        if started:
            subprocess.run(docker + ["stop", "-t", "5", name], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
