#!/usr/bin/env python3
"""Data-only validation and atomic installation of the Stalwart admin source."""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import stat
import tempfile
from pathlib import Path

DEFAULT_REPOSITORY_SOURCE = Path("/mnt/c/projects/AIAT/.env")
DEFAULT_PROTECTED_DESTINATION = Path("/etc/aiat/stalwart-admin-source.env")
ADMIN_SOURCE_KEYS = (
    "STALWART_RECOVERY_ADMIN",
    "admin-st",
    "guest",
)
ADMIN_SOURCE_KEY_SET = frozenset(ADMIN_SOURCE_KEYS)
ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


class AdminSourceRefused(RuntimeError):
    """A fail-closed refusal that is safe to report without credential data."""


def _refuse() -> None:
    raise AdminSourceRefused("protected admin source installation refused")


def _check_regular(details: os.stat_result, *, protected: bool) -> None:
    if not stat.S_ISREG(details.st_mode):
        _refuse()
    if protected and (
        details.st_uid != 0
        or details.st_gid != 0
        or stat.S_IMODE(details.st_mode) != 0o600
    ):
        _refuse()


def _read_regular_file(path: Path, *, protected: bool) -> str:
    """Read a regular file without following a symbolic link."""
    try:
        details = path.lstat()
        if stat.S_ISLNK(details.st_mode):
            _refuse()
        _check_regular(details, protected=protected)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
    except (OSError, AdminSourceRefused):
        _refuse()
    try:
        opened = os.fstat(descriptor)
        _check_regular(opened, protected=protected)
        if (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino):
            _refuse()
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            try:
                raw = handle.read()
            except OSError:
                _refuse()
    except OSError:
        _refuse()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        value = raw.decode("utf-8")
    except UnicodeError:
        _refuse()
    if "\x00" in value:
        _refuse()
    return value


def _parse_data_lines(value: str, *, require_newline: bool) -> dict[str, str]:
    if require_newline and not value.endswith("\n"):
        _refuse()
    parsed: dict[str, str] = {}
    for raw_line in value.splitlines():
        if not raw_line or raw_line.startswith("#"):
            continue
        key, separator, item = raw_line.partition("=")
        if separator != "=" or ENV_KEY.fullmatch(key) is None:
            _refuse()
        if key in parsed and key in ADMIN_SOURCE_KEY_SET:
            _refuse()
        parsed[key] = item
    return parsed


def parse_repository_source(path: Path) -> dict[str, str]:
    """Parse the repository environment as data and return only three values."""
    value = _read_regular_file(path, protected=False)
    parsed = _parse_data_lines(value, require_newline=True)
    selected: dict[str, str] = {}
    for key in ADMIN_SOURCE_KEYS:
        item = parsed.get(key)
        if item is None or not item:
            _refuse()
        selected[key] = item
    return selected


def validate_protected_admin_source_text(value: str) -> dict[str, str]:
    """Validate the exact on-disk representation used by activation."""
    if "\x00" in value or not value.endswith("\n"):
        _refuse()
    lines = value.splitlines()
    if len(lines) != len(ADMIN_SOURCE_KEYS):
        _refuse()
    parsed: dict[str, str] = {}
    for raw_line in lines:
        if not raw_line:
            _refuse()
        key, separator, item = raw_line.partition("=")
        if (
            separator != "="
            or ENV_KEY.fullmatch(key) is None
            or key not in ADMIN_SOURCE_KEY_SET
            or key in parsed
            or not item
        ):
            _refuse()
        parsed[key] = item
    if set(parsed) != ADMIN_SOURCE_KEY_SET:
        _refuse()
    recovery = parsed["STALWART_RECOVERY_ADMIN"]
    if recovery.count(":") != 1:
        _refuse()
    principal, password = recovery.split(":", 1)
    if principal != "admin" or not password or any(character.isspace() for character in password):
        _refuse()
    return parsed


def read_protected_admin_source(path: Path) -> dict[str, str]:
    value = _read_regular_file(path, protected=True)
    return validate_protected_admin_source_text(value)


def read_permanent_admin_password(path: Path) -> str:
    recovery = read_protected_admin_source(path)["STALWART_RECOVERY_ADMIN"]
    return recovery.split(":", 1)[1]


def _ensure_destination_parent(path: Path) -> None:
    parent = path.parent
    try:
        details = parent.lstat()
    except FileNotFoundError:
        try:
            parent.mkdir(mode=0o700, parents=True, exist_ok=False)
            details = parent.lstat()
        except OSError:
            _refuse()
    except OSError:
        _refuse()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        _refuse()
    if details.st_uid != 0 or stat.S_IMODE(details.st_mode) & 0o022:
        _refuse()


def _sync_parent(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _refuse()
    try:
        os.fsync(descriptor)
    except OSError:
        _refuse()
    finally:
        os.close(descriptor)


def install_admin_source(
    source: Path = DEFAULT_REPOSITORY_SOURCE,
    destination: Path = DEFAULT_PROTECTED_DESTINATION,
) -> None:
    """Install a root-owned protected source using an atomic replacement."""
    if os.geteuid() != 0:
        _refuse()
    values = parse_repository_source(source)
    payload = "".join(f"{key}={values[key]}\n" for key in ADMIN_SOURCE_KEYS)
    validate_protected_admin_source_text(payload)
    _ensure_destination_parent(destination)
    try:
        existing = destination.lstat()
    except FileNotFoundError:
        existing = None
    except OSError:
        _refuse()
    if existing is not None and (stat.S_ISLNK(existing.st_mode) or not stat.S_ISREG(existing.st_mode)):
        _refuse()

    descriptor = -1
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            dir=destination.parent,
        )
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, 0, 0)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
        _sync_parent(destination.parent)
    except (OSError, UnicodeError, AdminSourceRefused):
        _refuse()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name is not None:
            with contextlib.suppress(OSError):
                os.unlink(temporary_name)
    read_protected_admin_source(destination)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-file", type=Path, default=DEFAULT_REPOSITORY_SOURCE)
    parser.add_argument(
        "--destination-file",
        type=Path,
        default=DEFAULT_PROTECTED_DESTINATION,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    destination = args.destination_file.absolute()
    if os.geteuid() != 0:
        print("FINAL_STATUS=FAIL")
        print(f"DESTINATION={destination}")
        return 1
    try:
        install_admin_source(args.source_file.absolute(), destination)
    except AdminSourceRefused:
        print("FINAL_STATUS=FAIL")
        print(f"DESTINATION={destination}")
        return 1
    print("FINAL_STATUS=PASS")
    print(f"DESTINATION={destination}")
    print("DESTINATION_OWNER=root:root")
    print("DESTINATION_MODE=0600")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
