# AI Assistant Guide

This guide explains how to use Basic Memory's tools effectively when working with users.
It explains how to read, write, and navigate knowledge through the Model Context Protocol (MCP).

## Overview

Basic Memory allows users and LLMs to record context in local files using plain text Markdown formats to build a rich,
organized knowledge base through natural conversations and simple tools.

- LLMs can read and write notes
- Users can see content in real time
- Simple Markdown formats are parsed to create a semantic knowledge graph
- All data is local and stored in plain text files on the user's computer
- Files can be updated externally and synced back to the knowledge base

## Core Tools

Basic Memory provides several tools through the MCP (Model Context Protocol) for LLMs:

```python
# Writing knowledge
response = await write_note(
    title="Search Design",
    content=content,
    folder="specs",
    tags=["search", "design"],
    verbose=True  # Get parsing details
)

# Reading knowledge
content = await read_note("Search Design")  # By title
content = await read_note("specs/search")  # By path
content = await read_note("memory://specs/search")  # By memory url

# Building context
context = await build_context("memory://specs/search")

# Following relations
impl = await build_context("memory://specs/search/implements/*")

# Checking changes
activity = await recent_activity(timeframe="1 week")

# Creating a json canvas diagram
activity = await canvas(...)

```

## Semantic Markup in Plain Text

Knowledge is encoded within standard markdown using semantic conventions that are both human-readable and
machine-processable.

**Key aspects:**

- Files in the knowledge base are each an `Entity` within the system
- Markdown files can contain semantic content through simple markup.
- `Observations` as categorized list items
- `Relations` as wiki-style links with types
- Frontmatter for metadata
- Minimal specialized syntax

**Examples:**

- Observation syntax: `- [category] Content text #tag1 #tag2 (optional context)`
- Relation syntax: `- relation_type [[Entity]] (optional context)`
- Inline relations through `[[Entity]]` Wiki Link style references

## Knowledge Graph Through Relations

Connections between documents create a knowledge graph without requiring a specialized database.

**Key aspects:**

- Relations create edges between document nodes
- Relation types provide semantic meaning to connections
- Navigation between knowledge via relation traversal
- Emergent structure through use

**Examples:**

- `implements`, `extends`, `relates_to` relations
- Following paths like `docs/search/implements/*`
- Context building by walking the graph

## Understanding Users

Users will interact in patterns like:

1. Creating knowledge:
   ```
   Human: "Let's write up what we discussed about search."
   
   Response: I'll create a note capturing our discussion.
   ```

AI Actions:

- record note via `write_note("...")`

1. Referencing existing knowledge:
   ```
   Human: "Take a look at memory://specs/search"
   
   Response: Let me build context from that and related documents.
   ```

AI Actions:

- build context via `build_context("memory://specs/search")`
- examine results
- read content via `read_note()`


2. Finding information:
   ```
   Human: "What were our decisions about auth?"
   
   Response: I'll search for relevant notes and build context.
   ```

AI Actions:

- search via `search("auth")`
- examine results
- read content

## Key Things to Remember

3. **Files are Truth**
    - Everything lives in local files
    - Users control their files
    - Always check verbose output
    - The user can update files locally outside the LLM
    - Changes need to be synced by the user

4. **Building Context**
    - Start specific
    - Follow relations
    - Check recent changes
    - Build incrementally

5. **Writing Knowledge**
    - Using the same title + folder will overwrite a note
    - Use semantic markup
    - Create useful relations
    - Keep files organized

## Common Patterns

### Capturing Discussions

```python
# Document a decision
response = await write_note(
    title="Auth System Decision",
    folder="decisions",
    content="""# Auth System Decision
    
    ## Context
    Evaluated different auth approaches...
    
    ## Decision
    Selected JWT-based authentication because...
    
    ## Observations
    - [decision] Using JWT for auth #auth
    - [tech] Implementing with bcrypt #security
    
    ## Relations
    - affects [[Auth System]]
    - based_on [[Security Requirements]]
    """
)

```

### Building Understanding

```python
async def explore_topic(topic):
    # Get main context
    context = await build_context(f"memory://{topic}")

    # Find implementations
    impl = await build_context(
        f"memory://{topic}/implements/*"
    )

    # Get recent changes
    activity = await recent_activity(timeframe="1 week")
    relevant = [r for r in activity.primary_results
                if topic in r.permalink]

    # Build comprehensive view
    for result in relevant:
        details = await build_context(
            f"memory://{result.permalink}"
        )
```

### Handling Files

```python
# Check before writing
try:
    existing = await read_note("Search Design")
    # Update existing
    await write_note(
        title="Search Design",
        content=updated_content,
        verbose=True
    )
except:
    # Create new
    await write_note(
        title="Search Design",
        content=new_content,
    )
```

## Error Handling

Common issues to watch for:

6. **Missing Content**
   ```python
   try:
       content = await read_note("Document")
   except:
       # Try search
       results = await search({"text": "Document"})
   ```

7. **Unresolved Relations**
   ```python
   response = await write_note(..., verbose=True)
   for relation in response['relations']:
       if not relation['target']:
           # Relation didn't resolve
           # Might need sync
           # Or target doesn't exist
   ```

8. **Pattern Matching**
   ```python
   # If pattern fails, try:
   # - More specific path
   # - Direct lookup
   # - Search instead
   # - Recent activity
   ```

## Best Practices

1. **Read and write Notes as needed**
    - Write notes to record context
    - See what was parsed
    - Check relations
    - Verify changes

2. **Build Context Carefully**
    - Start specific
    - Follow logical paths
    - Combine approaches
    - Stay relevant

3. **Write Clean Content**
    - Clear structure
    - Good organization
    - Useful relations
    - Regular cleanup

Built with ♥️ by Basic Machines