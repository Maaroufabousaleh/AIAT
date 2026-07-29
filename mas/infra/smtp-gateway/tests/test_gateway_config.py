from pathlib import Path
import re


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
    for gate in ("internal-relay", "external-inbound", "dns-mx", "identity-https", "resend"):
        assert gate in gates
    assert "IDENTITY_DNS_MODE gateway_reverse_proxy" in gates


def test_no_private_keys_are_in_templates() -> None:
    for path in (GATEWAY / "wireguard").glob("*conf.example"):
        text = path.read_text(encoding="utf-8")
        assert "PrivateKey = <" in text
        assert "BEGIN" not in text
        assert "wg genkey" not in text
