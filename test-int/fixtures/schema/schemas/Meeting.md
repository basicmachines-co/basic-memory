---
title: Meeting
type: schema
entity: Meeting
version: 1
schema:
  attendees(array): string, who was present
  decisions(array): string, what was decided
  action_items(array): string, follow-up tasks
  date?: string, when the meeting occurred
settings:
  validation: warn
---

# Meeting

A meeting or group discussion. Captures attendees, decisions made, and action items
for follow-up. All array fields can have multiple entries.

## Observations
- [purpose] Structured meeting notes that capture outcomes not just discussion
- [convention] Action items should include the responsible person where possible
