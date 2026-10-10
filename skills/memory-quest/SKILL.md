---
name: memory-quest
description: "Finish Basic Memory's Memory Quest (Mission 8): confirm this assistant reaches the user's Basic Memory Cloud account and starts work from Basic Memory, then — with approval — save a Memory Master note carrying the verification code from app.basicmemory.com/memory-quest. Use when the user runs /memory-quest or asks to verify their assistant for Memory Quest."
---

# Memory Quest

Memory Quest's last mission checks that this assistant is set up to use Basic Memory as a natural starting point for work. You complete it by saving one note — the user's **Memory Master** note — through Basic Memory, carrying a one-time code from the Basic Memory web app. The cloud recognizes that write and awards the mission.

The note write *is* the verification. There is no other endpoint to call and nothing to report afterwards.

## When to Use

- The user runs `/memory-quest` (or `$memory-quest`, or picks it from a skill menu)
- The user asks to verify their assistant, finish Mission 8, or become a Memory Master
- Memory Quest in the web app pointed them here

Take no task context from the invocation. This skill checks setup; it does not need to find memory relevant to a task.

## Steps

Work through these in order. Stop at the first step that fails, explain what's missing, and say how to fix it. Never skip ahead to the note write.

### 1. Confirm a cloud connection

Call `list_workspaces`.

- If it fails, Basic Memory isn't connected to this assistant. Tell the user to connect it and run `/memory-quest` again.
- If the only workspace has `tenant_id` `personal`, this assistant is connected to a local-only Basic Memory. Memory Quest lives in Basic Memory Cloud: tell the user to connect their cloud account (`bm cloud login` for the CLI, or the Basic Memory connector in a chat app) and try again.
- Otherwise the connection works. You may show the user one line about what you can see, such as the workspace names. That is a courtesy, not evidence.

### 2. Confirm the guidance is active

Mission 8 is about Basic Memory being this assistant's starting point, so check that the guidance which makes that happen is actually loaded. It is active when either is true:

- Your context contains the Basic Memory session briefing — a section headed `# Basic Memory — session context`, injected at session start by the Basic Memory plugin for Claude Code or Codex.
- Your persistent instructions (custom instructions, project instructions, or an agent context file) tell you to use Basic Memory at the start of tasks to find relevant context.

If neither is true, stop. Tell the user to install the Basic Memory plugin for their assistant, or to save the memory routine from the Memory Quest panel in their assistant's instructions, then start a fresh conversation and run `/memory-quest` again. Do not report the guidance as active when it isn't: that report is the one part of this check the server cannot see for itself.

### 3. Ask for the verification code

Ask the user to open **https://app.basicmemory.com/memory-quest** and paste the code it shows. Codes look like `MQ-7HX2-K9QD-4TRM` and last 30 minutes.

Accept the code as the user gives it; case and separators don't matter. If what they paste is clearly not a code (wrong prefix, too short), ask again rather than guessing.

### 4. Ask before saving

Say plainly what you are about to do: save a note titled "Memory Master" to their Basic Memory, and that saving it is what completes Mission 8. Ask for approval.

If they decline, stop. Say that the mission stays incomplete and they can run `/memory-quest` again any time.

### 5. Save the Memory Master note

First choose a project in Basic Memory Cloud. Call `list_memory_projects` and pick a project whose source is exactly `cloud`: the default one if it qualifies, otherwise ask the user which to use. Don't use a project listed as `local` or `local+cloud`. A local Basic Memory server writes those on this machine, so the cloud never sees the note and the mission stays incomplete, even though the write succeeds. If no project is listed as `cloud`, stop and tell the user to create one in Basic Memory Cloud or connect their cloud account, then try again.

With approval, call `write_note` once:

- `title`: `Memory Master`
- `directory`: `memory-quest`
- `note_type`: `memory_master`
- `project_id`: the chosen cloud project's `external_id`
- `metadata`: `{"memory_quest_code": "<the code exactly as the user gave it>", "memory_quest_guidance": "active"}`
- `content`: the template below, unchanged except where it says to fill in

Pass the code and guidance in `metadata`, never inside `content`. They must arrive as frontmatter fields for the cloud to recognize the write.

If `write_note` reports that the note already exists (for example, from an earlier attempt), call it again with the same arguments plus `overwrite: true`.

### 6. Tell the user what happens next

Tell them the note is saved and to open Memory Quest in the web app, where Mission 8 shows complete once the cloud has processed it. You cannot see the award yourself, so don't claim it happened. If Memory Quest still shows Mission 8 as current after a minute, the usual cause is an expired or replaced code: get a new code and run `/memory-quest` again.

## Memory Master template

Use this as the note's `content`. Replace `<today's date>` with today's date; leave everything else as written. The `::bm-*` blocks are live views the Basic Memory web app renders from the user's own knowledge base each time the note is opened.

<!-- Maintainers: basic-memory-cloud parses a copy of this template
(packages/comark-harness/src/memory-master.md) against the Comark profile.
Update both together. -->


```markdown
Memory Master since <today's date>. Every Memory Quest mission is complete.

## The quest

::bm-check{title="Memory Quest"}
- [x] Capture your first memory
- [x] Create a relationship
- [x] Build and explore the knowledge graph
- [x] Master advanced search
- [x] Structure knowledge with schemas
- [x] Share a note safely
- [x] Bring in knowledge you already have
- [x] Make Memory a Habit
::

## Your knowledge today

::bm-stat{operation="bm.search.metadata" aggregate="count" label="Notes in this project"}
::

::bm-kpi{operation="bm.activity.recent" metric="notes-updated" window="30d" title="Notes updated in the last 30 days"}
::

::bm-spark{operation="bm.activity.recent" metric="notes-updated" window="30d" interval="day" title="Updates per day"}
::

::bm-rank{operation="bm.search.metadata" aggregate="count" group-by="type" limit="5" title="Your most-used note types"}
::

## What changes now

Your assistant now starts from what you already know: it checks Basic Memory for relevant context when it begins work, and offers to save what's worth keeping when it's done.
```

## Guidelines

- **One write, with approval.** Never write the note before step 4, and never write a second note to retry. Overwrite the same one.
- **Report guidance honestly.** Only send `memory_quest_guidance: "active"` after step 2 passes.
- **Don't invent outcomes.** You can't observe the award. Point the user to Memory Quest instead of announcing success.
- **Keep the code in metadata.** A code in the note body verifies nothing.
- **Stay on task.** This skill doesn't search for memory relevant to a task, and doesn't change the user's other notes or settings.
