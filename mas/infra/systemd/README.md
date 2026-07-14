# MAS Systemd Integration

This directory contains systemd service files and control scripts for managing the MAS (Multi-Agent System) platform as individual systemd services.

## Files

- `mas-template.service`: Template for generating individual service files
- `masctl`: Control script for managing individual services via docker-compose
- `masctl-all`: Control script for managing all services via systemd
- `mas-*.service`: Generated service files for each MAS component

## Installation

1. Install the AIAT `mas/` workspace at `/opt/mas`, then copy the systemd
   service files and control scripts:
   ```bash
   sudo mkdir -p /opt/mas
   sudo cp -a . /opt/mas/
   sudo cp infra/systemd/mas-*.service /etc/systemd/system/
   sudo cp infra/systemd/masctl /usr/local/bin/
   sudo cp infra/systemd/masctl-all /usr/local/bin/
   sudo chmod +x /usr/local/bin/masctl /usr/local/bin/masctl-all
   ```

2. Copy the .env file to the mas directory:
   ```bash
   sudo cp .env /opt/mas/
   ```

3. Reload systemd daemon:
   ```bash
   sudo systemctl daemon-reload
   ```

## Usage

### Individual service management
```bash
# Start a specific service
sudo systemctl start mas-orchestrator-api.service

# Stop a specific service
sudo systemctl stop mas-orchestrator-api.service

# Restart a specific service
sudo systemctl restart mas-orchestrator-api.service

# Check status of a specific service
sudo systemctl status mas-orchestrator-api.service

# View logs for a specific service
sudo journalctl -u mas-orchestrator-api.service -f
```

### Using masctl (alternative to systemctl)
```bash
# Start a service
sudo masctl orchestrator-api start

# Stop a service
sudo masctl orchestrator-api stop

# Restart a service
sudo masctl orchestrator-api restart

# Check status
sudo masctl orchestrator-api status

# View logs
sudo masctl orchestrator-api logs
```

### Managing all services
```bash
# Start all services
sudo masctl-all start

# Stop all services
sudo masctl-all stop

# Restart all services
sudo masctl-all restart

# Check status of all services
sudo masctl-all status
```

## Service Dependencies

The service files are designed to respect Docker Compose dependencies:
- `mas-redis-acl-init` runs once to set up Redis ACL
- `mas-redis`, `mas-postgres`, and `mas-minio` are infrastructure dependencies
- `mas-message-router` depends on Redis ACL initialization
- `mas-tool-service` depends on Redis ACL initialization
- `mas-orchestrator-api` depends on Redis, Postgres, PgBouncer, and Message Router
- `mas-dashboard` depends on Orchestrator API, Message Router, and Tool Service
- Team runners depend on shared infrastructure services

## Notes

1. The `masctl` script uses the production Compose file by default. Set
   `MAS_COMPOSE_MODE=dev` in the environment when the development overlay is
   explicitly required. Set `MAS_ROOT` only when the workspace is installed
   somewhere other than `/opt/mas`.

2. Resource limits (`MemoryMax=512M`, `CPUQuota=50%`) constrain the short-lived
   Compose controller process; container limits and restart behavior remain
   owned by Docker Compose.

3. For development or debugging, you may want to modify the WorkingDirectory and EnvironmentFile paths to match your local setup.

4. The `redis-acl-init` service is configured with `Restart=no` as it's meant to run only once during initial setup.

5. Docker Compose owns container restart behavior. The systemd units remain
   active after the short-lived Compose control command completes.

## Troubleshooting

- If services fail to start, check the journal: `journalctl -u mas-<service-name>.service`
- Ensure Docker is running and accessible to the systemd services
- Verify that the .env file contains all required environment variables
- Check that the MAS Docker images are built and available locally
