# Backup and Restore Procedures

**Last Updated:** May 5, 2026  
**Version:** 1.0

---

## Overview

This document provides comprehensive procedures for backing up and restoring DragonsVault data, including database, uploaded files, and configuration.

---

## Table of Contents

1. [What to Backup](#what-to-backup)
2. [Backup Procedures](#backup-procedures)
3. [Restore Procedures](#restore-procedures)
4. [Automated Backups](#automated-backups)
5. [Testing Backups](#testing-backups)
6. [Disaster Recovery](#disaster-recovery)
7. [Troubleshooting](#troubleshooting)

---

## What to Backup

### Critical Data (Must Backup)

1. **PostgreSQL Database**
   - All application data (cards, folders, games, users)
   - Location: PostgreSQL server
   - Size: Varies (typically 100MB - 10GB)

2. **Uploaded Files**
   - CSV imports, temporary files
   - Location: `instance/uploads/`
   - Size: Varies (typically <1GB)

3. **Secrets**
   - Secret keys, API tokens
   - Location: `.secrets/` directory
   - Size: <1KB

### Important Data (Should Backup)

4. **Configuration Files**
   - `.env` file
   - `docker-compose.yml` (if customized)
   - Nginx configuration (if customized)

5. **Cache Data** (Optional)
   - Scryfall bulk data
   - Location: `instance/data/`
   - Size: ~500MB
   - Note: Can be re-downloaded, but takes time

### Not Required to Backup

- Docker images (can be rebuilt)
- Python virtual environments (can be recreated)
- Node modules (can be reinstalled)
- Temporary files
- Log files (unless needed for audit)

---

## Backup Procedures

### 1. PostgreSQL Database Backup

#### Manual Backup

```bash
# Full database backup
pg_dump -h localhost -U dvapp -d dragonsvault > backup_$(date +%Y%m%d_%H%M%S).sql

# Compressed backup (recommended for large databases)
pg_dump -h localhost -U dvapp -d dragonsvault | gzip > backup_$(date +%Y%m%d_%H%M%S).sql.gz

# Custom format (supports parallel restore)
pg_dump -h localhost -U dvapp -d dragonsvault -Fc -f backup_$(date +%Y%m%d_%H%M%S).dump

# Using Docker
docker compose exec postgres pg_dump -U dvapp dragonsvault > backup_$(date +%Y%m%d_%H%M%S).sql
```

#### Backup with Docker Compose

```bash
# Create backup directory
mkdir -p backups

# Backup database
docker compose exec -T postgres pg_dump -U dvapp dragonsvault | gzip > backups/db_$(date +%Y%m%d_%H%M%S).sql.gz

# Verify backup
gunzip -c backups/db_*.sql.gz | head -n 20
```

#### Backup Specific Tables

```bash
# Backup only user data
pg_dump -h localhost -U dvapp -d dragonsvault -t users -t user_settings > users_backup.sql

# Backup only game data
pg_dump -h localhost -U dvapp -d dragonsvault -t game_sessions -t game_players > games_backup.sql
```

### 2. File System Backup

#### Uploaded Files

```bash
# Backup uploads directory
tar -czf uploads_backup_$(date +%Y%m%d_%H%M%S).tar.gz instance/uploads/

# Using rsync (incremental)
rsync -av --delete instance/uploads/ /backup/location/uploads/
```

#### Secrets

```bash
# Backup secrets (IMPORTANT: Store securely!)
tar -czf secrets_backup_$(date +%Y%m%d_%H%M%S).tar.gz .secrets/

# Encrypt backup (recommended)
tar -czf - .secrets/ | gpg --symmetric --cipher-algo AES256 > secrets_backup_$(date +%Y%m%d_%H%M%S).tar.gz.gpg
```

#### Configuration Files

```bash
# Backup configuration
tar -czf config_backup_$(date +%Y%m%d_%H%M%S).tar.gz \
  .env \
  docker-compose.yml \
  infra/nginx.conf \
  infra/redis.conf
```

#### Scryfall Cache (Optional)

```bash
# Backup Scryfall data (large, can be re-downloaded)
tar -czf scryfall_backup_$(date +%Y%m%d_%H%M%S).tar.gz instance/data/
```

### 3. Complete System Backup

```bash
#!/bin/bash
# complete_backup.sh - Full system backup script

BACKUP_DIR="/backup/dragonsvault"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="dragonsvault_${DATE}"

mkdir -p "${BACKUP_DIR}/${BACKUP_NAME}"

echo "Starting backup: ${BACKUP_NAME}"

# 1. Database backup
echo "Backing up database..."
docker compose exec -T postgres pg_dump -U dvapp dragonsvault | \
  gzip > "${BACKUP_DIR}/${BACKUP_NAME}/database.sql.gz"

# 2. Uploaded files
echo "Backing up uploads..."
tar -czf "${BACKUP_DIR}/${BACKUP_NAME}/uploads.tar.gz" instance/uploads/

# 3. Secrets (encrypted)
echo "Backing up secrets..."
tar -czf - .secrets/ | \
  gpg --symmetric --cipher-algo AES256 > "${BACKUP_DIR}/${BACKUP_NAME}/secrets.tar.gz.gpg"

# 4. Configuration
echo "Backing up configuration..."
tar -czf "${BACKUP_DIR}/${BACKUP_NAME}/config.tar.gz" \
  .env docker-compose.yml infra/

# 5. Create manifest
echo "Creating manifest..."
cat > "${BACKUP_DIR}/${BACKUP_NAME}/manifest.txt" <<EOF
Backup Date: $(date)
Hostname: $(hostname)
Database Size: $(du -h "${BACKUP_DIR}/${BACKUP_NAME}/database.sql.gz" | cut -f1)
Uploads Size: $(du -h "${BACKUP_DIR}/${BACKUP_NAME}/uploads.tar.gz" | cut -f1)
Secrets Size: $(du -h "${BACKUP_DIR}/${BACKUP_NAME}/secrets.tar.gz.gpg" | cut -f1)
Config Size: $(du -h "${BACKUP_DIR}/${BACKUP_NAME}/config.tar.gz" | cut -f1)
EOF

# 6. Create checksum
echo "Creating checksums..."
cd "${BACKUP_DIR}/${BACKUP_NAME}"
sha256sum * > checksums.txt

echo "Backup complete: ${BACKUP_DIR}/${BACKUP_NAME}"
echo "Total size: $(du -sh "${BACKUP_DIR}/${BACKUP_NAME}" | cut -f1)"
```

---

## Restore Procedures

### 1. PostgreSQL Database Restore

#### Full Restore

```bash
# From plain SQL backup
psql -h localhost -U dvapp -d dragonsvault < backup_20260505_120000.sql

# From compressed backup
gunzip -c backup_20260505_120000.sql.gz | psql -h localhost -U dvapp -d dragonsvault

# From custom format
pg_restore -h localhost -U dvapp -d dragonsvault backup_20260505_120000.dump

# Using Docker
docker compose exec -T postgres psql -U dvapp dragonsvault < backup_20260505_120000.sql
```

#### Restore to New Database

```bash
# Create new database
createdb -h localhost -U dvapp dragonsvault_restored

# Restore backup
psql -h localhost -U dvapp -d dragonsvault_restored < backup.sql

# Verify data
psql -h localhost -U dvapp -d dragonsvault_restored -c "SELECT COUNT(*) FROM users;"
```

#### Restore Specific Tables

```bash
# Restore only users table
pg_restore -h localhost -U dvapp -d dragonsvault -t users backup.dump

# Or from SQL backup
psql -h localhost -U dvapp -d dragonsvault < users_backup.sql
```

### 2. File System Restore

#### Uploaded Files

```bash
# Restore uploads
tar -xzf uploads_backup_20260505_120000.tar.gz

# Using rsync
rsync -av /backup/location/uploads/ instance/uploads/
```

#### Secrets

```bash
# Restore secrets (plain)
tar -xzf secrets_backup_20260505_120000.tar.gz

# Restore secrets (encrypted)
gpg --decrypt secrets_backup_20260505_120000.tar.gz.gpg | tar -xzf -
```

#### Configuration

```bash
# Restore configuration
tar -xzf config_backup_20260505_120000.tar.gz
```

### 3. Complete System Restore

```bash
#!/bin/bash
# complete_restore.sh - Full system restore script

BACKUP_DIR="/backup/dragonsvault"
BACKUP_NAME="$1"

if [ -z "$BACKUP_NAME" ]; then
  echo "Usage: $0 <backup_name>"
  echo "Available backups:"
  ls -1 "$BACKUP_DIR"
  exit 1
fi

RESTORE_FROM="${BACKUP_DIR}/${BACKUP_NAME}"

if [ ! -d "$RESTORE_FROM" ]; then
  echo "Backup not found: $RESTORE_FROM"
  exit 1
fi

echo "Restoring from: $RESTORE_FROM"

# Verify checksums
echo "Verifying checksums..."
cd "$RESTORE_FROM"
sha256sum -c checksums.txt || {
  echo "Checksum verification failed!"
  exit 1
}

# Stop services
echo "Stopping services..."
docker compose down

# 1. Restore database
echo "Restoring database..."
docker compose up -d postgres
sleep 5
gunzip -c "${RESTORE_FROM}/database.sql.gz" | \
  docker compose exec -T postgres psql -U dvapp dragonsvault

# 2. Restore uploads
echo "Restoring uploads..."
tar -xzf "${RESTORE_FROM}/uploads.tar.gz"

# 3. Restore secrets
echo "Restoring secrets..."
gpg --decrypt "${RESTORE_FROM}/secrets.tar.gz.gpg" | tar -xzf -

# 4. Restore configuration
echo "Restoring configuration..."
tar -xzf "${RESTORE_FROM}/config.tar.gz"

# Start services
echo "Starting services..."
docker compose up -d

echo "Restore complete!"
echo "Please verify the application is working correctly."
```

---

## Automated Backups

### Cron Job Setup

```bash
# Edit crontab
crontab -e

# Add daily backup at 2 AM
0 2 * * * /path/to/dragonsvault/scripts/complete_backup.sh

# Add weekly backup on Sunday at 3 AM
0 3 * * 0 /path/to/dragonsvault/scripts/complete_backup.sh

# Cleanup old backups (keep last 30 days)
0 4 * * * find /backup/dragonsvault -type d -mtime +30 -exec rm -rf {} \;
```

### Systemd Timer (Alternative to Cron)

```ini
# /etc/systemd/system/dragonsvault-backup.service
[Unit]
Description=DragonsVault Backup
After=network.target

[Service]
Type=oneshot
User=dragonsvault
ExecStart=/path/to/dragonsvault/scripts/complete_backup.sh
```

```ini
# /etc/systemd/system/dragonsvault-backup.timer
[Unit]
Description=DragonsVault Daily Backup
Requires=dragonsvault-backup.service

[Timer]
OnCalendar=daily
OnCalendar=02:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
# Enable timer
sudo systemctl enable dragonsvault-backup.timer
sudo systemctl start dragonsvault-backup.timer

# Check status
sudo systemctl status dragonsvault-backup.timer
```

### Docker-based Backup Container

```yaml
# Add to docker-compose.yml
services:
  backup:
    image: postgres:16
    volumes:
      - ./backups:/backups
      - /var/run/docker.sock:/var/run/docker.sock:ro
    environment:
      PGHOST: postgres
      PGUSER: dvapp
      PGDATABASE: dragonsvault
      PGPASSWORD: ${POSTGRES_PASSWORD}
    command: >
      sh -c "
      while true; do
        pg_dump -Fc > /backups/backup_$$(date +%Y%m%d_%H%M%S).dump
        find /backups -name '*.dump' -mtime +7 -delete
        sleep 86400
      done
      "
    depends_on:
      - postgres
```

---

## Testing Backups

### Regular Backup Testing

```bash
# 1. Create test backup
./scripts/complete_backup.sh

# 2. Restore to test environment
./scripts/complete_restore.sh dragonsvault_20260505_120000

# 3. Verify data integrity
docker compose exec web flask shell
>>> from models import User, Folder, Card
>>> print(f"Users: {User.query.count()}")
>>> print(f"Folders: {Folder.query.count()}")
>>> print(f"Cards: {Card.query.count()}")
>>> exit()

# 4. Test application functionality
curl http://localhost:5000/readyz
curl http://localhost:5000/observability/health

# 5. Cleanup test environment
docker compose down
```

### Backup Verification Checklist

- [ ] Backup files created successfully
- [ ] Checksums verified
- [ ] Backup size is reasonable (not 0 bytes)
- [ ] Restore completes without errors
- [ ] Database contains expected data
- [ ] Application starts successfully
- [ ] Users can log in
- [ ] Critical features work (view cards, folders, games)
- [ ] Uploaded files are accessible

---

## Disaster Recovery

### Recovery Time Objective (RTO)

- **Target RTO**: 4 hours
- **Maximum RTO**: 24 hours

### Recovery Point Objective (RPO)

- **Target RPO**: 24 hours (daily backups)
- **Maximum RPO**: 7 days (weekly backups)

### Disaster Recovery Steps

1. **Assess the Situation**
   - Identify what failed (database, server, data corruption)
   - Determine last known good state
   - Identify most recent backup

2. **Prepare New Environment** (if needed)
   ```bash
   # Clone repository
   git clone https://github.com/JBSmith29/DragonsVault.git
   cd DragonsVault
   
   # Copy configuration
   scp backup-server:/backup/dragonsvault/latest/config.tar.gz .
   tar -xzf config.tar.gz
   ```

3. **Restore Database**
   ```bash
   # Start PostgreSQL
   docker compose up -d postgres
   
   # Restore database
   gunzip -c /backup/dragonsvault/latest/database.sql.gz | \
     docker compose exec -T postgres psql -U dvapp dragonsvault
   ```

4. **Restore Files**
   ```bash
   # Restore uploads
   tar -xzf /backup/dragonsvault/latest/uploads.tar.gz
   
   # Restore secrets
   gpg --decrypt /backup/dragonsvault/latest/secrets.tar.gz.gpg | tar -xzf -
   ```

5. **Start Services**
   ```bash
   docker compose up -d
   ```

6. **Verify Recovery**
   ```bash
   # Health check
   curl http://localhost:5000/observability/health
   
   # Test login
   curl -X POST http://localhost:5000/login \
     -d "username=admin&password=test"
   ```

7. **Communicate Status**
   - Notify users of recovery progress
   - Document what happened
   - Update runbook if needed

---

## Troubleshooting

### Backup Issues

#### Backup Fails with "Permission Denied"

```bash
# Fix permissions
sudo chown -R $(whoami):$(whoami) /backup/dragonsvault
chmod 755 /backup/dragonsvault
```

#### Backup is Too Large

```bash
# Use compression
pg_dump dragonsvault | gzip -9 > backup.sql.gz

# Exclude large tables (if appropriate)
pg_dump dragonsvault --exclude-table=audit_logs > backup.sql
```

#### Backup Takes Too Long

```bash
# Use parallel dump (custom format)
pg_dump -Fd -j 4 -f backup_dir dragonsvault

# Or use pg_basebackup for physical backup
pg_basebackup -D /backup/pgdata -Ft -z -P
```

### Restore Issues

#### Restore Fails with "Database Already Exists"

```bash
# Drop and recreate database
dropdb dragonsvault
createdb dragonsvault
psql dragonsvault < backup.sql
```

#### Restore Fails with "Role Does Not Exist"

```bash
# Create missing role
createuser dvapp

# Or restore with --no-owner
pg_restore --no-owner -d dragonsvault backup.dump
```

#### Restore is Slow

```bash
# Disable triggers during restore
psql dragonsvault -c "ALTER TABLE cards DISABLE TRIGGER ALL;"
psql dragonsvault < backup.sql
psql dragonsvault -c "ALTER TABLE cards ENABLE TRIGGER ALL;"

# Or use parallel restore
pg_restore -j 4 -d dragonsvault backup.dump
```

---

## Best Practices

1. **3-2-1 Rule**
   - 3 copies of data
   - 2 different storage types
   - 1 off-site backup

2. **Test Regularly**
   - Test restores monthly
   - Document any issues
   - Update procedures as needed

3. **Automate Everything**
   - Use cron/systemd for scheduling
   - Monitor backup success/failure
   - Alert on backup failures

4. **Secure Backups**
   - Encrypt sensitive data
   - Restrict backup access
   - Store secrets separately

5. **Monitor Backup Health**
   - Check backup size trends
   - Verify checksums
   - Test restore procedures

6. **Document Everything**
   - Keep runbooks updated
   - Document special procedures
   - Track backup locations

---

## Backup Retention Policy

| Backup Type | Retention | Storage Location |
|-------------|-----------|------------------|
| Daily | 7 days | Local disk |
| Weekly | 4 weeks | Local disk + S3 |
| Monthly | 12 months | S3 |
| Yearly | 7 years | S3 Glacier |

---

## Emergency Contacts

- **Database Admin**: [Contact Info]
- **System Admin**: [Contact Info]
- **On-Call Engineer**: [Contact Info]
- **Backup Service**: [Contact Info]

---

## Revision History

| Date | Version | Changes | Author |
|------|---------|---------|--------|
| 2026-05-05 | 1.0 | Initial version | Kiro AI |

---

**Next Review Date:** August 5, 2026
