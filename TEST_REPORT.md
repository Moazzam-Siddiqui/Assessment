# Test report

**Status:** implementation and test harness committed; actual LLM-based v1/v2 runs require an API key and must be performed by the reviewer/operator.

## Test methodology

The expected outcome is grounded in the assignment's FAQ and mock orders. Intent classification is checked against a small hand-labelled set. Tests focus on safety invariants: order ownership, schema-valid fallback, ticket de-duplication, and draft-only behavior.

## Run

```bash
pytest -q
```

The assignment asks for v1 on at least 10 emails and v2 on all 25. Do not claim those model runs have been completed until they are run with the configured provider; fill in measured intent/action accuracy and JSON validity after running. The CLI accepts `--version v1` or `--version v2` and stores the version in each audit row.
