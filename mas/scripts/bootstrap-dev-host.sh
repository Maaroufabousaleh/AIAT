#!/usr/bin/env bash
# Provision and validate the AIAT development host inside WSL2.
#
# This is an operator/host action.  It deliberately does not run inside an
# AIAT application container and never grants an AIAT worker access to the
# Docker socket.  The local daemon uses its own socket, data root, and systemd
# unit so an existing Docker Desktop daemon remains untouched.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
MAS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
REPO_ROOT="$(cd "$MAS_ROOT/.." && pwd -P)"

DOCKER_CONTEXT_NAME="${AIAT_DOCKER_CONTEXT_NAME:-aiat-wsl}"
DOCKER_SOCKET_PATH="${AIAT_DOCKER_SOCKET_PATH:-/run/aiat-docker/docker.sock}"
DOCKER_SOCKET="unix://${DOCKER_SOCKET_PATH}"
DOCKER_CONFIG_PATH="${AIAT_DOCKER_CONFIG_PATH:-/etc/docker/aiat-daemon.json}"
DOCKER_SERVICE_PATH="${AIAT_DOCKER_SERVICE_PATH:-/etc/systemd/system/aiat-docker.service}"
DOCKER_DATA_ROOT="${AIAT_DOCKER_DATA_ROOT:-/var/lib/docker-aiat}"
DOCKER_EXEC_ROOT="${AIAT_DOCKER_EXEC_ROOT:-/var/run/docker-aiat}"
DOCKER_PIDFILE="${AIAT_DOCKER_PIDFILE:-/run/aiat-docker/dockerd.pid}"
HOST_ARTIFACT="${AIAT_DEV_HOST_READINESS_ARTIFACT:-$MAS_ROOT/docs/provenance/dev_host_readiness.json}"
REDIS_INSIGHT_PORT="${REDIS_INSIGHT_PORT:-8003}"

declare -A COMPOSE_HOST_PORTS=()
NEXT_DYNAMIC_PORT=18200

# The version and archive digest are reviewed repository pins.  Refreshing
# either value is an explicit provenance change, not an apt "latest" install.
GVISOR_VERSION="${AIAT_GVISOR_VERSION:-20260914.0}"
GVISOR_DEB_SHA256="${AIAT_GVISOR_DEB_SHA256:-d2f167823d8112fb2ec9151c2645b06c095aee97159700385d90843dd6b3bdd9}"
GVISOR_SMOKE_IMAGE="${AIAT_GVISOR_SMOKE_IMAGE:-ubuntu@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517}"
PORT_PROBE_IMAGE="alpine:3.21.3@sha256:a8560b36e8b8210634f77d9f7f9efd7ffa463e380b75e2e74aff4511df3ef88c"

DOCKER_REPO="https://download.docker.com/linux/ubuntu"
DOCKER_KEY_URL="${AIAT_DOCKER_KEY_URL:-$DOCKER_REPO/gpg}"
GVISOR_KEY_URL="${AIAT_GVISOR_KEY_URL:-https://gvisor.dev/archive.key}"
DISTRO="${WSL_DISTRO_NAME:-Ubuntu}"

HOST_ONLY=0
SKIP_TESTS=0
SKIP_COMPOSE=0
FORCE_PACKAGE_REFRESH=0
FORCE_COMPOSE_BUILD=0
STARTED_AT="$(date --iso-8601=seconds)"
TEMP_FILES=()

declare -A STATE=(
  [status]="DEV_BLOCKED"
  [failure_reason]=""
  [docker_daemon_before]="unknown"
  [docker_engine]="not_checked"
  [docker_compose]="not_checked"
  [runsc_package]="not_checked"
  [runsc_registered]="not_checked"
  [gvisor_smoke]="not_checked"
  [sandbox_readiness]="not_checked"
  [kata]="OPTIONAL_UNAVAILABLE"
  [compose]="not_checked"
  [migration]="not_checked"
  [network_boundary]="not_checked"
  [release_ledger]="not_checked"
  [tests]="not_run"
)

log() { printf '[aiat-host] %s\n' "$*"; }
warn() { printf '[aiat-host] WARNING: %s\n' "$*" >&2; }
fail() {
  STATE[failure_reason]="$*"
  STATE[status]="DEV_BLOCKED"
  sync_artifact_state
  write_artifact || true
  printf '[aiat-host] ERROR: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  local path
  for path in "${TEMP_FILES[@]}"; do
    if [ -n "$path" ] && [ -d "$path" ]; then
      rmdir -- "$path" 2>/dev/null || true
    elif [ -n "$path" ] && [ -e "$path" ]; then
      rm -f -- "$path" || true
    fi
  done
}
trap cleanup EXIT

sync_artifact_state() {
  local key variable
  for key in "${!STATE[@]}"; do
    variable="AIAT_BOOTSTRAP_STATE_${key^^}"
    printf -v "$variable" "%s" "${STATE[$key]}"
    export "$variable"
  done
}

