# Assistant Setup — Loading Instructions Every Session

The knowledge base is self-describing: its rules live in instruction notes inside it. But a fresh assistant session doesn't know those notes exist. This reference covers the last mile — a **persistent instruction stub** in whatever always-loaded mechanism the user's platform provides, pointing at the startup router.

## The stub pattern

The stub is deliberately tiny. It carries only:

1. Which Basic Memory **project** to use (always, explicitly)
2. The instruction to **read the startup router before any knowledge-base work** and follow its dispatch table
3. The 3–5 rules that must hold **before** the router loads (because they govern the loading itself)

Everything else — conventions, schemas, workflows, reference data — lives in the router and the notes it dispatches to. This keeps the always-loaded footprint small and means the user edits notes, not platform settings, when conventions change.

### Stub template

Adapt the wording; keep the shape:

```
You maintain my knowledge base in Basic Memory, project "{project-name}".

Rules that apply before anything else:
1. Every Basic Memory call (read, search, write, list) uses project "{project-name}" explicitly.
2. At the start of every session, before any knowledge-base work, read the note
   "Startup Router" (memory://instructions/startup-router) and follow its dispatch
   table for the task at hand. Take no write actions until the notes it lists are loaded.
3. Folder paths use exact casing as given in the instruction notes.
4. Before overwriting any existing note, read it in full first.
5. When unsure where something belongs, ask me instead of guessing.
```

Rules 1, 3, 4 are in the stub rather than the router because they protect the system even if the router fails to load or has been corrupted.

## Where the stub goes

Determine what platform and mechanism **you** are running in, and give the user concrete steps for it. Common mechanisms, by shape rather than brand:

| Mechanism shape | Typical examples | How to place the stub |
|:--|:--|:--|
| **Project-level instructions** | A "project" or "space" in a chat app with a custom-instructions field | Paste the stub into the project instructions. Best option when available — scoped, persistent, and editable. |
| **Global custom instructions / personalization** | Account-level "how should I respond" settings | Works, but applies to every conversation — wrap the stub in "When I ask about my knowledge base: ...". |
| **Agent context files** | A markdown file the agent auto-loads from the working directory or home directory (many CLI/desktop agents support one) | Add the stub as a section of that file. |
| **System prompt** | Self-hosted assistants, API integrations, custom apps | Prepend the stub to the system prompt. |
| **No persistent mechanism** | Bare chat with MCP tools | Fall back: create a note titled something unmissable like `START HERE — Assistant Instructions` at the KB root, and teach the user to open each session with "read Start Here first". Weakest option; say so honestly. |

If you can't determine the platform, show the stub and this table and let the user place it. If the user runs the KB from multiple assistants/platforms (desktop app + CLI + phone), place the stub in each — the router being shared is exactly what keeps them consistent.

## The router reference must be durable

Inside the stub, reference the router by **title** or **`memory://` path** — never paste a raw permalink string copied from a tool result. Permalinks can regenerate when notes are rewritten; the stub is the one piece of the system that isn't a note and won't be caught by note-level maintenance, so it must use the most durable reference form available.

## Verification test

Never call Phase 4 done without testing. With the user:

1. **Simulate a fresh session** — new conversation on their platform (or in this session, deliberately act as if starting cold).
2. Give a natural task from one of their domains: "add a task: call the dentist Tuesday."
3. **Watch the order of operations.** Pass = the assistant reads the router, loads the dispatched instruction note(s), THEN writes — correct folder, correct schema fields, correct naming. Fail = it writes first or freelances the format.
4. If it fails: is the stub actually saved? Does the router reference resolve (test with `read_note`)? Is the "before any knowledge-base work" wording strong enough? Fix and re-test.
5. Also verify the **escape hatch**: ask something ambiguous ("save this somewhere") and confirm the assistant asks rather than guesses.

Leave the user with this framing: the stub + router is the system's ignition. If future sessions ever start behaving inconsistently, the first diagnostic is always "did the router get loaded?"
