# Contributing

## Before every push

1. `make check` — lint + smoke tests must be green
2. If you added a route, add a test for it
3. If you fixed a bug, add a test that would have caught it
4. If you added a table, add it to tests/test_db.py

## No going around the gates

- Do not push directly to main without `make check` passing
- Do not merge a PR with a failing CI check
- If CI is broken, fix CI first — don't bypass

## Common sense

- Small commits with clear messages
- Don't commit commented-out code
- Don't commit `print()` debug statements
- Don't commit credentials
