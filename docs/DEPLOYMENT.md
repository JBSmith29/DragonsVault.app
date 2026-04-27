# Production Deployment Guide

This guide covers deploying DragonsVault to production environments.

## Prerequisites

- Docker Engine 24+ or Docker Desktop
- Docker Compose v2.20+
- 4GB+ RAM (8GB+ recommended)
- 20GB+ disk space
- Domain name with DNS configured
- SSL/TLS certificate (Let's Encrypt recommended)

## Pre-Deployment Checklist

### 1. Secrets Management

**Generate secure secrets:**

```bash
# Flask secret key (min 32 chars)
python -c "import secrets; print(secrets.token_hex(32))" > .secrets/secret_key

# Django secret key (min 50 chars)
python -c "import secrets; print(secrets.token_urlsafe(50))" > .secrets/django_secret_key

# PostgreSQL password
python -c "import secrets; print(secrets.token_urlsafe(32))" > .secrets/postgres_password

# Game engine shared secret
python -c "import secrets; print(secrets.token_hex(32))" > .secrets/game_engine_secret
```

**Set file permissions:**

```bash
chmod 600 .secrets/*
```

### 2. Environment Configuration

Create `.env` from `.env.example`:

```bash
cp .env.example .env
```

**Required variables:**

```bash
# Database
POSTGRES_PASSWORD=$(cat .secrets/postgres_password)
DATABASE_URL=postgresql+psycopg2://dvapp:${POSTGRES_PASSWORD}@pgbouncer:6432/dragonsvault

# Django
DJANGO_SECRET_KEY=$(cat .secrets/django_secret_key)
DJANGO_ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com

# Game Engine
GAME_ENGINE_SHARED_SECRET=$(cat .secrets/game_engine_secret)

# Optional: External services
MTGJSON_API_TOKEN=your_token_here
HCAPTCHA_SITE_KEY=your_site_key
HCAPTCHA_SECRET_KEY=your_secret_key
```

### 3. SSL/TLS Certificate

**Option A: Let's Encrypt (Recommended)**

```bash
# Install certbot
sudo apt-get install certbot

# Obtain certificate
sudo certbot certonly --standalone -d yourdomain.com -d www.yourdomain.com

# Copy to project
sudo cp /etc/letsencrypt/live/yourdomain.com/fullchain.pem infra/ssl/
sudo cp /etc/letsencrypt/live/yourdomain.com/privkey.pem infra/ssl/
sudo chown $USER:$USER infra/ssl/*.pem
```

**Option B: Self-Signed (Development Only)**

```bash
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout infra/ssl/privkey.pem \
  -out infra/ssl/fullchain.pem \
  -subj "/CN=yourdomain.com"
```

### 4. Nginx Configuration

Update `infra/nginx.conf`:

```nginx
server {
    listen 443 ssl http2;
    server_name yourdomain.com www.yourdomain.com;

    ssl_certificate /etc/nginx/ssl/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # ... rest of config
}
```

## Deployment Steps

### 1. Initialize Database

```bash
# Start database services
docker compose up -d postgres pgbouncer

# Wait for PostgreSQL to be ready
docker compose exec postgres pg_isready -U dvapp

# Run migrations
docker compose run --rm web flask db upgrade

# Initialize service schemas
docker compose exec postgres psql -U dvapp -d dragonsvault -f /app/backend/scripts/init_service_schemas.sql
```

### 2. Start Core Services

```bash
# Start all services
docker compose up -d

# Verify health
docker compose ps
curl http://localhost/healthz
curl http://localhost/readyz
```

### 3. Create Admin User

```bash
docker compose exec web flask create-admin \
  --email admin@yourdomain.com \
  --username admin \
  --password "$(openssl rand -base64 16)"
```

### 4. Initial Data Sync

```bash
# Sync Scryfall oracle data (takes 5-10 minutes)
docker compose exec web flask fetch-scryfall-bulk
docker compose exec web flask refresh-scryfall

# Sync Commander Spellbook combos
docker compose exec web flask sync-spellbook-combos

# Initialize full-text search
docker compose exec web flask fts-ensure
docker compose exec web flask fts-reindex

# Refresh oracle tags
docker compose exec web flask refresh-oracle-tags-full
```

### 5. Configure Reverse Proxy

**Option A: Use Built-in Nginx**

```bash
# Nginx is already configured in docker-compose.yml
# Just expose port 443
```

Update `docker-compose.yml`:

```yaml
nginx:
  ports:
    - "80:80"
    - "443:443"
  volumes:
    - ./infra/ssl:/etc/nginx/ssl:ro
```

**Option B: External Reverse Proxy (Caddy, Traefik)**

```caddyfile
# Caddyfile
yourdomain.com {
    reverse_proxy localhost:80
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}
```

### 6. Enable Monitoring

```bash
# Check metrics endpoint
curl http://localhost/metrics

# Optional: Set up Prometheus scraping
# Add to prometheus.yml:
scrape_configs:
  - job_name: 'dragonsvault'
    static_configs:
      - targets: ['yourdomain.com:80']
    metrics_path: '/metrics'
```

## Post-Deployment

### 1. Verify Services

```bash
# Check all containers are running
docker compose ps

# Check logs for errors
docker compose logs --tail=100 web
docker compose logs --tail=100 worker
docker compose logs --tail=100 postgres

# Test API endpoints
curl https://yourdomain.com/api/ops/health
curl https://yourdomain.com/api/cards/v1/ping
curl https://yourdomain.com/api/prices/v1/ping
```

### 2. Configure Backups

**Database Backups:**

```bash
# Create backup script
cat > /usr/local/bin/backup-dragonsvault.sh <<'EOF'
#!/bin/bash
BACKUP_DIR=/backups/dragonsvault
DATE=$(date +%Y%m%d_%H%M%S)
docker compose exec -T postgres pg_dump -U dvapp dragonsvault | gzip > $BACKUP_DIR/db_$DATE.sql.gz
find $BACKUP_DIR -name "db_*.sql.gz" -mtime +7 -delete
EOF

chmod +x /usr/local/bin/backup-dragonsvault.sh

# Add to crontab (daily at 2 AM)
echo "0 2 * * * /usr/local/bin/backup-dragonsvault.sh" | crontab -
```

**Volume Backups:**

```bash
# Backup instance data
docker run --rm -v dragonsvault_pgdata:/data -v /backups:/backup \
  alpine tar czf /backup/pgdata_$(date +%Y%m%d).tar.gz /data
```

### 3. Set Up Log Rotation

```bash
# /etc/logrotate.d/dragonsvault
/var/lib/docker/containers/*/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```

### 4. Configure Firewall

```bash
# Allow HTTP/HTTPS
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Block direct access to services
sudo ufw deny 5000/tcp
sudo ufw deny 5432/tcp
sudo ufw deny 6379/tcp
sudo ufw deny 6432/tcp
```

## Scaling

### Horizontal Scaling

**Scale workers:**

```bash
docker compose up -d --scale worker=3
```

**Scale microservices:**

```bash
docker compose up -d --scale card-data=2 --scale price-service=2
```

### Vertical Scaling

Update resource limits in `docker-compose.resources.yml`:

```yaml
web:
  deploy:
    resources:
      limits:
        cpus: '8.0'
        memory: 8G
```

### Database Scaling

**Enable connection pooling:**

```bash
# Already configured via PgBouncer
# Adjust pool size in docker-compose.yml:
DEFAULT_POOL_SIZE: 50
MAX_CLIENT_CONN: 1000
```

**Read replicas (future):**

```yaml
postgres-replica:
  image: postgres:16-alpine
  environment:
    POSTGRES_REPLICATION_MODE: slave
    POSTGRES_MASTER_HOST: postgres
```

## Monitoring & Observability

### Health Checks

```bash
# Overall health
curl https://yourdomain.com/readyz

# Individual services
curl https://yourdomain.com/api/user/v1/ping
curl https://yourdomain.com/api/cards/v1/ping
curl https://yourdomain.com/api/prices/v1/ping
```

### Metrics

```bash
# Prometheus format
curl https://yourdomain.com/metrics

# JSON format
curl https://yourdomain.com/metrics?format=json
```

### Logs

```bash
# Follow logs
docker compose logs -f web worker

# Search logs
docker compose logs web | grep ERROR

# Export logs
docker compose logs --no-color > logs_$(date +%Y%m%d).txt
```

## Troubleshooting

### Service Won't Start

```bash
# Check logs
docker compose logs <service>

# Check health
docker compose ps
docker inspect <container_id>

# Restart service
docker compose restart <service>
```

### Database Connection Issues

```bash
# Check PostgreSQL
docker compose exec postgres pg_isready -U dvapp

# Check PgBouncer
docker compose exec pgbouncer psql -h localhost -p 6432 -U dvapp -d dragonsvault

# Check connections
docker compose exec postgres psql -U dvapp -d dragonsvault -c "SELECT count(*) FROM pg_stat_activity;"
```

### High Memory Usage

```bash
# Check resource usage
docker stats

# Restart services
docker compose restart web worker

# Clear Redis cache
docker compose exec redis redis-cli FLUSHDB
```

### Slow Queries

```bash
# Enable query logging
docker compose exec postgres psql -U dvapp -d dragonsvault -c "ALTER SYSTEM SET log_min_duration_statement = 1000;"
docker compose restart postgres

# Check slow queries
docker compose exec postgres psql -U dvapp -d dragonsvault -c "SELECT query, calls, total_time, mean_time FROM pg_stat_statements ORDER BY mean_time DESC LIMIT 10;"
```

## Maintenance

### Update Application

```bash
# Pull latest code
git pull origin main

# Rebuild images
docker compose build

# Run migrations
docker compose run --rm web flask db upgrade

# Restart services
docker compose up -d
```

### Update Dependencies

```bash
# Update Python packages
docker compose run --rm web pip list --outdated
docker compose run --rm web pip-audit

# Rebuild after updates
docker compose build --no-cache
```

### Database Maintenance

```bash
# Vacuum (already automated via pgmaintenance service)
docker compose exec postgres vacuumdb --all --analyze-in-stages

# Reindex
docker compose exec postgres reindexdb -U dvapp dragonsvault

# Check table sizes
docker compose exec postgres psql -U dvapp -d dragonsvault -c "SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size FROM pg_tables ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC LIMIT 10;"
```

## Security Hardening

### 1. Enable Security Headers

Already configured via Flask-Talisman:
- HSTS
- CSP
- X-Frame-Options
- X-Content-Type-Options

### 2. Rate Limiting

Configured via Flask-Limiter:
- 200 requests/minute per IP (default)
- 1000 requests/hour per user

### 3. Secrets Rotation

```bash
# Rotate Flask secret key
python -c "import secrets; print(secrets.token_hex(32))" > .secrets/secret_key
docker compose restart web worker scheduler

# Rotate database password
# 1. Update password in PostgreSQL
# 2. Update .env
# 3. Restart all services
```

### 4. Audit Logs

```bash
# Query audit log
docker compose exec web flask shell
>>> from models import AuditLog
>>> AuditLog.query.filter_by(action='login').order_by(AuditLog.created_at.desc()).limit(10).all()
```

## Rollback Procedure

```bash
# 1. Stop services
docker compose down

# 2. Restore database backup
gunzip < /backups/dragonsvault/db_YYYYMMDD_HHMMSS.sql.gz | \
  docker compose exec -T postgres psql -U dvapp dragonsvault

# 3. Checkout previous version
git checkout <previous-commit>

# 4. Rebuild and start
docker compose build
docker compose up -d
```

## Support

- Documentation: [README.md](../README.md)
- Maintenance: [MAINTENANCE.md](../MAINTENANCE.md)
- Security: [SECURITY.md](../SECURITY.md)
- Issues: [GitHub Issues](https://github.com/JBSmith29/DragonsVault/issues)
