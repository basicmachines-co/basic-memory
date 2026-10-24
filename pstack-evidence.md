# pstack evidence — basic-memory #1850

## Principles applied
- Laziness Protocol: batch the existing OR-chain delete; no API change.
- Prove It Works: regression deletes 1200 pending chunks and asserts both tables empty.
- Make Operations Idempotent: batches preserve pending-only match semantics.

## Repro → fix
Unbatched OR of 4-term ANDs exceeds SQLITE_MAX_EXPR_DEPTH (~995). `_DELETE_BATCH_SIZE = 200` splits UPDATE/DELETE work; one commit after all batches.
