# AI Assistant Guide for Basic Memory

This guide helps AIs use Basic Memory tools effectively when working with users. It covers reading, writing, and
navigating knowledge through the Model Context Protocol (MCP).

## Overview

Basic Memory allows you and users to record context in local Markdown files, building a rich knowledge base through
natural conversations. The system automatically creates a semantic knowledge graph from simple text patterns.

- **Local-First**: All data is stored in plain text files on the user's computer
- **Real-Time**: Users see content updates immediately
- **Bi-Directional**: Both you and users can read and edit notes
- **Semantic**: Simple patterns create a structured knowledge graph
- **Persistent**: Knowledge persists across sessions and conversations

## Project Management and Configuration

Basic Memory uses a **stateless architecture** where each tool call can specify which project to work with. This provides three ways to determine the active project:

### Three-Tier Project Resolution

1. **CLI Constraint (Highest Priority)**: When Basic Memory is started with `--project project-name`, all operations are constrained to that project
2. **Explicit Project Parameter (Medium Priority)**: When you specify `project="project-name"` in tool calls
3. **Default Project Mode (Lowest Priority)**: When `default_project_mode=true` in configuration, tools automatically use the configured `default_project`

### Default Project Mode

When `default_project_mode` is enabled in the user's configuration:
- All tools become more convenient - no need to specify project repeatedly
- Perfect for users who primarily work with a single project
- Still allows explicit project specification when needed
- Falls back gracefully to multi-project mode if no default is configured

```python
# With default_project_mode enabled, these are equivalent:
await write_note("My Note", "Content", "folder")
await write_note("My Note", "Content", "folder", project="default-project")

# You can still override with explicit project:
await write_note("My Note", "Content", "folder", project="other-project")
```

### Project Discovery

If you're unsure which project to use:
```python
# Discover available projects
projects = await list_memory_projects()

# See recent activity across projects for recommendations
activity = await recent_activity()  # Shows cross-project activity and suggestions
```

## The Importance of the Knowledge Graph

**Basic Memory's value comes from connections between notes, not just the notes themselves.**

When writing notes, your primary goal should be creating a rich, interconnected knowledge graph:

1. **Increase Semantic Density**: Add multiple observations and relations to each note
2. **Use Accurate References**: Aim to reference existing entities by their exact titles
3. **Create Forward References**: Feel free to reference entities that don't exist yet - Basic Memory will resolve these
   when they're created later
4. **Create Bidirectional Links**: When appropriate, connect entities from both directions
5. **Use Meaningful Categories**: Add semantic context with appropriate observation categories
6. **Choose Precise Relations**: Use specific relation types that convey meaning

Remember: A knowledge graph with 10 heavily connected notes is more valuable than 20 isolated notes. Your job is to help
build these connections!

## Core Tools Reference

### Knowledge Creation and Editing

```python
# Writing knowledge - THE MOST IMPORTANT TOOL!
response = await write_note(
    title="Search Design",  # Required: Note title
    content="# Search Design\n...",  # Required: Note content
    folder="specs",  # Required: Folder to save in
    tags=["search", "design"],  # Optional: Tags for categorization
    project="my-project"  # Optional: Explicit project (uses default if not specified)
)

# Editing existing notes
await edit_note(
    identifier="Search Design",  # Required: Note to edit
    operation="append",  # Required: append, prepend, find_replace, replace_section
    content="\n## New Section\nAdditional content",  # Required: Content to add/replace
    project="my-project"  # Optional: Explicit project
)

# Moving notes
await move_note(
    identifier="Search Design",  # Required: Note to move
    destination_path="archive/old-search-design.md",  # Required: New location
    project="my-project"  # Optional: Explicit project
)

# Deleting notes
success = await delete_note(
    identifier="Old Draft",  # Required: Note to delete
    project="my-project"  # Optional: Explicit project
)
```

### Knowledge Reading and Discovery

```python
# Reading knowledge
content = await read_note("Search Design")  # By title (uses default project)
content = await read_note("specs/search-design")  # By path
content = await read_note("memory://specs/search")  # By memory URL
content = await read_note("Search Design", project="work-docs")  # Explicit project

# Reading raw file content (text, images, binaries)
file_data = await read_content(
    path="assets/diagram.png",  # Required: File path
    project="my-project"  # Optional: Explicit project
)

# Viewing notes as formatted artifacts
await view_note(
    identifier="Search Design",  # Required: Note to view
    project="my-project",  # Optional: Explicit project
    page=1,  # Optional: Pagination
    page_size=10  # Optional: Items per page
)

# Browsing directory contents
listing = await list_directory(
    dir_name="/specs",  # Optional: Directory path (default: "/")
    depth=2,  # Optional: Recursion depth
    file_name_glob="*.md",  # Optional: File pattern filter
    project="my-project"  # Optional: Explicit project
)
```

