from pathlib import Path
import os
import re
import shlex
import subprocess


ROOT = Path(__file__).resolve().parents[4]
GATEWAY = ROOT / "mas" / "infra" / "smtp-gateway"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def test_gateway_example_is_distinct_and_fail_closed() -> None:
    env = read_env(GATEWAY / ".env.smtp-gateway.example")
    assert env["DEPLOYMENT_TOPOLOGY"] == "smtp_gateway_vps_home_stalwart_resend"
    assert env["AGENT_MAIL_DOMAIN"] == "agents.aiat.ca"
    assert env["MAIL_HOSTNAME"] == "mail.aiat.ca"
    assert env["IDENTITY_HOSTNAME"] == "identity.aiat.ca"
    assert env["DIRECT_MX_OUTBOUND_ENABLED"] == "false"
    assert env["DEFAULT_OUTBOUND_ENABLED"] == "false"
    assert env["OUTBOUND_RELAY_CERTIFIED"] == "false"
    assert env["OUTBOUND_RELAY_HOST"] == "smtp.resend.com"
    assert env["OUTBOUND_RELAY_PORT"] == "465"
    assert env["GATEWAY_QUEUE_PATH"]
    assert env["GATEWAY_QUEUE_LIMIT_MODE"] == "filesystem_quota"
    assert env["GATEWAY_QUEUE_QUOTA_EVIDENCE"].startswith("/secure/evidence/")
    assert int(env["GATEWAY_QUEUE_MAX_BYTES"]) == 10 * 1024 * 1024 * 1024


def test_constrained_host_profile_matches_live_shape_and_ports() -> None:
    env = read_env(GATEWAY / "profiles" / "oci-e2.1-micro-host.env.example")
    assert env["GATEWAY_RUNTIME"] == "host_postfix_wireguard"
    assert env["GATEWAY_RUNTIME_PROFILE"] == "oci_e2_1_micro_host"
    assert env["GATEWAY_CONTAINER_RUNTIME"] == "false"
    assert env["GATEWAY_OCPU"] == "1"
    assert env["GATEWAY_RAM_MB"] == "1024"
    assert env["GATEWAY_SWAP_MB"] == "1024"
    assert env["HOME_STALWART_SMTP_PORT"] == "2525"
    assert env["HOST_POSTFIX_TRANSPORT_TARGET"] == "10.77.0.2:2525"
    assert env["PUBLIC_SMTP25_ACTIVATED"] == "false"
    assert env["IDENTITY_DNS_MODE"] == "blocked"
    assert env["OUTBOUND_RELAY_CERTIFIED"] == "false"


def test_constrained_compose_disables_nonessential_containers() -> None:
    override = (GATEWAY / "docker-compose.oci-e2.1-micro.yml").read_text(encoding="utf-8")
    assert "profiles: [oci-e2-1-micro]" in override
    assert "mem_limit: 384m" in override
    assert "cpus: \"0.50\"" in override
    assert "oci-e2-1-micro-disabled" in override
    assert "ingress:" in override
    assert "log-sanitizer:" in override


def test_gateway_images_are_version_and_digest_pinned() -> None:
    compose = (GATEWAY / "docker-compose.yml").read_text(encoding="utf-8")
    images = re.findall(r"^\s+image:\s+([^\s]+)$", compose, re.MULTILINE)
    assert len(images) == 3
    assert all("@sha256:" in image for image in images)
    assert all(re.match(r"^[^:]+:[^@]+@sha256:[0-9a-f]{64}$", image) for image in images)


def test_gateway_has_no_compose_public_port_or_docker_socket() -> None:
    compose = (GATEWAY / "docker-compose.yml").read_text(encoding="utf-8")
    assert "network_mode: host" in compose
    assert "ports:" not in compose
    assert "docker.sock" not in compose


def test_postfix_is_receive_only_for_the_owned_domain() -> None:
    init = (GATEWAY / "postfix" / "gateway-init.sh").read_text(encoding="utf-8")
    assert "relay_domains = $AGENT_MAIL_DOMAIN" in init
    assert "reject_unauth_destination" in init
    assert "reject_unknown_recipient_domain" in init
    assert "relayhost =" in init
    assert "direct Internet MX delivery is disabled" in init
    assert "enable_original_recipient = yes" in init
    assert "postconf -M 'submission/inet='" in init
    assert "postconf -M 'smtps/inet='" in init


