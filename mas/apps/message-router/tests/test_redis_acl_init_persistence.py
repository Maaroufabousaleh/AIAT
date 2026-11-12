"""Failure to persist bootstrap-user removal must block router startup."""

import os
import subprocess
from pathlib import Path


def test_missing_bootstrap_password_fails_before_connecting(tmp_path):
    cli = tmp_path / "redis-cli"
    marker = tmp_path / "redis-cli-called"
    cli.write_text(f'#!/bin/sh\nprintf called > "{marker}"\n')
    cli.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}",
               ROUTER_PASSWORD="test", TOOLCACHE_PASSWORD="test")
    env.pop("REDIS_INIT_PASSWORD", None)
    script = Path(__file__).resolve().parents[3] / "infra/compose/redis-acl-init.sh"
    result = subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "REDIS_INIT_PASSWORD must be configured" in result.stderr
    assert not marker.exists()


def test_weak_bootstrap_password_fails_before_connecting(tmp_path):
    cli = tmp_path / "redis-cli"
    marker = tmp_path / "redis-cli-called"
    cli.write_text(f'#!/bin/sh\nprintf called > "{marker}"\n')
    cli.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}",
               REDIS_INIT_PASSWORD="changeme", ROUTER_PASSWORD="test", TOOLCACHE_PASSWORD="test")
    script = Path(__file__).resolve().parents[3] / "infra/compose/redis-acl-init.sh"
    result = subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "REDIS_INIT_PASSWORD must be at least 32 characters" in result.stderr
    assert not marker.exists()


def test_acl_save_failure_fails_closed(tmp_path):
    cli = tmp_path / "redis-cli"
    cli.write_text(
        '#!/bin/sh\ncase "$*" in\n'
        '  *"ACL SETUSER"*) echo OK ;;\n'
        '  *ping*) echo PONG ;;\n'
        "  *) cat >/dev/null; printf 'OK\\n0\\nERR ACL SAVE failed\\n' ;;\n"
        'esac\n'
    )
    cli.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}",
               REDIS_INIT_PASSWORD="I" * 43, ROUTER_PASSWORD="test", TOOLCACHE_PASSWORD="test")
    script = Path(__file__).resolve().parents[3] / "infra/compose/redis-acl-init.sh"
    result = subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "could not disable and persist" in result.stderr


def test_bootstrap_is_disabled_before_acl_save_on_same_connection(tmp_path):
    cli = tmp_path / "redis-cli"
    transcript = tmp_path / "admin-commands"
    acl = tmp_path / "users.acl"
    acl.write_text("user default off\n")
    cli.write_text(
        '#!/bin/sh\ncase "$*" in\n'
        '  *"ACL SETUSER"*) echo OK ;;\n'
        '  *ping*) echo PONG ;;\n'
        '  *) cat > "$ACL_COMMAND_TRANSCRIPT"; printf "OK\\n0\\nOK\\n" ;;\n'
        'esac\n'
    )
    cli.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}",
               REDIS_INIT_PASSWORD="I" * 43,
               ROUTER_PASSWORD="test", TOOLCACHE_PASSWORD="test",
               ACL_COMMAND_TRANSCRIPT=str(transcript), REDIS_ACL_FILE=str(acl))
    script = Path(__file__).resolve().parents[3] / "infra/compose/redis-acl-init.sh"
    result = subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert transcript.read_text().splitlines() == ["ACL SETUSER default off", "CLIENT KILL USER default SKIPME YES", "ACL SAVE"]


def test_repeat_run_without_verifier_fails_closed(tmp_path):
    cli = tmp_path / "redis-cli"
    cli.write_text(
        '#!/bin/sh\ncase "$*" in\n'
        '  *"--user default"*) echo NOAUTH ;;\n'
        '  *eval*) echo 1 ;;\n'
        '  *ping*) echo PONG ;;\n'
        '  *) exit 1 ;;\n'
        'esac\n'
    )
    cli.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}",
               REDIS_INIT_PASSWORD="I" * 43,
               ROUTER_PASSWORD="test", TOOLCACHE_PASSWORD="test")
    env.pop("REDIS_ACL_VERIFY_USER", None)
    env.pop("REDIS_ACL_VERIFY_PASSWORD_FILE", None)
    script = Path(__file__).resolve().parents[3] / "infra/compose/redis-acl-init.sh"
    result = subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "cannot verify the effective and saved bootstrap ACL" in result.stderr


def test_first_run_rejects_enabled_saved_bootstrap(tmp_path):
    cli = tmp_path / "redis-cli"
    acl = tmp_path / "users.acl"
    acl.write_text("user default on\n")
    cli.write_text(
        '#!/bin/sh\ncase "$*" in\n'
        '  *"ACL SETUSER"*) echo OK ;;\n'
        '  *ping*) echo PONG ;;\n'
        '  *) cat >/dev/null; printf "OK\\n0\\nOK\\n" ;;\n'
        'esac\n'
    )
    cli.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}:{os.environ['PATH']}",
               REDIS_INIT_PASSWORD="I" * 43, ROUTER_PASSWORD="test",
               TOOLCACHE_PASSWORD="test", REDIS_ACL_FILE=str(acl))
    script = Path(__file__).resolve().parents[3] / "infra/compose/redis-acl-init.sh"
    result = subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "saved ACL does not show the disabled bootstrap user" in result.stderr
