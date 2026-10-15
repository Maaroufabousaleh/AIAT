"""Static safety checks for the disposable integrated OpenCode profile."""

from __future__ import annotations

import copy
import importlib.util
import shutil
import socket as socket_module
import unittest
from pathlib import Path
from types import SimpleNamespace
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
            **MODULE._service_addresses("10.254.240.0/28"),
            "AIAT_LEVEL_A_SUBNET": "10.254.240.0/28",
            "AIAT_LEVEL_A_IMAGE_NAMESPACE": "aiat-level-a-123456789abc",
            "AIAT_LEVEL_A_DOCKER_GID": "10001",
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
            "AIAT_LEVEL_A_DOCKER_SOCKET_PATH": "/tmp/aiat-level-a-docker.sock",
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

    def test_base_image_check_accepts_a_profile_built_image_shared_by_host(self) -> None:
        commands: list[list[str]] = []
        original_run = MODULE._run
        MODULE._run = lambda argv, **_kwargs: commands.append(argv) or SimpleNamespace(returncode=1)
        try:
            MODULE._check_local_base_images(
                {
                    "services": {
                        "orchestrator-api": {
                            "image": "aiat-level-a-123456789abc/orchestrator-api:local",
                            "build": {"context": "/tmp/build-context"},
                        },
                        "worker-host": {
                            "image": "aiat-level-a-123456789abc/orchestrator-api:local",
                            "pull_policy": "never",
                        },
                    }
                },
                {},
            )
        finally:
            MODULE._run = original_run
        self.assertEqual(commands, [])

    def test_compose_start_diagnostic_is_bounded_and_does_not_return_raw_output(self) -> None:
        diagnostic = MODULE._safe_compose_start_reason(
            "failed to create container: permission denied; password=must-not-be-retained"
        )
        self.assertEqual(diagnostic, "host_permission_denied")
        self.assertNotIn("must-not-be-retained", diagnostic)
        self.assertEqual(MODULE._safe_compose_start_reason("unrecognized failure"), "compose_start_rejected_unclassified")

    def test_local_gateway_clients_use_the_profile_master_key(self) -> None:
        config = rendered_config()
        services = config["services"]
        master_key = services["litellm"]["environment"]["LITELLM_MASTER_KEY"]
        self.assertEqual(services["tool-service"]["environment"]["LLM_API_KEY"], master_key)
        self.assertEqual(services["orchestrator-api"]["environment"]["LLM_API_KEY"], master_key)
        self.assertEqual(services["opencode-runtime"]["environment"]["AIAT_GATEWAY_API_KEY"], master_key)

        config["services"]["tool-service"]["environment"]["LLM_API_KEY"] = "mismatched-test-key"
        with self.assertRaisesRegex(MODULE.HarnessError, "local_gateway_client_key_mismatch:tool-service"):
            MODULE._assert_profile_config(config)

    def test_docker_socket_is_exclusive_to_dedicated_worker_host(self) -> None:
        config = rendered_config()
        services = config["services"]
        worker_host = services["worker-host"]
        self.assertEqual(worker_host["user"], "10001:10001")
        self.assertEqual(set(worker_host["networks"]), {"control", "sandbox_gateway"})
        self.assertEqual(worker_host["environment"]["AIAT_LOCAL_HOST_QUEUE_ENABLED"], "true")
        self.assertEqual(worker_host["environment"]["AIAT_ALLOW_EXTERNAL_PROVIDER_DISPATCH"], "false")
        for service_name, service in services.items():
            socket_mounts = [
                volume
                for volume in service.get("volumes") or []
                if isinstance(volume, dict)
                and (
                    volume.get("source") == "/var/run/docker.sock"
                    or volume.get("target") == "/var/run/docker.sock"
                )
            ]
            if service_name == "worker-host":
                self.assertEqual(len(socket_mounts), 1)
                self.assertEqual(socket_mounts[0]["type"], "bind")
                self.assertTrue(Path(socket_mounts[0]["source"]).is_absolute())
                self.assertEqual(socket_mounts[0]["target"], "/var/run/docker.sock")
            else:
                self.assertEqual(socket_mounts, [], service_name)
                self.assertNotIn("/var/run/docker.sock", str(service).lower())
        self.assertEqual(services["orchestrator-api"]["environment"]["AIAT_LOCAL_HOST_QUEUE_ENABLED"], "false")

    def test_docker_socket_identity_accepts_only_the_selected_local_unix_socket(self) -> None:
        import tempfile

        def inspected_context(host: str) -> SimpleNamespace:
            return SimpleNamespace(
                returncode=0,
                stdout=MODULE.json.dumps(
                    [{"Name": "test-local", "Endpoints": {"docker": {"Host": host}}}]
                ),
            )

        original_run = MODULE._run
        try:
            with tempfile.TemporaryDirectory(prefix="aiat-docker-socket-test-") as temporary:
                socket_path = Path(temporary) / "docker.proxy.sock"
                with socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM) as daemon_socket:
                    daemon_socket.bind(str(socket_path))
                    endpoint = f"unix://{socket_path}"
                    MODULE._run = lambda *_args, **_kwargs: inspected_context(endpoint)
                    gid, resolved_path = MODULE._docker_socket_identity({})
                    self.assertTrue(gid.isdigit())
                    self.assertEqual(resolved_path, str(socket_path.resolve()))

            MODULE._run = lambda *_args, **_kwargs: inspected_context("tcp://127.0.0.1:2375")
            with self.assertRaisesRegex(MODULE.HarnessError, "worker_host_requires_local_docker_socket"):
                MODULE._docker_socket_identity({})
        finally:
            MODULE._run = original_run

    def test_validator_rejects_socket_on_api_and_missing_host_runtime_network(self) -> None:
        config = copy.deepcopy(rendered_config())
        config["services"]["orchestrator-api"]["volumes"].append(
            {
                "type": "bind",
                "source": "/var/run/docker.sock",
                "target": "/var/run/docker.sock",
            }
        )
        with self.assertRaisesRegex(MODULE.HarnessError, "docker_socket_mount_forbidden:orchestrator-api"):
            MODULE._assert_profile_config(config)

        config = copy.deepcopy(rendered_config())
        config["services"]["worker-host"]["networks"] = {"control": {}}
        with self.assertRaisesRegex(MODULE.HarnessError, "worker_host_docker_authority_boundary_invalid"):
            MODULE._assert_profile_config(config)

    def test_oci_worker_command_does_not_inherit_host_docker_socket(self) -> None:
        from mas_core.worker_registry.runtime_adapters import OCIAdapter

        adapter = OCIAdapter(
            "example.invalid/worker@sha256:" + "a" * 64,
            worker_id="00000000-0000-4000-8000-000000000001",
            sandbox_profile="sandboxed",
            sandbox_runtime="runsc",
        )
        self.assertEqual(adapter.command[0], "docker")
        self.assertIn("--network", adapter.command)
        self.assertEqual(adapter.command[adapter.command.index("--network") + 1], "none")
        self.assertNotIn("/var/run/docker.sock", adapter.command)
        self.assertNotIn("DOCKER_HOST", adapter.environment)
        self.assertNotIn("DOCKER_CONTEXT", adapter.environment)

    def test_local_host_health_endpoint_is_loopback_status_only(self) -> None:
        import asyncio
        import importlib.util
        from types import SimpleNamespace

        runner_path = MODULE.MAS_ROOT / "apps/orchestrator-api/orchestrator_api/local_host_runner.py"
        spec = importlib.util.spec_from_file_location("level_a_local_host_runner", runner_path)
        assert spec is not None and spec.loader is not None
        host_runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(host_runner)

        async def request(active: bool) -> bytes:
            runtime = SimpleNamespace(
                active=active,
                public_status=lambda: {"status": "READY" if active else "DEGRADED", "lease_valid": active},
            )
            server = await asyncio.start_server(
                lambda reader, writer: host_runner._health_response(reader, writer, runtime=runtime),
                host="127.0.0.1",
                port=0,
            )
            try:
                port = server.sockets[0].getsockname()[1]
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
                await writer.drain()
                response = await reader.read()
                writer.close()
                await writer.wait_closed()
                return response
            finally:
                server.close()
                await server.wait_closed()

        ready = asyncio.run(request(True))
        degraded = asyncio.run(request(False))
        self.assertIn(b"HTTP/1.1 200 OK", ready)
        self.assertIn(b'"lease_valid":true', ready)
        self.assertIn(b"HTTP/1.1 503 Service Unavailable", degraded)
        self.assertNotIn(b"host_id", ready)

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
        self.assertEqual(network.prefixlen, 28)
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

    def test_driver_accepts_canonical_readiness_worker_block_without_dispatch(self) -> None:
        self.assertTrue(
            MODULE._pending_worker_gate_verified(
                {
                    "exit_code": 2,
                    "result": {
                        "worker_run_id": None,
                        "stages": {
                            "WORKER_SELECTED": {
                                "status": "BLOCKED",
                                "reason": "CANONICAL_WORKER_READINESS_BLOCKED",
                                "evidence": {
                                    "readiness": {
                                        "status": "blocked",
                                        "blocker_codes": [
                                            "worker_not_active",
                                            "active_shell_version_id_missing",
                                            "worker_assignment_missing",
                                        ],
                                    }
                                },
                            },
                            "WORKER_RUN_SUBMITTED": {
                                "status": "NOT_RUN",
                                "reason": "STAGE_NOT_REACHED",
                            },
                        },
                    },
                }
            )
        )

    def test_driver_does_not_treat_project_only_readiness_block_as_worker_gate(self) -> None:
        self.assertFalse(
            MODULE._pending_worker_gate_verified(
                {
                    "exit_code": 2,
                    "result": {
                        "worker_run_id": None,
                        "stages": {
                            "WORKER_SELECTED": {
                                "status": "BLOCKED",
                                "reason": "CANONICAL_WORKER_READINESS_BLOCKED",
                                "evidence": {
                                    "readiness": {
                                        "status": "blocked",
                                        "blocker_codes": ["worker_assignment_missing"],
                                    }
                                },
                            },
                            "WORKER_RUN_SUBMITTED": {
                                "status": "NOT_RUN",
                                "reason": "STAGE_NOT_REACHED",
                            },
                        },
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
