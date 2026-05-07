#!/bin/bash
#
# Setup automated database backups via cron
# This script configures a cron job to run daily backups at 2 AM
#

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

# Get the absolute path to the backup script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_SCRIPT="${SCRIPT_DIR}/backup-database.sh"
APP_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="${APP_DIR}/logs"

# Create logs directory
mkdir -p "$LOG_DIR"

# Verify backup script exists
if [ ! -f "$BACKUP_SCRIPT" ]; then
    echo "Error: Backup script not found at ${BACKUP_SCRIPT}"
    exit 1
fi

# Make sure backup script is executable
chmod +x "$BACKUP_SCRIPT"

log_info "Setting up automated database backups..."
log_info "Backup script: ${BACKUP_SCRIPT}"
log_info "Log directory: ${LOG_DIR}"

# Create cron job entry
CRON_JOB="0 2 * * * cd ${APP_DIR} && ${BACKUP_SCRIPT} >> ${LOG_DIR}/backup.log 2>&1"

# Check if cron job already exists
if crontab -l 2>/dev/null | grep -q "$BACKUP_SCRIPT"; then
    log_warn "Cron job already exists. Updating..."
    # Remove old entry
    crontab -l 2>/dev/null | grep -v "$BACKUP_SCRIPT" | crontab -
fi

# Add new cron job
(crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -

log_info "Cron job added successfully!"
log_info ""
log_info "Schedule: Daily at 2:00 AM"
log_info "Logs: ${LOG_DIR}/backup.log"
log_info ""
log_info "To view current cron jobs:"
log_info "  crontab -l"
log_info ""
log_info "To view backup logs:"
log_info "  tail -f ${LOG_DIR}/backup.log"
log_info ""
log_info "To manually run backup:"
log_info "  ${BACKUP_SCRIPT}"
log_info ""
log_info "To remove cron job:"
log_info "  crontab -e  # then delete the line with backup-database.sh"

# Test the backup script
log_info "Testing backup script..."
if "$BACKUP_SCRIPT"; then
    log_info "✓ Backup test successful!"
else
    log_warn "Backup test failed. Check the script and try again."
    exit 1
fi

log_info ""
log_info "========================================="
log_info "Automated backups configured successfully!"
log_info "========================================="

exit 0
