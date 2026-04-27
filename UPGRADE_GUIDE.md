# Upgrade Guide

This guide helps you upgrade your existing DragonsVault installation with the latest improvements.

## Overview

Recent improvements include:
- Secure secrets management
- API documentation (OpenAPI/Swagger)
- Test coverage reporting
- Comprehensive documentation
- Security scanning
- Resource limits for Docker

## Prerequisites

- Existing DragonsVault installation
- Git access to repository
- Docker and Docker Compose
- Backup of your data (recommended)

## Upgrade Steps

### 1. Backup Your Data

**Database backup:**

```bash
# PostgreSQL
docker compose exec postgres pg_dump -U dvapp dragonsvault | gzip > backup_$(date +%Y%m%d).sql.gz

# SQLite
cp instance/dragonsvault.db backup_$(date +%Y%m%d).db
```

**Environment backup:**

```bash
cp .env .env.backup
```

### 2. Pull Latest Changes

```bash
git fetch origin
git pull origin main
```

### 3. Update Environment Configuration

**Create secrets directory:**

```bash
mkdir -p .secrets
chmod 700 .secrets
```

**Generate new secrets (if not already set):**

```bash
# Flask secret key
python -c "import secrets; print(secrets.token_hex(32))" > .secrets/secret_key

# Django secret key
python -c "import secrets; print(secrets.token_urlsafe(50))" > .secrets/django_secret_key

# Set permissions
chmod 600 .secrets/*
```

**Update .env file:**

If you have an existing `.env` file, compare it with `.env.example`:

```bash
# Review differences
diff .env .env.example

# Update your .env with any new required variables
```

**Required variables in .env:**

```bash
POSTGRES_PASSWORD=your_secure_password_here
DATABASE_URL=postgresql+psycopg2://dvapp:${POSTGRES_PASSWORD}@pgbouncer:6432/dragonsvault
DJANGO_SECRET_KEY=your_django_secret_key_here
DJANGO_ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
```

### 4. Install Development Dependencies (Optional)

If you're developing:

```bash
pip install -r backend/requirements-dev.txt
```

### 5. Update Pre-commit Hooks (Optional)

If you use pre-commit:

```bash
pre-commit install
pre-commit autoupdate
```

### 6. Rebuild Docker Images

```bash
docker compose build --no-cache
```

### 7. Run Database Migrations

```bash
docker compose run --rm web flask db upgrade
```

### 8. Restart Services

```bash
docker compose down
docker compose up -d
```

### 9. Verify Upgrade

**Check service health:**

```bash
docker compose ps
curl http://localhost/healthz
curl http://localhost/readyz
```

**Test new features:**

```bash
# API documentation
open http://localhost/api/docs

# Check metrics
curl http://localhost/metrics

# Verify services
curl http://localhost/api/ops/health
```

### 10. Run Tests (Optional)

```bash
# Run test suite with coverage
docker compose run --rm web pytest --cov=backend --cov-report=html

# View coverage report
open htmlcov/index.html
```

## New Features Available

### 1. API Documentation

Visit `http://localhost/api/docs` to see interactive API documentation powered by Swagger UI.

### 2. Test Coverage Reports

Run tests with coverage:

```bash
pytest --cov=backend --cov-report=html
```

View report at `htmlcov/index.html`.

### 3. Security Scanning

Run security audit:

```bash
pip-audit -r backend/requirements.txt
```

### 4. Resource Limits

Apply resource limits (optional):

```bash
docker compose -f docker-compose.yml -f docker-compose.resources.yml up -d
```

### 5. Documentation

New documentation available:
- [Database Schema](docs/DATABASE_SCHEMA.md)
- [Deployment Guide](docs/DEPLOYMENT.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Architecture Decisions](docs/adr/)

## Configuration Changes

### Environment Variables

**New optional variables:**

```bash
# Coverage threshold (default: 60)
PYTEST_COV_FAIL_UNDER=60

# API documentation (enabled by default)
ENABLE_API_DOCS=1
```

### Docker Compose

**Resource limits (optional):**

Create `docker-compose.override.yml` to customize:

```yaml
services:
  web:
    deploy:
      resources:
        limits:
          cpus: '4.0'
          memory: 4G
```

## Troubleshooting

### Migration Fails

```bash
# Check current version
docker compose exec web flask db current

# Stamp current version if needed
docker compose exec web flask db stamp head

# Retry upgrade
docker compose exec web flask db upgrade
```

### Services Won't Start

```bash
# Check logs
docker compose logs web --tail=100

# Verify environment
docker compose config

# Check for port conflicts
sudo netstat -tulpn | grep -E ':(80|443|5000|5432|6379)'
```

### Database Connection Issues

```bash
# Check PostgreSQL
docker compose exec postgres pg_isready -U dvapp

# Check PgBouncer
docker compose exec pgbouncer psql -h localhost -p 6432 -U dvapp -d dragonsvault -c "SELECT 1;"

# Verify DATABASE_URL
echo $DATABASE_URL
```

### Permission Issues

```bash
# Fix secrets permissions
chmod 700 .secrets
chmod 600 .secrets/*

# Fix instance directory
sudo chown -R $USER:$USER instance/
```

## Rollback Procedure

If you need to rollback:

### 1. Stop Services

```bash
docker compose down
```

### 2. Restore Code

```bash
git checkout <previous-commit>
```

### 3. Restore Database

**PostgreSQL:**

```bash
gunzip < backup_YYYYMMDD.sql.gz | docker compose exec -T postgres psql -U dvapp dragonsvault
```

**SQLite:**

```bash
cp backup_YYYYMMDD.db instance/dragonsvault.db
```

### 4. Restore Environment

```bash
cp .env.backup .env
```

### 5. Restart Services

```bash
docker compose up -d
```

## Post-Upgrade Tasks

### 1. Update Documentation

If you have custom documentation, update it to reference new docs.

### 2. Configure Monitoring

Set up monitoring for new metrics:

```bash
# Prometheus scraping
curl http://localhost/metrics

# Health checks
curl http://localhost/readyz
```

### 3. Review Security Scan Results

```bash
# Run security audit
pip-audit -r backend/requirements.txt

# Check for secrets
pre-commit run gitleaks --all-files
```

### 4. Update CI/CD

If you have custom CI/CD, integrate new security workflow:

```yaml
# .github/workflows/security.yml is now available
```

## Getting Help

If you encounter issues:

1. Check [Troubleshooting Guide](docs/TROUBLESHOOTING.md)
2. Review [Deployment Guide](docs/DEPLOYMENT.md)
3. Check logs: `docker compose logs --tail=100`
4. File an issue: [GitHub Issues](https://github.com/JBSmith29/DragonsVault/issues)

## Verification Checklist

After upgrade, verify:

- [ ] All services running: `docker compose ps`
- [ ] Health checks passing: `curl http://localhost/readyz`
- [ ] Database accessible: `docker compose exec web flask shell`
- [ ] API docs available: `http://localhost/api/docs`
- [ ] Tests passing: `pytest`
- [ ] Coverage reporting: `pytest --cov=backend`
- [ ] Security scan clean: `pip-audit -r backend/requirements.txt`
- [ ] No secrets in repo: `pre-commit run gitleaks --all-files`

## Next Steps

1. Review new documentation in `docs/`
2. Set up production deployment using [Deployment Guide](docs/DEPLOYMENT.md)
3. Configure monitoring and alerting
4. Run security scans regularly
5. Improve test coverage to 70%+

## Support

- Documentation: [README.md](README.md)
- Deployment: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
- Troubleshooting: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- Issues: [GitHub Issues](https://github.com/JBSmith29/DragonsVault/issues)
