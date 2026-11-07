"""Read-cache retention policy.

Entries are retired explicitly when their group changes, so these TTLs bound memory,
not freshness. A note nobody edits stays cached for a week.
"""

READ_CACHE_TTL_SECONDS = 7 * 24 * 3600
SEARCH_READ_CACHE_TTL_SECONDS = 24 * 3600
# Recent activity is relative to "now", so an unchanged project still ages out of
# its window; keep it short.
RECENT_ACTIVITY_READ_CACHE_TTL_SECONDS = 15 * 60
# Project resolve is retired by the project routes, but a host can also create,
# rename or delete projects outside them (provisioning, restores). A short TTL
# bounds how long such a change can go unseen.
PROJECT_RESOLVE_READ_CACHE_TTL_SECONDS = 10 * 60
# Larger responses are served but not stored, so a few huge notes or listings cannot
# crowd hot entries out of Redis for the length of these TTLs.
READ_CACHE_MAX_PAYLOAD_BYTES = 1024 * 1024
