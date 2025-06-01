---
title: AI Assistant Guide
type: note
permalink: docs/ai-assistant-guide
---
> Note: This is an optional document that can be copy/pasted into the project knowledge for an LLM to provide a full description of how it can work with Basic Memory. It is provided as a helpful resource. The tools contain extensive usage description prompts with enable the LLM to understand them. 

You can [download](https://github.com/basicmachines-co/basic-memory/blob/main/docs/AI%20Assistant%20Guide.md) the contents of this file from GitHub
# AI Assistant Guide for Basic Memory

This guide helps you, the AI assistant, use Basic Memory tools effectively when working with users. It covers reading, writing, and navigating knowledge through the Model Context Protocol (MCP).

## Quick Reference

**Essential Tools:**
- `write_note()` - Create/update notes (primary tool)
- `read_note()` - Read existing content
- `search_notes()` - Find information
- `edit_note()` - Modify existing notes incrementally (v0.13.0)
- `move_note()` - Organize files with database consistency (v0.13.0)

**Project Management (v0.13.0):**
- `list_projects()` - Show available projects
- `switch_project()` - Change active project
- `get_current_project()` - Current project info

**Key Principles:**
1. **Build connections** - Rich knowledge graphs > isolated notes
2. **Ask permission** - "Would you like me to record this?"
3. **Use exact titles** - For accurate `[[WikiLinks]]`
4. **Leverage v0.13.0** - Edit incrementally, organize proactively, switch projects contextually

## Overview

Basic Memory allows you and users to record context in local Markdown files, building a rich knowledge base through natural conversations. The system automatically creates a semantic knowledge graph from simple text patterns.

- **Local-First**: All data is stored in plain text files on the user's computer
- **Real-Time**: Users see content updates immediately
- **Bi-Directional**: Both you and users can read and edit notes
- **Semantic**: Simple patterns create a structured knowledge graph
- **Persistent**: Knowledge persists across sessions and conversations

## The Importance of the Knowledge Graph

Basic Memory's value comes from connections between notes, not just the notes themselves. When writing notes, your primary goal should be creating a rich, interconnected knowledge graph.

When creating content, focus on:

1. **Increasing Semantic Density**: Add multiple observations and relations to each note
2. **Using Accurate References**: Aim to reference existing entities by their exact titles
3. **Creating Forward References**: Feel free to reference entities that don't exist yet - Basic Memory will resolve these when they're created later
4. **Creating Bidirectional Links**: When appropriate, connect entities from both directions
5. **Using Meaningful Categories**: Add semantic context with appropriate observation categories
6. **Choosing Precise Relations**: Use specific relation types that convey meaning

Remember that a knowledge graph with 10 heavily connected notes is more valuable than 20 isolated notes. Your job is to help build these connections.

## Core Tools Reference

### Essential Content Management

**Writing knowledge** (most important tool):
```
write_note(
    title="Search Design",
    content="# Search Design\n...",
    folder="specs",                     # Optional
    tags=["search", "design"],          # v0.13.0: now searchable!
    project="work-notes"                # v0.13.0: target specific project
)
```

**Reading knowledge:**
```
read_note("Search Design")              # By title
read_note("specs/search-design")        # By path
read_note("memory://specs/search")      # By memory URL
```

**Incremental editing** (v0.13.0):
```
edit_note(
    identifier="Search Design",
    operation="append",                 # append, prepend, find_replace, replace_section
    content="\n## New Section\nContent here..."
)
```

**File organization** (v0.13.0):
```
move_note(
    identifier="Old Note",
    destination="archive/old-note.md"   # Folders created automatically
)
```

### Project Management (v0.13.0)

```
list_projects()                         # Show available projects
switch_project("work-notes")            # Change active project
get_current_project()                   # Current project info
```

### Search & Discovery

```
search_notes("authentication system")   # v0.13.0: includes frontmatter tags
build_context("memory://specs/search")  # Follow knowledge graph connections
recent_activity(timeframe="1 week")     # Check what's been updated
```

## memory:// URLs Explained

Basic Memory uses a special URL format to reference entities in the knowledge graph:

- `memory://title` - Reference by title
- `memory://folder/title` - Reference by folder and title 
- `memory://permalink` - Reference by permalink
- `memory://path/relation_type/*` - Follow all relations of a specific type
- `memory://path/*/target` - Find all entities with relations to target

## Semantic Markdown Format

Knowledge is encoded in standard markdown using simple patterns:

**Observations** - Facts about an entity:
```markdown
- [category] This is an observation #tag1 #tag2 (optional context)
```

**Relations** - Links between entities:
```markdown
- relation_type [[Target Entity]] (optional context)
```

**Common Categories & Relation Types:**
- Categories: `[idea]`, `[decision]`, `[question]`, `[fact]`, `[requirement]`, `[technique]`, `[recipe]`, `[preference]`
- Relations: `relates_to`, `implements`, `requires`, `extends`, `part_of`, `pairs_with`, `inspired_by`, `originated_from`

## When to Record Context

**Always consider recording context when**:

1. Users make decisions or reach conclusions
2. Important information emerges during conversation
3. Multiple related topics are discussed
4. The conversation contains information that might be useful later
5. Plans, tasks, or action items are mentioned

**Protocol for recording context**:

1. Identify valuable information in the conversation
2. Ask the user: "Would you like me to record our discussion about [topic] in Basic Memory?"
3. If they agree, use `write_note` to capture the information
4. If they decline, continue without recording
5. Let the user know when information has been recorded: "I've saved our discussion about [topic] to Basic Memory."

## Understanding User Interactions

Users will interact with Basic Memory in patterns like:

1. **Creating knowledge**:
   ```
   Human: "Let's write up what we discussed about search."
   
   You: I'll create a note capturing our discussion about the search functionality.
   [Use write_note() to record the conversation details]
   ```

2. **Referencing existing knowledge**:
   ```
   Human: "Take a look at memory://specs/search"
   
   You: I'll examine that information.
   [Use build_context() to gather related information]
   [Then read_note() to access specific content]
   ```

3. **Finding information**:
   ```
   Human: "What were our decisions about auth?"
   
   You: Let me find that information for you.
   [Use search_notes() to find relevant notes]
   [Then build_context() to understand connections]
   ```

4. **Editing existing notes (v0.13.0)**:
   ```
   Human: "Add a section about deployment to my API documentation"
   
   You: I'll add that section to your existing documentation.
   [Use edit_note() with operation="append" to add new content]
   ```

5. **Project management (v0.13.0)**:
   ```
   Human: "Switch to my work project and show recent activity"
   
   You: I'll switch to your work project and check what's been updated recently.
   [Use switch_project() then recent_activity()]
   ```

6. **File organization (v0.13.0)**:
   ```
   Human: "Move my old meeting notes to the archive folder"
   
   You: I'll organize those notes for you.
   [Use move_note() to relocate files with database consistency]
   ```

## Key Things to Remember

1. **Files are Truth**
   - All knowledge lives in local files on the user's computer
   - Users can edit files outside your interaction
   - Changes need to be synced by the user (usually automatic)
   - Always verify information is current with `recent_activity()`

2. **Building Context Effectively**
   - Start with specific entities
   - Follow meaningful relations
   - Check recent changes
   - Build context incrementally
   - Combine related information

3. **Writing Knowledge Wisely**
   - Same title+folder overwrites existing notes
   - Structure with clear headings and semantic markup
   - Use tags for searchability (v0.13.0: frontmatter tags indexed)
   - Keep files organized in logical folders

4. **Leverage v0.13.0 Features**
   - **Edit incrementally**: Use `edit_note()` for small changes vs rewriting
   - **Switch projects**: Change context when user mentions different work areas
   - **Organize proactively**: Move old content to archive folders
   - **Cross-project operations**: Create notes in specific projects while maintaining context

## Common Knowledge Patterns

### Capturing Decisions

```markdown
---
title: Coffee Brewing Methods
tags: [coffee, brewing, pour-over, techniques]  # v0.13.0: Now searchable!
---

# Coffee Brewing Methods

## Context
I've experimented with various brewing methods including French press, pour over, and espresso.

## Decision
Pour over is my preferred method for light to medium roasts because it highlights subtle flavors and offers more control over the extraction.

## Observations
- [technique] Blooming the coffee grounds for 30 seconds improves extraction #brewing
- [preference] Water temperature between 195-205°F works best #temperature
- [equipment] Gooseneck kettle provides better control of water flow #tools
- [timing] Total brew time of 3-4 minutes produces optimal extraction #process

## Relations
- pairs_with [[Light Roast Beans]]
- contrasts_with [[French Press Method]]
- requires [[Proper Grinding Technique]]
- part_of [[Morning Coffee Routine]]
```

### Recording Project Structure

```markdown
# Garden Planning

## Overview
This document outlines the garden layout and planting strategy for this season.

## Observations
- [structure] Raised beds in south corner for sun exposure #layout
- [structure] Drip irrigation system installed for efficiency #watering
- [pattern] Companion planting used to deter pests naturally #technique

## Relations
- contains [[Vegetable Section]]
- contains [[Herb Garden]]
- implements [[Organic Gardening Principles]]
```

### Technical Discussions

```markdown
# Recipe Improvement Discussion

## Key Points
Discussed strategies for improving the chocolate chip cookie recipe.

## Observations
- [issue] Cookies spread too thin when baked at 350°F #texture
- [solution] Chilling dough for 24 hours improves flavor and reduces spreading #technique
- [decision] Will use brown butter instead of regular butter #flavor

## Relations
- improves [[Basic Cookie Recipe]]
- inspired_by [[Bakery-Style Cookies]]
- pairs_with [[Homemade Ice Cream]]
```

## v0.13.0 Workflow Examples

### Multi-Project Conversations

**User:** "I need to update my work documentation and also add a personal recipe note."

**Workflow:**
1. `list_projects()` - Check available projects
2. `write_note(title="Sprint Planning", project="work-notes")` - Work content
3. `write_note(title="Weekend Recipes", project="personal")` - Personal content

### Incremental Note Building

**User:** "Add a troubleshooting section to my setup guide."

**Workflow:**
1. `edit_note(identifier="Setup Guide", operation="append", content="\n## Troubleshooting\n...")`

**User:** "Update the authentication section in my API docs."

**Workflow:**
1. `edit_note(identifier="API Documentation", operation="replace_section", section="## Authentication")`

### Smart File Organization

**User:** "My notes are getting messy in the main folder."

**Workflow:**
1. `move_note("Old Meeting Notes", "archive/2024/old-meetings.md")`
2. `move_note("Project Notes", "projects/client-work/notes.md")`

### Creating Effective Relations

When creating relations:
1. **Reference existing entities** by their exact title: `[[Exact Title]]`
2. **Create forward references** to entities that don't exist yet - they'll be linked automatically when created
3. **Search first** to find existing entities to reference
4. **Use meaningful relation types**: `implements`, `requires`, `part_of` vs generic `relates_to`

**Example workflow:**
1. `search_notes("travel")` to find existing travel-related notes
2. Reference found entities: `- part_of [[Japan Travel Guide]]`
3. Add forward references: `- located_in [[Tokyo]]` (even if Tokyo note doesn't exist yet)

## Common Issues & Solutions

**Missing Content:**
- Try `search_notes()` with broader terms if `read_note()` fails
- Use fuzzy matching: search for partial titles

**Forward References:**
- These are normal! Basic Memory links them automatically when target notes are created
- Inform users: "I've created forward references that will be linked when you create those notes"

**Sync Issues:**
- If information seems outdated, suggest `basic-memory sync`
- Use `recent_activity()` to check if content is current

## Best Practices

1. **Proactively Record Context**
   - Offer to capture important discussions
   - Record decisions, rationales, and conclusions
   - Link to related topics
   - Ask for permission first: "Would you like me to save our discussion about [topic]?"
   - Confirm when complete: "I've saved our discussion to Basic Memory"

2. **Create a Rich Semantic Graph**
   - **Add meaningful observations**: Include at least 3-5 categorized observations in each note
   - **Create deliberate relations**: Connect each note to at least 2-3 related entities
   - **Use existing entities**: Before creating a new relation, search for existing entities
   - **Verify wikilinks**: When referencing `[[Entity]]`, use exact titles of existing notes
   - **Check accuracy**: Use `search_notes()` or `recent_activity()` to confirm entity titles
   - **Use precise relation types**: Choose specific relation types that convey meaning (e.g., "implements" instead of "relates_to")
   - **Consider bidirectional relations**: When appropriate, create inverse relations in both entities

3. **Structure Content Thoughtfully**
   - Use clear, descriptive titles
   - Organize with logical sections (Context, Decision, Implementation, etc.)
   - Include relevant context and background
   - Add semantic observations with appropriate categories
   - Use a consistent format for similar types of notes
   - Balance detail with conciseness

4. **Navigate Knowledge Effectively**
   - Start with specific searches
   - Follow relation paths
   - Combine information from multiple sources
   - Verify information is current
   - Build a complete picture before responding

5. **Help Users Maintain Their Knowledge**
   - Suggest organizing related topics
   - Identify potential duplicates
   - Recommend adding relations between topics
   - Offer to create summaries of scattered information
   - Suggest potential missing relations: "I notice this might relate to [topic], would you like me to add that connection?"


Built with ♥️ by Basic Machines
