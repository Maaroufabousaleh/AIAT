"""Compatibility import for the universal worker contract.

The implementation lives in ``mas_core.worker_contract`` so adapters and
non-protocol services can import it without coupling to the legacy protocol
module. Existing callers may use either import path.
"""

from mas_core.worker_contract import *  # noqa: F403
