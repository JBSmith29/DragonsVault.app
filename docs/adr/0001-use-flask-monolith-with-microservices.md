# ADR 0001: Use Flask Monolith with Microservices Hybrid

## Status
Accepted

## Context
DragonsVault started as a Flask monolith handling all functionality (cards, decks, games, users). As the application grew, we needed to:
- Isolate data-intensive operations (Scryfall sync, pricing)
- Enable independent scaling of services
- Maintain rapid development velocity
- Avoid full microservices complexity

## Decision
We will use a **hybrid architecture**:
- **Flask monolith** (`web` service) handles UI, core business logic, and orchestration
- **Microservices** for specific domains:
  - `card-data`: Scryfall oracle data sync and queries
  - `price-service`: MTGJSON pricing data
  - `edhrec-service`: EDHREC API caching
  - `user-manager`: User authentication (experimental)
  - `folder-service`: Folder/deck management (experimental)
- **Django API** (`django-api`) as experimental migration path at `/api-next`

## Consequences

### Positive
- Monolith provides fast development for core features
- Microservices isolate expensive operations (Scryfall sync, pricing)
- Independent scaling per service
- Shared PostgreSQL database simplifies transactions
- Gradual migration path to full microservices

### Negative
- Increased operational complexity (15+ containers)
- Network latency between services
- Distributed debugging challenges
- Duplicate code between monolith and microservices
- Database coupling limits true service independence

### Mitigation
- Use PgBouncer for connection pooling
- Implement circuit breakers for service calls
- Centralized logging with request IDs
- Health checks and readiness probes
- Shared code via Python packages (future)

## Alternatives Considered

### Full Monolith
- **Pros**: Simplest deployment, no network overhead
- **Cons**: Cannot scale Scryfall sync independently, single point of failure
- **Rejected**: Data sync operations block web requests

### Full Microservices
- **Pros**: Maximum isolation and scalability
- **Cons**: High complexity, distributed transactions, eventual consistency
- **Rejected**: Overkill for current scale, slows development

### Serverless Functions
- **Pros**: Auto-scaling, pay-per-use
- **Cons**: Cold starts, vendor lock-in, complex local development
- **Rejected**: Not suitable for long-running Scryfall sync

## References
- [Monolith First](https://martinfowler.com/bliki/MonolithFirst.html) by Martin Fowler
- [Microservices Trade-Offs](https://martinfowler.com/articles/microservice-trade-offs.html)