usage() {
  cat <<'EOF'
Usage: mas/scripts/bootstrap-dev-host.sh [options]

Provision the AIAT development Docker/gVisor host in WSL2, start the local
Compose profile, migrate the local database, and run bounded local evidence.

Options:
  --host-only       Provision and validate the host, but do not start Compose.
  --skip-compose    Same host validation; skip Compose and all dependent checks.
  --skip-tests      Start/migrate/check services but skip the broad repository suite.
  --rebuild         Rebuild local Compose images before starting services.
  --refresh         Refresh apt metadata even when installed packages look current.
  -h, --help        Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --host-only) HOST_ONLY=1 ;;
    --skip-compose) SKIP_COMPOSE=1 ;;
    --skip-tests) SKIP_TESTS=1 ;;
    --rebuild) FORCE_COMPOSE_BUILD=1 ;;
    --refresh) FORCE_PACKAGE_REFRESH=1 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown option: $1" ;;
  esac
  shift
done

is_wsl2() {
  grep -Eiq '(microsoft|wsl)' /proc/version 2>/dev/null ||
    [ -n "${WSL_INTEROP:-}" ] || [ -n "${WSL_DISTRO_NAME:-}" ]
}

root_exec() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    sudo -- "$@"
  elif command -v wsl.exe >/dev/null 2>&1; then
    # WSL's root entry point does not require the operator to type a second
    # password and confines elevation to this distro.
    wsl.exe -d "$DISTRO" -u root -- "$@"
  else
    return 127
  fi
}

require_root_path() {
  if ! root_exec true >/dev/null 2>&1; then
    fail "root operations are unavailable; run from the configured WSL distro with sudo or wsl.exe root access"
  fi
}

write_root_file_if_changed() {
  local target="$1" mode="$2" content="$3" tmp
  tmp="$(mktemp)"
  TEMP_FILES+=("$tmp")
  printf '%s\n' "$content" >"$tmp"
  if root_exec test -f "$target" && root_exec cmp -s "$tmp" "$target"; then
    return 1
  fi
  root_exec install -D -m "$mode" "$tmp" "$target"
  return 0
}

docker_cli() {
  DOCKER_CONTEXT="$DOCKER_CONTEXT_NAME" command docker --context "$DOCKER_CONTEXT_NAME" "$@"
}

detect_current_docker() {
  local context endpoint info_os
  if ! command -v docker >/dev/null 2>&1; then
    STATE[docker_daemon_before]="docker_cli_missing"
    return 0
  fi
  context="$(docker context show 2>/dev/null || printf 'unknown')"
  endpoint="$(docker context inspect "$context" --format '{{.Endpoints.docker.Host}}' 2>/dev/null || printf 'unknown')"
  info_os="$(docker info --format '{{.OperatingSystem}}' 2>/dev/null || printf 'unreachable')"
  if printf '%s' "$info_os" | grep -Eiq 'docker desktop'; then
    STATE[docker_daemon_before]="docker_desktop:${context}:${endpoint}"
  elif [ "$info_os" = "unreachable" ]; then
    STATE[docker_daemon_before]="unreachable:${context}:${endpoint}"
  else
    STATE[docker_daemon_before]="configurable:${context}:${endpoint}"
  fi
  log "current Docker CLI context=${context} endpoint=${endpoint} daemon=${info_os}"
}

ensure_docker_repository() {
  local arch codename key_tmp source_content changed=0
  arch="$(dpkg --print-architecture)"
  codename="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-noble}")"
  key_tmp="$(mktemp)"
  TEMP_FILES+=("$key_tmp")
  curl --fail --silent --show-error --location "$DOCKER_KEY_URL" -o "$key_tmp" \
    || fail "could not download the Docker apt signing key"
  if ! root_exec test -f /etc/apt/keyrings/docker.asc || ! root_exec cmp -s "$key_tmp" /etc/apt/keyrings/docker.asc; then
    root_exec install -D -m 0644 "$key_tmp" /etc/apt/keyrings/docker.asc
    changed=1
  fi
  source_content="Types: deb
URIs: $DOCKER_REPO
Suites: $codename
Components: stable
Architectures: $arch
Signed-By: /etc/apt/keyrings/docker.asc"
  if write_root_file_if_changed /etc/apt/sources.list.d/docker.sources 0644 "$source_content"; then
    changed=1
  fi
  if [ "$changed" -eq 1 ] || [ "$FORCE_PACKAGE_REFRESH" -eq 1 ]; then
    log "refreshing Docker apt metadata"
    root_exec env DEBIAN_FRONTEND=noninteractive apt-get update
  fi
}

ensure_gvisor_repository() {
  local key_tmp key_armored source_content changed=0
  key_tmp="$(mktemp)"
  key_armored="$(mktemp)"
  TEMP_FILES+=("$key_tmp")
  TEMP_FILES+=("$key_armored")
  curl --fail --silent --show-error --location "$GVISOR_KEY_URL" -o "$key_tmp" \
    || fail "could not download the gVisor apt signing key"
  if command -v gpg >/dev/null 2>&1 && grep -q "BEGIN PGP PUBLIC KEY BLOCK" "$key_tmp"; then
    gpg --batch --dearmor <"$key_tmp" >"$key_armored"
  else
    cp "$key_tmp" "$key_armored"
  fi
  if ! root_exec test -f /usr/share/keyrings/gvisor-archive-keyring.gpg || ! root_exec cmp -s "$key_armored" /usr/share/keyrings/gvisor-archive-keyring.gpg; then
    root_exec install -D -m 0644 "$key_armored" /usr/share/keyrings/gvisor-archive-keyring.gpg
    changed=1
  fi
  source_content="deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main"
  if write_root_file_if_changed /etc/apt/sources.list.d/gvisor.list 0644 "$source_content"; then
    changed=1
  fi
  if [ "$changed" -eq 1 ] || [ "$FORCE_PACKAGE_REFRESH" -eq 1 ]; then
    log "refreshing gVisor apt metadata"
    root_exec env DEBIAN_FRONTEND=noninteractive apt-get update
  fi
}

