"""Worker registry — configuration-driven integration units.

Sub-packages
------------
seeder        — Manifest-to-DB seeding utility
ingestion     — GitHub repo ingestion and upstream mirror management
evaluator     — Repository evaluation engine
adapter_factory — Adapter creation for native/wrapper/fork workers
compat_tests  — Compatibility test harness
"""

from mas_core.worker_registry.seeder import seed_workers_from_directory

__all__ = ["seed_workers_from_directory"]
