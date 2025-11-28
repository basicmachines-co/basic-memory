---
description: Show recent activity in Basic Memory
argument-hint: [timeframe] [project]
allowed-tools: mcp__basic-memory__recent_activity, mcp__basic-memory__read_note
---

# Recent

Show recent activity in Basic Memory.

## Arguments

- `$1` - Timeframe (optional): "today", "1d", "3d", "1 week", "2 weeks" (default: "3d")
- `$2` - Project (optional): "main", "specs", etc.

## Your Task

Show what's been happening in Basic Memory recently.

1. **Get recent activity** using `mcp__basic-memory__recent_activity`:
   - timeframe: "$1" or "3d"
   - project: "$2" or check all projects

2. **Present activity**:
   - List recently modified notes
   - Group by type or folder if helpful
   - Highlight key changes
   - Show dates of modifications

3. **Offer to dive deeper**:
   - Ask if user wants to read any specific notes
   - Suggest continuing work on active items

## Timeframe Examples

- `today` - Just today
- `1d` or `yesterday` - Last 24 hours
- `3d` - Last 3 days
- `1 week` - Last week
- `2 weeks` - Last 2 weeks