def test_ingress_and_home_overlay_are_wireguard_scoped() -> None:
    caddy = (GATEWAY / "ingress" / "Caddyfile").read_text(encoding="utf-8")
    overlay = (GATEWAY / "home" / "docker-compose.gateway-home.yml").read_text(encoding="utf-8")
    assert "respond @admin 404" in caddy
    assert "HOME_WIREGUARD_IP" in caddy
    assert "!override" in overlay
    assert '"${HOME_WIREGUARD_IP:?HOME_WIREGUARD_IP is required}:25:25"' in overlay
    assert '"${HOME_WIREGUARD_IP:?HOME_WIREGUARD_IP is required}:8010:8010"' in overlay
    assert "gateway-home-disabled" in overlay


def test_host_adoption_has_separate_gate_and_evidence_commands() -> None:
    adoption = (GATEWAY / "scripts" / "adopt-host-postfix.sh").read_text(encoding="utf-8")
    evidence = (GATEWAY / "scripts" / "collect-host-evidence.sh").read_text(encoding="utf-8")
    gates = (GATEWAY / "scripts" / "validate-host-gates.sh").read_text(encoding="utf-8")
    assert "validate-host-postfix.sh" in adoption
    assert "postmap" in adoption
    assert "POSTFIX_QUEUE_MUTATION=NOT_PERFORMED" in evidence
    assert "WIREGUARD_KEY_MUTATION=NOT_PERFORMED" in evidence
    for gate in ("pre-activation", "internal-relay", "external-inbound", "dns-mx", "identity-https", "resend"):
        assert gate in gates
    assert "IDENTITY_DNS_MODE gateway_reverse_proxy" in gates


def test_resend_certification_script_is_one_message_and_secret_safe() -> None:
    script = (GATEWAY / "scripts" / "certify-resend.sh").read_text(encoding="utf-8")
    preflight = (GATEWAY / "scripts" / "preflight-resend-certification.sh").read_text(encoding="utf-8")
    completion = (GATEWAY / "scripts" / "complete-resend-certification.sh").read_text(encoding="utf-8")
    assert "--approve-one-message" in script
    assert "EmailSubmission/set" in script
    assert "verify-stalwart-relay.sh" in preflight
    assert "openssl s_client -connect smtp.resend.com:465" in preflight
    assert "test \"$container_fingerprint\" = \"$local_fingerprint\"" in preflight
    assert "RESEND_PROVIDER_MESSAGE_ID=PENDING_EXTERNAL_PROVIDER_CORRELATION" in script
    assert "RESEND_PROVIDER_MESSAGE_ID=$stalwart_submission_id" not in script
    assert "--reply-token-stdin" in completion
    assert "RESEND_API_KEY" in preflight
    assert 'echo "$resend_api_key"' not in preflight
    assert "RESEND_API_KEY=NOT_RECORDED" not in script


def test_resend_certification_is_local_loopback_only_and_never_uses_wireguard_http() -> None:
    scripts = "\n".join(
        (GATEWAY / "scripts" / name).read_text(encoding="utf-8")
        for name in (
            "preflight-resend-certification.sh",
            "configure-stalwart-resend-route.sh",
            "certify-resend.sh",
        )
    )
    assert "http://127.0.0.1:18080" in scripts
    assert "10.77.0.2:8080" not in scripts
    assert "10.77.0.1:8080" not in scripts
    assert 'test "$jmap_url" = http://127.0.0.1:18080' in scripts


