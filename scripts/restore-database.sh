#!/bin/bash
#
# Database Restore Script for DragonsVault
# Restores PostgreSQL database from backup file
#
# Usage: ./scripts/restore-database.sh <backup_file>
# Example: ./scripts/restore-database.sh backups/postgres/dragonsvault_20260430_020000.sql.gz
#

set -e

# Configuration
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check arguments
if [ $# -eq 0 ]; then
    log_error "No backup file specified"
    echo "Usage: $0 <backup_file>"
    echo "Example: $0 backups/postgres/dragonsvault_20260430_020000.sql.gz"
    exit 1
fi

BACKUP_FILE=$1

# Check if backup file exists
if [ ! -f "$BACKUP_FILE" ]; then
    log_error "Backup file not found: ${BACKUP_FILE}"
    exit 1
fi

# Verify backup file
log_info "Verifying backup file..."
if ! gunzip -t "$BACKUP_FILE" 2>/dev/null; then
    log_error "Backup file is corrupted or invalid"
    exit 1
fi

log_info "Backup file verified successfully"

# Check if docker compose is available
if ! command -v docker &> /dev/null; then
    log_error "Docker is not installed or not in PATH"
    exit 1
fi

# Check if postgres container is running
if ! docker compose -f "$COMPOSE_FILE" ps postgres | grep -q "Up"; then
    log_error "PostgreSQL container is not running"
    log_info "Start it with: docker compose up -d postgres"
    exit 1
fi

# Display warning
log_warn "========================================="
log_warn "WARNING: DATABASE RESTORE"
log_warn "========================================="
log_warn "This will REPLACE the current database!"
log_warn "All existing data will be LOST!"
log_warn ""
log_warn "Backup file: ${BACKUP_FILE}"
log_warn "Database: dragonsvault"
log_warn ""
log_warn "It is recommended to:"
log_warn "  1. Stop the application first"
log_warn "  2. Create a backup of current data"
log_warn "  3. Verify the backup file"
log_warn "========================================="
echo ""

# Confirmation
read -p "Are you absolutely sure you want to proceed? (type 'yes' to continue): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    log_info "Restore cancelled by user"
    exit 0
fi

# Second confirmation
read -p "Last chance! Type 'RESTORE' to confirm: " FINAL_CONFIRM

if [ "$FINAL_CONFIRM" != "RESTORE" ]; then
    log_info "Restore cancelled by user"
    exit 0
fi

# Stop web services
log_info "Stopping web services..."
docker compose -f "$COMPOSE_FILE" stop web worker scheduler 2>/dev/null || true

# Wait for connections to close
log_info "Waiting for connections to close..."
sleep 3

# Terminate existing connections
log_info "Terminating existing database connections..."
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U dvapp -d postgres -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'dragonsvault' AND pid <> pg_backend_pid();" \
    2>/dev/null || true

# Drop and recreate database
log_info "Dropping existing database..."
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U dvapp -d postgres -c \
    "DROP DATABASE IF EXISTS dragonsvault;" || {
    log_error "Failed to drop database"
    exit 1
}

log_info "Creating new database..."
docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U dvapp -d postgres -c \
    "CREATE DATABASE dragonsvault OWNER dvapp;" || {
    log_error "Failed to create database"
    exit 1
}

# Restore from backup
log_info "Restoring from backup..."
log_info "This may take several minutes depending on database size..."

if gunzip -c "$BACKUP_FILE" | docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U dvapp dragonsvault; then
    log_info "Database restored successfully"
else
    log_error "Restore failed"
    exit 1
fi

# Verify restore
log_info "Verifying restore..."
TABLE_COUNT=$(docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U dvapp dragonsvault -t -c \
    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';" | tr -d ' ')

if [ "$TABLE_COUNT" -gt 0 ]; then
    log_info "Verification passed: ${TABLE_COUNT} tables found"
else
    log_error "Verification failed: No tables found"
    exit 1
fi

# Restart services
log_info "Restarting services..."
docker compose -f "$COMPOSE_FILE" up -d web worker scheduler

# Wait for services to be ready
log_info "Waiting for services to be ready..."
sleep 5

# Check health
log_info "Checking application health..."
if docker compose -f "$COMPOSE_FILE" exec -T web python -c \
    "import urllib.request,sys; req=urllib.request.Request('http://localhost:5000/readyz', headers={'X-Forwarded-Proto':'https'}); sys.exit(0 if urllib.request.urlopen(req, timeout=10).getcode()==200 else 1)" 2>/dev/null; then
    log_info "Application is healthy"
else
    log_warn "Application health check failed - check logs"
fi

# Summary
log_info "========================================="
log_info "Restore Summary:"
log_info "  Backup file: ${BACKUP_FILE}"
log_info "  Tables restored: ${TABLE_COUNT}"
log_info "  Status: SUCCESS"
log_info "========================================="
log_info "Restore process complete!"
log_info ""
log_info "Next steps:"
log_info "  1. Verify application functionality"
log_info "  2. Check logs: docker compose logs -f web"
log_info "  3. Test critical features"

exit 0
