---
name: basic-memory-setup
description: Guide Basic Memory setup in Tau. Use when a user wants to install or configure the Tau memory extension, choose a memory destination or capture policy, verify connectivity, or troubleshoot setup. Check compatibility and obtain approval before installation, configuration changes, or test writes.
---

# Set up Basic Memory in Tau

Help the user choose where memory goes and what gets saved. Ask one decision at a
time. Set up Basic Memory's shared workflow: schema-backed checkpoints, tasks,
decisions, categorized observations, and verified relations that other agents can
find. Tau provides the lifecycle callbacks; it does not define a separate memory
system. Do not create projects, reorganize notes, change credentials, or install
packages as incidental setup work.

## 1. Inspect before changing anything

- Locate the integration directory: the installed `~/.tau/extensions/basic-memory`
  from `bm install tau`, or `integrations/tau` in an explicitly identified Basic
  Memory checkout. Do not assume the current directory or this skill's directory
  contains the extension. A packaged install does not require a source checkout.
- Read the integration directory's `README.md`, `pyproject.toml`, `bridge.py`, and
  `knowledge.py` for installation instructions, the dependency pin, Settings,
  and the general/coding profile models.
  Published reference: https://github.com/basicmachines-co/basic-memory/blob/feat-tau-1487/integrations/tau/README.md
- Check executable availability with `command -v uv`, `command -v bm`, and
  `command -v tau`. Inspect versions/help only for executables that exist.
  The integration requires Python 3.13+, uv, and a configured Basic Memory CLI.
- Distinguish the user's installed Tau from the isolated integration environment.
  Stock Tau 0.4.1 lacks continuity APIs. The upstream contribution is
  https://github.com/huggingface/tau/pull/687, from Basic Machines. Use the checked-in
  immutable dependency pin, not an invented version or a moving branch. A version
  string alone cannot distinguish stock 0.4.1 from its compatible fork.
- Identify the effective config path: `TAU_BASIC_MEMORY_CONFIG` if explicitly set,
  otherwise `~/.tau/basic-memory.json`. Inspect only that file, not unrelated Tau
  catalogs, credentials, logs, or conversation storage. Never dump its contents
  into output. Do not accept repository-local config as a capture destination.
- If the extension is loaded, ask the user to run `/bm-status`. A text reply does
  not run a slash command or control the current Tau session. Do not start a
  second Tau process against the current session's storage.

If prerequisites are absent, explain the missing prerequisite and obtain approval
for the specific installation. Consult current Basic Memory install docs rather
than guessing package versions or changing the user's global Tau installation.

## 2. Choose destination and policy

Offer **tools only**, **recall only**, or **full continuity**. Recommend full
continuity for ordinary coding only after explaining its costs and disclosure.

For recall or continuity, list existing projects using the advertised
`bm_list_memory_projects` tool when available, or `bm project list`. Check current
CLI help or tool schemas for routing arguments. Identify local/cloud/team routing
for the exact destination; if routing cannot be established, stop and ask.
Do not dump Basic Memory's config or authentication material. Ask the user to
choose an existing project explicitly, including its workspace when applicable.
Never select the default project or a team workspace on the user's behalf.

Explain:
- Full continuity sends new public user/final-assistant text and the prior handoff
  to Tau's active model for synthesis. It adds requests, latency, and model billing.
- Notes are saved to the selected Basic Memory project. Cloud/team destinations
  disclose those notes there; get explicit approval for that destination.
- Recall-only still retrieves note contents into Tau's model context. Local notes
  do not imply local model inference. It disables automatic writes, not explicit
  tool calls or `/bm-checkpoint` and `/bm-remember` requests.
- Transcripts stay off unless separately requested. Hidden reasoning, raw tool
  payloads, and images are excluded from automatic capture. Credential masking is
  best-effort, not a general secret detector. Recommend tools-only for sensitive
  sessions; even explicit tool calls can disclose data.

Also ask about optional `read_projects` (up to six explicit read-only sources).
Explain that shared context does not authorize writes back to those projects.
Choose `placement_conventions` based on a small approved inspection of existing
notes. Checkpoints belong in their configured folder; durable decisions and tasks
belong with their topics, linked from checkpoints rather than duplicated each turn.

Use these policy overlays, replacing `CHOSEN_PROJECT` only after approval:

