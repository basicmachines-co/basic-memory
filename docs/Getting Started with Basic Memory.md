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

```bash
# Install with uv (recommended).  
uv tool install basic-memory

# Or with pip
pip install basic-memory
```

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
# Update with uv (recommended)
uv tool upgrade basic-memory 

# Or with pip 
pip install --upgrade basic-memory
```

> **Note**: After updating, you'll need to restart Claude Desktop and your sync process for changes to take effect.

### 5. Change the default project directory

By default, Basic Memory will create a project in the  `basic-memory` folder in your home directory. You can change this via the `project` [[CLI Reference#project|cli command]]. 

```  
# Add a new project  
basic-memory project add work ~/work-basic-memory  
  
# Set the default project  
basic-memory project default work  

# List all configured projects  
basic-memory project list  
```

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
   ---
   
   # Coffee Brewing Methods
   
   ## Observations
   - [method] Pour over provides more clarity...
   - [technique] Water temperature at 205°F...
   
   ## Relations
   - relates_to [[Other Coffee Topics]]
   ```

5. **Start the sync process** in a Terminal window (optional):
   ```bash
   basic-memory sync --watch
   ```
   Keep this running in the background.

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

### Building On Previous Knowledge

Basic Memory enables continuous knowledge building:

1. **Reference previous discussions** in new conversations
2. **Add to existing notes** through conversations
3. **Create connections** between related topics
4. **Follow relationships** to build comprehensive context

## Importing Existing Conversations

Import your existing AI conversations:

```bash
# From Claude
basic-memory import claude conversations

# From ChatGPT
basic-memory import chatgpt
```

After importing, the changes will be synced. Initial syncs may take a few moments. You can see info about your project by running `basic-memrory project info`.

## Quick Tips

- Basic Memory will sync changes from your project in real time.
- Use special prompts (Continue Conversation, Recent Activity, Search) to start contextual discussions
- Build connections between notes for a richer knowledge graph
- Use direct `memory://` URLs with a permalink when you need precise context. See [[User Guide#Using memory // URLs]]
- Use git to version control your knowledge base (git integration is on the roadmap)
- Review and edit AI-generated notes for accuracy

## Next Steps

After getting started, explore these areas:

1. **Read the [[User Guide]]** for comprehensive usage instructions
2. **Understand the [[Knowledge Format]]** to learn how knowledge is structured
3. **Set up [[Obsidian Integration]]** for visual knowledge navigation
4. **Learn about [[Canvas]]** visualizations for mapping concepts
5. **Review the [[CLI Reference]]** for command line tools