package_installed() {
  dpkg-query -W -f='${Status} ${Version}\n' "$1" 2>/dev/null | grep -q '^install ok installed '
}

ensure_packages() {
  local packages=(docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin)
  local package
  ensure_docker_repository
  ensure_gvisor_repository
  if ! package_installed docker-ce || ! package_installed docker-ce-cli || ! package_installed containerd.io ||
     ! package_installed docker-buildx-plugin || ! package_installed docker-compose-plugin; then
    log "installing Docker Engine, containerd, Buildx, and Compose v2"
    root_exec env DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"
  fi
  local installed_gvisor
  installed_gvisor="$(dpkg-query -W -f='${Version}' runsc 2>/dev/null || true)"
  if [ "$installed_gvisor" != "$GVISOR_VERSION" ]; then
    log "installing pinned gVisor runsc package ${GVISOR_VERSION}"
    root_exec env DEBIAN_FRONTEND=noninteractive apt-get install -y "runsc=${GVISOR_VERSION}"
  fi
  if [ "$(dpkg-query -W -f='${Version}' runsc 2>/dev/null || true)" != "$GVISOR_VERSION" ]; then
    fail "installed runsc version does not match repository pin ${GVISOR_VERSION}"
  fi
  local deb_dir deb
  deb_dir="$(mktemp -d)"
  TEMP_FILES+=("$deb_dir")
  (cd "$deb_dir" && apt-get download "runsc=${GVISOR_VERSION}" >/dev/null)
  deb="$(find "$deb_dir" -maxdepth 1 -type f -name 'runsc_*.deb' -print -quit)"
  [ -n "$deb" ] || fail "apt did not provide the pinned runsc archive"
  if [ "$(sha256sum "$deb" | awk '{print $1}')" != "$GVISOR_DEB_SHA256" ]; then
    fail "pinned runsc archive checksum mismatch"
  fi
}

ensure_local_docker_service() {
  local config service changed=0
  config="$(cat <<EOF
{
  "data-root": "$DOCKER_DATA_ROOT",
  "exec-root": "$DOCKER_EXEC_ROOT",
  "pidfile": "$DOCKER_PIDFILE",
  "hosts": ["$DOCKER_SOCKET"],
  "group": "docker",
  "default-runtime": "runc",
  "runtimes": {
    "runsc": {
      "path": "/usr/bin/runsc"
    }
  }
}
EOF
)"
  if write_root_file_if_changed "$DOCKER_CONFIG_PATH" 0644 "$config"; then
    changed=1
  fi
  service="$(cat <<EOF
[Unit]
Description=AIAT development Docker Engine
Requires=containerd.service
After=containerd.service network-online.target
Wants=network-online.target

[Service]
Type=notify
ExecStartPre=/usr/bin/mkdir -p /run/aiat-docker
ExecStart=/usr/bin/dockerd --config-file=$DOCKER_CONFIG_PATH
ExecReload=/bin/kill -s HUP \$MAINPID
Restart=on-failure
RestartSec=2
TimeoutStartSec=0
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF
)"
  if write_root_file_if_changed "$DOCKER_SERVICE_PATH" 0644 "$service"; then
    changed=1
  fi
  root_exec systemctl daemon-reload
  root_exec systemctl enable --now containerd.service
  if [ "$changed" -eq 1 ]; then
    log "starting/restarting the AIAT Docker Engine because configuration changed"
    root_exec systemctl restart aiat-docker.service 2>/dev/null || root_exec systemctl start aiat-docker.service
  else
    root_exec systemctl enable aiat-docker.service >/dev/null
    if ! root_exec systemctl is-active --quiet aiat-docker.service; then
      log "starting the AIAT Docker Engine"
      root_exec systemctl start aiat-docker.service
    fi
  fi
  root_exec systemctl enable aiat-docker.service >/dev/null
}

port_available() {
  local port="$1" probe_name
  probe_name="aiat-port-probe-${port}-$$"
  if ! docker_cli image inspect "$PORT_PROBE_IMAGE" >/dev/null 2>&1; then
    docker_cli pull "$PORT_PROBE_IMAGE" >/dev/null || return 1
  fi
  if ! docker_cli create --name "$probe_name" --network bridge --publish "0.0.0.0:${port}:80" "$PORT_PROBE_IMAGE" /bin/sh -c "sleep 5" >/dev/null 2>&1; then
    docker_cli rm -f "$probe_name" >/dev/null 2>&1 || true
    return 1
  fi
  if docker_cli start "$probe_name" >/dev/null 2>&1; then
    docker_cli stop --time 1 "$probe_name" >/dev/null 2>&1 || true
    docker_cli rm "$probe_name" >/dev/null 2>&1 || true
    return 0
  fi
  docker_cli rm -f "$probe_name" >/dev/null 2>&1 || true
  return 1
}

