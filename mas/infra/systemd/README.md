# MAS Systemd Integration

This directory contains systemd service files and control scripts for managing the MAS (Multi-Agent System) platform as individual systemd services.

## Files

- `mas-template.service`: Template for generating individual service files
- `masctl`: Control script for managing individual services via docker-compose
- `masctl-all`: Control script for managing all services via systemd
- `mas-*.service`: Generated service files for each MAS component

## Installation

1. Copy the systemd service files to the systemd directory:
   ```bash
   sudo cp mas-infra/systemd/mas-*.service /etc/systemd/system/
   sudo cp mas-infra/systemd/masctl /usr/local/bin/
   sudo cp mas-infra/systemd/masctl-all /usr/local/bin/
   sudo chmod +x /usr/local/bin/masctl /usr/local/bin/masctl-all
   ```

2. Copy the .env file to the mas directory:
   ```bash
   sudo mkdir -p /opt/mas
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

1. The `masctl` script acts as a bridge between systemd and docker-compose, allowing systemd to manage MAS services defined in docker-compose.yml.

2. Resource limits (MemoryLimit=512M, CPUQuota=50%) in the template service match the Docker Compose defaults. Adjust these values in individual service files if needed for specific services.

3. For development or debugging, you may want to modify the WorkingDirectory and EnvironmentFile paths to match your local setup.

4. The `redis-acl-init` service is configured with `Restart=no` as it's meant to run only once during initial setup.

5. All services use `Restart=unless-stopped` to match the Docker Compose restart policy.

## Troubleshooting

- If services fail to start, check the journal: `journalctl -u mas-<service-name>.service`
- Ensure Docker is running and accessible to the systemd services
- Verify that the .env file contains all required environment variables
- Check that the MAS Docker images are built and available locally