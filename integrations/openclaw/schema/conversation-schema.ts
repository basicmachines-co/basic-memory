/**
 * Canonical Conversation schema note content, seeded into new projects on first startup.
 * If the schema already exists (user may have customized it), seeding is skipped.
 */
export const CONVERSATION_SCHEMA_CONTENT = `---
title: Conversation
type: schema
entity: Conversation
version: 1
schema:
  date: "string, ISO date YYYY-MM-DD"
  session_id?: "string, unique session identifier"
  channel?: "string, where the conversation happened (webchat, telegram, discord, etc)"
  participants?: "array, who was in the conversation"
  topic?: "string, brief description of main topic"
  summary?: "string, one-paragraph summary of key points"
  key_decisions?: "array, decisions made during conversation"
  action_items?: "array, things to do as a result"
settings:
  validation: warn
---

# Conversation

A record of a conversation session between the agent and user(s).

## Observations
- [convention] Conversation files live in memory/conversations/ with format conversations-YYYY-MM-DD.md
- [convention] Messages are appended as the conversation progresses
- [convention] Summary and key_decisions populated at session end or by memory-reflect
- [convention] Skip routine greetings, heartbeat acks, and tool call details
- [convention] Focus on decisions, actions taken, and key context needed for continuity
`