select_host_port() {
  local variable="$1" default="$2" service="${3:-}" target="${4:-}" requested candidate existing
  requested="${!variable:-$default}"

  # Reuse a binding already owned by the current AIAT Compose project.  This
  # makes repeated bootstrap runs stable even when the selected port is above
  # the conventional defaults and avoids an unnecessary service restart.
  if [ -n "$service" ] && [ -n "$target" ]; then
    existing="$(existing_compose_host_port "$service" "$target")"
    if [ -n "$existing" ] && [ -z "${COMPOSE_HOST_PORTS[$existing]:-}" ]; then
      COMPOSE_HOST_PORTS["$existing"]="$variable"
      printf -v "$variable" '%s' "$existing"
      export "$variable"
      return 0
    fi
  fi

  candidate="$requested"
  while :; do
    if port_available "$candidate" && [ -z "${COMPOSE_HOST_PORTS[$candidate]:-}" ]; then
      COMPOSE_HOST_PORTS["$candidate"]="$variable"
      printf -v "$variable" '%s' "$candidate"
      export "$variable"
      if [ "$candidate" != "$requested" ]; then
        warn "${variable}=${requested} is already in use; using ${candidate} for this development run"
      fi
      return 0
    fi
    candidate="$NEXT_DYNAMIC_PORT"
    NEXT_DYNAMIC_PORT=$((NEXT_DYNAMIC_PORT + 1))
    if [ "$NEXT_DYNAMIC_PORT" -gt 18300 ]; then
      fail "could not find an available host port for ${variable}"
    fi
  done
}

select_compose_ports() {
  # WSL/Windows forwarding can reserve conventional ports outside the Linux
  # socket table. Use a high local port by default; an explicit operator value
  # is still honored when available.
  select_host_port AIAT_DEV_REDIS_PORT 18030 redis 6379
  select_host_port AIAT_DEV_POSTGRES_PORT 18031 postgres 5432
  select_host_port AIAT_DEV_MINIO_API_PORT 18032 minio 9000
  select_host_port AIAT_DEV_MINIO_CONSOLE_PORT 18033 minio 9001
  select_host_port AIAT_DEV_PGADMIN_PORT 18034 pgadmin 80
  select_host_port REDIS_INSIGHT_PORT 18035 redis-insight 5540
  select_host_port AIAT_DEV_PROMETHEUS_PORT 18036 prometheus 9090
  select_host_port AIAT_DEV_LITELLM_PORT 18037 litellm 4000
  select_host_port AIAT_DEV_OMNIROUTE_API_PORT 18038 omniroute 20128
  select_host_port AIAT_DEV_OMNIROUTE_METRICS_PORT 18039 omniroute 20129
  select_host_port AIAT_DEV_DASHBOARD_PORT 18040 dashboard 3000
  select_host_port AIAT_DEV_ORCHESTRATOR_PORT 18041 orchestrator-api 8000
  select_host_port AIAT_DEV_MESSAGE_ROUTER_PORT 18042 message-router 8001
  select_host_port AIAT_DEV_TOOL_SERVICE_PORT 18043 tool-service 8002
  select_host_port AIAT_DEV_PM_GATEWAY_PORT 18044 pm-gateway 8010
  # Docker/WSL port-forwarding proxies can release a probe slightly after
  # its container is removed. Let the daemon finish releasing every probe
  # before Compose attempts the selected bindings.
  sleep 2
}

existing_compose_host_port() {
  local service="$1" target="$2" container output
  container="mas-${service}-1"
  if [ "$service" = "dashboard" ]; then
    container="mas-dashboard"
  fi
  output="$(docker_cli port "$container" "${target}/tcp" 2>/dev/null | head -n1 || true)"
  printf '%s\n' "$output" | sed -nE 's/.*:([0-9]+)$/\1/p'
}

read_env_value() {
  local key="$1" env_file="$REPO_ROOT/.env" line
  [ -f "$env_file" ] || return 0
  line="$(grep -E "^${key}=" "$env_file" | head -n1 || true)"
  line="${line#*=}"
  printf "%s\n" "$line"
}

configure_local_check_environment() {
  local raw_dsn password
  export AIAT_ORCHESTRATOR_URL="http://127.0.0.1:${AIAT_DEV_ORCHESTRATOR_PORT}"
  export ORCHESTRATOR_API_URL="$AIAT_ORCHESTRATOR_URL"
  export AIAT_TOOL_SERVICE_URL="http://127.0.0.1:${AIAT_DEV_TOOL_SERVICE_PORT}"
  export TOOL_SERVICE_URL="$AIAT_TOOL_SERVICE_URL"
  if [ -z "${AIAT_MIGRATION_HEAD_DSN:-}" ]; then
    raw_dsn="$(read_env_value DATABASE_URL)"
    if [ -n "$raw_dsn" ]; then
      if [[ "$raw_dsn" == *'${'* ]]; then
        password="$(read_env_value POSTGRES_PASSWORD)"
        raw_dsn="${raw_dsn//\$\{POSTGRES_PASSWORD\}/$password}"
      fi
      raw_dsn="${raw_dsn//localhost/127.0.0.1}"
      raw_dsn="${raw_dsn//:5432/:${AIAT_DEV_POSTGRES_PORT}}"
      export AIAT_MIGRATION_HEAD_DSN="$raw_dsn"
    fi
  fi
}

