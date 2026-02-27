# Phase 02: Chat App / Agent Integration

## Goal

Connect your chat application's agent to the Basic Memory sidecar on Railway. The agent uses MCP tools to read, write, and search the shared knowledge base. Per-user preferences are scoped by directory convention.

## Prerequisites

- Phase 00 complete: Railway sidecar running, accessible on internal network
- Phase 01 complete: R2 sync working between local and Railway
- A chat application (or agent framework) that can make HTTP requests

---

## Step 1: Understand the Connection

The Basic Memory sidecar runs an MCP server over SSE (Server-Sent Events) on port 8000. Your chat app connects to it as an MCP client.

```
Chat App / Agent
    │
    │  MCP over SSE (HTTP)
    │  http://basic-memory-sidecar.railway.internal:8000/sse
    │
    ▼
Basic Memory Sidecar
    │
    │  (auto-indexes, searches, builds context)
    │
    ▼
Markdown Files ←──rclone bisync──→ R2 ←──rclone bisync──→ Local
```

### Connection details:

- **Protocol**: SSE (Server-Sent Events) — standard MCP transport
- **URL**: `http://<railway-service>:8000/sse` (internal) or `https://<railway-url>/sse` (public)
- **Auth**: None by default. If exposing publicly, add auth at the Railway/proxy level
- **No API key needed**: The sidecar runs in LOCAL mode (`BASIC_MEMORY_FORCE_LOCAL=true` is set automatically for SSE transport — see `src/basic_memory/cli/commands/mcp.py` lines 48-61)

### If your agent framework supports MCP natively:

Configure it as an MCP server connection:
```json
{
  "mcpServers": {
    "basic-memory": {
      "transport": "sse",
      "url": "http://basic-memory-sidecar.railway.internal:8000/sse"
    }
  }
}
```

### If your agent framework uses HTTP/REST:

Basic Memory also exposes a FastAPI REST API. The MCP tools are wrappers around these endpoints. You can call the API directly:

```
GET  /v2/projects                                    → list projects
POST /v2/projects/{id}/knowledge/entities             → create note
GET  /v2/projects/{id}/knowledge/entities/{entity_id} → read note
PATCH /v2/projects/{id}/knowledge/entities/{entity_id} → edit note
GET  /v2/projects/{id}/search?query=...               → search
GET  /v2/projects/{id}/memory/context?url=memory://...→ build context
```

The API is the same layer that MCP tools use internally (via ASGI transport).

---

## Step 2: Set Up Knowledge Directory Structure

If not already created in Phase 00:

```bash
# On Railway sidecar (or let the agent create these via write_note)
mkdir -p /app/data/shared/notes
mkdir -p /app/data/shared/users
mkdir -p /app/data/shared/conversations
mkdir -p /app/data/shared/decisions
```

### Directory purposes:

| Directory | Purpose | Who writes |
|-----------|---------|------------|
| `notes/` | Shared knowledge — product info, troubleshooting, guides | Agent (from any user's conversation) |
| `users/` | Per-user preferences and context | Agent (scoped to specific user) |
| `conversations/` | Conversation summaries worth keeping | Agent (after significant conversations) |
| `decisions/` | Important decisions with rationale | Agent or human |

---

## Step 3: Configure Agent System Prompt

Add Basic Memory instructions to your agent's system prompt. Adapt based on your agent framework:

```
You have access to a shared knowledge base via Basic Memory MCP tools.

## Knowledge Base Usage

**At conversation start:**
1. Read user preferences: read_note("users/{username}")
   - If the note exists, follow the user's preferences
   - If not found, proceed with defaults

**During conversation:**
2. Search before creating: search_notes("relevant topic") before writing new notes
3. Build context when needed: build_context("memory://notes/topic", depth=2)

**When you learn something worth keeping:**
4. Write shared knowledge to "notes/" directory:
   write_note(
     title="Descriptive Title",
     content="# Title\n\n## Observations\n- [category] fact #tag\n\n## Relations\n- relates_to [[Other Note]]",
     directory="notes"
   )

**After significant conversations:**
5. Summarize to "conversations/" directory (only if the conversation produced lasting insights)

**For user-specific preferences:**
6. Write to "users/" directory:
   write_note(
     title="{username}",
     content="# {username}\n\n- [preference] User prefers concise answers\n- [preference] Works in Python primarily",
     directory="users"
   )

## Observation Categories
Use these categories in observations:
- [fact] — verified information
- [decision] — choices made with rationale
- [preference] — user-specific preferences
- [technique] — how-to knowledge
- [issue] — known problems
- [idea] — suggestions for future work

## Relations
Link related notes with WikiLinks:
- relates_to [[Other Note]]
- depends_on [[Prerequisite]]
- implements [[Specification]]
- part_of [[Larger Topic]]

## Important Rules
- Search before creating to avoid duplicates
- Use descriptive titles (they become the filename)
- Include 3-5 observations per note
- Include relations to connect knowledge
- Don't save trivial conversation details — focus on lasting insights
```

---

## Step 4: Implement User Scoping

The agent identifies users by whatever mechanism your chat app uses (login, session, username). Pass the username to the agent context so it can read/write user-specific notes.

### Reading user preferences:

```python
# At conversation start, the agent calls:
user_prefs = await read_note(f"users/{username}")
# Returns the user's preference note, or "not found" error
```

### Writing user preferences:

```python
# When the agent learns a user preference:
await write_note(
    title=username,
    content=f"# {username}\n\n- [preference] {preference_text}",
    directory="users"
)
```

### Example user note (`users/alice.md`):

```markdown
---
title: alice
type: note
permalink: users/alice
---

# alice

- [preference] Prefers detailed technical explanations #communication
- [preference] Primary language: Python #tech
- [preference] Timezone: PST #scheduling
- [context] Onboarded 2026-02-26
```

---

## Step 5: Test the Integration

### Test 1: Agent writes a shared note

Have a user ask the agent something that produces knowledge. Verify:

```bash
# On Railway:
basic-memory tool search-notes --query "the topic discussed"
# Should find the note the agent created

# Verify file exists:
ls /app/data/shared/notes/
```

### Test 2: Agent reads existing knowledge

Create a note manually, then ask the agent about that topic:

```bash
# Create a note via CLI on Railway:
basic-memory tool write-note \
  --title "Product FAQ" \
  --content "# Product FAQ\n\n- [fact] Free trial is 14 days #pricing\n- [fact] Supports up to 10 team members on starter plan #pricing" \
  --directory "notes"
```

Ask the agent: "What's the free trial length?" — it should search and find the answer.

### Test 3: User preferences work

```bash
# Create a user preference note:
basic-memory tool write-note \
  --title "alice" \
  --content "# alice\n\n- [preference] Always respond in bullet points" \
  --directory "users"
```

Start a conversation as alice — the agent should read preferences and adjust its style.

### Test 4: Sync reaches local

After the agent creates notes on Railway:

```bash
# Trigger sync (or wait for cron)
bm-sync  # your local alias from Phase 01

# Check locally
ls ~/basic-memory/notes/
cat ~/basic-memory/notes/product-faq.md
```

---

## Step 6: Monitor and Iterate

### Check Basic Memory health:

```bash
# On Railway:
basic-memory status
basic-memory doctor  # full consistency check
```

### Check sync status:

```bash
# Verify files match between local and R2
rclone check ~/basic-memory r2:basic-memory-sync/shared \
  --filter-from ~/.config/rclone/bm-sync-filter.txt
```

### Review knowledge quality:

Periodically browse the knowledge base to ensure the agent is writing useful, well-structured notes:

```bash
# List all notes
basic-memory tool list-directory --dir-name "notes" --depth 2

# Check recent activity
basic-memory tool recent-activity --timeframe "1 week"
```

---

## Verification Checklist

- [ ] Chat app connects to sidecar MCP endpoint (SSE or REST)
- [ ] Agent can call `list_memory_projects()` and see the "shared" project
- [ ] Agent can `write_note()` — creates a markdown file on Railway volume
- [ ] Agent can `search_notes()` — finds existing knowledge
- [ ] Agent can `read_note()` — retrieves specific notes
- [ ] Agent can `build_context()` — traverses knowledge graph
- [ ] User preferences: agent reads `users/{name}` at conversation start
- [ ] User preferences: agent writes preferences when discovered
- [ ] Shared knowledge: agent writes to `notes/` after learning something
- [ ] Sync: agent-created notes appear locally after sync
- [ ] Sync: locally-created notes are findable by the agent after sync
- [ ] `basic-memory doctor` passes on Railway with no errors

## Security Considerations

- **Internal networking**: Keep the sidecar on Railway's internal network if possible. Only the chat app needs access.
- **No sensitive data**: Don't store API keys, passwords, or PII in notes. The knowledge base is for product knowledge and user preferences only.
- **R2 bucket access**: Keep the R2 API token scoped to the single bucket. Don't use a global Cloudflare API key.

## What's Next

After completing all three phases, you have:
1. A running Basic Memory sidecar on Railway
2. Bidirectional sync with local via R2
3. An agent that reads/writes shared knowledge

From here, iterate on:
- **Knowledge quality**: Review and refine what the agent captures
- **Agent prompting**: Tune when and how the agent uses Basic Memory
- **Backup verification**: Periodically test recovery from R2
- **Monitoring**: Set up alerts for sync failures or Railway volume usage

---

## Confidence Level: 90%

The MCP SSE connection is standard and well-tested. The REST API fallback is available if SSE doesn't fit your agent framework. The main uncertainty is how your specific chat app/agent framework connects to MCP — this varies by framework. If your framework doesn't support MCP natively, use the REST API directly. If the SSE connection fails, check Railway networking (internal vs public URL) before investigating further.
