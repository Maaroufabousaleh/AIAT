"""Certify bounded Redis stream recovery across a local AIAT Redis restart.

The live mode uses the router's production Redis stream helpers and a unique
fixture stream. It refuses to restart Redis while any team runner is active or
any canonical team stream has pending entries. The fixture is always cleaned
up in a ``finally`` block; no model, worker, or provider is dispatched.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import dotenv_values
from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "aiat.redis-restart-recovery.v1"
DEFAULT_CONTEXT = "aiat-wsl"
DEFAULT_COMPOSE_PROJECT = "mas"
DEFAULT_REDIS_CONTAINER = "mas-redis-1"
DEFAULT_ROUTER_CONTAINER = "mas-message-router-1"
MAX_BASELINE_STREAM_ENTRIES = 1_000
HEALTH_TIMEOUT_SECONDS = 90

# Load only non-secret settings from the same Compose env file. The password
# is read separately and is never copied into the process command line or the
# evidence report.
_ENV_FILE = dotenv_values(REPO_ROOT / ".env")
for _name in (
    "STREAM_PREFIX",
    "GROUP_PREFIX",
    "DEDUPE_PREFIX",
    "DEDUPE_TTL_SECONDS",
    "REQUEUE_DEDUPE_TTL_SECONDS",
    "MAX_DELIVERY_ATTEMPTS",
    "RECLAIM_IDLE_MS",
    "KNOWN_TEAMS",
):
    if not os.environ.get(_name) and _ENV_FILE.get(_name):
        os.environ[_name] = str(_ENV_FILE[_name])

sys.path.insert(0, str(REPO_ROOT / "apps" / "message-router"))
from message_router.config import settings  # noqa: E402
from message_router.redis_client import (  # noqa: E402
    atomic_publish_message,
    atomic_requeue_message,
    group_name,
    reclaim_idle_messages,
    requeue_dedupe_key,
    scoped_dedupe_key,
    stream_key,
    xack,
)


def _response_error_code(error: ResponseError) -> str:
    """Return only Redis' stable response code, never the raw server text."""

    return str(error).split(maxsplit=1)[0].upper()


def _docker(context: str, *arguments: str, timeout: float = 30) -> str:
    environment = os.environ.copy()
    environment["DOCKER_CONTEXT"] = context
    result = subprocess.run(
        ["docker", *arguments],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=environment,
    )
    if result.returncode != 0:
        raise RuntimeError("docker_command_failed")
    return (result.stdout or "").strip()


def _compose_labels(context: str, container: str) -> dict[str, str]:
    raw = _docker(
        context,
        "inspect",
        "--format",
        "{{json .Config.Labels}}",
        container,
    )
    value = json.loads(raw)
    return value if isinstance(value, dict) else {}


def _active_team_runner_count(context: str, project: str) -> int:
    ids = _docker(
        context,
        "ps",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--format",
        "{{.ID}}",
    ).splitlines()
    count = 0
    for container_id in ids:
        labels = _compose_labels(context, container_id)
        service = str(labels.get("com.docker.compose.service") or "")
        if service.startswith("team-"):
            count += 1
    return count


def _container_health(context: str, container: str) -> str:
    return _docker(
        context,
        "inspect",
        "--format",
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
        container,
    )


async def _pending_count(client: Redis, key: str, group: str) -> int:
    try:
        summary = await client.xpending(key, group)
    except ResponseError as exc:
        if _response_error_code(exc) == "NOGROUP":
            return 0
        raise
    return int(summary.get("pending", 0))


async def _known_stream_snapshot(client: Redis) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for team_id in settings.known_teams:
        key = stream_key(team_id)
        entries = await client.xrange(key, min="-", max="+", count=MAX_BASELINE_STREAM_ENTRIES + 1)
        if len(entries) > MAX_BASELINE_STREAM_ENTRIES:
            raise RuntimeError("known_stream_snapshot_limit_exceeded")
        entry_ids = [str(entry_id) for entry_id, _fields in entries]
        snapshot[team_id] = {
            "entry_count": len(entry_ids),
            "entry_id_sha256": hashlib.sha256("\n".join(entry_ids).encode()).hexdigest(),
            "pending_count": await _pending_count(client, key, group_name(team_id)),
        }
    return snapshot


