# ADR 0003: Use RQ for Background Jobs

## Status
Accepted

## Context
DragonsVault has long-running tasks that should not block web requests:
- Scryfall bulk data sync (5-10 minutes)
- CSV imports (1-5 minutes for large files)
- Deck statistics computation
- Email sending
- Cache warming

We need a task queue that is:
- Simple to deploy and operate
- Reliable (retries, failure handling)
- Observable (job status, queue depth)
- Compatible with Flask

## Decision
We will use **RQ (Redis Queue)** with Redis as the broker.

### Architecture
- **Web service** enqueues jobs via `rq.Queue`
- **Worker service** processes jobs from Redis
- **Scheduler service** enqueues periodic jobs (weekly refresh)
- **Redis DB 0** stores job data and results

### Job Types
- `default` queue: Imports, stats, cache refresh
- Future: `high` queue for priority jobs, `low` queue for batch operations

## Consequences

### Positive
- Simple Python API (`@job` decorator)
- Redis is already required (caching, rate limiting)
- Built-in retry logic and failure handling
- RQ Dashboard for monitoring (optional)
- Synchronous fallback for testing (`IMPORT_RUN_INLINE=1`)

### Negative
- Redis is a single point of failure (no HA by default)
- No built-in scheduling (requires separate scheduler service)
- Limited to Python (cannot call other languages)
- No distributed tracing out of the box

### Mitigation
- Use Redis persistence (`--save 60 1`)
- Implement health checks for worker service
- Add request IDs to job context for tracing
- Monitor queue depth via `/metrics` endpoint
- Document job retry policies

## Job Patterns

### Fire-and-Forget
```python
from services.task_queue import enqueue_job
enqueue_job('services.import_service.process_csv', file_path, user_id)
```

### Wait for Result
```python
job = enqueue_job('services.stats.compute_deck_stats', deck_id)
result = job.result  # Blocks until complete
```

### Scheduled Jobs
```python
# In scheduler service
schedule.every().sunday.at("00:00").do(enqueue_scryfall_refresh)
```

## Alternatives Considered

### Celery
- **Pros**: Feature-rich, distributed, multiple brokers (Redis, RabbitMQ)
- **Cons**: Complex configuration, heavyweight, overkill for our scale
- **Rejected**: Too complex for current needs

### APScheduler
- **Pros**: In-process, no external dependencies
- **Cons**: Not distributed, no persistence, lost on restart
- **Rejected**: Cannot scale across multiple workers

### AWS SQS + Lambda
- **Pros**: Serverless, auto-scaling, managed
- **Cons**: Vendor lock-in, cold starts, complex local dev
- **Rejected**: Requires AWS, not self-hostable

### Dramatiq
- **Pros**: Modern, type-safe, good performance
- **Cons**: Less mature, smaller community
- **Rejected**: RQ is simpler and sufficient

## Monitoring

### Metrics
- Queue depth: `rq info` or `/metrics?format=json`
- Job success/failure rates: RQ Dashboard
- Worker health: Health check endpoint

### Alerts
- Queue depth > 100: Workers may be stuck
- Worker down: No heartbeat for 5 minutes
- Job failure rate > 10%: Investigate errors

## References
- [RQ Documentation](https://python-rq.org/)
- [Redis Persistence](https://redis.io/docs/management/persistence/)
- [Background Jobs Best Practices](https://www.honeybadger.io/blog/background-jobs-best-practices/)
