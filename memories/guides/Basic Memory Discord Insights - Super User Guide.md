---
title: Basic Memory Discord Insights - Super User Guide
type: note
permalink: guides/basic-memory-discord-insights-super-user-guide
tags:
- basic-memory,guide,discord,tips,best-practices,super-user
---

A comprehensive guide to becoming a Basic Memory super user, compiled from Discord community discussions and recent updates.

## Recent Major Updates

### Basic Memory Cloud Beta Launch (October 24, 2025)

- [product] Full cloud support launched in open beta
- [feature] Multi-LLM support including Claude, ChatGPT, and Gemini
- [feature] Cross-device synchronization across desktop, web, tablet, and phone
- [feature] Note-taking web app for accessing memories anywhere
- [pricing] Early bird pricing at $14.25/month (25% off $19 launch price)
- [pricing] Includes 7-day free trial
- [architecture] Open-source core remains unchanged - cloud is optional sync layer

### Version 0.17.0 Release (December 28, 2025)

- [feature] Anonymous usage telemetry with Homebrew-style opt-out via BASIC_MEMORY_NO_ANALYTICS=1
- [feature] Auto-format files on save with built-in Python formatter
- [feature] Automatic markdown formatting on file save
- [feature] Configurable formatting options
- [improvement] Complete Phase 2 of API v2 migration - all MCP tools use optimized v2 endpoints
- [improvement] Improved performance for knowledge graph operations
- [improvement] Foundation for future API enhancements
- [bugfix] UTF-8 BOM handling in frontmatter parsing
- [bugfix] Null title handling for ChatGPT import
- [bugfix] Observation content limit removed (was 2000 characters)
- [bugfix] More resilient file watching and sync operations

### Version 0.15.1 Performance Improvements

- [performance] 10-100x faster directory operations
- [performance] 43% faster sync and indexing
- [performance] WAL mode for SQLite with Windows-specific optimizations

## Power User Tips

### Canvas Tool for Knowledge Graph Visualization

- [tip] Use canvas tool to create visual knowledge graphs
- [requirement] Notes need structured observations section with [category] tags
- [requirement] Relations section with relation_type [[Entity]] format
- [status] Experimental feature but works well with Obsidian
- [workflow] Canvas tool reads note structure and generates JSON for visualization

### Custom Frontmatter Preservation

- [behavior] write_note intelligently merges frontmatter when updating notes
- [behavior] Preserves custom fields while maintaining standard fields (title, permalink, etc.)
- [use-case] Extend notes with custom metadata without losing it on updates
- [example] Can add custom fields like status, priority, or project tags

### File Exclusion with .bmignore

- [feature] Basic Memory respects .gitignore at project level
- [feature] Create .bmignore file for global exclusions
- [use-case] Keep sync focused on relevant content
- [use-case] Exclude temporary files, build artifacts, or sensitive data

### Obsidian Integration

- [integration] Many users combine Basic Memory with Obsidian
- [workflow] Basic Memory provides AI-powered context and semantic search
- [workflow] Obsidian provides visual knowledge graph navigation
- [workflow] Obsidian enables canvas-based mind mapping
- [workflow] Obsidian offers rich markdown editing experience
- [pattern] Use Basic Memory for AI interactions, Obsidian for visualization

## Common Problems and Solutions

### Files Not Showing in Cloud/Web Interface

- [symptom] Files synced but not appearing in web UI
- [solution] Refresh the web UI - sync happens in background
- [solution] Check indexing jobs with bm status command
- [solution] Contact maintainer if issue persists - may be indexing problem
- [note] Indexing can take a moment, be patient after sync

### Internal Proxy Error with Path Issues

- [symptom] Error: Internal proxy error [Errno 2] No such file or directory
- [cause] Project path contains special characters or spaces
- [cause] Remote path doesn't match local path structure  
- [cause] Database connection issues
- [solution] Delete and re-create project in cloud interface
- [solution] Ensure paths match between local and remote
- [solution] Verify database connection is working