wait_for_local_docker() {
  local attempts=0
  while [ "$attempts" -lt 30 ]; do
    if docker_cli info >/dev/null 2>&1; then
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 1
  done
  fail "AIAT Docker Engine did not become reachable at ${DOCKER_SOCKET}"
}

ensure_docker_context() {
  local endpoint
  endpoint="$(docker context inspect "$DOCKER_CONTEXT_NAME" --format '{{.Endpoints.docker.Host}}' 2>/dev/null || true)"
  if [ -z "$endpoint" ]; then
    docker context create "$DOCKER_CONTEXT_NAME" --docker "host=$DOCKER_SOCKET" >/dev/null
  elif [ "$endpoint" != "$DOCKER_SOCKET" ]; then
    docker context update "$DOCKER_CONTEXT_NAME" --docker "host=$DOCKER_SOCKET" >/dev/null
  fi
  docker context use "$DOCKER_CONTEXT_NAME" >/dev/null
  wait_for_local_docker
}

verify_host_runtime() {
  local runtimes runsc_version kata_runtime kata_check
  docker_cli version >/dev/null || fail "Docker version check failed"
  docker_cli compose version >/dev/null || fail "Docker Compose v2 check failed"
  docker_cli info >/dev/null || fail "Docker info check failed"
  runtimes="$(docker_cli info --format '{{json .Runtimes}}')"
  printf '%s' "$runtimes" | python3 -c 'import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if "runsc" in d else 1)' \
    || fail "runsc is not registered in the AIAT Docker daemon"
  runsc_version="$(runsc --version 2>/dev/null | awk '/release-/{sub(/^release-/, "", $3); print $3; exit}' || true)"
  [ "$runsc_version" = "$GVISOR_VERSION" ] || fail "runsc binary version does not match ${GVISOR_VERSION}"
  kata_runtime="$(printf '%s' "$runtimes" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("available" if any(k == "kata" or k.startswith("kata-") for k in d) else "optional_unavailable")')"
  kata_check="optional_unavailable"
  if command -v kata-check >/dev/null 2>&1; then
    kata-check >/dev/null 2>&1 && kata_check="pass" || kata_check="fail"
  fi
  if [ "$kata_runtime" = "available" ] && [ "$kata_check" != "fail" ]; then
    STATE[kata]="AVAILABLE"
  else
    STATE[kata]="OPTIONAL_UNAVAILABLE"
  fi
  STATE[docker_engine]="pass"
  STATE[docker_compose]="pass"
  STATE[runsc_package]="$GVISOR_VERSION"
  STATE[runsc_registered]="pass"
  log "Docker Engine, Compose v2, runsc registration, and pinned runsc version verified"
}

run_gvisor_smoke() {
  log "running bounded digest-pinned gVisor smoke: ${GVISOR_SMOKE_IMAGE}"
  docker_cli run --rm --runtime=runsc --network=none --read-only --cap-drop=ALL \
    --security-opt=no-new-privileges --pids-limit=64 --memory=128m --cpus=0.25 \
    "$GVISOR_SMOKE_IMAGE" /bin/true >/dev/null
  STATE[gvisor_smoke]="pass"
}

run_sandbox_readiness() {
  local output
  output="$(mktemp)"
  TEMP_FILES+=("$output")
  if DOCKER_CONTEXT="$DOCKER_CONTEXT_NAME" AIAT_DOCKER_CONTEXT="$DOCKER_CONTEXT_NAME" \
      uv run --isolated python "$SCRIPT_DIR/check_sandbox_runtime_readiness.py" \
      --live --smoke --check-kata --image "$GVISOR_SMOKE_IMAGE" --json >"$output"; then
    STATE[sandbox_readiness]="pass"
  else
    cat "$output" >&2 || true
    fail "sandbox runtime readiness check failed"
  fi
  SANDBOX_READINESS_FILE="$output"
}

compose_files() {
  printf '%s\n' "$MAS_ROOT/infra/compose/docker-compose.yml" "$MAS_ROOT/infra/compose/docker-compose.dev.yml"
}

compose_images_need_build() {
  local image
  local -a images=(
    "mas/team-runner:dev"
    "mas/redis-acl-init:dev"
    "mas/message-router:dev"
    "mas/tool-service:dev"
    "mas/opencode-runtime:dev"
    "mas/orchestrator-api:dev"
    "mas/pm-gateway:dev"
    "mas/dashboard:dev"
  )
  [ "$FORCE_COMPOSE_BUILD" -eq 1 ] && return 0
  for image in "${images[@]}"; do
    if ! docker_cli image inspect "$image" >/dev/null 2>&1; then
      return 0
    fi
  done
  return 1
}