def _write_resend_secret_file(path: Path) -> Path:
    path.write_text(
        "RESEND_API_KEY=re_test_key_long_enough_to_be_rejected\n"
        "STALWART_API_KEY=admin-test-token\n"
        "STALWART_JMAP_SERVICE_TOKEN=jmap-test-token\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _run_resend_preflight(profile: Path, secret: Path, stubs: Path, *options: str) -> subprocess.CompletedProcess[str]:
    script = shlex.quote(_wsl_path(GATEWAY / "scripts" / "preflight-resend-certification.sh"))
    profile_path = shlex.quote(_wsl_path(profile))
    secret_path = shlex.quote(_wsl_path(secret))
    stub_path = shlex.quote(_wsl_path(stubs))
    quoted_options = " ".join(shlex.quote(option) for option in options)
    command = (
        f"PATH={stub_path}:/usr/bin:/bin; export PATH; exec {script} {profile_path} "
        f"--secret-file {secret_path} --stalwart-container stalwart-test "
        f"--account-id account-test --sender gateway-test@agents.aiat.ca {quoted_options}"
    )
    return subprocess.run(
        ["bash", "-c", command],
        cwd=GATEWAY,
        text=True,
        capture_output=True,
        check=False,
    )


def test_resend_preflight_refuses_wireguard_http_url(tmp_path: Path) -> None:
    secret = _write_resend_secret_file(tmp_path / "resend.env")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    result = _run_resend_preflight(
        GATEWAY / ".env.smtp-gateway.example", secret, stubs,
        "--jmap-url", "http://10.77.0.2:8080",
    )
    assert result.returncode != 0
    assert "JMAP must remain local" in result.stderr


def test_resend_preflight_rejects_a_sufficient_length_key_when_container_source_differs(tmp_path: Path) -> None:
    secret = _write_resend_secret_file(tmp_path / "resend.env")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_stub(stubs, "docker", """
case "${1:-}" in
  inspect) printf '%s\\n' true ;;
  exec) printf '%s\\n' '0000000000000000000000000000000000000000000000000000000000000000' ;;
  *) exit 1 ;;
esac
""")
    _write_stub(stubs, "ss", "printf '%s\\n' 'LISTEN 0 100 127.0.0.1:18080 0.0.0.0:*'")
    result = _run_resend_preflight(GATEWAY / ".env.smtp-gateway.example", secret, stubs)
    assert result.returncode != 0
    assert "does not match the running Stalwart secret source" in result.stderr


def test_no_private_keys_are_in_templates() -> None:
    for path in (GATEWAY / "wireguard").glob("*conf.example"):
        text = path.read_text(encoding="utf-8")
        assert "PrivateKey = <" in text
        assert "BEGIN" not in text
        assert "wg genkey" not in text


def _write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_bytes(f"#!/bin/sh\nset -eu\n{body}\n".encode("utf-8"))
    path.chmod(0o755)


def _wsl_path(path: Path) -> str:
    windows_path = path.resolve().as_posix()
    if ":" not in windows_path:
        return windows_path
    drive, remainder = windows_path.split(":", 1)
    return f"/mnt/{drive.lower()}{remainder}"


def _host_gate_harness(tmp_path: Path, *, public_smtp25: str = "false",
                       identity_mode: str = "blocked",
                       identity_certified: str = "false",
                       outbound_certified: str = "false") -> tuple[Path, dict[str, str], Path]:
    values = read_env(GATEWAY / "profiles" / "oci-e2.1-micro-host.env.example")
    values.update({
        "SMTP_GATEWAY_PUBLIC_IP": "192.0.2.10",
        "PUBLIC_MAIL_IP": "192.0.2.10",
        "PUBLIC_SMTP25_ACTIVATED": public_smtp25,
        "IDENTITY_DNS_MODE": identity_mode,
        "IDENTITY_HTTPS_INGRESS_CERTIFIED": identity_certified,
        "OUTBOUND_RELAY_CERTIFIED": outbound_certified,
    })
    values["HOST_POSTFIX_MAIN_CF"] = _wsl_path(tmp_path / "main.cf")
    values["HOST_POSTFIX_TRANSPORT_FILE"] = _wsl_path(tmp_path / "transport")
    values["HOST_POSTFIX_QUEUE_PATH"] = _wsl_path(tmp_path / "queue")
    evidence = tmp_path / "evidence"
    evidence.mkdir(parents=True)
    evidence_paths = {
        "GATE_INTERNAL_RELAY_EVIDENCE": evidence / "internal.txt",
        "GATE_EXTERNAL_INBOUND_EVIDENCE": evidence / "external.txt",
        "GATE_DNS_MX_EVIDENCE": evidence / "dns.txt",
        "GATE_IDENTITY_HTTPS_EVIDENCE": evidence / "identity.txt",
        "GATE_RESEND_EVIDENCE": evidence / "resend.txt",
    }
    values.update({key: _wsl_path(path) for key, path in evidence_paths.items()})
    (tmp_path / "main.cf").write_text("# test fixture\n", encoding="utf-8")
    (tmp_path / "transport").write_text(
        "agents.aiat.ca smtp:[10.77.0.2]:2525\n", encoding="utf-8"
    )
    (tmp_path / "queue").mkdir()
    for key in (
        "GATE_INTERNAL_RELAY_EVIDENCE",
        "GATE_EXTERNAL_INBOUND_EVIDENCE",
        "GATE_DNS_MX_EVIDENCE",
        "GATE_IDENTITY_HTTPS_EVIDENCE",
        "GATE_RESEND_EVIDENCE",
    ):
        evidence_paths[key].write_bytes(
            {
                "GATE_INTERNAL_RELAY_EVIDENCE": "GATEWAY_INTERNAL_RELAY_CERTIFIED=PASS\n",
                "GATE_EXTERNAL_INBOUND_EVIDENCE": (
                    "EXTERNAL_INBOUND_SMTP_CERTIFIED=PASS\n"
                    "EXTERNAL_SOURCE_IP=52.103.2.17\n"
                    "EXTERNAL_PROBE_ORIGIN=Outlook SMTP server\n"
                    "DESTINATION_HOSTNAME=mail.aiat.ca\n"
                    "DESTINATION_TCP_PORT=25\n"
                    "SMTP_ACCEPTANCE=250 2.0.0 Message queued\n"
                    "PRODUCTION_RECIPIENT=gateway-test@agents.aiat.ca\n"
                    "POSTFIX_QUEUE_ID=4A1B2C3D4E\n"
                    "DOWNSTREAM_RELAY_TARGET=10.77.0.2:2525\n"
                    "FINAL_STATUS=sent\n"
                ),
                "GATE_DNS_MX_EVIDENCE": "DNS_MX_CERTIFIED=PASS\n",
                "GATE_IDENTITY_HTTPS_EVIDENCE": "HTTPS_IDENTITY_INGRESS_CERTIFIED=PASS\n",
                "GATE_RESEND_EVIDENCE": (
                    "RESEND_OUTBOUND_RELAY_CERTIFIED=PASS\n"
                    "RELAY_HOST=smtp.resend.com\n"
                    "RELAY_PORT=465\n"
                    "TLS_MODE=implicit\n"
                    "TLS_VERIFICATION=PASS\n"
                    "SMTP_AUTHENTICATION=PASS\n"
                    "AUTH_USERNAME=resend\n"
                    "PRODUCTION_SENDER=gateway-test@agents.aiat.ca\n"
                    "EXTERNAL_RECIPIENT=operator@example.net\n"
                    "STALWART_ROUTE=resend-relay\n"
                    "DIRECT_MX_OUTBOUND_ENABLED=false\n"
                    "STALWART_SUBMISSION_ID=submission_123456789\n"
                    "RESEND_PROVIDER_MESSAGE_ID=re_123456789\n"
                    "ORIGINAL_MESSAGE_ID=<aiat-cert-123@example.aiat.ca>\n"
                    "EXTERNAL_RECEIPT_ID=mailbox-receipt-123456\n"
                    "DELIVERY_STATUS=delivered\n"
                    "REPLY_RECEIVED=PASS\n"
                    "REPLY_MESSAGE_ID=<reply-123@example.net>\n"
                    "REPLY_IN_REPLY_TO=<aiat-cert-123@example.aiat.ca>\n"
                    "REPLY_TOKEN_VERIFIED=PASS\n"
                    "CERTIFIED_AT=2026-07-29T20:00:00Z\n"
                ),
            }[key].encode("utf-8")
        )
    profile = tmp_path / "profile.env"
    profile.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )

    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_stub(stubs, "postconf", """
case "${2:-}" in
  relay_domains) printf '%s\\n' 'agents.aiat.ca' ;;
  transport_maps) printf '%s\\n' 'hash:transport' ;;
  relayhost) printf '\\n' ;;
  smtpd_relay_restrictions) printf '%s\\n' 'permit_mynetworks reject_unauth_destination' ;;
  mydestination) printf '%s\\n' 'localhost localhost.localdomain' ;;
  *) exit 1 ;;
esac
""")
    _write_stub(stubs, "postqueue", "printf '%s\\n' '-- 0 Kbytes in 0 Requests.'")
    _write_stub(stubs, "postfix", "exit 0")
    _write_stub(stubs, "wg", """
if [ "${1:-}" = show ] && [ "${3:-}" = latest-handshakes ]; then
  printf 'peer-test %s\\n' "$(date +%s)"
fi
exit 0
""")
    _write_stub(stubs, "ip", "printf '%s\\n' 'aiat-gateway UP 10.77.0.1/24'")
    _write_stub(stubs, "ss", "printf '%s\\n' 'LISTEN 0 100 0.0.0.0:25 0.0.0.0:*'")
    public_firewall_rule = " 'tcp dport 25 accept'" if public_smtp25 == "true" else ""
    _write_stub(stubs, "nft", f"printf '%s\\n' 'udp dport 51820 accept'{public_firewall_rule}")
    _write_stub(stubs, "nc", """
case " $* " in
  *'192.0.2.10 25'*) exit 1 ;;
  *) exit 0 ;;
esac
""")
    _write_stub(stubs, "timeout", 'seconds=$1; shift; exec "$@"')
    _write_stub(stubs, "openssl", "printf '%s\\n' 'Verification: OK'")
    _write_stub(stubs, "systemctl", "exit 0")
    _write_stub(stubs, "dig", """
case " $* " in
  *' MX '*) printf '%s\\n' '10 mail.aiat.ca.' ;;
  *) printf '%s\\n' '192.0.2.10' ;;
esac
""")
    _write_stub(stubs, "curl", "printf '200'")
    return profile, values, stubs


