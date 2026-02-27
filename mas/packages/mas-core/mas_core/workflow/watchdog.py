from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(slots=True, frozen=True)
class WatchdogConfig:
    """Schedule-aware watchdog defaults from the architecture plan."""

    timeout_seconds: int = 3600
    grace_seconds_after_boot: int = 300


def watchdog_elapsed_seconds(
    *,
    now: datetime,
    project_updated_at: datetime,
    boot_at: datetime | None,
) -> float:
    """Elapsed active-time seconds, excluding pre-boot downtime."""

    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    if project_updated_at.tzinfo is None:
        project_updated_at = project_updated_at.replace(tzinfo=UTC)
    if boot_at is None:
        baseline = project_updated_at
    else:
        if boot_at.tzinfo is None:
            boot_at = boot_at.replace(tzinfo=UTC)
        baseline = max(project_updated_at, boot_at)
    elapsed = (now - baseline).total_seconds()
    return max(0.0, elapsed)


def should_watchdog_fire(
    *,
    now: datetime,
    project_updated_at: datetime,
    boot_at: datetime | None,
    config: WatchdogConfig,
) -> bool:
    """
    Return ``True`` when project staleness exceeds timeout.

    Grace behavior follows section 11.2:
    - skip checks during boot grace window
    - measure elapsed from max(project.updated_at, boot_at)
    """

    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    if boot_at is not None:
        if boot_at.tzinfo is None:
            boot_at = boot_at.replace(tzinfo=UTC)
        if (now - boot_at).total_seconds() < config.grace_seconds_after_boot:
            return False

    elapsed = watchdog_elapsed_seconds(
        now=now,
        project_updated_at=project_updated_at,
        boot_at=boot_at,
    )
    return elapsed >= config.timeout_seconds