### Search and Context

```python
# Searching for knowledge
results = await search_notes(
    query="authentication system",  # Required: Text to search for
    project="my-project",  # Optional: Explicit project
    page=1,  # Optional: Pagination
    page_size=10,  # Optional: Results per page
    search_type="text",  # Optional: "text", "title", or "permalink"
    types=["entity"],  # Optional: Filter by content types
    entity_types=["observation"],  # Optional: Filter by entity types
    after_date="1 week"  # Optional: Recent content only
)

# Building context from the knowledge graph
context = await build_context(
    url="memory://specs/search",  # Required: Starting point
    project="my-project",  # Optional: Explicit project
    depth=2,  # Optional: How many hops to follow
    timeframe="1 month",  # Optional: Recent timeframe
    max_related=10  # Optional: Max related items
)

# Checking recent changes
activity = await recent_activity(
    type=["entity", "relation"],  # Optional: Entity types to include
    depth=1,  # Optional: Related items to include
    timeframe="1 week",  # Optional: Time window
    project="my-project"  # Optional: Explicit project (None for cross-project discovery)
)
```

### Visualization and Project Management

```python
# Creating a knowledge visualization
canvas_result = await canvas(
    nodes=[{"id": "note1", "type": "file", "file": "Search Design.md"}],  # Required: Nodes
    edges=[{"id": "edge1", "fromNode": "note1", "toNode": "note2"}],  # Required: Edges
    title="Project Overview",  # Required: Canvas title
    folder="diagrams",  # Required: Storage location
    project="my-project"  # Optional: Explicit project
)

# Project management
projects = await list_memory_projects()  # List all available projects
project_info = await project_info(project="my-project")  # Get project statistics
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
- Relations: `relates_to`, `implements`, `requires`, `extends`, `part_of`, `pairs_with`, `inspired_by`,
  `originated_from`

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
   await write_note(
       title="Search Functionality Discussion",
       content="# Search Functionality Discussion\n...",
       folder="discussions"
   )
   ```

2. **Referencing existing knowledge**:
   ```
   Human: "Take a look at memory://specs/search"

   You: I'll examine that information.
   context = await build_context(url="memory://specs/search")
   content = await read_note("specs/search")
   ```

3. **Finding information**:
   ```
   Human: "What were our decisions about auth?"

   You: Let me find that information for you.
   results = await search_notes(query="auth decisions")
   context = await build_context(url=f"memory://{results[0].permalink}")
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
    - Using the same title+folder will overwrite existing notes
    - Structure content with clear headings and sections
    - Use semantic markup for observations and relations
    - Keep files organized in logical folders

## Common Knowledge Patterns

### Capturing Decisions

```markdown
# Coffee Brewing Methods

## Context

I've experimented with various brewing methods including French press, pour over, and espresso.

## Decision

Pour over is my preferred method for light to medium roasts because it highlights subtle flavors and offers more control
over the extraction.

## Observations

- [technique] Blooming the coffee grounds for 30 seconds improves extraction #brewing
- [preference] Water temperature between 195-205°F works best #temperature
- [equipment] Gooseneck kettle provides better control of water flow #tools

## Relations

- pairs_with [[Light Roast Beans]]
- contrasts_with [[French Press Method]]
- requires [[Proper Grinding Technique]]
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

### Creating Effective Relations

When creating relations, you can:

1. Reference existing entities by their exact title
2. Create forward references to entities that don't exist yet

```python
# Example workflow for creating notes with effective relations
async def create_note_with_effective_relations():
    # Search for existing entities to reference
    search_results = await search_notes(query="travel")
    existing_entities = [result.title for result in search_results.primary_results]

    # Check if specific entities exist
    packing_tips_exists = "Packing Tips" in existing_entities
    japan_travel_exists = "Japan Travel Guide" in existing_entities

    # Prepare relations section - include both existing and forward references
    relations_section = "## Relations\n"

    # Existing reference - exact match to known entity
    if packing_tips_exists:
        relations_section += "- references [[Packing Tips]]\n"
    else:
        # Forward reference - will be linked when that entity is created later
        relations_section += "- references [[Packing Tips]]\n"

    # Another possible reference
    if japan_travel_exists:
        relations_section += "- part_of [[Japan Travel Guide]]\n"

    # You can also check recently modified notes to reference them
    recent = await recent_activity(timeframe="1 week")
    recent_titles = [item.title for item in recent.primary_results]

    if "Transportation Options" in recent_titles:
        relations_section += "- relates_to [[Transportation Options]]\n"

    # Always include meaningful forward references, even if they don't exist yet
    relations_section += "- located_in [[Tokyo]]\n"
    relations_section += "- visited_during [[Spring 2023 Trip]]\n"

    # Now create the note with both verified and forward relations
    content = f"""# Tokyo Neighborhood Guide

## Overview
Details about different Tokyo neighborhoods and their unique characteristics.

## Observations
- [area] Shibuya is a busy shopping district #shopping
- [transportation] Yamanote Line connects major neighborhoods #transit
- [recommendation] Visit Shimokitazawa for vintage shopping #unique
- [tip] Get a Suica card for easy train travel #convenience

{relations_section}
    """

    result = await write_note(
        title="Tokyo Neighborhood Guide",
        content=content,
        folder="travel"
    )

    # You can check which relations were resolved and which are forward references
    if result and 'relations' in result:
        resolved = [r['to_name'] for r in result['relations'] if r.get('target_id')]
        forward_refs = [r['to_name'] for r in result['relations'] if not r.get('target_id')]

        print(f"Resolved relations: {resolved}")
        print(f"Forward references that will be resolved later: {forward_refs}")
```

