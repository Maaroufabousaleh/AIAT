"""Opt-in real Redis initializer test; uses only an isolated temporary directory."""

import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import pytest


@pytest.mark.skipif(os.environ.get("AIAT_REDIS_INTEGRATION") != "1", reason="opt-in disposable Redis")
def test_initializer_restart_and_verifier() -> None:
    docker = ["docker"]
    socket = os.environ.get("AIAT_TEST_DOCKER_HOST")
    if socket:
        docker += ["-H", socket]
    name = "aiat-redis-init-test-" + uuid.uuid4().hex
    base = "redis:7.2-alpine@sha256:05a97a479bc73de66f087dc05b569010772880f778cc8671fa6b8aadee32e5c6"
    candidate = os.environ["AIAT_REDIS_TEST_IMAGE"]

    def call(args, *, stdin=None, check=True):
        result = subprocess.run(docker + args, input=stdin, capture_output=True, text=True, timeout=90)
        if check and result.returncode:
            raise AssertionError("disposable Redis command failed")
        return result

    def redis(*commands):
        return call(["exec", "-i", name, "redis-cli", "--raw"], stdin="\n".join(commands) + "\n", check=False).stdout.strip().splitlines()

    with tempfile.TemporaryDirectory(prefix="aiat-disposable-redis-") as tmp:
        root = Path(tmp)
        root.chmod(0o755)
        acl = root / "users.acl"
        acl.write_text("user default on >redis_init_temp_pass ~* &* +@all\n")
        acl.chmod(0o666)
        verifier = root / "verifier"
        verifier.write_text("verifier-fixture")
        verifier.chmod(0o600)
        server_started = False
        try:
            call(["run", "-d", "--rm", "--name", name, "--network", "none",
                  "--mount", f"type=bind,source={root},target=/data", "--entrypoint", "sh", base,
                  "-c", "exec redis-server --bind 127.0.0.1 --aclfile /data/users.acl --save '' --appendonly no"])
            server_started = True
            for _ in range(30):
                if redis("AUTH default redis_init_temp_pass", "PING") == ["OK", "PONG"]:
                    break
                time.sleep(0.5)
            else:
                raise AssertionError("disposable Redis did not start")
            assert redis("AUTH default redis_init_temp_pass",
                         "ACL SETUSER verifier on >verifier-fixture -@all +ping +acl|getuser",
                         "ACL SETUSER test_admin on >admin-fixture ~* &* +@all", "ACL SAVE") == ["OK"] * 4

            def initialize(with_verifier):
                args = ["run", "--rm", "--network", f"container:{name}",
                        "--mount", f"type=bind,source={root},target=/acl-state,readonly",
                        "-e", "REDIS_HOST=127.0.0.1", "-e", "ROUTER_PASSWORD=router-fixture",
                        "-e", "TOOLCACHE_PASSWORD=cache-fixture"]
                if with_verifier:
                    args += ["-e", "REDIS_ACL_VERIFY_USER=verifier",
                             "-e", "REDIS_ACL_VERIFY_PASSWORD_FILE=/acl-state/verifier"]
                return call(args + [candidate], check=False).returncode

            session = subprocess.Popen(docker + ["exec", "-i", name, "redis-cli", "--raw"],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            try:
                session.stdin.write("AUTH default redis_init_temp_pass\n")
                session.stdin.flush()
                assert session.stdout.readline().strip() == "OK"
                assert any("user=default" in row for row in redis("AUTH test_admin admin-fixture", "CLIENT LIST"))
                assert initialize(False) == 0
                assert not any("user=default" in row for row in redis("AUTH test_admin admin-fixture", "CLIENT LIST"))
            finally:
                session.stdin.close()
                session.wait(timeout=10)
            assert redis("AUTH default redis_init_temp_pass", "PING") != ["OK", "PONG"]
            assert initialize(False) != 0
            assert initialize(True) == 0
            assert redis("AUTH test_admin admin-fixture", "ACL DRYRUN verifier ACL SAVE")[-1] != "OK"
            call(["restart", "--time", "3", name])
            for _ in range(30):
                if redis("AUTH test_admin admin-fixture", "PING") == ["OK", "PONG"]:
                    break
                time.sleep(0.5)
            else:
                raise AssertionError("disposable Redis did not restart")
            assert redis("AUTH default redis_init_temp_pass", "PING") != ["OK", "PONG"]
            assert initialize(True) == 0
            assert redis("AUTH test_admin admin-fixture", "ACL SETUSER default on resetpass >other-fixture", "ACL SAVE") == ["OK"] * 3
            assert initialize(True) != 0
            assert redis("AUTH test_admin admin-fixture", "ACL SETUSER default off") == ["OK"] * 2
            assert initialize(True) != 0  # effective state and persisted state disagree
            assert redis("AUTH test_admin admin-fixture", "ACL SAVE") == ["OK"] * 2
            assert initialize(True) == 0
            assert redis("AUTH test_admin admin-fixture", "ACL SETUSER verifier resetpass >rotated-fixture", "ACL SAVE") == ["OK"] * 3
            assert initialize(True) != 0
            verifier.write_text("rotated-fixture")
            assert initialize(True) == 0
        finally:
            if server_started:
                call(["stop", "--time", "3", name])
