"""Shared risk classification utilities for worker evaluation."""

from __future__ import annotations

from typing import Any


def worker_risk_labels(worker: dict[str, Any]) -> set[str]:
    """Extract all risk-related labels from a worker dict.

    Scans top-level keys ``risk_tier``, ``risk_level``, ``classification``,
    nested ``adapter_config`` and ``wrapper_config`` blocks, and the ``tags``
    list. Labels are normalized to lowercase with hyphens replaced by
    underscores.
    """
    labels: set[str] = set()
    for key in ("risk_tier", "risk_level", "classification"):
        value = worker.get(key)
        if isinstance(value, str):
            labels.add(value.lower().replace("-", "_"))

    for nested_key in ("adapter_config", "wrapper_config"):
        nested = worker.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in ("risk_tier", "risk_level", "classification"):
            value = nested.get(key)
            if isinstance(value, str):
                labels.add(value.lower().replace("-", "_"))
        if nested.get("dual_use") is True:
            labels.add("dual_use")

    tags = worker.get("tags")
    if isinstance(tags, list):
        labels.update(str(tag).lower().replace("-", "_") for tag in tags)
    return labels


def is_medium_or_dual_use_worker(worker: dict[str, Any]) -> bool:
    """Return True if the worker is classified as medium-risk or dual-use."""
    labels = worker_risk_labels(worker)
    return bool(labels & {"medium", "medium_risk", "dual_use", "dualuse"})