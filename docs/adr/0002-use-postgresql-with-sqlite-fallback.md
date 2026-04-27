# ADR 0002: Use PostgreSQL with SQLite Fallback

## Status
Accepted

## Context
DragonsVault needs a database that supports:
- Complex queries (joins, aggregations, full-text search)
- Concurrent writes from multiple services
- ACID transactions
- JSON data storage
- Easy local development

## Decision
We will use **PostgreSQL as the primary database** with **SQLite as a fallback** for:
- Local development without Docker
- Testing (pytest fixtures)
- Single-user deployments

All code must be compatible with both databases.

## Consequences

### Positive
- PostgreSQL provides production-grade performance and features
- SQLite enables zero-config local development
- PgBouncer connection pooling reduces connection overhead
- Alembic migrations work with both databases (batch mode for SQLite)
- Full-text search via PostgreSQL FTS or SQLite FTS5

### Negative
- Must avoid PostgreSQL-specific features (JSONB operators, arrays)
- SQLite has limited concurrency (single writer)
- Different SQL dialects require careful testing
- Migration complexity (batch mode for SQLite)

### Mitigation
- Use SQLAlchemy ORM to abstract database differences
- Test migrations against both databases
- Document PostgreSQL-only features (if used)
- Use `batch_alter_table` for SQLite compatibility

## Database Schema Strategy

### Shared Schema (`public`)
- Core tables (users, folders, cards, games)
- Managed by Alembic migrations
- Accessed by monolith and microservices

### Service Schemas
- `card_data`: Oracle-level Scryfall data
- `price_service`: MTGJSON pricing cache
- `edhrec_service`: EDHREC API cache
- `user_manager`: User service (future)
- `folder_service`: Folder service (future)

### Schema Isolation
- Services use `search_path` to isolate schemas
- Prevents accidental cross-service queries
- Enables independent schema evolution

## Alternatives Considered

### PostgreSQL Only
- **Pros**: Simplest, best performance, full feature set
- **Cons**: Requires Docker for local dev, harder to test
- **Rejected**: Slows local development iteration

### MySQL/MariaDB
- **Pros**: Widely used, good performance
- **Cons**: Less advanced features (JSON, FTS), different SQL dialect
- **Rejected**: PostgreSQL has better JSON and FTS support

### MongoDB
- **Pros**: Flexible schema, horizontal scaling
- **Cons**: No ACID transactions (pre-4.0), eventual consistency, learning curve
- **Rejected**: MTG data is highly relational

### SQLite Only
- **Pros**: Zero config, embedded, fast for reads
- **Cons**: Single writer, limited concurrency, no network access
- **Rejected**: Cannot support multi-user production deployment

## References
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [SQLite When To Use](https://www.sqlite.org/whentouse.html)
- [SQLAlchemy Dialects](https://docs.sqlalchemy.org/en/20/dialects/)
