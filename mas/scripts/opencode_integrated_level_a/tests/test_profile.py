"""Static safety checks for the disposable integrated OpenCode profile."""

from __future__ import annotations

import copy
import importlib.util
import shutil
import unittest
from pathlib import Path
from typing import Any


SCRIPT = Path(__file__).resolve().parents[1] / "run.py"
SPEC = importlib.util.spec_from_file_location("opencode_integrated_level_a", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def rendered_config() -> dict[str, Any]:
    import tempfile

    env = MODULE._docker_environment()
    with tempfile.TemporaryDirectory(prefix="aiat-level-a-config-test-") as temporary:
        tempdir = Path(temporary)
        values = {
            **MODULE._service_addresses("10.254.240.0/29"),
            "AIAT_LEVEL_A_SUBNET": "10.254.240.0/29",
            "AIAT_LEVEL_A_IMAGE_NAMESPACE": "aiat-level-a-123456789abc",
            "AIAT_LEVEL_A_LITELLM_IMAGE": MODULE.LITELLM_IMAGE,
            "AIAT_LEVEL_A_DB_PASSWORD": "test-db-password",
            "AIAT_LEVEL_A_MINIO_PASSWORD": "test-minio-password",
            "AIAT_LEVEL_A_MINIO_ARTIFACT_PASSWORD": "test-artifact-password",
            "AIAT_LEVEL_A_ROUTER_SECRET": "test-router-secret",
            "AIAT_LEVEL_A_TOOL_SECRET": "test-tool-secret",
            "AIAT_LEVEL_A_API_KEY": "test-api-key",
            "AIAT_LEVEL_A_OPERATOR_KEY": "test-operator-key",
            "AIAT_LEVEL_A_WORKER_KEY": "test-worker-key",
            "AIAT_LEVEL_A_CEO_KEY": "test-ceo-key",
            "AIAT_LEVEL_A_OPENCODE_PASSWORD": "test-opencode-password",
            "AIAT_LEVEL_A_LITELLM_MASTER_KEY": "sk-test-master-key",
            "AIAT_LEVEL_A_HOST_TOKEN": "test-host-token",
        }
        env_file = MODULE._write_environment(values, tempdir)
        base = MODULE._compose_base("aiat-level-a-123456789abc", env_file)
        return MODULE._render_config(base, env)


class IntegratedLevelAProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("docker") is None:
            raise unittest.SkipTest("Docker CLI is required to render the Compose profile")

    def test_compose_profile_renders_and_preserves_isolation(self) -> None:
        config = rendered_config()
        self.assertEqual(config["networks"]["control"]["internal"], True)
        self.assertEqual(config["networks"]["sandbox_gateway"]["internal"], True)
        self.assertEqual(config["services"]["opencode-runtime"]["runtime"], "runsc")
        self.assertNotIn("postgres", config["services"]["opencode-runtime"]["networks"])
        self.assertNotIn("redis", config["services"]["opencode-runtime"]["networks"])
        self.assertNotIn("minio", config["services"]["opencode-runtime"]["networks"])

    def test_gateway_must_remain_fixture_only(self) -> None:
        content = (MODULE.ROOT / "mas/scripts/opencode_integrated_level_a/litellm_config.yaml").read_text()
        self.assertIn("http://provider-fixture:8000/v1", content)
        self.assertIn("model: openai/fixture-model", content)
        self.assertNotIn("api_key: os.environ", content)

    def test_validator_rejects_non_internal_network(self) -> None:
        config = copy.deepcopy(rendered_config())
        config["networks"]["sandbox_gateway"]["internal"] = False
        with self.assertRaisesRegex(MODULE.HarnessError, "profile_network_must_block_external_egress"):
            MODULE._assert_profile_config(config)

    def test_validator_rejects_isolation_downgrade(self) -> None:
        config = copy.deepcopy(rendered_config())
        config["services"]["opencode-runtime"]["runtime"] = "runc"
        with self.assertRaisesRegex(MODULE.HarnessError, "opencode_runtime_must_use_runsc"):
            MODULE._assert_profile_config(config)

    def test_validator_rejects_external_provider_dispatch(self) -> None:
        config = copy.deepcopy(rendered_config())
        config["services"]["orchestrator-api"]["environment"]["AIAT_ALLOW_EXTERNAL_PROVIDER_DISPATCH"] = "true"
        with self.assertRaisesRegex(MODULE.HarnessError, "external_provider_dispatch_must_remain_disabled"):
            MODULE._assert_profile_config(config)

    def test_validator_rejects_database_on_sandbox_network(self) -> None:
        config = copy.deepcopy(rendered_config())
        config["services"]["postgres"]["networks"]["sandbox_gateway"] = {}
        with self.assertRaisesRegex(MODULE.HarnessError, "protected_service_on_sandbox_network:postgres"):
            MODULE._assert_profile_config(config)

    def test_network_selector_skips_docker_and_host_routes(self) -> None:
        selected = MODULE._select_subnet(["172.28.240.0/20", "172.29.240.0/20"])
        network = MODULE.ipaddress.ip_network(selected)
        self.assertEqual(network.prefixlen, 29)
        self.assertTrue(network.is_private)
        self.assertFalse(network.overlaps(MODULE.ipaddress.ip_network("172.28.240.0/20")))
        self.assertFalse(network.overlaps(MODULE.ipaddress.ip_network("172.29.240.0/20")))

    def test_worker_selection_uses_exact_governed_manifest_name(self) -> None:
        worker_id = "00000000-0000-4000-8000-000000000001"
        selected = MODULE._select_coding_worker_id(
            [
                {"id": "00000000-0000-4000-8000-000000000002", "name": "tester"},
                {"id": worker_id, "name": "coding_worker"},
            ]
        )
        self.assertEqual(selected, worker_id)

    def test_worker_selection_does_not_invent_or_ambiguously_choose(self) -> None:
        self.assertIsNone(MODULE._select_coding_worker_id([]))
        with self.assertRaisesRegex(MODULE.HarnessError, "coding_worker_manifest_ambiguous"):
            MODULE._select_coding_worker_id(
                [
                    {"id": "00000000-0000-4000-8000-000000000001", "name": "coding_worker"},
                    {"id": "00000000-0000-4000-8000-000000000002", "name": "coding_worker"},
                ]
            )

    def test_driver_blocked_status_requires_pending_worker_gate(self) -> None:
        self.assertTrue(
            MODULE._pending_worker_gate_verified(
                {
                    "exit_code": 2,
                    "result": {
                        "worker_run_id": None,
                        "stages": {
                            "WORKER_SELECTED": {
                                "status": "BLOCKED",
                                "reason": "WORKER_EVALUATION_NOT_APPROVED",
                            },
                            "WORKER_RUN_SUBMITTED": {
                                "status": "NOT_RUN",
                                "reason": "STAGE_NOT_REACHED",
                            },
                        }
                    },
                }
            )
        )
        self.assertFalse(
            MODULE._pending_worker_gate_verified(
                {
                    "exit_code": 2,
                    "result": {
                        "worker_run_id": None,
                        "stages": {
                            "WORKER_SELECTED": {"status": "BLOCKED", "reason": "WORKER_NOT_REGISTERED"},
                            "WORKER_RUN_SUBMITTED": {
                                "status": "NOT_RUN",
                                "reason": "STAGE_NOT_REACHED",
                            },
                        }
                    },
                }
            )
        )

    def test_api_project_cleanup_requires_canonical_delete_readback(self) -> None:
        self.assertTrue(
            MODULE._api_project_cleanup_verified(
                {
                    "result": {
                        "stages": {
                            "CLEANUP_VERIFIED": {
                                "status": "PASS",
                                "evidence": {"project_deleted": True},
                            }
                        }
                    }
                }
            )
        )
        self.assertFalse(
            MODULE._api_project_cleanup_verified(
                {"result": {"stages": {"CLEANUP_VERIFIED": {"status": "NOT_RUN"}}}}
            )
        )


if __name__ == "__main__":
    unittest.main()