def _run_host_gate(profile: Path, gate: str, stubs: Path) -> subprocess.CompletedProcess[str]:
    script = shlex.quote(_wsl_path(GATEWAY / "scripts" / "validate-host-gates.sh"))
    profile_path = shlex.quote(_wsl_path(profile))
    gate_name = shlex.quote(gate)
    stub_path = shlex.quote(_wsl_path(stubs))
    command = f"PATH={stub_path}:/usr/bin:/bin; export PATH; exec {script} {profile_path} {gate_name}"
    return subprocess.run(
        ["bash", "-c", command],
        cwd=GATEWAY,
        text=True,
        capture_output=True,
        check=False,
    )


def test_internal_relay_accepts_public_smtp25_false(tmp_path: Path) -> None:
    profile, _, stubs = _host_gate_harness(tmp_path, public_smtp25="false")
    result = _run_host_gate(profile, "internal-relay", stubs)
    assert result.returncode == 0, result.stderr


def test_internal_relay_accepts_public_smtp25_true(tmp_path: Path) -> None:
    profile, _, stubs = _host_gate_harness(tmp_path, public_smtp25="true")
    result = _run_host_gate(profile, "internal-relay", stubs)
    assert result.returncode == 0, result.stderr


def test_external_inbound_refuses_public_smtp25_false(tmp_path: Path) -> None:
    profile, _, stubs = _host_gate_harness(tmp_path, public_smtp25="false")
    result = _run_host_gate(profile, "external-inbound", stubs)
    assert result.returncode != 0
    assert "PUBLIC_SMTP25_ACTIVATED must be true" in result.stderr


