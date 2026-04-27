# Troubleshooting Runbook

Common issues and their solutions for DragonsVault.

## Table of Contents

- [Service Health](#service-health)
- [Database Issues](#database-issues)
- [Performance Problems](#performance-problems)
- [Import/Export Issues](#importexport-issues)
- [Authentication Problems](#authentication-problems)
- [Background Jobs](#background-jobs)
- [Cache Issues](#cache-issues)
- [Network & Connectivity](#network--connectivity)

## Service Health

### All Services Down

**Symptoms:**
- Cannot access application
- `docker compose ps` shows all services as "Exited"

**Diagnosis:**

```bash
docker compose ps
docker compose logs --tail=50
```

**Solutions:**

```bash
# Restart all services
docker compose down
docker compose up -d

# Check for port conflicts
sudo netstat -tulpn | grep -E ':(80|443|5000|5432|6379|6432)'

# Check disk space
df -h

# Check Docker daemon
sudo systemctl status docker
```

### Specific Service Won't Start

**Symptoms:**
- One service repeatedly restarting
- Health check failing

**Diagnosis:**

```bash
docker compose logs <service> --tail=100
docker inspect <container_id>
docker compose exec <service> sh  # If container is running
```

**Common Causes:**

1. **Missing environment variable:**
   ```bash
   # Check .env file
   cat .env
   # Verify required vars are set
   docker compose config
   ```

2. **Port already in use:**
   ```bash
   sudo lsof -i :<port>
   # Kill conflicting process or change port
   ```

3. **Database not ready:**
   ```bash
   # Wait for PostgreSQL
   docker compose exec postgres pg_isready -U dvapp
   # Check PgBouncer
   docker compose exec pgbouncer psql -h localhost -p 6432 -U dvapp -d dragonsvault -c "SELECT 1;"
   ```

4. **Out of memory:**
   ```bash
   docker stats
   # Increase memory limits in docker-compose.resources.yml
   ```

### Health Check Failing

**Symptoms:**
- Service shows as "unhealthy" in `docker compose ps`
- `/readyz` endpoint returns 503

**Diagnosis:**

```bash
# Check health endpoint directly
docker compose exec web curl -v http://localhost:5000/readyz

# Check dependencies
curl http://localhost/api/user/v1/ping
curl http://localhost/api/cards/v1/ping
curl http://localhost/api/prices/v1/ping
```

**Solutions:**

```bash
# Restart unhealthy service
docker compose restart <service>

# Check service logs
docker compose logs <service> --tail=100

# Verify database connectivity
docker compose exec web flask shell
>>> from extensions import db
>>> db.session.execute("SELECT 1").scalar()
```

## Database Issues

### Cannot Connect to Database

**Symptoms:**
- "Connection refused" errors
- "FATAL: password authentication failed"

**Diagnosis:**

```bash
# Check PostgreSQL is running
docker compose ps postgres

# Test connection
docker compose exec postgres psql -U dvapp -d dragonsvault -c "SELECT version();"

# Check PgBouncer
docker compose exec pgbouncer psql -h localhost -p 6432 -U dvapp -d dragonsvault -c "SELECT 1;"
```

**Solutions:**

1. **PostgreSQL not ready:**
   ```bash
   docker compose restart postgres
   # Wait 10 seconds
   docker compose exec postgres pg_isready -U dvapp
   ```

2. **Wrong password:**
   ```bash
   # Verify POSTGRES_PASSWORD in .env matches
   echo $POSTGRES_PASSWORD
   # Reset if needed
   docker compose down
   docker volume rm dragonsvault_pgdata
   docker compose up -d postgres
   ```

3. **PgBouncer connection pool exhausted:**
   ```bash
   # Check active connections
   docker compose exec pgbouncer psql -h localhost -p 6432 -U pgbouncer -d pgbouncer -c "SHOW POOLS;"
   
   # Increase pool size in docker-compose.yml
   DEFAULT_POOL_SIZE: 50
   MAX_CLIENT_CONN: 1000
   ```

### Migration Failures

**Symptoms:**
- `flask db upgrade` fails
- "relation already exists" errors

**Diagnosis:**

```bash
# Check current migration version
docker compose exec web flask db current

# Check migration history
docker compose exec web flask db history

# Check for conflicts
docker compose exec postgres psql -U dvapp -d dragonsvault -c "\dt"
```

**Solutions:**

1. **Stamp current version:**
   ```bash
   docker compose exec web flask db stamp head
   ```

2. **Rollback and retry:**
   ```bash
   docker compose exec web flask db downgrade -1
   docker compose exec web flask db upgrade
   ```

3. **Manual fix:**
   ```bash
   # Drop conflicting table
   docker compose exec postgres psql -U dvapp -d dragonsvault -c "DROP TABLE IF EXISTS <table_name> CASCADE;"
   docker compose exec web flask db upgrade
   ```

### Slow Queries

**Symptoms:**
- Pages load slowly
- Database CPU at 100%

**Diagnosis:**

```bash
# Enable slow query logging
docker compose exec postgres psql -U dvapp -d dragonsvault -c "ALTER SYSTEM SET log_min_duration_statement = 1000;"
docker compose restart postgres

# Check active queries
docker compose exec postgres psql -U dvapp -d dragonsvault -c "SELECT pid, now() - query_start AS duration, query FROM pg_stat_activity WHERE state = 'active' ORDER BY duration DESC;"

# Check table sizes
docker compose exec postgres psql -U dvapp -d dragonsvault -c "SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size FROM pg_tables ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC LIMIT 10;"
```

**Solutions:**

1. **Missing indexes:**
   ```bash
   # Check for missing indexes
   docker compose exec postgres psql -U dvapp -d dragonsvault -c "SELECT schemaname, tablename, attname, n_distinct, correlation FROM pg_stats WHERE schemaname = 'public' AND n_distinct > 100 ORDER BY n_distinct DESC LIMIT 20;"
   
   # Add indexes via migration
   docker compose exec web flask db revision -m "add_missing_indexes"
   ```

2. **Vacuum needed:**
   ```bash
   docker compose exec postgres vacuumdb --all --analyze-in-stages
   ```

3. **Too many connections:**
   ```bash
   # Check connection count
   docker compose exec postgres psql -U dvapp -d dragonsvault -c "SELECT count(*) FROM pg_stat_activity;"
   
   # Increase PgBouncer pool
   # Edit docker-compose.yml and restart
   ```

## Performance Problems

### High Memory Usage

**Symptoms:**
- OOM kills
- Swap usage high
- Services restarting

**Diagnosis:**

```bash
# Check container memory
docker stats

# Check host memory
free -h

# Check for memory leaks
docker compose exec web python -c "import psutil; print(psutil.virtual_memory())"
```

**Solutions:**

1. **Increase memory limits:**
   ```yaml
   # docker-compose.resources.yml
   web:
     deploy:
       resources:
         limits:
           memory: 4G
   ```

2. **Reduce worker count:**
   ```bash
   # Edit .env
   WEB_CONCURRENCY=2
   WEB_THREADS=2
   docker compose restart web
   ```

3. **Clear caches:**
   ```bash
   docker compose exec redis redis-cli FLUSHDB
   docker compose exec web flask cache clear
   ```

### High CPU Usage

**Symptoms:**
- CPU at 100%
- Slow response times

**Diagnosis:**

```bash
# Check container CPU
docker stats

# Check processes
docker compose exec web top

# Profile Python code
docker compose exec web python -m cProfile -o profile.stats app.py
```

**Solutions:**

1. **Scale workers:**
   ```bash
   docker compose up -d --scale worker=3
   ```

2. **Optimize queries:**
   ```bash
   # Enable query logging
   # Identify N+1 queries
   # Add eager loading
   ```

3. **Add caching:**
   ```python
   from extensions import cache
   
   @cache.memoize(timeout=300)
   def expensive_function():
       pass
   ```

### Slow Page Loads

**Symptoms:**
- Pages take >5 seconds to load
- Timeouts

**Diagnosis:**

```bash
# Check response times
curl -w "@curl-format.txt" -o /dev/null -s http://localhost/

# Check database queries
# Enable Flask-DebugToolbar in development

# Check cache hit rate
docker compose exec redis redis-cli INFO stats | grep keyspace
```

**Solutions:**

1. **Enable caching:**
   ```bash
   # Verify CACHE_TYPE=RedisCache in .env
   docker compose restart web
   ```

2. **Optimize images:**
   ```bash
   # Use CDN for static assets
   STATIC_ASSET_BASE_URL=https://cdn.example.com/static
   ```

3. **Add pagination:**
   ```python
   # Limit query results
   cards = Card.query.limit(100).all()
   ```

## Import/Export Issues

### CSV Import Fails

**Symptoms:**
- Import hangs
- "Invalid format" errors
- Partial imports

**Diagnosis:**

```bash
# Check worker logs
docker compose logs worker --tail=100

# Check job status
docker compose exec web flask rq info

# Test import manually
docker compose exec web flask shell
>>> from services.import_service import process_csv
>>> process_csv('/path/to/file.csv', user_id=1)
```

**Solutions:**

1. **File encoding issues:**
   ```bash
   # Convert to UTF-8
   iconv -f ISO-8859-1 -t UTF-8 input.csv > output.csv
   ```

2. **Large file timeout:**
   ```bash
   # Increase timeout in .env
   WEB_TIMEOUT=600
   docker compose restart web
   ```

3. **Invalid data:**
   ```bash
   # Check CSV format
   head -n 5 file.csv
   # Verify columns match expected format
   ```

### Export Hangs

**Symptoms:**
- Export never completes
- Browser timeout

**Diagnosis:**

```bash
# Check worker status
docker compose logs worker --tail=50

# Check memory usage
docker stats worker
```

**Solutions:**

1. **Too many cards:**
   ```bash
   # Add pagination to export
   # Export in batches
   ```

2. **Worker not running:**
   ```bash
   docker compose up -d worker
   ```

## Authentication Problems

### Cannot Login

**Symptoms:**
- "Invalid credentials" error
- Redirect loop

**Diagnosis:**

```bash
# Check user exists
docker compose exec web flask shell
>>> from models import User
>>> User.query.filter_by(email='user@example.com').first()

# Check password hash
>>> user.password_hash
```

**Solutions:**

1. **Reset password:**
   ```bash
   docker compose exec web flask reset-password --email user@example.com
   ```

2. **Session issues:**
   ```bash
   # Clear Redis sessions
   docker compose exec redis redis-cli FLUSHDB
   
   # Check SECRET_KEY is set
   echo $SECRET_KEY
   ```

3. **Archived user:**
   ```bash
   docker compose exec web flask shell
   >>> user = User.query.filter_by(email='user@example.com').first()
   >>> user.archived_at = None
   >>> db.session.commit()
   ```

### API Token Not Working

**Symptoms:**
- 401 Unauthorized
- "Invalid token" error

**Diagnosis:**

```bash
# Check token exists
docker compose exec web flask shell
>>> from models import User
>>> user = User.query.filter_by(email='user@example.com').first()
>>> user.api_token_hash

# Test token
curl -H "Authorization: Bearer <token>" http://localhost/api/ops/health
```

**Solutions:**

1. **Regenerate token:**
   ```bash
   docker compose exec web flask shell
   >>> user = User.query.filter_by(email='user@example.com').first()
   >>> token = user.issue_api_token()
   >>> db.session.commit()
   >>> print(token)
   ```

2. **Token expired:**
   ```bash
   # Check token age
   >>> user.api_token_created_at
   # Tokens don't expire by default, but check app logic
   ```

## Background Jobs

### Jobs Not Processing

**Symptoms:**
- Queue depth increasing
- Jobs stuck in "queued" state

**Diagnosis:**

```bash
# Check worker status
docker compose ps worker

# Check queue depth
docker compose exec web flask rq info

# Check Redis
docker compose exec redis redis-cli PING
```

**Solutions:**

1. **Worker not running:**
   ```bash
   docker compose up -d worker
   ```

2. **Worker crashed:**
   ```bash
   docker compose logs worker --tail=100
   docker compose restart worker
   ```

3. **Redis connection lost:**
   ```bash
   docker compose restart redis
   docker compose restart worker
   ```

### Job Failures

**Symptoms:**
- Jobs in "failed" state
- Error in job result

**Diagnosis:**

```bash
# Check failed jobs
docker compose exec web flask rq info

# Get job details
docker compose exec web flask shell
>>> from rq import Queue
>>> from redis import Redis
>>> q = Queue(connection=Redis.from_url('redis://redis:6379/0'))
>>> failed = q.failed_job_registry
>>> for job_id in failed.get_job_ids():
...     job = q.fetch_job(job_id)
...     print(job.exc_info)
```

**Solutions:**

1. **Retry failed jobs:**
   ```bash
   docker compose exec web flask rq requeue-failed
   ```

2. **Clear failed jobs:**
   ```bash
   docker compose exec web flask rq empty-failed
   ```

## Cache Issues

### Stale Data

**Symptoms:**
- Old data displayed
- Changes not reflected

**Diagnosis:**

```bash
# Check cache keys
docker compose exec redis redis-cli KEYS "dv:*"

# Check TTL
docker compose exec redis redis-cli TTL "dv:web:some_key"
```

**Solutions:**

1. **Clear cache:**
   ```bash
   docker compose exec redis redis-cli FLUSHDB
   ```

2. **Clear specific keys:**
   ```bash
   docker compose exec redis redis-cli DEL "dv:web:*"
   ```

3. **Reduce TTL:**
   ```bash
   # Edit .env
   CACHE_DEFAULT_TIMEOUT=300
   docker compose restart web
   ```

### Cache Miss Rate High

**Symptoms:**
- Slow performance
- High database load

**Diagnosis:**

```bash
# Check cache stats
docker compose exec redis redis-cli INFO stats

# Check hit rate
docker compose exec redis redis-cli INFO stats | grep keyspace_hits
```

**Solutions:**

1. **Increase cache size:**
   ```bash
   # Edit docker-compose.yml
   redis:
     command: ["redis-server", "--maxmemory", "2gb"]
   ```

2. **Increase TTL:**
   ```bash
   CACHE_DEFAULT_TIMEOUT=1800
   ```

## Network & Connectivity

### Cannot Reach External APIs

**Symptoms:**
- Scryfall sync fails
- EDHREC data not loading

**Diagnosis:**

```bash
# Test from container
docker compose exec web curl -v https://api.scryfall.com/
docker compose exec web curl -v https://edhrec.com/

# Check DNS
docker compose exec web nslookup api.scryfall.com
```

**Solutions:**

1. **DNS issues:**
   ```bash
   # Add DNS servers to docker-compose.yml
   web:
     dns:
       - 8.8.8.8
       - 8.8.4.4
   ```

2. **Firewall blocking:**
   ```bash
   # Check firewall rules
   sudo iptables -L
   
   # Allow outbound HTTPS
   sudo ufw allow out 443/tcp
   ```

3. **Rate limited:**
   ```bash
   # Check response headers
   curl -I https://api.scryfall.com/
   
   # Add delays between requests
   SCRYFALL_REQUEST_DELAY=2.0
   ```

### Service-to-Service Communication Fails

**Symptoms:**
- "Connection refused" between services
- Microservice timeouts

**Diagnosis:**

```bash
# Check service names resolve
docker compose exec web ping card-data
docker compose exec web curl http://card-data:5000/v1/ping

# Check network
docker network ls
docker network inspect dragonsvault_default
```

**Solutions:**

1. **Services not on same network:**
   ```bash
   docker compose down
   docker compose up -d
   ```

2. **Service not ready:**
   ```bash
   # Check health
   docker compose ps
   
   # Wait for service
   docker compose exec web sh -c 'until curl -f http://card-data:5000/readyz; do sleep 1; done'
   ```

## Getting Help

If you can't resolve the issue:

1. **Collect diagnostics:**
   ```bash
   docker compose ps > diagnostics.txt
   docker compose logs --tail=500 >> diagnostics.txt
   docker stats --no-stream >> diagnostics.txt
   ```

2. **Check documentation:**
   - [README.md](../README.md)
   - [MAINTENANCE.md](../MAINTENANCE.md)
   - [DEPLOYMENT.md](DEPLOYMENT.md)

3. **File an issue:**
   - [GitHub Issues](https://github.com/JBSmith29/DragonsVault/issues)
   - Include diagnostics and steps to reproduce
