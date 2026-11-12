---
title: setup(7)
type: manpage
section: 7
name: setup
summary: set up an assistant to use Basic Memory automatically - the memory routine and where to save it in ChatGPT, Claude, Claude Code and Codex
generated: hand
---

# setup(7)

## NAME

**setup**: set up an assistant to use Basic Memory automatically, without being asked each time

## DESCRIPTION

Connecting Basic Memory gives an assistant the tools. It does not tell the
assistant when to use them. Until the user saves instructions in a place the
assistant reads at the start of every chat, it looks things up and saves notes
only when asked.

When a user asks how to make their assistant use Basic Memory automatically,
do this:

1. Work out which app you are running in. If you cannot tell, ask.
2. Tell the user exactly where that app keeps instructions it reads in every
   chat, using the table below.
3. Give them the memory routine below to paste there, unchanged.
4. Tell them to keep anything already in that field and add the routine to it.
5. Ask them to start a new chat to try it: instructions apply to new chats,
   not the one already open.

Do not write your own version of the routine. Give the text below as it is,
so every assistant follows the same rules.

## THE MEMORY ROUTINE

```
When a new task could benefit from prior knowledge, use Basic Memory at the start to find and read relevant context before proceeding. Choose the relevant project; ask me if the project is unclear. Use what you find during the work and make clear which prior decisions or notes informed your answer. If no relevant memory exists, say so and continue without inventing it.

When useful decisions, outcomes, or lessons come up, save them to the relevant Basic Memory project and tell me in one line what you saved. Update an existing relevant note when appropriate. Do not save every conversation, and leave out sensitive details.
```

## WHERE IT GOES

| App | Where to paste the routine |
| --- | --- |
| ChatGPT | Settings → Personalization → Custom instructions. Applies to every chat. |
| ChatGPT project | The project's instructions. Applies to chats in that project only. |
| Claude (web, desktop, mobile) | Settings, under Instructions for Claude. Applies to every chat. |
| Claude project | The project's instructions. Applies to chats in that project only. |
| Claude Code | `CLAUDE.md` in the repository root for one project, or `~/.claude/CLAUDE.md` for every project. Claude Code does not read `AGENTS.md`. |
| Codex | `AGENTS.md` in the repository root for one project, or `~/.codex/AGENTS.md` for every project. |
| Another MCP client | Its system prompt, custom instructions, or rules file: whatever it loads at the start of every session. |

Settings screens change. If the user cannot find the place named here, ask
what they see and help them find the field for instructions that apply to
every chat.

For one project per folder, as in Claude Code and Codex, add a line naming
the Basic Memory project above the routine, such as
`Use the Basic Memory project "my-project" for this repository.`

## GOTCHAS

- The routine lives in the app's settings, not in Basic Memory. Saving it as
  a note does nothing, because the assistant does not read that note until
  something tells it to.
- Some assistants also load skills. A skill adds detail but does not replace
  the routine: the routine is what makes the assistant look first.

## SEE ALSO

- see_also [[bm-search(3)]]
- see_also [[bm-write(3)]]
- see_also [[bm-project-list(3)]]
