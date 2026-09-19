# Contributing

Thanks for taking an interest in Game of the Day.

The project favors small, reviewable changes with explicit behavior and failure modes over broad rewrites.

## Before opening a change

1. Check existing issues and architecture documentation.
2. Keep migration, refactoring, and product behavior changes separate when practical.
3. Do not add infrastructure, agents, frameworks, or model-routing layers without a concrete problem they solve.
4. Never include production identifiers, credentials, user data, or private operational logs.

## Engineering expectations

Changes should preserve the project's core boundaries:

- deterministic application code owns state, validation, retries, recovery, and side effects;
- AI output is treated as untrusted structured input and validated before use;
- raw feedback is durable before optional AI processing;
- externally visible actions are designed for safe retries and idempotency;
- transport-specific code should not own product state;
- failure paths should be observable and recoverable.

## Pull requests

Prefer focused pull requests with:

- a clear problem statement;
- the chosen approach and important trade-offs;
- tests or reproducible validation steps;
- migration/rollback notes when state or runtime behavior changes;
- documentation updates when contracts or architecture change.

Avoid mixing unrelated cleanup with behavior changes.

## Testing

Run the relevant local tests and contract checks before submitting a change. For infrastructure or migration work, distinguish between local validation, deployment readiness, and production verification.

The feedback storage contour has a stdlib-only regression suite that runs against temporary SQLite databases and refuses to touch any other path:

```bash
python3 -m unittest discover -s tests
```

Never use real production data in committed fixtures.

## Security and privacy

Read [`SECURITY.md`](SECURITY.md) before submitting operational or deployment changes. Public examples must use placeholders and synthetic data.

## Documentation language

Public repository documentation, issue titles/bodies, code comments intended for maintainers, and contribution material should be written in English.
