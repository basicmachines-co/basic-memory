"""Read-cache retention policy.

Entries are retired explicitly when their group changes, so these TTLs bound memory,
not freshness. A note nobody edits stays cached for a week.
"""

READ_CACHE_TTL_SECONDS = 7 * 24 * 3600
SEARCH_READ_CACHE_TTL_SECONDS = 24 * 3600
# Recent activity is relative to "now", so an unchanged project still ages out of
# its window; keep it short.
RECENT_ACTIVITY_READ_CACHE_TTL_SECONDS = 15 * 60