async def _wait_for_health(context: str, container: str) -> tuple[bool, float]:
    started = time.monotonic()
    deadline = started + HEALTH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            if _container_health(context, container) == "healthy":
                return True, (time.monotonic() - started) * 1000
        except (OSError, subprocess.SubprocessError, RuntimeError):
            pass
        await asyncio.sleep(2)
    return False, (time.monotonic() - started) * 1000


def _report_base(*, context: str, project: str, redis_container: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "live-local-compose-redis-restart-recovery",
        "status": "blocked",
        "observed_at": datetime.now(UTC).isoformat(),
        "environment": "WSL local AIAT Compose",
        "docker_context": context,
        "compose_project": project,
        "redis_container": redis_container,
        "router_container": DEFAULT_ROUTER_CONTAINER,
        "external_provider_dispatch_authorized": False,
        "external_provider_dispatch_performed": False,
        "worker_dispatch_performed": False,
        "credentials_persisted": False,
        "fixture_payload_retained_in_evidence": False,
        "licence_metadata_is_gate": False,
        "steps": {},
        "cleanup": {"attempted": False, "zero_residue": True},
    }


async def _live(args: argparse.Namespace) -> dict[str, Any]:
    report = _report_base(
        context=args.docker_context,
        project=args.compose_project,
        redis_container=args.redis_container,
    )
    report["source_revision"] = _git_revision()

    current_context = _docker(args.docker_context, "context", "show")
    if current_context != args.docker_context:
        report["reason"] = "docker_context_mismatch"
        return report
    labels = _compose_labels(args.docker_context, args.redis_container)
    if (
        labels.get("com.docker.compose.project") != args.compose_project
        or labels.get("com.docker.compose.service") != "redis"
    ):
        report["reason"] = "redis_container_is_not_the_expected_compose_service"
        return report
    report["steps"]["compose_identity"] = "pass"
    report["redis_image_ref"] = _docker(
        args.docker_context,
        "inspect",
        "--format",
        "{{.Config.Image}}",
        args.redis_container,
    )

    active_runners = _active_team_runner_count(args.docker_context, args.compose_project)
    report["active_team_runner_count"] = active_runners
    report["steps"]["provider_dispatch_guard"] = "pass" if active_runners == 0 else "blocked"
    if active_runners:
        report["reason"] = "team_runners_are_active"
        return report

    password = os.getenv("ROUTER_PASSWORD") or _ENV_FILE.get("ROUTER_PASSWORD")
    username = os.getenv("REDIS_USERNAME") or _ENV_FILE.get("REDIS_USERNAME") or "router_user"
    if not password:
        report["reason"] = "required_local_env_missing"
        report["required_input_names"] = ["ROUTER_PASSWORD"]
        return report

    host = _docker(
        args.docker_context,
        "inspect",
        "--format",
        '{{(index .NetworkSettings.Networks "mas_dev_host_access").IPAddress}}',
        args.redis_container,
    )
    if not host:
        report["reason"] = "redis_host_access_address_unavailable"
        return report

    client = Redis(
        host=host,
        port=6379,
        username=str(username),
        password=str(password),
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=10,
    )
    fixture_id = uuid4().hex
    team_id = f"cert-recovery-{fixture_id}"
    fixture_stream = stream_key(team_id)
    fixture_group = group_name(team_id)
    message_id = str(uuid4())
    dedupe_scope = f"redis-restart-{fixture_id}"
    publication_key = scoped_dedupe_key(message_id, dedupe_scope)
    fixture_key = f"{settings.dedupe_prefix}:certification:ttl:{fixture_id}"
    original_entry_id: str | None = None
    requeued_entry_id: str | None = None
    fixture_created = False
    original_reclaim_idle_ms = settings.reclaim_idle_ms
    cleanup_client: Redis | None = client
    failure_reason: str | None = None
    try:
        await client.ping()
        baseline = await _known_stream_snapshot(client)
        total_pending = sum(int(row["pending_count"]) for row in baseline.values())
        report["baseline"] = {
            "team_stream_count": len(baseline),
            "entry_count": sum(int(row["entry_count"]) for row in baseline.values()),
            "pending_entry_count": total_pending,
            "stream_history_sha256": hashlib.sha256(
                json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
        if total_pending:
            report["reason"] = "canonical_team_stream_has_pending_entries"
            report["steps"]["no_preexisting_pending_work"] = "blocked"
            return report
        report["steps"]["no_preexisting_pending_work"] = "pass"

        await client.xgroup_create(fixture_stream, fixture_group, id="0-0", mkstream=True)
        fixture_created = True
        fields = {
            "message_id": message_id,
            "envelope": json.dumps({"fixture": "redis-restart-recovery", "fixture_id": fixture_id}),
        }
        original_entry_id, first_duplicate = await atomic_publish_message(
            team_id,
            fields,
            message_id,
            dedupe_scope=dedupe_scope,
            redis=client,
        )
        duplicate_entry_id, second_duplicate = await atomic_publish_message(
            team_id,
            fields,
            message_id,
            dedupe_scope=dedupe_scope,
            redis=client,
        )
        report["steps"]["atomic_publish_deduplication"] = (
            "pass"
            if not first_duplicate and second_duplicate and original_entry_id == duplicate_entry_id
            else "fail"
        )
        first_delivery = await client.xreadgroup(
            fixture_group,
            "certification-before-restart",
            {fixture_stream: ">"},
            count=1,
        )
        delivered_ids = [str(entry_id) for _key, entries in first_delivery for entry_id, _ in entries]
        pending_before = await _pending_count(client, fixture_stream, fixture_group)
        report["steps"]["fixture_pending_before_restart"] = (
            "pass" if delivered_ids == [original_entry_id] and pending_before == 1 else "fail"
        )
        report["fixture"] = {"entry_count": 1, "pending_before_restart": pending_before}

        pre_restart_pass = all(
            report["steps"].get(name) == "pass"
            for name in ("atomic_publish_deduplication", "fixture_pending_before_restart")
        )
        if not pre_restart_pass:
            failure_reason = "fixture_precondition_failed"
        else:
            await client.aclose()
            cleanup_client = None
            restart_started = time.monotonic()
            _docker(
                args.docker_context,
                "restart",
                "--time",
                "10",
                args.redis_container,
                timeout=45,
            )
            report["redis_restart_duration_ms"] = (time.monotonic() - restart_started) * 1000
            redis_healthy, redis_wait_ms = await _wait_for_health(args.docker_context, args.redis_container)
            report["redis_health_wait_ms"] = redis_wait_ms
            report["steps"]["redis_restarted_healthy"] = "pass" if redis_healthy else "fail"
            if not redis_healthy:
                failure_reason = "redis_did_not_return_healthy"
            else:
                client = Redis(
                    host=host,
                    port=6379,
                    username=str(username),
                    password=str(password),
                    decode_responses=True,
                    socket_connect_timeout=5,
                    socket_timeout=10,
                )
                cleanup_client = client
                await client.ping()
                await asyncio.sleep(0.2)
                after_restart = await _known_stream_snapshot(client)
                report["steps"]["canonical_stream_history_preserved"] = (
                    "pass" if after_restart == baseline else "fail"
                )
                router_reconnected, router_wait_ms = await _wait_for_health(
                    args.docker_context,
                    args.router_container,
                )
                report["router_reconnect_wait_ms"] = router_wait_ms
                report["steps"]["router_reconnected_after_redis_restart"] = (
                    "pass" if router_reconnected else "fail"
                )
                pending_after = await _pending_count(client, fixture_stream, fixture_group)
                report["steps"]["fixture_pending_survived_restart"] = (
                    "pass" if pending_after == 1 else "fail"
                )

                settings.reclaim_idle_ms = 0
                report["reclaim_idle_threshold_override_ms"] = 0
                reclaimed = await reclaim_idle_messages(
                    team_id,
                    consumer_id="certification-after-restart",
                    redis=client,
                )
                report["steps"]["xautoclaim_recovered_pending_entry"] = (
                    "pass"
                    if len(reclaimed) == 1 and reclaimed[0][0] == original_entry_id
                    else "fail"
                )
                requeued_entry_id, first_requeue_duplicate = await atomic_requeue_message(
                    team_id,
                    original_entry_id,
                    {"envelope": fields["envelope"]},
                    redis=client,
                )
                replay_entry_id, replay_was_deduplicated = await atomic_requeue_message(
                    team_id,
                    original_entry_id,
                    {"envelope": fields["envelope"]},
                    redis=client,
                )
                fixture_entries = await client.xrange(fixture_stream, min="-", max="+", count=2)
                pending_after_requeue = await _pending_count(client, fixture_stream, fixture_group)
                report["steps"]["requeue_response_replay_is_idempotent"] = (
                    "pass"
                    if not first_requeue_duplicate
                    and replay_was_deduplicated
                    and requeued_entry_id == replay_entry_id
                    and len(fixture_entries) == 1
                    and str(fixture_entries[0][0]) == requeued_entry_id
                    and pending_after_requeue == 0
                    else "fail"
                )

                replacement_delivery = await client.xreadgroup(
                    fixture_group,
                    "certification-ack-after-restart",
                    {fixture_stream: ">"},
                    count=1,
                )
                replacement_ids = [
                    str(entry_id) for _key, entries in replacement_delivery for entry_id, _ in entries
                ]
                if replacement_ids == [requeued_entry_id]:
                    await xack(team_id, requeued_entry_id, redis=client)
                pending_after_ack = await _pending_count(client, fixture_stream, fixture_group)
                redelivery = await reclaim_idle_messages(
                    team_id,
                    consumer_id="certification-after-ack",
                    redis=client,
                )
                report["steps"]["ack_clears_pending_without_redelivery"] = (
                    "pass"
                    if replacement_ids == [requeued_entry_id]
                    and pending_after_ack == 0
                    and not redelivery
                    else "fail"
                )

                await client.set(fixture_key, "fixture", ex=2)
                await asyncio.sleep(2.2)
                report["steps"]["dedupe_ttl_expires"] = "pass" if await client.get(fixture_key) is None else "fail"
                final_snapshot = await _known_stream_snapshot(client)
                report["steps"]["canonical_stream_history_unchanged"] = (
                    "pass" if final_snapshot == baseline else "fail"
                )
                if args.restart_router:
                    router_restart_started = time.monotonic()
                    _docker(
                        args.docker_context,
                        "restart",
                        "--time",
                        "10",
                        args.router_container,
                        timeout=45,
                    )
                    report["router_restart_duration_ms"] = (
                        time.monotonic() - router_restart_started
                    ) * 1000
                    router_healthy, router_health_wait_ms = await _wait_for_health(
                        args.docker_context,
                        args.router_container,
                    )
                    report["router_health_wait_ms"] = router_health_wait_ms
                    await client.ping()
                    report["router_health_after_restart"] = (
                        "healthy" if router_healthy else _container_health(args.docker_context, args.router_container)
                    )
                    after_router_restart = await _known_stream_snapshot(client)
                    report["steps"]["router_restart_rehydrated_redis_state"] = (
                        "pass" if router_healthy and after_router_restart == baseline else "fail"
                    )
    except (OSError, RedisError, ResponseError, subprocess.SubprocessError, RuntimeError) as exc:
        failure_reason = type(exc).__name__
    finally:
        settings.reclaim_idle_ms = original_reclaim_idle_ms
        report["cleanup"]["attempted"] = fixture_created
        if not fixture_created:
            report["cleanup"]["zero_residue"] = True
            if cleanup_client is not None:
                await cleanup_client.aclose()
        elif cleanup_client is not None:
            try:
                if requeued_entry_id:
                    await cleanup_client.xack(fixture_stream, fixture_group, requeued_entry_id)
                if original_entry_id:
                    await cleanup_client.xack(fixture_stream, fixture_group, original_entry_id)
                destroyed = await cleanup_client.xgroup_destroy(fixture_stream, fixture_group)
                cleanup_keys = [fixture_stream, publication_key, fixture_key]
                if original_entry_id:
                    cleanup_keys.append(requeue_dedupe_key(team_id, original_entry_id))
                await cleanup_client.delete(*cleanup_keys)
                remaining = [await cleanup_client.get(key) for key in cleanup_keys if key != fixture_stream]
                stream_rows = await cleanup_client.xrange(fixture_stream, min="-", max="+", count=1)
                report["cleanup"]["zero_residue"] = bool(destroyed) and not any(remaining) and not stream_rows
            except (OSError, RedisError, ResponseError):
                report["cleanup"]["zero_residue"] = False
            finally:
                await cleanup_client.aclose()
        else:
            # Reconnect after the deliberate server restart so all fixture
            # resources are removed even if a post-restart assertion fails.
            try:
                cleanup_client = Redis(
                    host=host,
                    port=6379,
                    username=str(username),
                    password=str(password),
                    decode_responses=True,
                    socket_connect_timeout=5,
                    socket_timeout=10,
                )
                if original_entry_id:
                    await cleanup_client.xack(fixture_stream, fixture_group, original_entry_id)
                if requeued_entry_id:
                    await cleanup_client.xack(fixture_stream, fixture_group, requeued_entry_id)
                cleanup_keys = [fixture_stream, publication_key, fixture_key]
                if original_entry_id:
                    cleanup_keys.append(requeue_dedupe_key(team_id, original_entry_id))
                destroyed = await cleanup_client.xgroup_destroy(fixture_stream, fixture_group)
                await cleanup_client.delete(*cleanup_keys)
                remaining = [await cleanup_client.get(key) for key in cleanup_keys if key != fixture_stream]
                stream_rows = await cleanup_client.xrange(fixture_stream, min="-", max="+", count=1)
                report["cleanup"]["zero_residue"] = bool(destroyed) and not any(remaining) and not stream_rows
            except (OSError, RedisError, ResponseError):
                report["cleanup"]["zero_residue"] = False
            finally:
                if cleanup_client is not None:
                    await cleanup_client.aclose()

    if failure_reason:
        report["reason"] = failure_reason
    all_steps_pass = bool(report["steps"]) and all(value == "pass" for value in report["steps"].values())
    report["status"] = "pass" if all_steps_pass and report["cleanup"]["zero_residue"] else "fail"
    report["fixture"] = {
        **report.get("fixture", {}),
        "fixture_id_sha256": hashlib.sha256(fixture_id.encode()).hexdigest(),
        "payload_retained_in_evidence": False,
    }
    return report


def _git_revision() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = (result.stdout or "").strip()
    return value if result.returncode == 0 and len(value) == 40 else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="perform the live bounded Redis test")
    parser.add_argument(
        "--restart-redis",
        action="store_true",
        help="explicitly authorize restarting only the selected AIAT Redis container",
    )
    parser.add_argument(
        "--restart-router",
        action="store_true",
        help="explicitly authorize restarting only the selected AIAT message-router container",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--docker-context", default=DEFAULT_CONTEXT)
    parser.add_argument("--compose-project", default=DEFAULT_COMPOSE_PROJECT)
    parser.add_argument("--redis-container", default=DEFAULT_REDIS_CONTAINER)
    parser.add_argument("--router-container", default=DEFAULT_ROUTER_CONTAINER)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "docs" / "provenance" / "redis_restart_recovery_evidence.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.live or not args.restart_redis or not args.restart_router:
        report = _report_base(
            context=args.docker_context,
            project=args.compose_project,
            redis_container=args.redis_container,
        )
        report["reason"] = "explicit_live_redis_and_router_restart_flags_required"
        code = 2
    else:
        try:
            report = asyncio.run(_live(args))
        except (OSError, RedisError, ResponseError, subprocess.SubprocessError, RuntimeError) as exc:
            report = _report_base(
                context=args.docker_context,
                project=args.compose_project,
                redis_container=args.redis_container,
            )
            report["reason"] = type(exc).__name__
        code = 0 if report.get("status") == "pass" else 2 if report.get("status") == "blocked" else 1
    report["source_revision"] = report.get("source_revision") or _git_revision()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
