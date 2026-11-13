from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SCRIPTS))

import verify_image_promotion as promotion  # noqa: E402


MANIFEST_PATH = REPO / "mas" / "release" / "image-promotion-candidate.json"


class PromotionPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = promotion.load_manifest(MANIFEST_PATH)
        self.valid_dispatch = {
            "candidate_sha": self.manifest["source"]["sha"],
            "image": "redis",
            "ci_run_id": "43357795559",
            "worker_run_id": "43357795613",
            "redis_run_id": "43357795668",
            "run_ref": "refs/heads/main",
            "workflow_ref": (
                "Maaroufabousaleh/AIAT/.github/workflows/"
                "aiat-image-candidate-promotion.yml@refs/heads/main"
            ),
            "workflow_sha": "main-dispatch-sha",
        }

    def dispatch(self, **overrides: str) -> None:
        values = self.valid_dispatch | overrides
        promotion.validate_dispatch(self.manifest, **values)

    def test_accepts_exact_pinned_candidate_and_main_workflow(self) -> None:
        selected = promotion.validate_dispatch(self.manifest, **self.valid_dispatch)
        self.assertEqual(selected["package_name"], "aiat-redis-acl-init")

    def test_rejects_wrong_source_sha(self) -> None:
        with self.assertRaisesRegex(promotion.PromotionError, "candidate SHA"):
            self.dispatch(candidate_sha="0" * 40)

    def test_rejects_wrong_evidence_run(self) -> None:
        with self.assertRaisesRegex(promotion.PromotionError, "worker evidence run ID"):
            self.dispatch(worker_run_id="43357795614")

    def test_rejects_dispatch_from_candidate_branch(self) -> None:
        with self.assertRaisesRegex(promotion.PromotionError, "main branch"):
            self.dispatch(run_ref="refs/heads/agent/fix-review-p1")

    def test_rejects_untrusted_workflow_revision(self) -> None:
        with self.assertRaisesRegex(promotion.PromotionError, "trusted main-branch"):
            self.dispatch(workflow_ref="Maaroufabousaleh/AIAT/old-workflow.yml@refs/heads/main")

    def test_rejects_two_image_publish_to_enforce_redis_first(self) -> None:
        with self.assertRaisesRegex(promotion.PromotionError, "one image at a time"):
            self.dispatch(image="both")

    def test_rejects_failed_or_mismatched_run_metadata(self) -> None:
        expected = self.manifest["runs"]["worker"] | {"kind": "worker"}
        actual = {
            "id": 43357795613,
            "head_sha": self.manifest["source"]["sha"],
            "head_branch": "agent/fix-review-p1",
            "event": "push",
            "status": "completed",
            "conclusion": "failure",
            "path": expected["path"],
            "name": expected["name"],
        }
        with self.assertRaisesRegex(promotion.PromotionError, "conclusion"):
            promotion.validate_run_record(actual, expected, self.manifest["source"]["sha"])

    def test_rejects_artifact_metadata_404_as_unknown_not_absent(self) -> None:
        with self.assertRaisesRegex(promotion.PromotionError, "not verifiable"):
            promotion.validate_artifact_record(
                {}, self.manifest["images"]["redis"], self.manifest["source"]["sha"], http_status=404
            )

    def test_rejects_artifact_bound_to_different_run(self) -> None:
        expected = self.manifest["images"]["redis"]
        artifact = {
            "id": expected["artifact_id"],
            "name": expected["artifact_name"],
            "expired": False,
            "digest": expected["artifact_digest"],
            "workflow_run": {
                "id": "43357795667",
                "head_sha": self.manifest["source"]["sha"],
                "head_branch": expected["branch"],
            },
        }
        with self.assertRaisesRegex(promotion.PromotionError, "different workflow run"):
            promotion.validate_artifact_record(artifact, expected, self.manifest["source"]["sha"])

    def test_missing_attestation_references_fail_evidence_verification(self) -> None:
        expected = self.manifest["images"]["redis"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "candidate-image-identity.json").write_text(
                json.dumps(
                    {
                        "schema_version": "aiat.redis-acl-init-candidate.v1",
                        "source_commit": self.manifest["source"]["sha"],
                        "registry_reference": None,
                        "platform": "linux/amd64",
                        "docker_image_config_digest": expected["config_digest"],
                        "archive_sha256": expected["archive_sha256"],
                    }
                ),
                encoding="utf-8",
            )
            (root / "trivy-summary.json").write_text(
                json.dumps(
                    {
                        "schema_version": "aiat.trivy-evidence-summary.v1",
                        "status": "complete",
                        "reports": {
                            "redis_initializer_image": {"status": "read", "high_critical_count": 0},
                            "redis_initializer_dependencies": {"status": "read", "high_critical_count": 0},
                        },
                        "sbom": {"status": "read", "component_count": 1},
                    }
                ),
                encoding="utf-8",
            )
            (root / "image-sbom.cdx.json").write_text(
                json.dumps({"bomFormat": "CycloneDX", "components": [{"name": "fixture"}]}),
                encoding="utf-8",
            )
            (root / "source-commit.txt").write_text(self.manifest["source"]["sha"] + "\n", encoding="utf-8")
            (root / "candidate-image.tar").write_bytes(b"not reached because attestation references are missing")
            with self.assertRaisesRegex(promotion.PromotionError, "candidate evidence failed validation"):
                promotion.verify_candidate_evidence(self.manifest, "redis", root)

    def test_evidence_zip_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "malformed.zip"
            destination = root / "out"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../escaped.txt", "bad")
            with self.assertRaisesRegex(promotion.PromotionError, "unsafe path"):
                promotion.safe_extract_artifact(archive, destination)

    def test_package_404_fails_closed(self) -> None:
        def fake_get(api_base: str, token: str, path: str):
            if path.endswith("/environments/aiat-image-candidate-release"):
                return 200, {
                    "name": "aiat-image-candidate-release",
                    "protection_rules": [
                        {
                            "type": "required_reviewers",
                            "prevent_self_review": True,
                            "reviewers": [{"type": "User", "reviewer": {"login": "reviewer"}}],
                        }
                    ],
                    "deployment_branch_policy": {"protected_branches": True},
                }
            if path.endswith("/collaborators/reviewer/permission"):
                return 200, {"permission": "admin"}
            if path.endswith("/branches/main/protection"):
                return 200, {
                    "required_pull_request_reviews": {"required_approving_review_count": 1},
                    "enforce_admins": {"enabled": True},
                }
            raise promotion.PromotionError("read-only GitHub control check is unverifiable (HTTP 404)")

        with patch.object(promotion, "_api_get", side_effect=fake_get):
            with self.assertRaisesRegex(promotion.PromotionError, "HTTP 404"):
                promotion.verify_release_controls(
                    self.manifest,
                    "redis",
                    token="fake-read-only-token",
                package_metadata_token="fake-read-only-package-token",
                    api_base="https://api.github.com",
                    package_access_mode="repository_inherited",
                    package_access_verified_sha=self.manifest["source"]["sha"],
                )

    def test_missing_read_only_control_identity_blocks_publication(self) -> None:
        with self.assertRaisesRegex(promotion.PromotionError, "credentials are not configured"):
            promotion.verify_release_controls(
                self.manifest,
                "redis",
                token="",
                package_metadata_token="",
                api_base="https://api.github.com",
                package_access_mode="repository_inherited",
                package_access_verified_sha=self.manifest["source"]["sha"],
            )

    def test_missing_package_metadata_credential_fails_closed(self) -> None:
        with self.assertRaisesRegex(promotion.PromotionError, "GHCR metadata credential"):
            promotion.verify_release_controls(
                self.manifest,
                "redis",
                token="read-only-app-token",
                package_metadata_token="",
                api_base="https://api.github.com",
                package_access_mode="repository_inherited",
                package_access_verified_sha=self.manifest["source"]["sha"],
            )

    @patch("verify_image_promotion.urllib.request.urlopen")
    def test_preflight_accepts_only_authenticated_missing_candidate_manifest(self, urlopen) -> None:
        token_response = Mock()
        token_response.__enter__ = Mock(return_value=token_response)
        token_response.__exit__ = Mock(return_value=False)
        token_response.read.return_value = b'{"token":"pull-only-token"}'
        urlopen.side_effect = [
            token_response,
            urllib.error.HTTPError("https://ghcr.io/v2/manifest", 404, "Not Found", {}, None),
        ]
        promotion._require_candidate_tag_absent(
            "Maaroufabousaleh", "aiat-redis-acl-init", self.manifest["source"]["sha"], "read-only"
        )
        self.assertEqual(urlopen.call_count, 2)
        registry_request = urlopen.call_args_list[1].args[0]
        self.assertEqual(registry_request.get_method(), "HEAD")
        self.assertIn("candidate-" + self.manifest["source"]["sha"], registry_request.full_url)

    @patch("verify_image_promotion.urllib.request.urlopen")
    def test_preflight_rejects_existing_candidate_tag(self, urlopen) -> None:
        token_response = Mock()
        token_response.__enter__ = Mock(return_value=token_response)
        token_response.__exit__ = Mock(return_value=False)
        token_response.read.return_value = b'{"token":"pull-only-token"}'
        manifest_response = Mock()
        manifest_response.__enter__ = Mock(return_value=manifest_response)
        manifest_response.__exit__ = Mock(return_value=False)
        manifest_response.status = 200
        urlopen.side_effect = [token_response, manifest_response]
        with self.assertRaisesRegex(promotion.PromotionError, "already exists"):
            promotion._require_candidate_tag_absent(
                "Maaroufabousaleh", "aiat-redis-acl-init", self.manifest["source"]["sha"], "read-only"
            )


if __name__ == "__main__":
    unittest.main()