## Error Handling

Common issues to watch for:

1. **Missing Content**
   ```python
   try:
       content = await read_note("Document")
   except:
       # Try search instead
       results = await search_notes(query="Document")
       if results and results.primary_results:
           # Found something similar
           content = await read_note(results.primary_results[0].permalink)
   ```

2. **Forward References (Unresolved Relations)**
   ```python
   response = await write_note(
       title="My Note",
       content="Content with [[Forward Reference]]",
       folder="notes"
   )
   # Check for forward references (unresolved relations)
   forward_refs = []
   for relation in response.get('relations', []):
       if not relation.get('target_id'):
           forward_refs.append(relation.get('to_name'))

   if forward_refs:
       # This is a feature, not an error! Inform the user about forward references
       print(f"Note created with forward references to: {forward_refs}")
       print("These will be automatically linked when those notes are created.")

       # Optionally suggest creating those entities now
       print("Would you like me to create any of these notes now to complete the connections?")
   ```

3. **Project Discovery Issues**
   ```python
   # If user asks about content but no default project is configured
   try:
       results = await search_notes(query="user query")
   except Exception as e:
       if "project" in str(e).lower():
           # Show available projects and ask user to choose
           projects = await list_memory_projects()
           print(f"Available projects: {[p.name for p in projects]}")
           print("Which project should I search in?")
   ```

4. **Sync Issues**
   ```python
   # If information seems outdated
   activity = await recent_activity(timeframe="1 hour")
   if not activity or not activity.primary_results:
       print("It seems there haven't been recent updates. You might need to run 'basic-memory sync'.")
   ```

## Best Practices

1. **Smart Project Management**
    - **For new users**: Call `recent_activity()` without project parameter to discover active projects and get recommendations
    - **For known projects**: Use explicit project parameters when switching between multiple projects
    - **For single-project users**: Rely on default_project_mode for convenience
    - **When uncertain**: Use `list_memory_projects()` to show available options and ask the user
    - **Remember choices**: Once a user indicates their preferred project, use it consistently throughout the conversation

2. **Proactively Record Context**
    - Offer to capture important discussions
    - Record decisions, rationales, and conclusions
    - Link to related topics
    - Ask for permission first: "Would you like me to save our discussion about [topic]?"
    - Confirm when complete: "I've saved our discussion to Basic Memory"

3. **Create a Rich Semantic Graph**
    - **Add meaningful observations**: Include at least 3-5 categorized observations in each note
    - **Create deliberate relations**: Connect each note to at least 2-3 related entities
    - **Use existing entities**: Before creating a new relation, search for existing entities
    - **Verify wikilinks**: When referencing `[[Entity]]`, use exact titles of existing notes
    - **Check accuracy**: Use `search_notes()` or `recent_activity()` to confirm entity titles
    - **Use precise relation types**: Choose specific relation types that convey meaning (e.g., "implements" instead
      of "relates_to")
    - **Consider bidirectional relations**: When appropriate, create inverse relations in both entities

4. **Structure Content Thoughtfully**
    - Use clear, descriptive titles
    - Organize with logical sections (Context, Decision, Implementation, etc.)
    - Include relevant context and background
    - Add semantic observations with appropriate categories
    - Use a consistent format for similar types of notes
    - Balance detail with conciseness

5. **Navigate Knowledge Effectively**
    - Start with specific searches using `search_notes()`
    - Follow relation paths with `build_context()`
    - Combine information from multiple sources
    - Verify information is current with `recent_activity()`
    - Build a complete picture before responding
    - Use appropriate project context for searches

6. **Help Users Maintain Their Knowledge**
    - Suggest organizing related topics across projects when appropriate
    - Identify potential duplicates using search
    - Recommend adding relations between topics
    - Offer to create summaries of scattered information
    - Suggest potential missing relations: "I notice this might relate to [topic], would you like me to add that connection?"
    - Help users decide when to use explicit vs default project parameters

Built with ♥️ b
y Basic Machines