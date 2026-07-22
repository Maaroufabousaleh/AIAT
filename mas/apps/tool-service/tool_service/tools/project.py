"""Workflow/Document/Review tools — real implementations that call the orchestrator-api.

All state-mutating operations go through the orchestrator-api HTTP endpoints.
The orchestrator-api is the sole writer of ``projects.state`` and manages
all persistence atomically via AgentStorage + WorkflowController.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx

from mas_core.protocols.enums import AgentRole
from mas_tools_sdk.base import BaseTool
from mas_tools_sdk.groups import ToolGroup

from ._orch_client import orch_get, orch_post
from .adapters import _run_process
from .file import _workspace_root

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)
MESSAGE_ROUTER_URL = os.getenv("MESSAGE_ROUTER_URL") or os.getenv(
    "ROUTER_URL", "http://message-router:8001"
)


def _router_auth_headers() -> dict[str, str]:
    secret = os.getenv("ROUTER_SECRET") or os.getenv("AGENT_TOKEN_SECRET")
    if not secret:
        raise RuntimeError("ROUTER_SECRET must be configured for router publication")
    return {"Authorization": f"Bearer tool-service:{secret}"}


async def publish_message(envelope: dict[str, Any]) -> dict[str, Any]:
    """Publish a validated MAS envelope through the message-router."""
    async with httpx.AsyncClient(timeout=15, base_url=MESSAGE_ROUTER_URL) as client:
        resp = await client.post("/messages/publish", json=envelope, headers=_router_auth_headers())
        resp.raise_for_status()
        return resp.json()


# ── Project ────────────────────────────────────────────────────────────────


_GIT_OPERATIONS = {"init", "clone", "status", "sync", "commit", "push", "remove"}
_GIT_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,200}$")
_GIT_REMOTE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,62}$")
_GIT_SCP_RE = re.compile(r"^git@(?P<host>[A-Za-z0-9.-]+):(?P<path>[^\s]+)$")


def _git_allowed_hosts() -> set[str]:
    raw = os.getenv("TOOL_GIT_ALLOWED_HOSTS", "github.com")
    return {host.strip().lower() for host in raw.split(",") if host.strip()}


def _validate_git_url(value: str | None) -> str | None:
    """Allow configured Git hosts while rejecting credential-bearing URLs."""
    if value is None or not value.strip():
        return None
    url = value.strip()
    allowed_hosts = _git_allowed_hosts()
    scp_match = _GIT_SCP_RE.fullmatch(url)
    if scp_match:
        host = scp_match.group("host").lower()
        if host not in allowed_hosts:
            raise ValueError(f"Git host is not allowlisted: {host}")
        return url

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "ssh", "git"} or not parsed.hostname:
        raise ValueError("repository_url must be an HTTPS, SSH, or Git URL")
    if parsed.username or parsed.password:
        raise ValueError("repository_url must not contain credentials")
    host = parsed.hostname.lower()
    if host not in allowed_hosts:
        raise ValueError(f"Git host is not allowlisted: {host}")
    if not parsed.path or parsed.path == "/":
        raise ValueError("repository_url must identify a repository")
    return url


def _validate_git_options(
    *,
    branch: str,
    remote_name: str,
    project_id: str,
) -> tuple[str, str, str]:
    if not project_id or project_id in {".", ".."} or "/" in project_id or "\\" in project_id:
        raise ValueError("project_id must be a single workspace directory name")
    normalized_branch = branch.strip() or "main"
    if not _GIT_BRANCH_RE.fullmatch(normalized_branch) or normalized_branch.startswith(
        ("/", "-")) or ".." in normalized_branch:
        raise ValueError("branch must be a safe Git ref")
    normalized_remote = remote_name.strip() or "origin"
    if not _GIT_REMOTE_RE.fullmatch(normalized_remote):
        raise ValueError("remote_name must be a safe Git remote name")
    return project_id, normalized_branch, normalized_remote


async def _git_command(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float = 120,
    check: bool = True,
) -> dict[str, Any]:
    # Project workspaces are often backed by a Docker Desktop bind mount.  The
    # container user can legitimately differ from the mount owner, which makes
    # modern Git reject the repository as "dubious ownership".  Scope trust to
    # exactly this governed workspace for this invocation; do not mutate the
    # service user's global Git configuration or trust the entire workspace
    # root.
    safe_cwd = cwd.resolve()
    result = await _run_process(
        ["git", "-c", f"safe.directory={safe_cwd}", *argv],
        cwd=safe_cwd,
        timeout=timeout,
        max_output_bytes=256_000,
    )
    if not result.get("available"):
        raise RuntimeError("git is not installed in the tool-service runtime")
    if check and result.get("returncode") != 0:
        detail = (result.get("stderr") or result.get("stdout") or "git command failed").strip()
        raise ValueError(detail[:2_000])
    return result


async def _git_status(*, workspace: Path, project_id: str, remote_name: str) -> dict[str, Any]:
    if not (workspace / ".git").exists():
        return {
            "initialized": False,
            "project_id": project_id,
            "workspace_path": str(workspace),
            "workspace_relative_path": project_id,
            "remote": None,
            "branch": None,
            "head": None,
            "clean": None,
        }

    branch_result = await _git_command(["branch", "--show-current"], cwd=workspace)
    head_result = await _git_command(["rev-parse", "HEAD"], cwd=workspace)
    remote_result = await _git_command(
        ["remote", "get-url", remote_name],
        cwd=workspace,
        timeout=30,
        check=False,
    )
    remote_url = (
        remote_result.get("stdout", "").strip()
        if remote_result.get("returncode") == 0
        else None
    )
    porcelain_result = await _git_command(["status", "--porcelain"], cwd=workspace)
    return {
        "initialized": True,
        "project_id": project_id,
        "workspace_path": str(workspace),
        "workspace_relative_path": project_id,
        "remote": remote_url or None,
        "remote_name": remote_name,
        "branch": branch_result.get("stdout", "").strip() or None,
        "head": head_result.get("stdout", "").strip() or None,
        "clean": not bool(porcelain_result.get("stdout", "").strip()),
        "changes": [
            line
            for line in (porcelain_result.get("stdout", "") or "").splitlines()
            if line.strip()
        ][:100],
    }


class ProjectRepositoryTool(BaseTool):
    name = "project.repository"
    group = ToolGroup.WORKFLOW
    description = "Create and manage the project Git workspace through a bounded adapter."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.ADMIN]
    cache_ttl_seconds = 0
    idempotent = False
    max_concurrency = 2

    async def execute(self, **kwargs: Any) -> Any:
        operation = str(kwargs.get("operation") or "status").strip().lower()
        if operation not in _GIT_OPERATIONS:
            raise ValueError(f"operation must be one of {', '.join(sorted(_GIT_OPERATIONS))}")

        requested_branch = str(kwargs.get("branch") or "").strip()
        project_id, branch, remote_name = _validate_git_options(
            branch=requested_branch or "main",
            remote_name=str(kwargs.get("remote_name") or "origin"),
            project_id=str(kwargs.get("project_id") or ""),
        )
        repository_url = _validate_git_url(kwargs.get("repository_url"))
        root = _workspace_root()
        root.mkdir(parents=True, exist_ok=True)
        workspace = (root / project_id).resolve()
        workspace.relative_to(root)

        if operation == "remove":
            if workspace.exists():
                shutil.rmtree(workspace)
            return {
                "initialized": False,
                "removed": True,
                "project_id": project_id,
                "workspace_relative_path": project_id,
                "workspace_path": str(workspace),
            }

        if operation == "clone":
            if repository_url is None:
                raise ValueError("repository_url is required for clone")
            if (workspace / ".git").exists():
                return await _git_status(
                    workspace=workspace,
                    project_id=project_id,
                    remote_name=remote_name,
                )
            if workspace.exists() and any(workspace.iterdir()):
                raise ValueError("project workspace is not empty and is not a Git repository")
            workspace.parent.mkdir(parents=True, exist_ok=True)
            clone_args = ["clone", "--origin", remote_name]
            if requested_branch:
                clone_args.extend(["--branch", branch])
            clone_args.extend([repository_url, str(workspace)])
            await _git_command(clone_args, cwd=root, timeout=900)

        elif operation == "init":
            if (workspace / ".git").exists():
                return await _git_status(
                    workspace=workspace,
                    project_id=project_id,
                    remote_name=remote_name,
                )
            if workspace.exists() and any(workspace.iterdir()):
                raise ValueError("project workspace is not empty and is not a Git repository")
            workspace.mkdir(parents=True, exist_ok=True)
            await _git_command(["init", "-b", branch], cwd=workspace)
            await _git_command(["config", "user.name", "AIAT"], cwd=workspace)
            await _git_command(["config", "user.email", "aiat@local.invalid"], cwd=workspace)
            if repository_url:
                await _git_command(["remote", "add", remote_name, repository_url], cwd=workspace)
            manifest = workspace / ".aiat" / "project.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(
                json.dumps(
                    {
                        "project_id": project_id,
                        "managed_by": "AIAT",
                        "repository_url": repository_url,
                        "branch": branch,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            await _git_command(["add", ".aiat/project.json"], cwd=workspace)
            await _git_command(["commit", "-m", "Initialize AIAT project workspace"], cwd=workspace)

        elif operation == "sync":
            if not (workspace / ".git").exists():
                raise ValueError("project workspace is not initialized as a Git repository")
            await _git_command(["fetch", "--prune", remote_name], cwd=workspace, timeout=900)
            await _git_command(["pull", "--ff-only", remote_name, branch], cwd=workspace, timeout=900)

        elif operation == "commit":
            if not (workspace / ".git").exists():
                raise ValueError("project workspace is not initialized as a Git repository")
            message = str(kwargs.get("message") or "").strip()
            if not message or len(message) > 200:
                raise ValueError("commit message is required and must be at most 200 characters")
            await _git_command(["add", "-A"], cwd=workspace)
            commit = await _git_command(
                ["commit", "-m", message],
                cwd=workspace,
                check=False,
            )
            if commit.get("returncode") != 0 and "nothing to commit" not in (
                commit.get("stdout", "") + commit.get("stderr", "")
            ).lower():
                raise ValueError((commit.get("stderr") or commit.get("stdout") or "git commit failed")[:2_000])

        elif operation == "push":
            if not (workspace / ".git").exists():
                raise ValueError("project workspace is not initialized as a Git repository")
            await _git_command(["push", remote_name, branch], cwd=workspace, timeout=900)

        return await _git_status(
            workspace=workspace,
            project_id=project_id,
            remote_name=remote_name,
        )


class ProjectCreateTool(BaseTool):
    name = "project.create"
    group = ToolGroup.WORKFLOW
    description = "Create a new project record."
    allowed_roles = [AgentRole.ORCHESTRATOR]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        name = kwargs.get("name") or kwargs.get("title") or "Untitled Project"
        body = {
            "name": name,
            "description": kwargs.get("description"),
            "human_requester": kwargs.get("human_requester"),
            "config": kwargs.get("config"),
        }
        if kwargs.get("flow_id"):
            body["flow_id"] = kwargs["flow_id"]
        if kwargs.get("workspace"):
            body["workspace"] = kwargs["workspace"]
        if kwargs.get("initial_context"):
            body["initial_context"] = kwargs["initial_context"]
        return await orch_post("/projects", body)


class ProjectStatusTool(BaseTool):
    name = "project.status"
    group = ToolGroup.WORKFLOW
    description = "Get the current project status and state."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 0

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        try:
            UUID(str(project_id))
        except (TypeError, ValueError):
            return {"error": "invalid_project_id", "project_id": project_id}
        try:
            return await orch_get(f"/projects/{project_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return {"error": "project_not_found", "project_id": project_id}
            raise


class ProjectTransitionTool(BaseTool):
    name = "project.transition"
    group = ToolGroup.WORKFLOW
    description = "Transition the project to a new state via a workflow event."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        body = {
            "event": kwargs.get("event", ""),
            "actor_id": kwargs.get("actor_id", "unknown"),
            "context": kwargs.get("context"),
        }
        return await orch_post(f"/projects/{project_id}/transition", body)


class ProjectListTool(BaseTool):
    name = "project.list"
    group = ToolGroup.WORKFLOW
    description = "List projects with optional filters."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 0

    async def execute(self, **kwargs: Any) -> Any:
        params = {}
        if kwargs.get("state"):
            params["state"] = kwargs["state"]
        if kwargs.get("limit"):
            params["limit"] = kwargs["limit"]
        return await orch_get("/projects", params=params)


# ── Documents ──────────────────────────────────────────────────────────────


class DocumentCreateDraftTool(BaseTool):
    name = "document.create_draft"
    group = ToolGroup.DOCUMENT
    description = "Create a new document draft."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.ADMIN,
    ]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        doc_type = str(kwargs.get("doc_type", "PDR")).upper()
        existing = await orch_get(
            f"/projects/{project_id}/documents",
            params={"doc_type": doc_type},
        )
        latest = existing[0] if isinstance(existing, list) and existing else None
        content = str(kwargs.get("content") or "")

        # Recovery directives can be redelivered after a runner restart.  Do
        # not create a second immutable revision when the requested content is
        # byte-for-byte identical to the current durable blob.
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest() if content else None
        if (
            latest is not None
            and content_sha256
            and latest.get("blob_sha256") == content_sha256
        ):
            return {"status": "existing", "document": latest}

        # Documents are durable metadata plus an immutable blob reference. The
        # former implementation only queued CREATE_DOCUMENT to a team stream,
        # which made the tool report success while the API still had no
        # document to retrieve or submit for review.
        blob_ref: dict[str, Any] | None = None
        # A new content hash is an immutable revision even when the current
        # document already has a blob.  The old guard only uploaded when the
        # latest row had no blob, causing every real revision request to
        # return the previous version and re-submit it unchanged.
        if content:
            from .infra import get_blob_client

            safe_type = re.sub(r"[^a-z0-9_-]+", "_", doc_type.lower()).strip("_") or "document"
            version = int(latest.get("version") or 1) + 1 if latest else 1
            blob = await get_blob_client()
            ref = await blob.upload(
                project_id=str(project_id),
                key=f"documents/{safe_type}_v{version}.md",
                data=content.encode("utf-8"),
                content_type="text/markdown",
            )
            blob_ref = ref.to_dict()

        if latest is not None:
            if blob_ref is None:
                return {"status": "existing", "document": latest}
            revised = await orch_post(
                f"/projects/{project_id}/documents/{latest['id']}/revisions",
                {
                    "created_by": kwargs.get("created_by") or kwargs.get("actor_id") or "agent",
                    "blob_bucket": blob_ref["bucket"],
                    "blob_key": blob_ref["key"],
                    "blob_sha256": blob_ref["sha256"],
                },
            )
            return {"status": "revised", "document": revised, "blob": blob_ref}

        document = await orch_post(
            f"/projects/{project_id}/documents",
            {
                "doc_type": doc_type,
                "created_by": kwargs.get("created_by") or kwargs.get("actor_id") or "agent",
                "blob_bucket": blob_ref["bucket"] if blob_ref else None,
                "blob_key": blob_ref["key"] if blob_ref else None,
                "blob_sha256": blob_ref["sha256"] if blob_ref else None,
            },
        )
        return {"status": "created", "document": document, "blob": blob_ref}


class DocumentSubmitTool(BaseTool):
    name = "document.submit"
    group = ToolGroup.DOCUMENT
    description = "Submit a document draft for review."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.ADMIN,
    ]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        doc_type = kwargs.get("doc_type", "PDR")
        event_map = {
            "PDR": "pdr_submitted",
            "CDR": "cdr_submitted",
            "RR": "rr_submitted",
        }
        event = event_map.get(doc_type)
        if event is None:
            raise ValueError("doc_type must be one of PDR, CDR, or RR")
        return await orch_post(
            f"/projects/{project_id}/transition",
            {
                "event": event,
                "actor_id": kwargs.get("actor_id", "agent"),
                "context": {
                    "document_id": kwargs.get("document_id"),
                    "doc_type": doc_type,
                },
            },
        )


class DocumentReviseTool(BaseTool):
    name = "document.revise"
    group = ToolGroup.DOCUMENT
    description = "Revise a document based on review feedback."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.ADMIN,
    ]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        body = {
            "team_id": kwargs.get("team_id", "office_cto"),
            "payload": {
                "action": "REVISE_DOCUMENT",
                "project_id": kwargs.get("project_id", ""),
                "document_id": kwargs.get("document_id", ""),
                "feedback": kwargs.get("feedback", ""),
            },
        }
        return await orch_post("/tasks", body)


class DocumentGetLatestTool(BaseTool):
    name = "document.get_latest"
    group = ToolGroup.DOCUMENT
    description = "Retrieve the latest version of a document."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.C_SUITE,
        AgentRole.ADMIN,
        AgentRole.WORKER,
    ]
    cache_ttl_seconds = 0

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        doc_type = kwargs.get("doc_type")
        docs = await orch_get(
            f"/projects/{project_id}/documents",
            params={"doc_type": doc_type} if doc_type else None,
        )
        if isinstance(docs, list) and docs:
            return docs[0]
        return {"error": "No documents found"}


class DocumentListTool(BaseTool):
    name = "document.list"
    group = ToolGroup.DOCUMENT
    description = "List documents, optionally filtered by project or type."
    allowed_roles = [
        AgentRole.ORCHESTRATOR,
        AgentRole.EXECUTIVE,
        AgentRole.C_SUITE,
        AgentRole.ADMIN,
        AgentRole.WORKER,
    ]
    cache_ttl_seconds = 0

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        params = {}
        if kwargs.get("doc_type"):
            params["doc_type"] = kwargs["doc_type"]
        docs = await orch_get(f"/projects/{project_id}/documents", params=params)
        return {"documents": docs, "total": len(docs) if isinstance(docs, list) else 0}


# ── Reviews ────────────────────────────────────────────────────────────────


class ReviewStartSessionTool(BaseTool):
    name = "review.start_session"
    group = ToolGroup.REVIEW
    description = "Start a multi-reviewer review session."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = str(kwargs.get("project_id") or "")
        doc_type = str(kwargs.get("review_type") or kwargs.get("doc_type") or "PDR").upper()
        document_id = kwargs.get("document_id")
        if not document_id:
            documents = await orch_get(
                f"/projects/{project_id}/documents",
                params={"doc_type": doc_type},
            )
            if isinstance(documents, list) and documents:
                document_id = documents[0].get("id")
        if not document_id:
            return {"error": "document_not_found", "project_id": project_id, "doc_type": doc_type}

        resume_session: dict[str, Any] | None = None
        existing_sessions = await orch_get(
            f"/projects/{project_id}/review-sessions",
        )
        if isinstance(existing_sessions, list):
            matching = [
                session
                for session in existing_sessions
                if str(session.get("document_id")) == str(document_id)
            ]
            active_statuses = {"IN_PROGRESS", "PENDING", "OPEN", "STARTED"}
            active = [
                session
                for session in matching
                if str(session.get("status") or "").upper() in active_statuses
            ]
            if active:
                # A durable session can outlive the COO runner's in-memory
                # aggregation map. Re-publish the canonical submit with the
                # existing session ID so the runner rehydrates responses and
                # resumes fan-out instead of suppressing all further work.
                resume_session = active[0]
            elif matching and str(matching[0].get("status") or "").upper() == "COMPLETED":
                # Resume reconciliation uses this terminal result to advance
                # a project whose review completed just before a runner/API
                # restart. It must not be republished as an active session.
                return {
                    "status": "existing",
                    "session_id": matching[0].get("id"),
                    "document_id": str(document_id),
                    "doc_type": doc_type,
                    "session_status": "COMPLETED",
                }

        # The COO owns durable review sessions and fan-out. Publish the
        # canonical DOCUMENT_SUBMIT envelope so ExecutiveAgent can persist the
        # session and send REVIEW_REQUEST messages to the configured chiefs.
        envelope = {
            "message_id": str(uuid4()),
            "correlation_id": project_id,
            "msg_type": "DOCUMENT_SUBMIT",
            "sender_id": "orchestrator",
            "sender_team": "exec_ceo",
            "sender_role": AgentRole.ORCHESTRATOR.value,
            "recipient_team": "exec_coo",
            "project_id": project_id,
            "payload": {
                "document_id": str(document_id),
                "doc_type": doc_type,
                "session_id": (
                    str(resume_session.get("id")) if resume_session is not None else None
                ),
                "rehydrate_session": resume_session is not None,
                "document_payload": {
                    "document_id": str(document_id),
                    "doc_type": doc_type,
                    "project_id": project_id,
                },
            },
            "created_at": datetime.now(tz=UTC).isoformat(),
        }
        result = await publish_message(envelope)
        return {
            "status": "rehydration_published" if resume_session is not None else "published",
            "message_id": result.get("entry_id"),
            "session_id": (
                str(resume_session.get("id")) if resume_session is not None else None
            ),
            "document_id": str(document_id),
            "doc_type": doc_type,
        }


class ReviewSubmitResponseTool(BaseTool):
    name = "review.submit"
    group = ToolGroup.REVIEW
    description = "Submit a review verdict for a review session."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        body = {
            "team_id": kwargs.get("team_id", "exec_coo"),
            "payload": {
                "action": "SUBMIT_REVIEW",
                "session_id": kwargs.get("session_id", ""),
                "verdict": kwargs.get("verdict", "APPROVED"),
                "comments": kwargs.get("comments", []),
                "severity": kwargs.get("severity"),
                "reviewer_id": kwargs.get("reviewer_id", ""),
            },
        }
        return await orch_post("/tasks", body)


class ReviewSubmitVetoTool(BaseTool):
    name = "review.submit_veto"
    group = ToolGroup.REVIEW
    description = "Submit a CSO veto on a review."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        return await orch_post(
            f"/projects/{project_id}/transition",
            {
                "event": "cso_veto",
                "actor_id": kwargs.get("actor_id", "cso"),
                "context": {
                    "reason": kwargs.get("reason", "Security concern"),
                    "session_id": kwargs.get("session_id"),
                },
            },
        )


class ReviewAggregateTool(BaseTool):
    name = "review.aggregate"
    group = ToolGroup.REVIEW
    description = "Aggregate review verdicts into a final decision."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    blocked_roles = [AgentRole.WORKER, AgentRole.SUB_AGENT]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        return await orch_post(
            f"/projects/{project_id}/transition",
            {
                "event": "all_reviews_in",
                "actor_id": kwargs.get("actor_id", "coo"),
                "context": {
                    "session_id": kwargs.get("session_id"),
                    "aggregate_verdict": kwargs.get("verdict", "APPROVED"),
                },
            },
        )


# ── Approval ───────────────────────────────────────────────────────────────


class ApprovalOverrideCSOTool(BaseTool):
    name = "approval.override_cso"
    group = ToolGroup.REVIEW
    description = "CSO override: block or approve despite reviews."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE, AgentRole.C_SUITE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        action = kwargs.get("action", "approve")
        if action == "block":
            return await orch_post(
                f"/projects/{project_id}/transition",
                {
                    "event": "cso_veto",
                    "actor_id": kwargs.get("actor_id", "cso"),
                    "context": {"reason": kwargs.get("reason", "CSO override")},
                },
            )
        else:
            return await orch_post(
                f"/projects/{project_id}/transition",
                {
                    "event": "ceo_override",
                    "actor_id": kwargs.get("actor_id", "ceo"),
                    "context": {"reason": kwargs.get("reason", "CEO override")},
                },
            )


# ── Human interface ────────────────────────────────────────────────────────


class HumanNotifyTool(BaseTool):
    name = "human.notify"
    group = ToolGroup.WORKFLOW
    description = "Send a notification to the human operator."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        message = str(kwargs.get("message") or "").strip()
        if not message:
            return {"error": "message is required"}

        project_id = str(kwargs.get("project_id") or "operator-direct")
        envelope = {
            "message_id": str(uuid4()),
            "correlation_id": str(kwargs.get("correlation_id") or uuid4()),
            "parent_id": kwargs.get("parent_id"),
            "msg_type": "RESPONSE",
            "sender_id": str(kwargs.get("sender_id") or "ceo"),
            "sender_team": "exec_ceo",
            "sender_role": AgentRole.ORCHESTRATOR.value,
            "recipient_team": "exec_ceo",
            "project_id": project_id,
            "payload": {
                "response": message,
                "source": "human.notify",
                "notification_type": kwargs.get("notification_type", "INFO"),
            },
            "ack_required": False,
        }
        result = await publish_message(envelope)
        return {
            "notified": True,
            "entry_id": result.get("entry_id"),
            "project_id": project_id,
            "message": message,
            "notification_type": envelope["payload"]["notification_type"],
        }


class HumanAwaitDecisionTool(BaseTool):
    name = "human.await_decision"
    group = ToolGroup.WORKFLOW
    description = "Check for pending human decisions on a project."
    allowed_roles = [AgentRole.ORCHESTRATOR]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id", "")
        decisions = await orch_get(f"/projects/{project_id}/pending-decisions")
        if isinstance(decisions, list) and decisions:
            first_decision = decisions[0]
            return {
                "pending": True,
                "pending_count": len(decisions),
                "gate_id": first_decision.get("id"),
                "gate_type": first_decision.get("gate_type"),
                "first_decision": first_decision,
                "decisions": decisions,
            }
        return {
            "pending": False,
            "pending_count": 0,
            "decisions": [],
            "message": "No pending decisions",
        }


# ── Department task ────────────────────────────────────────────────────────


class DepartmentTaskTool(BaseTool):
    name = "department_task"
    group = ToolGroup.WORKFLOW
    description = "Dispatch a work task to a department team."
    allowed_roles = [AgentRole.ORCHESTRATOR, AgentRole.EXECUTIVE]
    cache_ttl_seconds = 0
    idempotent = False

    async def execute(self, **kwargs: Any) -> Any:
        body = {
            "team_id": kwargs.get("team", ""),
            "project_id": kwargs.get("project_id"),
            "payload": {
                "action": kwargs.get("action", "EXECUTE_TASK"),
                "description": kwargs.get("description", ""),
                "task": kwargs.get("description", ""),
                "context": (
                    f"Project {kwargs.get('project_id')}; sprint {kwargs.get('sprint_id')}; "
                    f"issue {kwargs.get('issue_id')}"
                ),
                "issue_id": kwargs.get("issue_id"),
                "sprint_id": kwargs.get("sprint_id"),
            },
        }
        return await orch_post("/tasks", body)
