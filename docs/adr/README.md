# Architecture Decision Records (ADRs)

This directory contains Architecture Decision Records (ADRs) for DragonsVault.

## What is an ADR?

An ADR is a document that captures an important architectural decision made along with its context and consequences.

## Format

Each ADR follows this structure:

1. **Title**: Short noun phrase (e.g., "Use PostgreSQL for Primary Database")
2. **Status**: Proposed, Accepted, Deprecated, Superseded
3. **Context**: What is the issue we're facing?
4. **Decision**: What did we decide to do?
5. **Consequences**: What are the positive and negative outcomes?
6. **Alternatives Considered**: What other options did we evaluate?
7. **References**: Links to relevant resources

## Index

- [ADR-0001](0001-use-flask-monolith-with-microservices.md) - Use Flask Monolith with Microservices Hybrid
- [ADR-0002](0002-use-postgresql-with-sqlite-fallback.md) - Use PostgreSQL with SQLite Fallback
- [ADR-0003](0003-use-rq-for-background-jobs.md) - Use RQ for Background Jobs

## Creating a New ADR

1. Copy the template below
2. Number it sequentially (e.g., `0004-my-decision.md`)
3. Fill in all sections
4. Submit a pull request
5. Update this index

## Template

```markdown
# ADR XXXX: [Title]

## Status
[Proposed | Accepted | Deprecated | Superseded by ADR-YYYY]

## Context
[Describe the issue, including technical, political, social, and project context]

## Decision
[Describe the decision and its rationale]

## Consequences

### Positive
- [Benefit 1]
- [Benefit 2]

### Negative
- [Drawback 1]
- [Drawback 2]

### Mitigation
- [How we address drawback 1]
- [How we address drawback 2]

## Alternatives Considered

### [Alternative 1]
- **Pros**: [Benefits]
- **Cons**: [Drawbacks]
- **Rejected**: [Why]

### [Alternative 2]
- **Pros**: [Benefits]
- **Cons**: [Drawbacks]
- **Rejected**: [Why]

## References
- [Link 1]
- [Link 2]
```

## Resources

- [Documenting Architecture Decisions](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions) by Michael Nygard
- [ADR GitHub Organization](https://adr.github.io/)
- [When Should I Write an ADR?](https://engineering.atspotify.com/2020/04/when-should-i-write-an-architecture-decision-record/)