start_compose_and_migrate() {
  local service attempt
  if compose_images_need_build; then
    log "building the AIAT development Compose profile"
    if ! (cd "$MAS_ROOT/infra/compose" && DOCKER_CONTEXT="$DOCKER_CONTEXT_NAME" ./mas.sh build); then
      fail "AIAT Compose image build failed"
    fi
  else
    log "local AIAT Compose images are present; skipping rebuild (use --rebuild to refresh)"
  fi
  log "starting the database prerequisites before API migration"
  if ! (cd "$MAS_ROOT/infra/compose" && DOCKER_CONTEXT="$DOCKER_CONTEXT_NAME" ./mas.sh up -d postgres pgbouncer); then
    fail "AIAT database prerequisites failed to start"
  fi
  log "migrating the local development database through the current source head"
  if ! (cd "$MAS_ROOT/infra/compose" && DOCKER_CONTEXT="$DOCKER_CONTEXT_NAME" ./mas.sh migrate-no-deps); then
    fail "AIAT local database migration failed"
  fi
  log "starting the AIAT development Compose profile"
  for attempt in 1 2 3; do
    if (cd "$MAS_ROOT/infra/compose" && DOCKER_CONTEXT="$DOCKER_CONTEXT_NAME" ./mas.sh up -d); then
      break
    fi
    if [ "$attempt" -eq 3 ]; then
      fail "AIAT development Compose profile failed to start after ${attempt} attempts"
    fi
    warn "Compose startup attempt ${attempt} did not complete; retrying after the daemon settles"
    sleep 10
  done
  if ! docker_cli inspect "mas-orchestrator-api-1" >/dev/null 2>&1; then
    fail "AIAT development Compose profile failed to start"
  fi
  ensure_compose_port_bindings
  STATE[compose]="started"
  service="orchestrator-api"
  [ "$service" = "orchestrator-api" ] || fail "orchestrator-api Compose service is missing"
  STATE[migration]="applied"
}

reconcile_local_object_store() {
  # MinIO stores IAM users in its persistent volume. A checked-in local .env
  # change therefore cannot repair an existing mas_agent user by merely
  # recreating the application container. Reconcile the one development
  # service account before live object-store evidence; this is idempotent,
  # data-preserving, and never prints credentials.
  log "reconciling the local MinIO agent account without touching object data"
  if ! (cd "$MAS_ROOT/infra/compose" && DOCKER_CONTEXT="$DOCKER_CONTEXT_NAME" ./scripts/reconcile-minio-agent-user.sh); then
    fail "local MinIO agent-account reconciliation failed"
  fi
}

compose_port_binding_ok() {
  local service="$1" target="$2" expected_port="$3" container actual
  container="mas-${service}-1"
  if [ "$service" = "dashboard" ]; then
    container="mas-dashboard"
  fi
  if ! docker_cli inspect "$container" >/dev/null 2>&1; then
    return 1
  fi
  actual="$(docker_cli port "$container" "${target}/tcp" 2>/dev/null || true)"
  printf '%s\n' "$actual" | grep -Eq "(^|:)${expected_port}$"
}

ensure_compose_port_bindings() {
  local -a stale=()
  local service target variable expected
  local -a bindings=(
    "redis 6379 AIAT_DEV_REDIS_PORT"
    "postgres 5432 AIAT_DEV_POSTGRES_PORT"
    "minio 9000 AIAT_DEV_MINIO_API_PORT"
    "pgadmin 80 AIAT_DEV_PGADMIN_PORT"
    "redis-insight 5540 REDIS_INSIGHT_PORT"
    "prometheus 9090 AIAT_DEV_PROMETHEUS_PORT"
    "litellm 4000 AIAT_DEV_LITELLM_PORT"
    "omniroute 20128 AIAT_DEV_OMNIROUTE_API_PORT"
    "dashboard 3000 AIAT_DEV_DASHBOARD_PORT"
    "orchestrator-api 8000 AIAT_DEV_ORCHESTRATOR_PORT"
    "message-router 8001 AIAT_DEV_MESSAGE_ROUTER_PORT"
    "tool-service 8002 AIAT_DEV_TOOL_SERVICE_PORT"
    "pm-gateway 8010 AIAT_DEV_PM_GATEWAY_PORT"
  )
  for binding in "${bindings[@]}"; do
    read -r service target variable <<<"$binding"
    expected="${!variable}"
    if ! compose_port_binding_ok "$service" "$target" "$expected"; then
      stale+=("$service")
    fi
  done
  if [ "${#stale[@]}" -eq 0 ]; then
    return 0
  fi
  log "recreating Compose services with stale host-port bindings: ${stale[*]}"
  if ! (cd "$MAS_ROOT/infra/compose" && DOCKER_CONTEXT="$DOCKER_CONTEXT_NAME" ./mas.sh up --force-recreate -d "${stale[@]}"); then
    fail "AIAT Compose host-port binding reconciliation failed"
  fi
  for binding in "${bindings[@]}"; do
    read -r service target variable <<<"$binding"
    expected="${!variable}"
    if ! compose_port_binding_ok "$service" "$target" "$expected"; then
      fail "Compose service ${service} did not expose the selected host port ${expected}"
    fi
  done
}

