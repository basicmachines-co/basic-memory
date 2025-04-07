---
title: Knowledge Format
type: note
permalink: docs/knowledge-format
tags:
- architecture
- patterns
- knowledge
- design
---

# Knowledge Format

Basic Memory uses standard Markdown with simple semantic patterns to create a knowledge graph. This document details the file structure and patterns used to organize knowledge.

## File-First Architecture

All knowledge in Basic Memory is stored in plain text Markdown files:

- Files are the source of truth for all knowledge
- Changes to files automatically update the knowledge graph
- You maintain complete ownership and control
- Files work with git and other version control systems
- Knowledge persists independently of any AI conversation

## Core Document Structure

Every document uses this basic structure:

```markdown
---
title: Document Title
type: note
tags: [tag1, tag2]
permalink: custom-path
---

# Document Title

Regular markdown content...

## Observations
- [category] Content with #tags (optional context)

## Relations
- relation_type [[Other Document]] (optional context)
```

### Frontmatter

The YAML frontmatter at the top of each file defines essential metadata:

```yaml
---
title: Document Title    # Used for linking and references
type: note               # Document type
tags: [tag1, tag2]       # For organization and searching
permalink: custom-link   # Optional custom URL path
---
```

The title is particularly important as it's used to create links between documents.

### Observations

Observations are facts or statements about a topic:

```markdown
## Observations
- [tech] Uses SQLite for storage #database
- [design] Follows local-first architecture #architecture
- [decision] Selected bcrypt for passwords #security (Based on audit)
```

Each observation contains:
- **Category** in [brackets] - classifies the information type
- **Content text** - the main information
- Optional **#tags** - additional categorization
- Optional **(context)** - supporting details

Common categories include:
- `[tech]`: Technical details
- `[design]`: Architecture decisions
- `[feature]`: User capabilities
- `[decision]`: Choices that were made
- `[principle]`: Fundamental concepts
- `[method]`: Approaches or techniques
- `[preference]`: Personal opinions

### Relations

Relations connect documents to form the knowledge graph:

```markdown
## Relations
- implements [[Search Design]]
- depends_on [[Database Schema]]
- relates_to [[User Interface]]
```

You can also create inline references:
```markdown
This builds on [[Core Design]] and uses [[Utility Functions]].
```

Common relation types include:
- `implements`: Implementation of a specification
- `depends_on`: Required dependency
- `relates_to`: General connection
- `inspired_by`: Source of ideas
- `extends`: Enhancement
- `part_of`: Component relationship
- `contains`: Hierarchical relationship
- `pairs_with`: Complementary relationship

## Knowledge Graph

Basic Memory automatically builds a knowledge graph from your document connections:

- Each document becomes a node in the graph
- Relations create edges between nodes
- Relation types add semantic meaning to connections
- Forward references can link to documents that don't exist yet

This graph enables rich context building and navigation across your knowledge base.

## Permalinks and memory:// URLs

Every document in Basic Memory has a unique permalink that serves as its stable identifier:

### How Permalinks Work

- **Automatically assigned**: The system generates a permalink for each document
- **Based on title**: By default, derived from the document title
- **Always unique**: If conflicts exist, the system adds a suffix to ensure uniqueness
- **Stable reference**: Remains the same even if the file moves in the directory structure
- **Used in memory:// URLs**: Forms the basis of the memory:// addressing scheme

You can specify a custom permalink in the frontmatter:
```yaml
---
title: Authentication Approaches
permalink: auth-approaches-2024
---
```

If not specified, one will be generated automatically from the title, if the note has has a frontmatter section. 

By default a  notes' permalink value will not change if the file is moved. It's a **stable** identifier :). But if you'd rather permalinks are always updated when a file moves, you can set the config setting in the global config. 

The config file for Basic Memory is in the home directory under `.basic-memory/config.json`.

To change the behavior, set the following value:
```
 ~/.basic-memory/config.json 
{
  "update_permalinks_on_move": true
}
```

### Using memory:// URLs

The memory:// URL scheme provides a reliable way to reference knowledge:

```
memory://auth-approaches-2024        # Direct access by permalink
memory://Authentication Approaches   # Access by title (automatically resolves)
memory://project/auth-approaches     # Access by path
```

Memory URLs support pattern matching for more powerful queries:

```
memory://auth*                      # All documents with permalinks starting with "auth"
memory://*/approaches               # All documents with permalinks ending with "approaches"
memory://project/*/requirements     # All requirements documents in the project folder
memory://docs/search/implements/*   # Follow all implements relations from search docs
```

This addressing scheme ensures content remains accessible even as your knowledge base evolves and files are reorganized.

## File Organization

Organize files in any structure that suits your needs:

```
docs/
  architecture/
    design.md
    patterns.md
  features/
    search.md
    auth.md
```

You can:
- Group by topic in folders
- Use a flat structure with descriptive filenames
- Tag files for easier discovery
- Add custom metadata in frontmatter

The system will build the semantic knowledge graph regardless of how you organize your files.

## Relations
- implemented_by [[User Guide]] (How to work with this format)
- relates_to [[Getting Started with Basic Memory]] (Setup instructions)
- explained_in [[Introduction to Basic Memory]] (Overview of the system)