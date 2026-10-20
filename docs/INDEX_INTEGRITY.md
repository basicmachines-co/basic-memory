# Inspecting existing project data

`basic-memory doctor` checks existing projects alongside its disposable
file/database smoke test. Use `basic-memory doctor --project <name-or-UUID>` to
limit the integrity inspection to one project. Local routing remains the default.

For every selected project, doctor reports:

- Entity count and search row counts split into entities, observations and relations.
- Duplicate `(project_id, type, id)` keys and the number of extra copies.
- Search rows without a matching backing object and owning entity in the same project.
- Entities without their entity search row (the entity is unsearchable).
- Inverted `updated_at`/`created_at` timestamps as an informational count only.

Search rows normally outnumber entities because observations and relations have
their own rows. An unresolved relation target is valid and is not an orphan.
At most 20 examples are returned per project; counts include all matching rows.
Inverted timestamps are never treated as corruption: without frontmatter dates the
parser falls back to the file's ctime and mtime, and restoring or copying a vault
with preserved mtimes legitimately produces `updated_at` before `created_at`.

Observed anomalies produce a nonzero exit code, but they no longer prevent the
disposable smoke test from running: doctor reports the anomalies, still verifies
the file/database pipeline, and only then exits nonzero. Database failures and
missing search tables are errors, never healthy results. Indexing is eventually
consistent: an anomaly observed during indexing may be transient. Wait for
indexing to finish and rerun doctor before deciding whether repair is necessary.
Doctor does not repair existing project data.

The same read-only report is available at
`GET /v2/projects/{project_external_id}/integrity` and through
`ProjectClient.get_integrity()`. The route uses the existing project resolution
dependency and supports SQLite and PostgreSQL. It does not initialize the search
index, purge rows, or acquire synchronization locks.
