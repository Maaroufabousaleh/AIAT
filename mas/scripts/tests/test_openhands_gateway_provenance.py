"""Tests for diagnostic OpenHands gateway provenance checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _module():
    path = Path(__file__).resolve().parents[1] / "verify_openhands_gateway_provenance.py"
    spec = importlib.util.spec_from_file_location("verify_openhands_gateway_provenance", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _result(stdout: str = "", *, returncode: int = 0):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


def test_annotated_tag_is_peeled_to_expected_commit() -> None:
    module = _module()

    def runner(command, **_kwargs):
        assert command[:2] == ["git", "ls-remote"]
        return _result(
            "dc765d4738b8c68b8e0d57bff5e343fbf0be41ae refs/tags/v3.8.38\n"
            "7b139fdb5e42658a49f9d99ddf0eeeba9a994fd8 refs/tags/v3.8.38^{}\n"
        )

    report = module.resolve_release_tag(
        repo="https://example.invalid/OmniRoute.git",
        tag="v3.8.38",
        expected_commit="7b139fdb5e42658a49f9d99ddf0eeeba9a994fd8",
        runner=runner,
    )
    assert report["resolution_status"] == "PASS"
    assert report["tag_type"] == "ANNOTATED"
    assert report["target"] == "7b139fdb5e42658a49f9d99ddf0eeeba9a994fd8"


def test_lightweight_tag_uses_direct_commit_when_no_peeled_ref_exists() -> None:
    module = _module()

    def runner(command, **_kwargs):
        return _result("6e8282d40655d47ed1557f030e53d6819e464e79 refs/tags/v1.90.0\n")

    report = module.resolve_release_tag(
        repo="https://example.invalid/litellm.git",
        tag="v1.90.0",
        expected_commit="6e8282d40655d47ed1557f030e53d6819e464e79",
        runner=runner,
    )
    assert report["resolution_status"] == "PASS"
    assert report["tag_type"] == "LIGHTWEIGHT"
    assert report["target"] == "6e8282d40655d47ed1557f030e53d6819e464e79"


def test_missing_tag_fails_closed() -> None:
    module = _module()
    report = module.resolve_release_tag(
        repo="https://example.invalid/litellm.git",
        tag="v1.90.0",
        expected_commit="6e8282d40655d47ed1557f030e53d6819e464e79",
        runner=lambda _command, **_kwargs: _result(""),
    )
    assert report["resolution_status"] == "FAILED"
    assert report["failure_class"] == "RELEASE_TAG_MISSING"


def test_wrong_target_and_moved_tag_fail_with_scalar_reason() -> None:
    module = _module()

    def runner(command, **_kwargs):
        return _result("0" * 40 + " refs/tags/v1.90.0\n")

    report = module.resolve_release_tag(
        repo="https://example.invalid/litellm.git",
        tag="v1.90.0",
        expected_commit="6e8282d40655d47ed1557f030e53d6819e464e79",
        runner=runner,
    )
    assert report["failure_class"] == "RELEASE_TAG_TARGET_MISMATCH"
    assert report["tag_type"] == "LIGHTWEIGHT"
    assert report["target"] == "0" * 40


def test_moved_annotated_tag_is_rejected_after_peeling() -> None:
    module = _module()

    def runner(command, **_kwargs):
        return _result(
            "dc765d4738b8c68b8e0d57bff5e343fbf0be41ae refs/tags/v3.8.38\n"
            + "0" * 40
            + " refs/tags/v3.8.38^{}\n"
        )

    report = module.resolve_release_tag(
        repo="https://example.invalid/OmniRoute.git",
        tag="v3.8.38",
        expected_commit="7b139fdb5e42658a49f9d99ddf0eeeba9a994fd8",
        runner=runner,
    )
    assert report["failure_class"] == "RELEASE_TAG_TARGET_MISMATCH"
    assert report["tag_type"] == "ANNOTATED"


def test_malformed_tag_response_fails_without_retaining_response() -> None:
    module = _module()
    report = module.resolve_release_tag(
        repo="https://example.invalid/litellm.git",
        tag="v1.90.0",
        expected_commit="6e8282d40655d47ed1557f030e53d6819e464e79",
        runner=lambda _command, **_kwargs: _result("not-a-git-response\n"),
    )
    assert report["failure_class"] == "RELEASE_TAG_MALFORMED_RESPONSE"
    assert report["response_payload_retained"] is False


def test_all_six_checks_are_retained_independently() -> None:
    module = _module()

    def runner(command, **_kwargs):
        if command[0] == "docker" and "litellm" in command[-3]:
            return _result("1.90.0\n")
        if command[0] == "docker" and "omniroute" in command[-3]:
            return _result("3.8.38\n")
        if command[:2] == ["git", "ls-remote"] and "litellm" in command[2]:
            return _result("6e8282d40655d47ed1557f030e53d6819e464e79 refs/tags/v1.90.0\n")
        if command[:2] == ["git", "ls-remote"]:
            return _result(
                "dc765d4738b8c68b8e0d57bff5e343fbf0be41ae refs/tags/v3.8.38\n"
                "7b139fdb5e42658a49f9d99ddf0eeeba9a994fd8 refs/tags/v3.8.38^{}\n"
            )
        raise AssertionError(command)

    def archive_hasher(url: str):
        if "litellm" in url:
            return module.LITELLM["source_archive_sha256"], None
        return module.OMNIROUTE["source_archive_sha256"], None

    report = module.evaluate(runner=runner, archive_hasher=archive_hasher)
    assert report["status"] == "PASS"
    assert report["components"]["litellm"]["runtime_version"]["status"] == "PASS"
    assert report["components"]["litellm"]["release_tag"]["tag_type"] == "LIGHTWEIGHT"
    assert report["components"]["omniroute"]["release_tag"]["tag_type"] == "ANNOTATED"
    assert report["components"]["omniroute"]["source_archive"]["status"] == "PASS"


def test_version_failure_does_not_hide_tag_or_archive_results() -> None:
    module = _module()

    def runner(command, **_kwargs):
        if command[0] == "docker" and "litellm" in command[-3]:
            return _result("1.89.0\n")
        if command[0] == "docker" and "omniroute" in command[-3]:
            return _result("3.8.38\n")
        if command[:2] == ["git", "ls-remote"] and "litellm" in command[2]:
            return _result("6e8282d40655d47ed1557f030e53d6819e464e79 refs/tags/v1.90.0\n")
        if command[:2] == ["git", "ls-remote"]:
            return _result(
                "dc765d4738b8c68b8e0d57bff5e343fbf0be41ae refs/tags/v3.8.38\n"
                "7b139fdb5e42658a49f9d99ddf0eeeba9a994fd8 refs/tags/v3.8.38^{}\n"
            )
        raise AssertionError(command)

    report = module.evaluate(
        runner=runner,
        archive_hasher=lambda url: (
            (module.LITELLM if "litellm" in url else module.OMNIROUTE)["source_archive_sha256"],
            None,
        ),
    )
    assert report["status"] == "FAILED_CERTIFICATION_IMPLEMENTATION"
    assert report["failure_class"] == "LITELLM_RUNTIME_VERSION_MISMATCH"
    assert report["components"]["litellm"]["release_tag"]["resolution_status"] == "PASS"
    assert report["components"]["omniroute"]["source_archive"]["status"] == "PASS"
