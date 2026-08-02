#!/usr/bin/env python3
"""Install the protected Stalwart admin source from the repository env file."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _load_helper() -> Any:
    path = Path(__file__).with_name("stalwart_admin_source.py")
    specification = importlib.util.spec_from_file_location("stalwart_admin_source", path)
    if specification is None or specification.loader is None:
        raise RuntimeError("admin-source helper is unavailable")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


if __name__ == "__main__":
    raise SystemExit(_load_helper().main())