run_local_evidence() {
  local output attempt
  output="$(mktemp)"
  TEMP_FILES+=("$output")
  if DOCKER_CONTEXT="$DOCKER_CONTEXT_NAME" uv run --isolated python "$SCRIPT_DIR/check_database_migration_head.py" --live --json >"$output"; then
    MIGRATION_CHECK_FILE="$output"
  else
    cat "$output" >&2 || true
    fail "local database migration-head check failed"
  fi
  for attempt in 1 2 3; do
    output="$(mktemp)"
    TEMP_FILES+=("$output")
    if DOCKER_CONTEXT="$DOCKER_CONTEXT_NAME" uv run --isolated python "$SCRIPT_DIR/check_network_boundary.py" --live --json >"$output"; then
      STATE[network_boundary]="pass"
      NETWORK_CHECK_FILE="$output"
      break
    fi
    NETWORK_CHECK_FILE="$output"
    if [ "$attempt" -eq 3 ]; then
      cat "$output" >&2 || true
      STATE[network_boundary]="blocked_or_failed"
      fail "local network-boundary check failed after ${attempt} attempts"
    fi
    warn "network-boundary probe attempt ${attempt} did not pass; retrying after service readiness settles"
    sleep 10
  done
  if [ "$SKIP_TESTS" -eq 0 ]; then
    log "running the configured repository validation suite"
    (cd "$MAS_ROOT" && uv run --isolated pytest -q)
    STATE[tests]="pass"
  else
    STATE[tests]="skipped_by_operator"
  fi
  output="$(mktemp)"
  TEMP_FILES+=("$output")
  if AIAT_DEV_HOST_STATUS=DEV_READY DOCKER_CONTEXT="$DOCKER_CONTEXT_NAME" uv run --isolated python "$SCRIPT_DIR/check_release_ledger.py" --live --compose-local --json >"$output"; then
    RELEASE_LEDGER_FILE="$output"
  else
    # A release ledger may legitimately be non-release on WSL.  Keep its
    # machine-readable report and classify it as pending rather than blocking
    # ordinary development.
    RELEASE_LEDGER_FILE="$output"
    STATE[release_ledger]="release_pending"
    return 0
  fi
  STATE[release_ledger]="release_pending"
}

