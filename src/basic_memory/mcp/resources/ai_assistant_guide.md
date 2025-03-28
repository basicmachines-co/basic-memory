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

```python
# Writing knowledge - THE MOST IMPORTANT TOOL!
response = await write_note(
    title="Search Design",  # Required: Note title
    content="# Search Design\n...",  # Required: Note content
    folder="specs",  # Optional: Folder to save in
    tags=["search", "design"],  # Optional: Tags for categorization
    verbose=True  # Optional: Get parsing details
)

# Reading knowledge
content = await read_note("Search Design")  # By title
content = await read_note("specs/search-design")  # By path
content = await read_note("memory://specs/search")  # By memory URL

# Searching for knowledge
results = await search_notes(
    query="authentication system",  # Text to search for
    page=1,  # Optional: Pagination
    page_size=10  # Optional: Results per page
)

# Building context from the knowledge graph
context = await build_context(
    url="memory://specs/search",  # Starting point
    depth=2,  # Optional: How many hops to follow
    timeframe="1 month"  # Optional: Recent timeframe
)

# Checking recent changes
activity = await recent_activity(
    type="all",  # Optional: Entity types to include
    depth=1,  # Optional: Related items to include
    timeframe="1 week"  # Optional: Time window
)

# Creating a knowledge visualization
canvas_result = await canvas(
    nodes=[{"id": "note1", "label": "Search Design"}],  # Nodes to display
    edges=[{"from": "note1", "to": "note2"}],  # Connections
    title="Project Overview",  # Canvas title
    folder="diagrams"  # Storage location
)
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
    search_results = await search_notes("travel")
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
        verbose=True
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
       results = await search_notes("Document")
       if results and results.primary_results:
           # Found something similar
           content = await read_note(results.primary_results[0].permalink)
   ```

2. **Forward References (Unresolved Relations)**
   ```python
   response = await write_note(..., verbose=True)
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

3. **Sync Issues**
   ```python
   # If information seems outdated
   activity = await recent_activity(timeframe="1 hour")
   if not activity or not activity.primary_results:
       print("It seems there haven't been recent updates. You might need to run 'basic-memory sync'.")
   ```

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
    - **Use precise relation types**: Choose specific relation types that convey meaning (e.g., "implements" instead
      of "relates_to")
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
    - Suggest potential missing relations: "I notice this might relate to [topic], would you like me to add that
      connection?"

Built with ♥️ b
y Basic Machines