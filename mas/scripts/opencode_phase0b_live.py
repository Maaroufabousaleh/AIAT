"""Run the local authenticated OpenCode contract capture without persisting secrets."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx


def main() -> int:
    root = Path(os.getenv("AIAT_OPENCODE_EVIDENCE_DIR", r"C:\tmp\aiat-opencode-phase0b-live"))
    workspace = root / "workspace"
    logs = root / "logs"
    workspace.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    binary = Path(os.getenv("OPENCODE_BINARY", r"C:\Users\Maaro\AppData\Roaming\npm\node_modules\opencode-ai\bin\opencode.exe"))
    password = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode()
    username = "aiat-phase0b"
    env = os.environ.copy()
    env["OPENCODE_SERVER_PASSWORD"] = password
    env["OPENCODE_SERVER_USERNAME"] = username
    with (logs / "stdout.txt").open("wb") as stdout, (logs / "stderr.txt").open("wb") as stderr:
        process = subprocess.Popen(
            [str(binary), "serve", "--hostname", "127.0.0.1", "--port", "8091"],
            cwd=workspace,
            env=env,
            stdout=stdout,
            stderr=stderr,
        )
    auth = httpx.BasicAuth(username, password)
    try:
        with httpx.Client(base_url="http://127.0.0.1:8091", auth=auth, timeout=5.0) as client:
            for _ in range(60):
                try:
                    if client.get("/global/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
            else:
                raise RuntimeError("OpenCode did not become ready")
            unauth = httpx.get("http://127.0.0.1:8091/global/health", timeout=5.0)
            wrong = httpx.get("http://127.0.0.1:8091/global/health", auth=httpx.BasicAuth(username, "wrong"), timeout=5.0)
            openapi_unauth = httpx.get("http://127.0.0.1:8091/doc", timeout=5.0)
            if (unauth.status_code, wrong.status_code, openapi_unauth.status_code) != (401, 401, 401):
                raise RuntimeError(f"authentication boundary failed: {unauth.status_code}/{wrong.status_code}/{openapi_unauth.status_code}")
        os.environ["OPENCODE_SERVER_USERNAME"] = username
        os.environ["OPENCODE_SERVER_PASSWORD"] = password
        verifier = Path(__file__).with_name("opencode_phase0b_verify.py")
        config_digest = hashlib.sha256(json.dumps({"auth_mode": "native_basic_auth", "password_env": "OPENCODE_SERVER_PASSWORD", "username_env": "OPENCODE_SERVER_USERNAME", "hostname": "127.0.0.1", "port": 8091}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        args = [sys.executable, str(verifier), "--base-url", "http://127.0.0.1:8091", "--commit-sha", "F8C45BAE73A8F1E2088023FDD34DC2FE0A7F93F505F073E0703E4E1A19AFE8FF", "--binary", str(binary), "--expected-binary-sha256", "F8C45BAE73A8F1E2088023FDD34DC2FE0A7F93F505F073E0703E4E1A19AFE8FF", "--config-sha256", config_digest, "--container-image-digest", "sha256:48c93f5f65a0ad622b1e65da6fbcf4f2d0f738e295b7317aff6276f4d0a14635"]
        subprocess.run(args, check=True)
        print("authentication=401/401/200 openapi_unauth=401")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