### Tools only

```json
{
  "project": null,
  "auto_recall": false,
  "capture_knowledge": false,
  "checkpoint_on_compact": false,
  "summarize_on_shutdown": false,
  "capture_transcript": false
}
```

### Recall only

```json
{
  "project": "CHOSEN_PROJECT",
  "auto_recall": true,
  "capture_knowledge": false,
  "checkpoint_on_compact": false,
  "summarize_on_shutdown": false,
  "capture_transcript": false
}
```

### Full continuity

```json
{
  "project": "CHOSEN_PROJECT",
  "auto_recall": true,
  "capture_knowledge": true,
  "checkpoint_on_compact": true,
  "summarize_on_shutdown": true,
  "capture_transcript": false
}
```

Coding sessions default to `tau/{repo name}` within the chosen project, using the
final component of the confirmed repository identity, not the worktree directory name.
General sessions default to `tau/checkpoints`; separately enabled transcripts use
`tau/transcripts`. Omit `checkpoint_folder` (or use null) for automatic placement;
set it explicitly only for a user-approved override.
Preserve existing folder, command, arguments, timeout, and
budget settings unless the user approves changing them. Never put API keys in
this file; Basic Memory owns authentication and project routing.

### General or coding profile

Ask whether this checkout is for coding or general work. Do not infer coding
consent merely because Git is present. General checkpoints use `session`; coding
checkpoints use the shared `coding_session` contract.

For coding, resolve the Git top-level root and stable repository identity, such as
`owner/name`. Inspect remotes narrowly without echoing embedded credentials. Ask
the user to confirm both; do not invent an identity when remotes are ambiguous.
Explain that branch, SHA, cwd, and optional PR metadata are saved with checkpoints.
The repository label is user-confirmed identity, not a write destination.

Store coding profiles only in the user-owned `repositories` list. Each entry has
an absolute checkout `root`, `repository`, `kind: coding`, and its own explicit
project/read sources/placement. It does not inherit the global project. The closest
matching root wins; Git must confirm that exact root before a coding write. Register
another worktree explicitly with the same repository identity. Never silently
copy a parent's mapping into a nested Git checkout.

Example configuration for coding in one approved checkout and tools-only elsewhere
(the global lifecycle flags still apply to every profile):

```json
{
  "project": null,
  "repositories": [
    {
      "kind": "coding",
      "root": "/absolute/path/to/approved-checkout",
      "repository": "owner/repository",
      "project": "CHOSEN_PROJECT",
      "read_projects": [],
      "placement_conventions": "Decisions in decisions/, tasks in tasks/. Search before creating notes."
    }
  ],
  "auto_recall": true,
  "capture_knowledge": true,
  "checkpoint_on_compact": true,
  "summarize_on_shutdown": true,
  "capture_transcript": false
}
```

Do not replace other repository entries when updating this checkout. A general
user-level project applies outside configured coding roots; confirm that broader
scope explicitly. Opt-out flags disable automatic operations across profiles.
Existing legacy Tau receipts remain usable; old notes are not silently rewritten
to retrofit repository metadata.

## 3. Apply approved configuration

Show a safe summary of the proposed destination, policy, effective config path,
and changes. Ask for approval before writing. These examples are overlays, not
permission to overwrite an existing config wholesale.

Parse the existing JSON strictly. If malformed or containing unknown fields, stop
and explain the problem without printing sensitive values; do not replace it with
defaults. Merge the approved fields and validate the complete candidate using
`tau.bridge.Settings.model_validate` in the integration's environment, before writing.
Do not print the config, validation input values, or unfiltered exceptions. Report
invalid field paths and a safe explanation. Validation must not start MCP or call
a model. Preserve the existing file's permissions and use a private file for a new
config; avoid leaving backups containing private arguments in the checkout.

Write only after successful validation. Report configuration validation separately
from connection or end-to-end verification. If already configured correctly, leave
it unchanged and proceed to verification.

### Seed shared schemas with approval

Read the schema files in `<integration-directory>/schemas/`. They are bundled
copies of the repository's `integrations/shared/schemas/`, shared with other hosts.

After restating the exact write project and receiving approval, search for existing
schema notes and read any matching definitions. Offer missing schemas only:
- `coding-session.md` for coding, or `session.md` for general use.
- `task.md` and `decision.md` for both profiles.

