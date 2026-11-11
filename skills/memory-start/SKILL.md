---
name: memory-start
description: "Help a new user's first Basic Memory session: save one short note, show it, and explain how to get it back. Use when getting started, or when a project is new, empty, or holds only starter notes."
---

# Memory Start

A new user has just connected Basic Memory. Show them the whole loop in their
first session: save one thing, see it, get it back in a new chat, add to it.
Keep it short. Use their real work, not a demo.

Speak plainly. Don't explain schemas, observations, or the knowledge graph.
Say "note", not "entity".

## Project scope

If the project is unknown, discover it with `bm_project_list()`. The
list marks the default project. Use it unless the user names another. Pass the
same `project` on every note, search, and folder call.

## Saving

Save by default. Connecting a memory service already says the user wants notes
saved, so don't ask before the first save. Tell the user in one line what you
saved and how to undo it.

If the user says "ask me first", say what you would save and where, and save
only on yes, for the rest of this conversation. To make that hold in every
chat, suggest they add a line like this to their assistant's custom
instructions:

> Ask me before saving anything to Basic Memory.

## 1. Save one thing

Do this on a project that holds only starter notes, or early in a first
session. Wait until the user has said what they are working on. Skip trivial
chats ("what's 2+2"), and skip anything plainly personal or sensitive, such as
health, money, or other people's private details, unless they ask you to save it.

Search first (`bm_search`) so you don't duplicate a note that already exists.
Then write one short note:

- a clear title, such as `Q3 pricing plan`;
- two or three sentences on what the work is and where it stands;
- two or three `[category]` facts under `## Observations`.

Not a transcript. Not a summary of the chat.

```
bm_write(
    project="<project>",
    title="Q3 pricing plan",
    directory="notes",
    overwrite=False,
    content="""Planning new pricing for the Q3 launch. Two tiers are on the table.
The team wants a decision before the July board meeting.

## Observations
- [goal] Pick between flat and usage-based pricing for Q3
- [deadline] Decision needed before the July board meeting
- [open_question] Whether the free tier stays""",
)
```

Then say what you saved in one line, with the link from the tool result, how
to undo it, and how to opt out:

> Saved "Q3 pricing plan" to Basic Memory. Say "undo" to remove it, or "ask me
> first" if you'd rather approve saves.

Use only links the tool returns. Never build a URL yourself.

## 2. See it

Offer to show the note. Give the link from the save. If the host has the Basic
Memory app (`bm_open` is available), offer to open it:

```
bm_open(view="note", identifier="Q3 pricing plan", project="<project>")
```

## 3. Get it back

Tell the user how to use the note in a new chat:

> In any new chat, ask me "what did we save about Q3 pricing?"

You can't check memory at the start of a new chat unless the user's
instructions or message prompt you to. So offer one line they can paste into
their assistant's custom instructions or project instructions:

> Use Basic Memory to find and save notes about my work.

Say where it goes on this host if you know it: project instructions or custom
instructions in a chat app, or the agent's context file in a coding tool. If
you don't know, give the line and say "paste this into your assistant's
custom instructions." Don't make them set this up now if they'd rather not.

## 4. Add to it

When the same topic comes up later, update that note instead of creating a
new one. Search for it, read it, then use `bm_edit` to append new facts or
change ones that are out of date. Say what you changed in one line.

## Undo

If the user says "undo" after a save, delete the note you just wrote and
confirm in one line:

```
bm_delete(project="<project>", identifier="<path from the save result>")
```

If the save updated an existing note, don't delete it. Edit it back to how it
was before and say so.

## What comes next

Don't offer a full setup in the first session. Once the user has a few notes
and has come back to them, offer memory-onboarding to design a fuller system
with note types and folders.
