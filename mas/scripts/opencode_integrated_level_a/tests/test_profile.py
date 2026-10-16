"""Static safety checks for the disposable integrated OpenCode profile."""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
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
        selected = MODULE._select_worker_id(
            [
                {"id": "00000000-0000-4000-8000-000000000002", "name": "tester"},
                {"id": worker_id, "name": "coding_worker"},
            ],
            "coding_worker",
        )
        self.assertEqual(selected, worker_id)

    def test_worker_selection_does_not_invent_or_ambiguously_choose(self) -> None:
        self.assertIsNone(MODULE._select_worker_id([], "coding_worker"))
        with self.assertRaisesRegex(MODULE.HarnessError, "worker_manifest_ambiguous"):
            MODULE._select_worker_id(
                [
                    {"id": "00000000-0000-4000-8000-000000000001", "name": "coding_worker"},
                    {"id": "00000000-0000-4000-8000-000000000002", "name": "coding_worker"},
                ],
                "coding_worker",
            )

    def test_worker_selection_supports_only_the_two_canonical_names(self) -> None:
        worker_id = "00000000-0000-4000-8000-000000000002"
        self.assertEqual(
            MODULE._select_worker_id(
                [{"id": worker_id, "name": "aiat_deterministic_file_worker"}],
                "aiat_deterministic_file_worker",
            ),
            worker_id,
        )
        with self.assertRaisesRegex(MODULE.HarnessError, "worker_name_unsupported"):
            MODULE._select_worker_id([], "some-other-worker")

    def test_worker_resolution_uses_authenticated_api_and_exact_requested_name(self) -> None:
        worker_id = "00000000-0000-4000-8000-000000000002"
        calls: list[list[str]] = []
        original_run = MODULE._run
        MODULE._run = lambda argv, **_kwargs: calls.append(argv) or SimpleNamespace(
            returncode=0,
            stdout=MODULE.json.dumps([{"id": worker_id, "name": "aiat_deterministic_file_worker"}]),
        )
        try:
            selected = MODULE._resolve_worker_id(
                ["docker", "compose"], {}, "aiat_deterministic_file_worker"
            )
        finally:
            MODULE._run = original_run
        self.assertEqual(selected, worker_id)
        lookup = calls[0][-1]
        self.assertIn("/capabilities/workers", lookup)
        self.assertIn("X-API-Key", lookup)
        self.assertIn("row.get('name')==name", lookup)
        self.assertIn('name="aiat_deterministic_file_worker"', lookup)

    def test_deterministic_worker_manifest_identity_is_source_bound(self) -> None:
        identity = MODULE._deterministic_worker_image_identity()
        self.assertEqual(
            identity["image_reference"],
            "aiat-deterministic-file-worker@" + identity["image_digest"],
        )
        self.assertEqual(identity["source_sha256"], "3b75bea1aa232ac5362d46b448cdc789a55f6e87e955ce4053a7d849ac858784")

    def test_deterministic_worker_local_image_requires_exact_digest_and_labels(self) -> None:
        identity = MODULE._deterministic_worker_image_identity()
        labels = {
            "org.opencontainers.image.version": identity["version"],
            "org.opencontainers.image.revision": identity["source_revision"],
            "com.aiat.worker.source-sha256": identity["source_sha256"],
        }
        calls: list[list[str]] = []
        original_run = MODULE._run
        source_bytes = MODULE.DETERMINISTIC_WORKER_SOURCE.read_bytes()

        def inspect(argv: list[str], **_kwargs: Any) -> SimpleNamespace:
            calls.append(argv)
            if argv[:3] == ["docker", "image", "inspect"] and "RepoDigests" in argv[-2]:
                stdout = MODULE.json.dumps([identity["image_reference"]])
            elif argv[:3] == ["docker", "image", "inspect"]:
                stdout = MODULE.json.dumps(labels)
            elif argv[:2] == ["docker", "create"]:
                stdout = "container-id"
            elif argv[:2] == ["docker", "cp"]:
                Path(argv[-1]).write_bytes(source_bytes)
                stdout = ""
            elif argv[:3] == ["docker", "rm", "--force"]:
                stdout = "removed"
            else:
                raise AssertionError(f"unexpected image verification command: {argv[:3]}")
            return SimpleNamespace(returncode=0, stdout=stdout)

        MODULE._run = inspect
        try:
            verified = MODULE._verify_local_deterministic_worker_image({})
        finally:
            MODULE._run = original_run
        self.assertTrue(verified["local_digest_verified"])
        self.assertTrue(verified["source_labels_verified"])
        self.assertTrue(verified["embedded_source_sha256_verified"])
        self.assertEqual(len(calls), 5)
        self.assertTrue(all(identity["image_reference"] in call for call in calls[:3]))
        self.assertIn("--network", calls[2])
        self.assertIn("none", calls[2])
        self.assertEqual(calls[3][0:2], ["docker", "cp"])
        self.assertEqual(calls[4][0:3], ["docker", "rm", "--force"])
        self.assertFalse(any("pull" in call or "build" in call for call in calls))
        self.assertFalse(any(":local" in " ".join(call) for call in calls))
        self.assertFalse(any(call[1] == "start" for call in calls))

    def test_deterministic_worker_image_embedded_source_mismatch_fails_and_cleans_up(self) -> None:
        identity = MODULE._deterministic_worker_image_identity()
        labels = {
            "org.opencontainers.image.version": identity["version"],
            "org.opencontainers.image.revision": identity["source_revision"],
            "com.aiat.worker.source-sha256": identity["source_sha256"],
        }
        calls: list[list[str]] = []
        original_run = MODULE._run

        def inspect(argv: list[str], **_kwargs: Any) -> SimpleNamespace:
            calls.append(argv)
            if argv[:3] == ["docker", "image", "inspect"] and "RepoDigests" in argv[-2]:
                output: Any = [identity["image_reference"]]
            elif argv[:3] == ["docker", "image", "inspect"]:
                output = labels
            elif argv[:2] == ["docker", "create"]:
                output = "container-id"
            elif argv[:2] == ["docker", "cp"]:
                Path(argv[-1]).write_bytes(b"different source bytes")
                output = ""
            elif argv[:3] == ["docker", "rm", "--force"]:
                output = "removed"
            else:
                raise AssertionError(f"unexpected image verification command: {argv[:3]}")
            return SimpleNamespace(returncode=0, stdout=MODULE.json.dumps(output) if isinstance(output, (dict, list)) else output)

        MODULE._run = inspect
        try:
            with self.assertRaisesRegex(
                MODULE.HarnessError,
                "deterministic_worker_image_embedded_source_mismatch",
            ):
                MODULE._verify_local_deterministic_worker_image({})
        finally:
            MODULE._run = original_run
        self.assertEqual(calls[-1][:3], ["docker", "rm", "--force"])

    def test_deterministic_worker_image_missing_or_mismatched_fails_closed(self) -> None:
        original_run = MODULE._run
        MODULE._run = lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="")
        try:
            with self.assertRaisesRegex(
                MODULE.HarnessError, "deterministic_worker_image_digest_not_present_locally"
            ):
                MODULE._verify_local_deterministic_worker_image({})
        finally:
            MODULE._run = original_run

        identity = MODULE._deterministic_worker_image_identity()
        MODULE._run = lambda argv, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=MODULE.json.dumps(
                ["aiat-deterministic-file-worker@sha256:" + "0" * 64]
                if "RepoDigests" in argv[-2]
                else {}
            ),
        )
        try:
            with self.assertRaisesRegex(
                MODULE.HarnessError, "deterministic_worker_image_digest_mismatch"
            ):
                MODULE._verify_local_deterministic_worker_image({})
        finally:
            MODULE._run = original_run

        calls = 0

        def wrong_labels(argv: list[str], **_kwargs: Any) -> SimpleNamespace:
            nonlocal calls
            calls += 1
            if "RepoDigests" in argv[-2]:
                output: Any = [identity["image_reference"]]
            else:
                output = {
                    "org.opencontainers.image.version": identity["version"],
                    "org.opencontainers.image.revision": "0" * 40,
                    "com.aiat.worker.source-sha256": identity["source_sha256"],
                }
            return SimpleNamespace(returncode=0, stdout=MODULE.json.dumps(output))

        MODULE._run = wrong_labels
        try:
            with self.assertRaisesRegex(
                MODULE.HarnessError, "deterministic_worker_image_source_labels_mismatch"
            ):
                MODULE._verify_local_deterministic_worker_image({})
        finally:
            MODULE._run = original_run

    def test_worker_name_cli_rejects_unlisted_candidates_before_runtime(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            MODULE.main(["--worker-name", "unlisted_worker", "--check-only"])

    def test_worker_name_cli_propagates_selected_candidate(self) -> None:
        calls: list[dict[str, Any]] = []
        original = MODULE.run_profile
        MODULE.run_profile = lambda **kwargs: calls.append(kwargs) or {"status": "CONFIG_PASS"}
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                status = MODULE.main(
                    ["--worker-name", "aiat_deterministic_file_worker", "--check-only"]
                )
        finally:
            MODULE.run_profile = original
        self.assertEqual(status, 0)
        self.assertEqual(calls[0]["worker_name"], "aiat_deterministic_file_worker")

    def test_driver_receives_selected_worker_name(self) -> None:
        calls: list[list[str]] = []
        original_run = MODULE._run

        def run(argv: list[str], **_kwargs: Any) -> SimpleNamespace:
            calls.append(argv)
            output = "{}" if "exec" in argv else ""
            return SimpleNamespace(returncode=0, stdout=output)

        MODULE._run = run
        try:
            MODULE._run_driver(
                ["docker", "compose"],
                {},
                MODULE.DEFAULT_DRIVER,
                {},
                "00000000-0000-4000-8000-000000000001",
                "aiat_deterministic_file_worker",
            )
        finally:
            MODULE._run = original_run
        self.assertTrue(
            any("AIAT_LEVEL_A_WORKER_NAME=aiat_deterministic_file_worker" in call for call in calls)
        )

    def test_artifact_pass_completion_policy_block_is_reportable_as_partial(self) -> None:
        worker_id = "00000000-0000-4000-8000-000000000001"
        stages = {
            name: {"status": "PASS"}
            for name in (
                "PROJECT_CREATED",
                "WORKER_SELECTED",
                "WORKER_RUN_SUBMITTED",
                "HOST_RESERVED",
                "HOST_CLAIMED",
                "HOST_CONSUMER_EXECUTED",
                "RUNTIME_STARTED",
                "TOOL_SERVICE_CALLED",
                "TOOL_EFFECT_PERSISTED",
                "FILE_GENERATED",
                "ARTIFACT_REGISTERED",
                "WORKER_RUN_SETTLED",
                "EVIDENCE_AVAILABLE",
            )
        }
        stages.update(
            {
                "PROJECT_CREATED": {
                    "status": "PASS",
                    "evidence": {"project_id": "project-1"},
                },
                "WORKER_SELECTED": {
                    "status": "PASS",
                    "evidence": {
                        "name": "aiat_deterministic_file_worker",
                        "worker_id": worker_id,
                    },
                },
                "WORKER_RUN_SUBMITTED": {
                    "status": "PASS",
                    "evidence": {"run_id": "run-1"},
                },
                "HOST_RESERVED": {
                    "status": "PASS",
                    "evidence": {"host_binding_id": "binding-1", "host_id": "host-1"},
                },
                "HOST_CONSUMER_EXECUTED": {
                    "status": "PASS",
                    "evidence": {
                        "claim_transition_id": "claim-1",
                        "dispatch_transition_id": "dispatch-1",
                        "running_transition_id": "running-1",
                        "running_transition_actor": "worker-run-controller",
                        "claim_preceded_running": True,
                    },
                },
                "RUNTIME_STARTED": {
                    "status": "PASS",
                    "evidence": {"accepted_event_observed": True},
                },
                "TOOL_SERVICE_CALLED": {
                    "status": "PASS",
                    "evidence": {"effect_count": 1, "tool_names": ["file_write"]},
                },
                "TOOL_EFFECT_PERSISTED": {
                    "status": "PASS",
                    "evidence": {"completed_effect_count": 1},
                },
                "FILE_GENERATED": {
                    "status": "PASS",
                    "evidence": {"files": ["expenses.py"]},
                },
                "ARTIFACT_REGISTERED": {
                    "status": "PASS",
                    "evidence": {
                        "artifacts": [
                            {
                                "artifact_id": "artifact-1",
                                "sha256": "a" * 64,
                                "size_bytes": 42,
                            }
                        ]
                    },
                },
                "WORKER_RUN_SETTLED": {
                    "status": "PASS",
                    "evidence": {"run_id": "run-1", "state": "SUCCEEDED"},
                },
                "EVIDENCE_AVAILABLE": {
                    "status": "PASS",
                    "evidence": {
                        "required_checks_pass": True,
                        "artifact_ref_count": 1,
                        "artifact_ids": ["artifact-1"],
                    },
                },
                "MODEL_REQUEST_RESOLVED": {"status": "N/A", "reason": "NON_MODEL_WORKER"},
                "LOCAL_MODEL_RESPONSE_RECEIVED": {"status": "N/A", "reason": "NON_MODEL_WORKER"},
                "PROJECT_COMPLETED": {
                    "status": "BLOCKED",
                    "reason": "PROJECT_WORKFLOW_COMPLETION_REQUIRES_ITS_GOVERNED_MILESTONES",
                    "evidence": {
                        "state": "INIT",
                        "evidence_chain_passed": True,
                        "completion_policy_ready": True,
                        "completion_policy_reason": None,
                    },
                },
                "CLEANUP_VERIFIED": {
                    "status": "PASS",
                    "evidence": {"project_deleted": True},
                },
            }
        )
        driver_result = {
            "exit_code": 2,
            "result": {"project_id": "project-1", "worker_run_id": "run-1", "stages": stages},
        }
        self.assertTrue(
            MODULE._worker_artifact_completion_blocked_verified(
                driver_result, worker_id=worker_id, provider_fixture_calls=0
            )
        )
        self.assertEqual(
            MODULE._classify_driver_result(
                driver_result,
                worker_name="aiat_deterministic_file_worker",
                worker_id=worker_id,
                provider_fixture_calls=0,
            ),
            (
                "DRIVER_PARTIAL",
                "WORKER_ARTIFACT_PASS_PROJECT_COMPLETION_BLOCKED",
                True,
            ),
        )

        with self.subTest("provider fixture was called"):
            self.assertFalse(
                MODULE._worker_artifact_completion_blocked_verified(
                    driver_result, worker_id=worker_id, provider_fixture_calls=1
                )
            )
            self.assertEqual(
                MODULE._classify_driver_result(
                    driver_result,
                    worker_name="aiat_deterministic_file_worker",
                    worker_id=worker_id,
                    provider_fixture_calls=1,
                ),
                ("DRIVER_BLOCKED", None, False),
            )
        with self.subTest("artifact is not in evidence"):
            unlinked = copy.deepcopy(driver_result)
            unlinked["result"]["stages"]["EVIDENCE_AVAILABLE"]["evidence"]["artifact_ids"] = []
            self.assertFalse(
                MODULE._worker_artifact_completion_blocked_verified(
                    unlinked, worker_id=worker_id, provider_fixture_calls=0
                )
            )
        with self.subTest("evidence linked while completion policy remains blocked"):
            policy_blocked = copy.deepcopy(driver_result)
            policy_blocked["result"]["stages"]["EVIDENCE_AVAILABLE"]["evidence"][
                "required_checks_pass"
            ] = False
            completion_evidence = policy_blocked["result"]["stages"]["PROJECT_COMPLETED"][
                "evidence"
            ]
            completion_evidence["completion_policy_ready"] = False
            completion_evidence["completion_policy_reason"] = "PROJECT_EVIDENCE_POLICY_INCOMPLETE"
            self.assertTrue(
                MODULE._worker_artifact_completion_blocked_verified(
                    policy_blocked, worker_id=worker_id, provider_fixture_calls=0
                )
            )
        with self.subTest("policy blocked without policy reason"):
            policy_unknown = copy.deepcopy(driver_result)
            policy_unknown["result"]["stages"]["PROJECT_COMPLETED"]["evidence"][
                "completion_policy_ready"
            ] = False
            self.assertFalse(
                MODULE._worker_artifact_completion_blocked_verified(
                    policy_unknown, worker_id=worker_id, provider_fixture_calls=0
                )
            )
        with self.subTest("cleanup absent"):
            unclean = copy.deepcopy(driver_result)
            unclean["result"]["stages"]["CLEANUP_VERIFIED"]["status"] = "FAIL"
            self.assertFalse(
                MODULE._worker_artifact_completion_blocked_verified(
                    unclean, worker_id=worker_id, provider_fixture_calls=0
                )
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

    def test_source_scan_and_image_binding_failures_remain_blocked(self) -> None:
        for blocker_code in (
            "security_scan_not_passed",
            "image_source_binding_not_verified",
        ):
            with self.subTest(blocker_code=blocker_code):
                driver_result = {
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
                                        "blocker_codes": [blocker_code],
                                    }
                                },
                            },
                            "WORKER_RUN_SUBMITTED": {
                                "status": "NOT_RUN",
                                "reason": "STAGE_NOT_REACHED",
                            },
                        }
                    },
                }
                self.assertTrue(MODULE._pending_worker_gate_verified(driver_result))
                self.assertEqual(
                    MODULE._classify_driver_result(
                        driver_result,
                        worker_name="aiat_deterministic_file_worker",
                        worker_id=None,
                        provider_fixture_calls=0,
                    ),
                    ("DRIVER_BLOCKED", None, False),
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