def test_external_inbound_accepts_public_smtp25_true_with_evidence(tmp_path: Path) -> None:
    profile, _, stubs = _host_gate_harness(tmp_path, public_smtp25="true")
    result = _run_host_gate(profile, "external-inbound", stubs)
    assert result.returncode == 0, result.stderr
    assert "self-probe failed" in result.stdout


def test_external_inbound_missing_evidence_fails(tmp_path: Path) -> None:
    profile, _, stubs = _host_gate_harness(tmp_path, public_smtp25="true")
    (tmp_path / "evidence" / "external.txt").unlink()
    result = _run_host_gate(profile, "external-inbound", stubs)
    assert result.returncode != 0
    assert "missing evidence file" in result.stderr


def test_external_inbound_malformed_evidence_fails(tmp_path: Path) -> None:
    profile, _, stubs = _host_gate_harness(tmp_path, public_smtp25="true")
    (tmp_path / "evidence" / "external.txt").write_bytes(
        b"EXTERNAL_INBOUND_SMTP_CERTIFIED=PASS\n"
        b"EXTERNAL_SOURCE_IP=not-an-ip\n"
        b"DESTINATION_HOSTNAME=mail.aiat.ca\n"
        b"DESTINATION_TCP_PORT=25\n"
        b"SMTP_ACCEPTANCE=250 5.1.1 rejected\n"
        b"PRODUCTION_RECIPIENT=test@example.net\n"
        b"POSTFIX_QUEUE_ID=bad\n"
        b"DOWNSTREAM_RELAY_TARGET=10.77.0.2:2525\n"
        b"FINAL_STATUS=failed\n"
    )
    result = _run_host_gate(profile, "external-inbound", stubs)
    assert result.returncode != 0
    assert "malformed EXTERNAL_SOURCE_IP" in result.stderr