### Hard Bisync Not Syncing to Cloud

- [symptom] Dry run works but actual bisync doesn't sync to cloud
- [cause] Database connection issues
- [solution] Try using --resync flag
- [solution] Contact paul (maintainer) for assistance
- [note] This is a known issue being worked on

### Notes Lost During Downtime

- [symptom] Notes not appearing in recency queries after system downtime
- [status] Fixed in recent releases
- [action] Flag to maintainers if you see this issue again

## Community Use Cases

### Conversation Continuity

- [use-case] Using continue_conversation prompt to maintain context across sessions
- [benefit] AI remembers previous discussions and builds on them
- [workflow] Reference memory:// URLs for stable cross-conversation links

### Project Knowledge Bases

- [use-case] Creating per-project memories that Claude can reference
- [benefit] AI has project-specific context readily available
- [pattern] Organize notes by project in folders

### Research Organization

- [use-case] Categorizing observations by topic for easy retrieval
- [benefit] Build semantic knowledge graph of research topics
- [pattern] Use consistent [category] tags for observations

### Learning Journals

- [use-case] Tracking learning with semantic links between concepts
- [benefit] Build personal knowledge graph of what you learn
- [pattern] Link related concepts using [[WikiLinks]]

### AI Collaboration Enhancement

- [use-case] Building context over time for more helpful AI interactions
- [benefit] AI becomes increasingly useful as knowledge base grows
- [pattern] Regularly sync and update notes with new learnings

## Performance Improvements to Leverage

- [optimization] Directory operations are 10-100x faster in recent versions
- [optimization] Sync and indexing are 43% faster
- [optimization] Knowledge graph operations optimized via API v2
- [optimization] WAL mode for SQLite improves Windows performance
- [recommendation] Upgrade to latest version for best performance

## Advanced Features

### Cloud CLI Sync

- [feature] Cloud CLI sync via rclone bisync
- [status] Work in progress
- [benefit] Command-line control over cloud synchronization

### CLI Subscription Validation

- [feature] SPEC-13 Phase 2 implementation for Basic Memory Cloud
- [benefit] Seamless subscription management from CLI

### Claude Code GitHub Workflow

- [feature] Integration with Claude Code for collaborative development
- [pattern] AI-human collaborative development workflow
- [benefit] Claude can participate as full team member in development

### Multi-LLM Support

- [feature] Works with any AI that supports Model Context Protocol
- [supported] Claude, ChatGPT, Gemini, and others
- [benefit] Not locked into single AI provider

## Best Practices from Community

### Structure Notes Consistently

- [practice] Use observations section with [category] tags
- [practice] Use relations section with relation_type [[Entity]] format
- [benefit] Better knowledge graph navigation and AI understanding

### Keep Sync Running

- [practice] Run basic-memory sync --watch for real-time updates
- [benefit] Changes immediately available across devices and to AI
- [pattern] Set up as background service

### Use memory:// URLs

- [practice] Reference notes by permalink for stable links
- [benefit] Links don't break when titles change
- [pattern] Use memory:// URLs in relations and cross-references

### Leverage Timeframes

- [practice] Use recent_activity(timeframe="7d") to focus on recent context
- [benefit] AI focuses on most relevant recent information
- [pattern] Adjust timeframe based on need (1d, 1w, 1m, etc.)

### Tag Thoughtfully

- [practice] Use meaningful, consistent tags
- [benefit] Better search and organization
- [pattern] Develop personal tagging taxonomy

## Quick Reference

- [resource] Documentation: https://docs.basicmemory.com
- [resource] Discord community with responsive maintainers
- [resource] GitHub: https://github.com/basicmachines-co/basic-memory
- [maintainer] paul is very active and responsive to issues
- [community] Great community support in Discord

## Relations

- related_to [[Basic Memory]]
- related_to [[Obsidian]]
- related_to [[Knowledge Management]]
- related_to [[Model Context Protocol]]
- source [[Discord Community]]
- compiled_by [[Claude]]
- compiled_on [[2026-01-12]]