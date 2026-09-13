#!/usr/bin/env python3
"""Check the repository boundary for local and production secret material.

This is intentionally a small, fail-closed guard for the Cloudflare mail-edge
and identity-service production boundary. It does not replace a credential
scanner. It verifies that representative local secret paths are ignored, that
tracked secret-looking filenames are examples only, and that the production
boundary does not contain literal secret assignments or secret generators.
Output is limited to counts and paths; values are never printed.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path, PurePosixPath

SECRET_VARIABLES = (
    "CLOUDFLARE_MAIL_EDGE_AUTH_SECRET",
    "RESEND_API_KEY",
    "RESEND_WEBHOOK_SIGNING_SECRET",
    "IDENTITY_DATABASE_PASSWORD",
    "IDENTITY_SERVICE_SECRET",
    "IDENTITY_CONTENT_ENCRYPTION_KEY",
    "AIAT_IDENTITY_CLIENT_PRIVATE_KEY",
    "AIAT_IDENTITY_TOOL_PRIVATE_KEY",
    "IDENTITY_CLIENT_PUBLIC_KEYS_JSON",
    "IDENTITY_CLIENT_SCOPES_JSON",
    "IDENTITY_BOOTSTRAP_TOKEN",
    "STALWART_API_KEY",
    "STALWART_JMAP_SERVICE_TOKEN",
    "MAIL_EDGE_AUTH_SECRET",
    "CLOUDFLARE_IDENTITY_TUNNEL_TOKEN",
)

LOCAL_SECRET_PROBES = (
    ".env",
    ".env.local",
    ".env.production",
    ".dev.vars",
    ".dev.vars.local",
    "mas/infra/cloudflare/.env.cloudflare-mail-edge.local",
    "mas/infra/cloudflare/email-worker/.dev.vars",
    "mas/.secrets/identity.env",
    "mas/infra/cloudflare/secrets/resend.credentials",
    "mas/infra/cloudflare/private.key",
    "mas/infra/cloudflare/identity.p12",
)

PRODUCTION_BOUNDARY_FILES = (
    "mas/infra/cloudflare/docker-compose.yml",
    "mas/infra/cloudflare/email-worker/wrangler.toml",
    "mas/apps/identity-service/identity_service/config.py",
    "mas/scripts/certify_cloudflare_mail_edge_live.py",
    "mas/scripts/certify_resend_live.py",
)

SECRET_ASSIGNMENT = re.compile(
    r"(?<![\${A-Z0-9_{])(?:"
    + "|".join(re.escape(name) for name in sorted(SECRET_VARIABLES, key=len, reverse=True))
    + r")(?:\s*[:=])",
    re.IGNORECASE,
)
SECRET_GENERATOR = re.compile(
    r"\b(?:"
    r"secrets\.(?:token_bytes|token_hex|token_urlsafe)|"
    r"openssl\s+rand|ssh-keygen|wg\s+genkey|"
    r"(?:Ed25519PrivateKey|Fernet)\.generate"
    r")\b",
    re.IGNORECASE,
)


def _repo_root(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(result.stdout.strip()).resolve()


def _tracked_paths(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [value for value in result.stdout.decode().split("\0") if value]


def _is_ignored(root: Path, relative_path: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--no-index", "-q", "--", relative_path],
        check=False,
    )
    return result.returncode == 0


def _is_example(path: str) -> bool:
    name = PurePosixPath(path).name
    return name.endswith(".example") or ".example." in name


def _tracked_secret_candidates(paths: list[str]) -> list[str]:
    candidates: list[str] = []
    for path in paths:
        name = PurePosixPath(path).name.lower()
        looks_like_local_secret = (
            name == ".env"
            or name.startswith(".env.")
            or name.endswith(".env")
            or name == ".dev.vars"
            or name.startswith(".dev.vars.")
            or name.endswith(
                (".secret", ".secrets", ".credential", ".credentials", ".key", ".p12", ".pfx")
            )
        )
        if looks_like_local_secret and not _is_example(path):
            candidates.append(path)
    return candidates


def _assignment_rhs(line: str, match: re.Match[str]) -> str:
    tail = line[match.end() :]
    # Python annotations use NAME: type = value; YAML uses NAME: value.
    if "=" in tail:
        return tail.rsplit("=", 1)[1]
    return tail


def _allowed_secret_rhs(rhs: str) -> bool:
    value = rhs.split("#", 1)[0].strip()
    if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
        value = value[1:-1].strip()
    if not value or value in {'""', "''", "{}"}:
        return True
    return value.startswith(
        (
            "${",
            "os.getenv(",
            "os.environ",
            "getenv(",
            "environ[",
            "process.env.",
            "Field(",
        )
    )


def _production_findings(root: Path) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    literal_assignments: list[tuple[str, int]] = []
    generators: list[tuple[str, int]] = []
    for relative in PRODUCTION_BOUNDARY_FILES:
        path = root / relative
        if not path.is_file():
            literal_assignments.append((relative, 0))
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            literal_assignments.append((relative, 0))
            continue
        for number, line in enumerate(lines, 1):
            match = SECRET_ASSIGNMENT.search(line)
            if match and not _allowed_secret_rhs(_assignment_rhs(line, match)):
                literal_assignments.append((relative, number))
            if SECRET_GENERATOR.search(line):
                generators.append((relative, number))
    return literal_assignments, generators


def check(root: Path) -> dict[str, int | list[str]]:
    ignored = [path for path in LOCAL_SECRET_PROBES if _is_ignored(root, path)]
    missing_ignored = [path for path in LOCAL_SECRET_PROBES if path not in ignored]
    tracked_candidates = _tracked_secret_candidates(_tracked_paths(root))
    literal_assignments, generators = _production_findings(root)
    return {
        "ignored_probe_count": len(ignored),
        "missing_ignored_probes": missing_ignored,
        "tracked_secret_candidates": tracked_candidates,
        "literal_secret_assignments": [f"{path}:{line}" for path, line in literal_assignments],
        "secret_generators": [f"{path}:{line}" for path, line in generators],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="repository root (defaults to git top-level)")
    args = parser.parse_args(argv)
    try:
        report = check(_repo_root(args.root))
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"SECRET_BOUNDARY=FAIL reason={type(exc).__name__}")
        return 2

    failures = (
        report["missing_ignored_probes"]
        or report["tracked_secret_candidates"]
        or report["literal_secret_assignments"]
        or report["secret_generators"]
    )
    if failures:
        print("SECRET_BOUNDARY=FAIL")
        for key in (
            "missing_ignored_probes",
            "tracked_secret_candidates",
            "literal_secret_assignments",
            "secret_generators",
        ):
            for value in report[key]:
                print(f"{key}={value}")
        return 1

    print("SECRET_BOUNDARY=PASS")
    print(f"IGNORED_PROBES={report['ignored_probe_count']}/{len(LOCAL_SECRET_PROBES)}")
    print(f"TRACKED_SECRET_CANDIDATES={len(report['tracked_secret_candidates'])}")
    print(f"PRODUCTION_LITERAL_SECRET_ASSIGNMENTS={len(report['literal_secret_assignments'])}")
    print(f"PRODUCTION_SECRET_GENERATORS={len(report['secret_generators'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