def test_resend_complete_evidence_passes_after_tls_preliminary_check(tmp_path: Path) -> None:
    profile, _, stubs = _host_gate_harness(tmp_path, public_smtp25="true")
    result = _run_host_gate(profile, "resend", stubs)
    assert result.returncode == 0, result.stderr


def test_resend_pending_submission_record_is_not_final_evidence(tmp_path: Path) -> None:
    profile, _, stubs = _host_gate_harness(tmp_path)
    (tmp_path / "evidence" / "resend.txt").write_text(
        "\n".join((
            "RESEND_CERTIFICATION_SUBMISSION=PASS",
            "CERTIFICATION_STATE=PENDING_EXTERNAL_CONFIRMATION",
            "RESEND_PROVIDER_MESSAGE_ID=PENDING_EXTERNAL_PROVIDER_CORRELATION",
            "DELIVERY_STATUS=PENDING_EXTERNAL_RECEIPT",
            "REPLY_RECEIVED=PENDING_EXTERNAL_REPLY",
        )) + "\n",
        encoding="utf-8",
    )
    result = _run_host_gate(profile, "resend", stubs)
    assert result.returncode != 0
    assert "RESEND_OUTBOUND_RELAY_CERTIFIED=PASS is missing" in result.stderr


def test_resend_rejects_local_jmap_submission_id_as_provider_id(tmp_path: Path) -> None:
    profile, _, stubs = _host_gate_harness(tmp_path)
    evidence = tmp_path / "evidence" / "resend.txt"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            "RESEND_PROVIDER_MESSAGE_ID=re_123456789",
            "RESEND_PROVIDER_MESSAGE_ID=submission_123456789",
        ),
        encoding="utf-8",
    )
    result = _run_host_gate(profile, "resend", stubs)
    assert result.returncode != 0
    assert "must not be the local STALWART_SUBMISSION_ID" in result.stderr


def test_resend_rejects_missing_actual_provider_correlation(tmp_path: Path) -> None:
    profile, _, stubs = _host_gate_harness(tmp_path)
    evidence = tmp_path / "evidence" / "resend.txt"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            "RESEND_PROVIDER_MESSAGE_ID=re_123456789\n", "",
        ),
        encoding="utf-8",
    )
    result = _run_host_gate(profile, "resend", stubs)
    assert result.returncode != 0
    assert "missing actual RESEND_PROVIDER_MESSAGE_ID" in result.stderr


def test_resend_rejects_missing_external_delivery(tmp_path: Path) -> None:
    profile, _, stubs = _host_gate_harness(tmp_path)
    evidence = tmp_path / "evidence" / "resend.txt"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            "DELIVERY_STATUS=delivered", "DELIVERY_STATUS=PENDING_EXTERNAL_RECEIPT",
        ),
        encoding="utf-8",
    )
    result = _run_host_gate(profile, "resend", stubs)
    assert result.returncode != 0
    assert "DELIVERY_STATUS must be delivered" in result.stderr