Use the advertised `bm_write_note` schema with `note_type="schema"`, directory
`schemas`, the schema frontmatter as metadata, and Markdown body as content. Do not
paste YAML frontmatter into the body. Disable overwrite. Do not replace a user's
customized schema, even if it differs from the bundle. Explain incompatibilities
and ask before making a separate migration. No empty folders, fabricated tasks,
or lifecycle-event notes are required. Seeding does not require inventing a new
`captureEvents` setting; Tau does not use the hook CLI's audit inbox.

## 4. Launch the compatible environment

For a packaged install, with approval for dependency installation, use:

```bash
bm install tau --sync
```

Then give the user this command to run in their terminal:

```bash
uv run --project ~/.tau/extensions/basic-memory tau
```

The installed extension is discovered automatically; do not also pass `-e`.
For source development, with approval, run `uv sync --project <integration-directory>`
and launch `uv run --project <integration-directory> tau -e <integration-directory>`.
Use actual resolved paths, not the placeholder literally. Do not install a second
copy alongside an existing source/copy install without choosing which one to keep.

This launches the pinned isolated environment, not the user's global Tau.
`bm install tau` installs this setup skill and the prompt templates separately;
loading from source alone does not discover them. Follow the README's manual
skill-copy instructions when using source development.

In an already compatible Tau session, ask the user to `/reload` after config
changes. **Reload/replacement shuts down the old lifecycle first:** if automatic
capture was previously enabled, that shutdown can still write using the old
settings. Warn before switching destinations or disabling capture. Editing config
is not an immediate guarantee that the running session has stopped capturing.

## 5. Verify in stages

1. **Config:** the candidate passes the actual Settings model. This alone proves
   neither connectivity nor memory persistence.
2. **Connection and structure:** `/bm-status` reports connected and shows the
   intended project, general/coding profile, read sources, and controls. Confirm
   the selected schemas exist using read/search tools. If shared sources were
   configured, verify a read-only query in each approved source.
3. **Recall:** for recall/continuity policies, ask permission to retrieve an
   existing non-sensitive note, then use `/bm-orient <topic>` and confirm the note
   content was inserted. An empty project can connect successfully while having
   nothing to recall; report that distinction.
4. **Optional write/read:** obtain approval for a uniquely named, non-sensitive
   setup test note in the exact project. Use the advertised `bm_write_note` schema
   with overwrite disabled, then `bm_read_note` on the returned path. Compare
   content. Do not infer a save from a command acknowledgement or a successful
   search alone. An uncertain write must not be automatically retried.
5. **Optional continuity:** explain that `/bm-checkpoint` summarizes eligible
   public session content, not just a synthetic test marker, and can incur model
   charges. Obtain consent before asking the user to invoke it. Verify the saved
   path by reading it. Use advertised `bm_schema_validate` on that note when
   available. For coding, check repository/root/branch/SHA metadata and a repository
   filter query; then verify recall after an approved reload. Do not compact a
   working session merely as a setup test. A tool unavailable behind a feature
   gate is an unverified stage, not permission to enable features silently.

Ask separately before deleting any test note. Never delete checkpoints or receipts
as automatic cleanup. Report what was actually tested and leave unrun stages
explicitly unverified. Do not declare full continuity verified from write/read alone.

## Troubleshooting and handoff

- Unsupported host: use the pinned environment; never patch installed Tau files.
- Disconnected: verify the configured executable and arguments, project access,
  and safe diagnostic status. Do not expose server stderr or credential values.
- Missing tools: use the advertised inventory and server feature gates. Do not
  invent tools or silently enable disabled server features.
- Failed/unconfirmed checkpoint: inspect availability and the confirmed path.
  `/reload` reconciles pending intents by reading; it is not a blind write retry.
- No project: automatic memory is intentionally off, not broken.
- To disable automatic memory, apply the tools-only overlay with approval and
  explain the old-lifecycle shutdown caveat before reload. Existing notes remain.

Finish with: chosen policy and destination, config path, compatible launch command,
verified stages, remaining blockers, and `/bm-status`, `/bm-orient`,
`/bm-checkpoint`, `/bm-remember`. No invented save confirmations or claims to have
changed the current Tau session through a reply.
