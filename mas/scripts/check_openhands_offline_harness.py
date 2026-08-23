"""Write deterministic OpenHands lifecycle/security fixture evidence."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from openhands_offline_harness import main as run_harness
except ImportError:  # pragma: no cover - package invocation fallback
    from scripts.openhands_offline_harness import main as run_harness  # type: ignore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    code = run_harness(args.output)
    print(f"offline OpenHands harness: {'PASS' if code == 0 else 'FAIL'}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