def test_resend_rejects_missing_correlated_reply(tmp_path: Path) -> None:
    profile, _, stubs = _host_gate_harness(tmp_path)
    evidence = tmp_path / "evidence" / "resend.txt"
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            "REPLY_RECEIVED=PASS", "REPLY_RECEIVED=PENDING_EXTERNAL_REPLY",
        ),
        encoding="utf-8",
    )
    result = _run_host_gate(profile, "resend", stubs)
    assert result.returncode != 0
    assert "REPLY_RECEIVED must be PASS" in result.stderr


def test_resend_missing_evidence_fails(tmp_path: Path) -> None:
    profile, _, stubs = _host_gate_harness(tmp_path)
    (tmp_path / "evidence" / "resend.txt").unlink()
    result = _run_host_gate(profile, "resend", stubs)
    assert result.returncode != 0
    assert "missing evidence file" in result.stderr


def test_resend_forged_marker_only_evidence_fails(tmp_path: Path) -> None:
    profile, _, stubs = _host_gate_harness(tmp_path)
    (tmp_path / "evidence" / "resend.txt").write_bytes(
        b"RESEND_OUTBOUND_RELAY_CERTIFIED=PASS\n"
    )
    result = _run_host_gate(profile, "resend", stubs)
    assert result.returncode != 0
    assert "RELAY_HOST must be smtp.resend.com" in result.stderr


def test_resend_malformed_evidence_fails(tmp_path: Path) -> None:
    profile, _, stubs = _host_gate_harness(tmp_path)
    (tmp_path / "evidence" / "resend.txt").write_bytes(
        b"RESEND_OUTBOUND_RELAY_CERTIFIED=PASS\n"
        b"RELAY_HOST=mx.example.net\n"
        b"RELAY_PORT=587\n"
        b"TLS_MODE=starttls\n"
        b"TLS_VERIFICATION=PASS\n"
        b"SMTP_AUTHENTICATION=PASS\n"
        b"AUTH_USERNAME=resend\n"
        b"PRODUCTION_SENDER=gateway-test@agents.aiat.ca\n"
        b"EXTERNAL_RECIPIENT=operator@example.net\n"
        b"STALWART_ROUTE=mx\n"
        b"DIRECT_MX_OUTBOUND_ENABLED=true\n"
        b"STALWART_SUBMISSION_ID=forged-submission-id\n"
        b"RESEND_PROVIDER_MESSAGE_ID=forged-provider-id\n"
        b"ORIGINAL_MESSAGE_ID=<forged@example.aiat.ca>\n"
        b"EXTERNAL_RECEIPT_ID=forged-receipt-id\n"
        b"DELIVERY_STATUS=delivered\n"
        b"REPLY_RECEIVED=PASS\n"
        b"REPLY_MESSAGE_ID=<reply@example.net>\n"
        b"REPLY_IN_REPLY_TO=<forged@example.aiat.ca>\n"
        b"REPLY_TOKEN_VERIFIED=PASS\n"
        b"CERTIFIED_AT=not-a-timestamp\n"
    )
    result = _run_host_gate(profile, "resend", stubs)
    assert result.returncode != 0
    assert "RELAY_HOST must be smtp.resend.com" in result.stderr


def test_pre_activation_is_the_explicit_public_smtp25_false_gate(tmp_path: Path) -> None:
    profile, _, stubs = _host_gate_harness(tmp_path, public_smtp25="false")
    result = _run_host_gate(profile, "pre-activation", stubs)
    assert result.returncode == 0, result.stderr

    active_profile, _, active_stubs = _host_gate_harness(tmp_path / "active", public_smtp25="true")
    result = _run_host_gate(active_profile, "pre-activation", active_stubs)
    assert result.returncode != 0
    assert "PUBLIC_SMTP25_ACTIVATED must be false" in result.stderr


def test_all_gates_keep_activation_states_independent(tmp_path: Path) -> None:
    profile, _, stubs = _host_gate_harness(
        tmp_path,
        public_smtp25="true",
        identity_mode="gateway_reverse_proxy",
        identity_certified="true",
        outbound_certified="false",
    )
    result = _run_host_gate(profile, "all", stubs)
    assert result.returncode == 0, result.stderr
    assert "all five host gateway certification gates passed" in result.stdout
