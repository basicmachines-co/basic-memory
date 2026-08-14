# Transaction Isolation And Concurrency

Basic Memory's PostgreSQL code assumes the database's default **Read Committed** isolation level.
SQLite remains a supported local/test backend, but its single-writer behavior is not evidence that
a PostgreSQL row-lock protocol is correct. Any design that depends on concurrent row behavior must
have a PostgreSQL regression test.

This document records the specific guarantees repository code may use. It is not permission to add
broad locks: optimistic compare-and-swap remains the default, and canonical note safety is the main
case where a narrow lock is justified.

## Read Committed Statement Model

Under PostgreSQL Read Committed:

- Each command sees rows committed before that command began, plus changes already made by its own
  transaction. Two commands in one transaction can therefore observe different committed state.
- `UPDATE`, `DELETE`, and locking `SELECT` statements wait when a target row is concurrently
  updated, deleted, or locked.
- After the wait, PostgreSQL applies the operation to the current row version and re-evaluates the
  command's `WHERE` predicate. If the row was deleted or no longer matches, the command affects no
  row.
- Row locks last until transaction end. `FOR UPDATE` conflicts with the weaker `FOR KEY SHARE`
  lock used to protect referenced keys, which is relevant when a child row is inserted through a
  foreign key.

The authoritative references are PostgreSQL's
[Transaction Isolation](https://www.postgresql.org/docs/current/transaction-iso.html) and
[Explicit Locking](https://www.postgresql.org/docs/current/explicit-locking.html) chapters.

Read Committed is deliberately not a transaction-wide snapshot. Never assume an earlier plain
`SELECT` still describes the row when a later statement writes it.

## Preferred Guarded-Write Pattern

Put the expected lineage in the write itself and inspect whether it matched:

```sql
UPDATE note_content
SET file_write_status = 'writing',
    last_materialization_attempt_at = :attempted_at
WHERE project_id = :project_id
  AND entity_id = :entity_id
  AND db_version = :db_version
  AND db_checksum = :db_checksum
RETURNING markdown_content, file_checksum;
```

This is a compare-and-swap claim, not a read followed by an ORM flush. If a concurrent delete wins,
the statement returns no row. If the claim wins, its NoteContent row lock remains held until commit,
and a later delete must observe the changed materialization lineage. Expected contention is returned
as a typed missing or stale outcome; it is not an ORM `StaleDataError` control path.

Use this pattern when one statement can name the invariant. Do not add a pre-read lock merely to
make a later unconditional write appear safe.

## Canonical Lock Order

When a transaction must touch accepted `NoteContent` and then mutate or delete its `Entity` or
relations, it acquires locks in this order:

1. Lock every relevant `NoteContent` row, sorted by `entity_id`.
2. Lock the corresponding `Entity` rows, also in stable order, only when the operation needs an
   explicit entity fence.
3. Mutate Entity, search, relation, or vector projection rows.

The shared entry point is
`lock_note_content_before_entity_mutation` in
`src/basic_memory/repository/relation_repository.py`. Reversing this order can deadlock accepted
writes, materialization, relation publication, and cascading deletes.

The Entity lock matters for a legacy indexed row with no `NoteContent`. A concurrent accepted write
can bootstrap that child row. Locking the referenced Entity makes PostgreSQL serialize the foreign
key insertion; a subsequent Read Committed statement then either sees the committed `NoteContent`
and preserves it, or the inserter waits until deletion completes and cannot accept content for a
missing Entity.

## External Storage Is Outside The Transaction

Object storage and the local filesystem cannot participate in the database transaction. Project
index deletion therefore uses three explicit phases:

1. Snapshot only valid DB delete candidates: storage-owned entities with no `NoteContent`, or notes
   where status is `synced`, `file_version == db_version`, and
   `file_checksum == db_checksum`.
2. Outside a DB transaction, positively verify that each candidate path is still absent from the
   current storage backend. A scan listing alone is not sufficient.
3. Lock NoteContent then Entity, reload the exact candidate lineage in a new Read Committed
   statement, and delete only candidates equal to the snapshot.

The materialization-attempt timestamp is part of a synchronized note candidate. It prevents an ABA
window where a same-version materialization leaves `synced`, writes the file, and returns to
`synced` while the storage absence probe is in flight.

Do not hold database locks across the storage probe. Candidate equality bridges the two systems
without a distributed transaction and keeps transient projection lag eventually consistent.

## Review Checklist

For a concurrency-sensitive persistence change:

- Name the canonical invariant and the exact row(s) that protect it.
- Prefer a conditional write with row-count or `RETURNING` evidence.
- If a lock is necessary, preserve NoteContent-before-Entity order and keep the locked set narrow.
- Do not infer PostgreSQL safety from SQLite.
- Add a PostgreSQL test for both winner orderings and use timeouts so a lock cycle fails loudly.
- Keep network and filesystem I/O outside database transactions.
