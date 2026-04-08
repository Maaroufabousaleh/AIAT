"""GitHub ingestion and upstream mirror management.

Handles cloning/fetching external worker repositories into a managed mirror
directory, pinning to revisions, and checking for updates.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from mas_core.memory.storage import AgentStorage

logger = logging.getLogger(__name__)

MIRROR_BASE = Path(os.environ.get("WORKER_MIRROR_DIR", "workers/mirror"))


async def ingest_repository(
    worker_id: str,
    source_repo: str,
    revision: str | None = None,
) -> Path:
    """Clone a GitHub repository into the managed mirror.

    Parameters
    ----------
    worker_id:
        Unique worker identifier (used as mirror subdirectory name).
    source_repo:
        GitHub repository URL (https or ssh).
    revision:
        Git ref to pin to (branch, tag, or commit SHA).

    Returns
    -------
    Path
        Path to the cloned mirror directory.
    """
    mirror_path = MIRROR_BASE / worker_id

    if (mirror_path / ".git").exists():
        logger.info("Mirror already exists for %s, fetching updates", worker_id)
        await _run_git("fetch", "origin", cwd=mirror_path)
    else:
        if mirror_path.exists() and any(mirror_path.iterdir()):
            raise FileExistsError(
                f"Mirror directory {mirror_path} exists and is not empty. Remove it before cloning."
            )
        logger.info("Cloning %s into %s", source_repo, mirror_path)
        await _run_git("clone", source_repo, str(mirror_path))

    if revision:
        await _run_git("fetch", "origin", cwd=mirror_path)
        await _run_git("checkout", revision, cwd=mirror_path)

    commit_sha = await _get_head_commit(mirror_path)
    logger.info("Mirror for %s pinned to %s", worker_id, commit_sha)
    return mirror_path


async def pull_upstream(
    *,
    worker_id: UUID,
    source_repo: str,
    storage: AgentStorage,
    target_revision: str | None = None,
) -> str:
    """Pull latest changes from upstream for an existing mirror.

    Parameters
    ----------
    worker_id:
        Worker database ID.
    source_repo:
        GitHub repository URL.
    storage:
        Connected AgentStorage instance.
    target_revision:
        Optional specific revision to pull.

    Returns
    -------
    str
        The new HEAD commit SHA.
    """
    worker = await storage.get_worker(worker_id)
    if worker is None:
        raise ValueError(f"Worker {worker_id} not found")

    wid = worker["name"]
    mirror_path = MIRROR_BASE / wid

    if not (mirror_path / ".git").exists():
        await ingest_repository(wid, source_repo, target_revision or worker.get("source_revision"))
    else:
        await _run_git("fetch", "origin", cwd=mirror_path)
        revision = target_revision or worker.get("source_revision") or "main"

        try:
            current_branch = await _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=mirror_path)
        except RuntimeError:
            current_branch = "HEAD"

        if current_branch == "HEAD":
            await _run_git("checkout", "-b", "mas-integration", cwd=mirror_path)
            await _run_git("fetch", "origin", revision, cwd=mirror_path)
            await _run_git(
                "reset",
                "--hard",
                f"origin/{revision}" if "/" in revision else revision,
                cwd=mirror_path,
            )
        else:
            await _run_git("checkout", revision, cwd=mirror_path)
            await _run_git("pull", "origin", cwd=mirror_path)

    commit_sha = await _get_head_commit(mirror_path)
    now = datetime.now(tz=UTC)

    await storage.update_worker_upstream(
        worker_id=worker_id,
        last_upstream_sync=now,
        upstream_commit_sha=commit_sha,
    )

    logger.info("Upstream pulled for %s: %s", wid, commit_sha)
    return commit_sha


async def check_for_updates(
    *,
    source_repo: str,
    current_revision: str | None = None,
    current_commit: str | None = None,
) -> dict[str, str | bool | None]:
    """Check if a remote repository has new commits compared to current state.

    Uses ``git ls-remote`` to get the latest commit without cloning.

    Returns
    -------
    dict
        {"has_updates": bool, "latest_commit": str, "current_commit": str}
    """
    ref = current_revision or "HEAD"
    output = await _run_git("ls-remote", source_repo, ref)
    if not output:
        raise RuntimeError(f"git ls-remote returned no output for {source_repo}")
    latest_commit = output.split()[0]

    return {
        "has_updates": latest_commit != current_commit if current_commit else True,
        "latest_commit": latest_commit,
        "current_commit": current_commit,
        "current_revision": current_revision,
    }


async def get_mirror_path(worker_id: str) -> Path | None:
    """Get the mirror path for a worker if it exists."""
    mirror_path = MIRROR_BASE / worker_id
    if (mirror_path / ".git").exists():
        return mirror_path
    return None


async def remove_mirror(worker_id: str) -> None:
    """Remove a worker's mirror directory."""
    mirror_path = MIRROR_BASE / worker_id
    if mirror_path.exists():
        shutil.rmtree(mirror_path)
        logger.info("Removed mirror for %s", worker_id)


async def _run_git(*args: str, cwd: Path | None = None) -> str:
    """Run a git command and return stdout."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr.decode()}")
    return stdout.decode().strip()


async def _get_head_commit(repo_path: Path) -> str:
    """Get the HEAD commit SHA of a repository."""
    return await _run_git("rev-parse", "HEAD", cwd=repo_path)
