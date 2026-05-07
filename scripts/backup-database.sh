#!/bin/bash
#
# Database Backup Script for DragonsVault
# Performs automated PostgreSQL backups with verification and retention
#
# Usage: ./scripts/backup-database.sh
# Cron: 0 2 * * * /app/scripts/backup-database.sh >> /var/log/backup.log 2>&1
#

set -e

# Configuration
BACKUP_DIR="${BACKUP_DIR:-./backups/postgres}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="dragonsvault_${TIMESTAMP}.sql.gz"
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

# Check if docker compose is available
if ! command -v docker &> /dev/null; then
    log_error "Docker is not installed or not in PATH"
    exit 1
fi

# Create backup directory
mkdir -p "$BACKUP_DIR"

# Check if postgres container is running
if ! docker compose -f "$COMPOSE_FILE" ps postgres | grep -q "Up"; then
    log_error "PostgreSQL container is not running"
    exit 1
fi

# Perform backup
log_info "Starting database backup..."
log_info "Backup file: ${BACKUP_FILE}"

if docker compose -f "$COMPOSE_FILE" exec -T postgres pg_dump -U dvapp dragonsvault | gzip > "${BACKUP_DIR}/${BACKUP_FILE}"; then
    log_info "Backup created successfully"
else
    log_error "Backup failed"
    exit 1
fi

# Verify backup
log_info "Verifying backup integrity..."
if gunzip -t "${BACKUP_DIR}/${BACKUP_FILE}" 2>/dev/null; then
    log_info "Backup verification passed"
else
    log_error "Backup verification failed - file may be corrupted"
    exit 1
fi

# Calculate and display size
SIZE=$(du -h "${BACKUP_DIR}/${BACKUP_FILE}" | cut -f1)
log_info "Backup size: ${SIZE}"

# Count total backups
TOTAL_BACKUPS=$(find "$BACKUP_DIR" -name "dragonsvault_*.sql.gz" | wc -l)
log_info "Total backups: ${TOTAL_BACKUPS}"

# Remove old backups
log_info "Cleaning old backups (older than ${RETENTION_DAYS} days)..."
DELETED=$(find "$BACKUP_DIR" -name "dragonsvault_*.sql.gz" -mtime +${RETENTION_DAYS} -type f | wc -l)

if [ "$DELETED" -gt 0 ]; then
    find "$BACKUP_DIR" -name "dragonsvault_*.sql.gz" -mtime +${RETENTION_DAYS} -type f -delete
    log_info "Deleted ${DELETED} old backup(s)"
else
    log_info "No old backups to delete"
fi

# Upload to S3 (optional)
if [ -n "$AWS_S3_BUCKET" ]; then
    log_info "Uploading to S3 bucket: ${AWS_S3_BUCKET}..."
    
    if command -v aws &> /dev/null; then
        if aws s3 cp "${BACKUP_DIR}/${BACKUP_FILE}" "s3://${AWS_S3_BUCKET}/backups/postgres/" --storage-class STANDARD_IA; then
            log_info "S3 upload successful"
        else
            log_warn "S3 upload failed (backup still saved locally)"
        fi
    else
        log_warn "AWS CLI not installed - skipping S3 upload"
    fi
fi

# Create backup manifest
MANIFEST_FILE="${BACKUP_DIR}/manifest.txt"
echo "${TIMESTAMP} ${BACKUP_FILE} ${SIZE}" >> "$MANIFEST_FILE"

# Summary
log_info "========================================="
log_info "Backup Summary:"
log_info "  File: ${BACKUP_FILE}"
log_info "  Size: ${SIZE}"
log_info "  Location: ${BACKUP_DIR}"
log_info "  Total backups: ${TOTAL_BACKUPS}"
log_info "========================================="
log_info "Backup process complete!"

exit 0
