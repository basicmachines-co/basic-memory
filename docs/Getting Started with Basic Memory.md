---
title: Getting Started with Basic Memory
type: note
permalink: docs/getting-started
---

# Getting Started with Basic Memory

This guide will help you install Basic Memory, configure it with Claude Desktop, and create your first knowledge notes
through conversations.

Basic Memory uses the [Model Context Protocol](https://modelcontextprotocol.io/introduction) (MCP) to connect with LLMs.
It can be used with any service that supports the MCP, but Claude Desktop works especially well.

## Installation

### Prerequisites

The easiest way to install basic memory is via `uv`. See the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).

### 1. Install Basic Memory

**v0.13.0 offers multiple installation options:**

```bash
# Stable release (recommended)
uv tool install basic-memory
# or: pip install basic-memory

# Beta releases (new features, testing)
pip install basic-memory --pre

# Development builds (latest changes)
pip install basic-memory --pre --force-reinstall
```

**Version Information:**
- **Stable**: Latest tested release (e.g., `0.13.0`)
- **Beta**: Pre-release versions (e.g., `0.13.0b1`) 
- **Development**: Auto-published from git commits (e.g., `0.12.4.dev26+468a22f`)

> **Important**: You need to install Basic Memory using one of the commands above to use the command line tools.

Using `uv tool install` will install the basic-memory package in a standalone virtual environment. See the [UV docs](https://docs.astral.sh/uv/concepts/tools/) for more info.

### 2. Configure Claude Desktop

Edit your Claude Desktop config, located at `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "basic-memory": {
      "command": "uvx",
      "args": [
        "basic-memory",
        "mcp"
      ]
    }
  }
}
```

**Restart Claude Desktop**. You should see Basic Memory tools available in the "tools" menu in Claude Desktop (the little hammer icon in the bottom-right corner of the chat interface). Click it to view available tools.
#### Fix Path to uv

If you get an error that says `ENOENT` , this most likely means Claude Desktop could not find your `uv` installation. Make sure that you have `uv` installed per the instructions above, then:

**Step 1: Find the absolute path to uvx**

Open Terminal and run:

```bash
which uvx
```

This will show you the full path (e.g., `/Users/yourusername/.cargo/bin/uvx`).

**Step 2: Edit Claude Desktop Configuration**

Edit the Claude Desktop config:

```json
{
  "mcpServers": {
    "basic-memory": {
      "command": "/absolute/path/to/uvx",
      "args": [
        "basic-memory",
        "mcp"
      ]
    }
  }
}
```

Replace `/absolute/path/to/uvx` with the actual path you found in Step 1.

**Step 3: Restart Claude Desktop**

Close and reopen Claude Desktop for the changes to take effect.

### 3. Sync changes in real time

> **Note**: The service will sync changes from your project directory in real time so they available for the AI assistant. 

To disable realtime sync, you can update the config. See [[CLI Reference#sync]].
### 4. Staying Updated

To update Basic Memory when new versions are released:

```bash
# Update stable release
uv tool upgrade basic-memory 
# or: pip install --upgrade basic-memory

# Update to latest beta (v0.13.0)
pip install --upgrade basic-memory --pre

# Get latest development build
pip install --upgrade basic-memory --pre --force-reinstall
```

**v0.13.0 Update Benefits:**
- **Fluid project switching** during conversations
- **Advanced note editing** capabilities
- **Smart file management** with move operations
- **Enhanced search** with frontmatter tag support

> **Note**: After updating, restart Claude Desktop for changes to take effect. No sync restart needed in v0.13.0.

### 5. Multi-Project Setup (Enhanced in v0.13.0)

By default, Basic Memory creates a project in `~/basic-memory`. v0.13.0 introduces **fluid project management** - switch between projects instantly during conversations.

```  
# Create a new project
basic-memory project create work ~/work-basic-memory  
  
# Set the default project  
basic-memory project set-default work  

# List all projects with status
basic-memory project list

# Get detailed project information
basic-memory project info
```

**New in v0.13.0:**
- **Instant switching**: Change projects during conversations without restart
- **Unified database**: All projects in single `~/.basic-memory/memory.db`
- **Better performance**: Optimized queries and reduced file I/O
- **Session context**: Maintains active project throughout conversations

## Troubleshooting Installation

### Common Issues

#### Claude Says "No Basic Memory Tools Available"

If Claude cannot find Basic Memory tools:

1. **Check absolute paths**: Ensure you're using complete absolute paths to uvx in the Claude Desktop configuration
2. **Verify installation**: Run `basic-memory --version` in Terminal to confirm Basic Memory is installed
3. **Restart applications**: Restart both Terminal and Claude Desktop after making configuration changes
4. **Check sync status**: You can view the sync status by running `basic-memory status
.
#### Permission Issues

If you encounter permission errors:

1. Check that Basic Memory has access to create files in your home directory
2. Ensure Claude Desktop has permission to execute the uvx command

## Creating Your First Knowledge Note

1. **Open Claude Desktop** and start a new conversation.

2. **Have a natural conversation** about any topic:
   ```
   You: "Let's talk about coffee brewing methods I've been experimenting with."
   Claude: "I'd be happy to discuss coffee brewing methods..."
   You: "I've found that pour over gives more flavor clarity than French press..."
   ```

3. **Ask Claude to create a note**:
   ```
   You: "Could you create a note summarizing what we've discussed about coffee brewing?"
   ```

4. **Confirm note creation**:
   Claude will confirm when the note has been created and where it's stored.

5. **View the created file** in your `~/basic-memory` directory using any text editor or Obsidian.
   The file structure will look similar to:
   ```markdown
   ---
   title: Coffee Brewing Methods
   permalink: coffee-brewing-methods
   tags: [coffee, brewing, equipment]  # v0.13.0: Now searchable!
   ---
   
   # Coffee Brewing Methods
   
   ## Observations
   - [method] Pour over provides more clarity...
   - [technique] Water temperature at 205°F...
   
   ## Relations
   - relates_to [[Other Coffee Topics]]
   ```

**v0.13.0 Improvements:**
- **Real-time sync**: Changes appear immediately, no background sync needed
- **Searchable tags**: Frontmatter tags are now indexed for search
- **Better file organization**: Enhanced file management capabilities

## Using Special Prompts

Basic Memory includes special prompts that help you start conversations with context from your knowledge base:

### Continue Conversation

To resume a previous topic:

```
You: "Let's continue our conversation about coffee brewing."
```

This prompt triggers Claude to:

1. Search your knowledge base for relevant content about coffee brewing
2. Build context from these documents
3. Resume the conversation with full awareness of previous discussions

### Recent Activity

To see what you've been working on:

```
You: "What have we been discussing recently?"
```

This prompt causes Claude to:

1. Retrieve documents modified in the recent past
2. Summarize the topics and main points
3. Offer to continue any of those discussions

### Search

To find specific information:

```
You: "Find information about pour over coffee methods."
```

Claude will:

1. Search your knowledge base for relevant documents
2. Summarize the key findings
3. Offer to explore specific documents in more detail

See [[User Guide#Using Special Prompts]] for further information.

## Using Your Knowledge Base

### Referencing Knowledge

In future conversations, reference your existing knowledge:

```
You: "What water temperature did we decide was optimal for coffee brewing?"
```

Or directly reference notes using memory:// URLs:

```
You: "Take a look at memory://coffee-brewing-methods and let's discuss how to improve my technique."
```

### Building On Previous Knowledge (Enhanced in v0.13.0)

Basic Memory enables continuous knowledge building:

1. **Reference previous discussions** in new conversations
2. **Edit notes incrementally** without rewriting entire documents
3. **Move and organize notes** as your knowledge base grows
4. **Switch between projects** instantly during conversations
5. **Search by tags** to find related content quickly
6. **Create connections** between related topics
7. **Follow relationships** to build comprehensive context

### v0.13.0 Workflow Examples

**Incremental Editing:**
```
You: "Add a section about espresso to my coffee brewing notes"
Claude: [Uses edit_note to append new section]
```

**File Organization:**
```
You: "Move my old meeting notes to an archive folder"
Claude: [Uses move_note with database consistency]
```

**Project Switching:**
```
You: "Switch to my work project and show recent activity"
Claude: [Switches projects and shows work-specific content]
```

## Importing Existing Conversations

Import your existing AI conversations:

```bash
# From Claude
basic-memory import claude conversations

# From ChatGPT
basic-memory import chatgpt
```

After importing, changes sync automatically in real-time. You can see project statistics by running `basic-memory project info`.

## Quick Tips

### General Usage
- Basic Memory syncs changes in real-time (no manual sync needed)
- Use special prompts (Continue Conversation, Recent Activity, Search) to start contextual discussions
- Build connections between notes for a richer knowledge graph
- Use direct `memory://` URLs with permalinks for precise context
- Review and edit AI-generated notes for accuracy

### v0.13.0 Features
- **Switch projects instantly**: "Switch to my work project" - no restart needed
- **Edit notes incrementally**: "Add a section about..." instead of rewriting
- **Organize with moves**: "Move this to my archive folder" with database consistency
- **Search by tags**: Frontmatter tags are now searchable
- **Try beta builds**: `pip install basic-memory --pre` for latest features

## Next Steps

After getting started, explore these areas:

1. **Read the [[User Guide]]** for comprehensive usage instructions
2. **Understand the [[Knowledge Format]]** to learn how knowledge is structured
3. **Set up [[Obsidian Integration]]** for visual knowledge navigation
4. **Learn about [[Canvas]]** visualizations for mapping concepts
5. **Review the [[CLI Reference]]** for command line tools
6. **Explore [[OAuth Authentication Guide]]** for secure remote access (v0.13.0)
7. **Set up multiple projects** for different knowledge areas (v0.13.0)