write_artifact() {
  local artifact_dir
  artifact_dir="$(dirname "$HOST_ARTIFACT")"
  mkdir -p "$artifact_dir"
  python3 - "$HOST_ARTIFACT" <<'PY'
import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

artifact_path = Path(sys.argv[1])
state = {key: os.environ.get(f"AIAT_BOOTSTRAP_STATE_{key.upper()}", "") for key in (
    "status", "failure_reason", "docker_daemon_before", "docker_engine", "docker_compose",
    "runsc_package", "runsc_registered", "gvisor_smoke", "sandbox_readiness", "kata",
    "compose", "migration", "network_boundary", "release_ledger", "tests",
)}
def read_json(name):
    raw = os.environ.get(name, "")
    if not raw:
        return None
    try:
        value = json.loads(Path(raw).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else value

report = {
    "schema_version": "aiat.dev-host-readiness.v1",
    "observed_at": datetime.now(timezone.utc).isoformat(),
    "wsl": {
        "detected": True,
        "kernel": platform.release(),
        "distribution": os.environ.get("WSL_DISTRO_NAME", "unknown"),
    },
    "docker": {
        "context": os.environ.get("AIAT_BOOTSTRAP_DOCKER_CONTEXT", "aiat-wsl"),
        "endpoint": os.environ.get("AIAT_BOOTSTRAP_DOCKER_ENDPOINT", "unix:///run/aiat-docker/docker.sock"),
        "daemon_before": state["docker_daemon_before"],
        "engine": state["docker_engine"],
        "compose_v2": state["docker_compose"],
        "registered_runtimes": os.environ.get("AIAT_BOOTSTRAP_RUNTIMES", "").split() if os.environ.get("AIAT_BOOTSTRAP_RUNTIMES") else [],
    },
    "gvisor": {
        "package_version": state["runsc_package"],
        "registered": state["runsc_registered"],
        "smoke": state["gvisor_smoke"],
        "smoke_image": os.environ.get("AIAT_BOOTSTRAP_SMOKE_IMAGE", ""),
        "package_sha256": os.environ.get("AIAT_BOOTSTRAP_GVISOR_DEB_SHA256", ""),
    },
    "sandbox_readiness": read_json("AIAT_BOOTSTRAP_SANDBOX_FILE"),
    "kata": {
        "status": state["kata"],
        "kvm_present": Path("/dev/kvm").exists(),
        "runtime_registered": any(name == "kata" or name.startswith("kata-") for name in os.environ.get("AIAT_BOOTSTRAP_RUNTIMES", "").split()),
    },
    "development": {
        "status": state["status"],
        "compose": state["compose"],
        "migration": state["migration"],
        "network_boundary": state["network_boundary"],
        "tests": state["tests"],
        "host_ports": {
            key: os.environ.get(key, "")
            for key in (
                "AIAT_DEV_REDIS_PORT",
                "AIAT_DEV_POSTGRES_PORT",
                "AIAT_DEV_MINIO_API_PORT",
                "AIAT_DEV_MINIO_CONSOLE_PORT",
                "AIAT_DEV_PGADMIN_PORT",
                "REDIS_INSIGHT_PORT",
                "AIAT_DEV_PROMETHEUS_PORT",
                "AIAT_DEV_LITELLM_PORT",
                "AIAT_DEV_OMNIROUTE_API_PORT",
                "AIAT_DEV_OMNIROUTE_METRICS_PORT",
                "AIAT_DEV_DASHBOARD_PORT",
                "AIAT_DEV_ORCHESTRATOR_PORT",
                "AIAT_DEV_MESSAGE_ROUTER_PORT",
                "AIAT_DEV_TOOL_SERVICE_PORT",
                "AIAT_DEV_PM_GATEWAY_PORT",
            )
        },
    },
    "release": {
        "status": "RELEASE_CERTIFICATION_PENDING",
        "ledger": read_json("AIAT_BOOTSTRAP_RELEASE_FILE"),
        "native_linux_required": True,
    },
    "migration_check": read_json("AIAT_BOOTSTRAP_MIGRATION_FILE"),
    "network_check": read_json("AIAT_BOOTSTRAP_NETWORK_FILE"),
    "failure_reason": state["failure_reason"] or None,
    "secret_free": True,
    "authority": "operator_host_bootstrap_only",
}
artifact_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

export AIAT_BOOTSTRAP_STATE_STATUS="${STATE[status]}"
export AIAT_BOOTSTRAP_STATE_FAILURE_REASON="${STATE[failure_reason]}"
export AIAT_BOOTSTRAP_STATE_DOCKER_DAEMON_BEFORE="${STATE[docker_daemon_before]}"
export AIAT_BOOTSTRAP_STATE_DOCKER_ENGINE="${STATE[docker_engine]}"
export AIAT_BOOTSTRAP_STATE_DOCKER_COMPOSE="${STATE[docker_compose]}"
export AIAT_BOOTSTRAP_STATE_RUNSC_PACKAGE="${STATE[runsc_package]}"
export AIAT_BOOTSTRAP_STATE_RUNSC_REGISTERED="${STATE[runsc_registered]}"
export AIAT_BOOTSTRAP_STATE_GVISOR_SMOKE="${STATE[gvisor_smoke]}"
export AIAT_BOOTSTRAP_STATE_SANDBOX_READINESS="${STATE[sandbox_readiness]}"
export AIAT_BOOTSTRAP_STATE_KATA="${STATE[kata]}"
export AIAT_BOOTSTRAP_STATE_COMPOSE="${STATE[compose]}"
export AIAT_BOOTSTRAP_STATE_MIGRATION="${STATE[migration]}"
export AIAT_BOOTSTRAP_STATE_NETWORK_BOUNDARY="${STATE[network_boundary]}"
export AIAT_BOOTSTRAP_STATE_RELEASE_LEDGER="${STATE[release_ledger]}"
export AIAT_BOOTSTRAP_STATE_TESTS="${STATE[tests]}"
export AIAT_BOOTSTRAP_DOCKER_CONTEXT="$DOCKER_CONTEXT_NAME"
export AIAT_BOOTSTRAP_DOCKER_ENDPOINT="$DOCKER_SOCKET"
export AIAT_BOOTSTRAP_SMOKE_IMAGE="$GVISOR_SMOKE_IMAGE"
export AIAT_BOOTSTRAP_GVISOR_DEB_SHA256="$GVISOR_DEB_SHA256"

if ! is_wsl2; then
  fail "this bootstrap is only supported inside WSL2"
fi
require_root_path
detect_current_docker
ensure_packages
ensure_local_docker_service
ensure_docker_context
verify_host_runtime
export AIAT_BOOTSTRAP_RUNTIMES="$(docker_cli info --format '{{json .Runtimes}}' | python3 -c 'import json,sys; print(" ".join(sorted(json.load(sys.stdin))))')"
run_gvisor_smoke
run_sandbox_readiness

if [ "$HOST_ONLY" -eq 0 ] && [ "$SKIP_COMPOSE" -eq 0 ]; then
  select_compose_ports
  configure_local_check_environment
  start_compose_and_migrate
  reconcile_local_object_store
  run_local_evidence
else
  STATE[compose]="skipped_by_operator"
  STATE[migration]="skipped_by_operator"
  STATE[network_boundary]="skipped_by_operator"
  STATE[release_ledger]="skipped_by_operator"
fi

if [ "${STATE[compose]}" = "started" ] && [ "${STATE[migration]}" = "applied" ] && [ "${STATE[gvisor_smoke]}" = "pass" ] && [ "${STATE[sandbox_readiness]}" = "pass" ]; then
  STATE[status]="DEV_READY"
else
  STATE[status]="DEV_BLOCKED"
fi

for key in "${!STATE[@]}"; do
  variable="AIAT_BOOTSTRAP_STATE_${key^^}"
  printf -v "$variable" '%s' "${STATE[$key]}"
  export "$variable"
done
export AIAT_BOOTSTRAP_SANDBOX_FILE="${SANDBOX_READINESS_FILE:-}"
export AIAT_BOOTSTRAP_MIGRATION_FILE="${MIGRATION_CHECK_FILE:-}"
export AIAT_BOOTSTRAP_NETWORK_FILE="${NETWORK_CHECK_FILE:-}"
export AIAT_BOOTSTRAP_RELEASE_FILE="${RELEASE_LEDGER_FILE:-}"
write_artifact

if [ "${STATE[status]}" = "DEV_READY" ]; then
  log "DEV_READY; RELEASE_CERTIFICATION_PENDING (native-Linux/operator gates remain separate)"
else
  fail "development host did not reach DEV_READY"
fi
