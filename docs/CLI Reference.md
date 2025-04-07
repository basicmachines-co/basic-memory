---
title: CLI Reference
type: note
permalink: docs/cli-reference
---

# CLI Reference

Basic Memory provides command line tools for managing your knowledge base. This reference covers the available commands and their options.

## Core Commands

### sync

Keeps files and the knowledge graph in sync:

```bash
# Basic sync
basic-memory sync

# Watch for changes
basic-memory sync --watch

# Show detailed sync information
basic-memory sync --verbose
```

Options:
- `--watch`: Continuously monitor for changes
- `--verbose`: Show detailed output

**Note**:

As of the v0.12.0 release syncing will occur in real time when the mcp process starts.
- The real time sync means that it is no longer necessary to run the `basic-memory sync --watch` process in a a terminal to sync changes to the db (so the AI can see them). This will be done automatically.

This behavior can be changed via the config. The config file for Basic Memory is in the home directory under `.basic-memory/config.json`.

To change the properties, set the following values:
```
 ~/.basic-memory/config.json 
{
  "sync_changes": false,
}
```

Thanks for using Basic Memory!
### import

Imports external knowledge sources:

```bash
# Claude conversations
basic-memory import claude conversations

# Claude projects
basic-memory import claude projects

# ChatGPT history
basic-memory import chatgpt

# ChatGPT history
basic-memory import memory-json /path/to/memory.json

```

> **Note**: After importing, run `basic-memory sync` to index the new files.
### status

Shows system status information:

```bash
# Basic status check
basic-memory status

# Detailed status
basic-memory status --verbose

# JSON output
basic-memory status --json
```


### project

Create multiple projects to manage your knowledge. 
  
```bash  
# List all configured projects  
basic-memory project list  
  
# Add a new project  
basic-memory project add work ~/work-basic-memory  
  
# Set the default project  
basic-memory project default work  
  
# Remove a project (doesn't delete files)  
basic-memory project remove personal  
  
# Show current project  
basic-memory project current  
```  

> Be sure to restart Claude Desktop after changing projects. 

#### Using Projects in Commands  
  
All commands support the `--project` flag to specify which project to use:  
  
```bash  
# Sync a specific project  
basic-memory --project=work sync  
  
# Run MCP server for a specific project  
basic-memory --project=personal mcp  
```  
  
You can also set the `BASIC_MEMORY_PROJECT` environment variable:  
  
```bash  
BASIC_MEMORY_PROJECT=work basic-memory sync  
```  

### help

The full list of commands and help for each can be viewed with the `--help` argument.

```
 ✗ basic-memory --help

 Usage: basic-memory [OPTIONS] COMMAND [ARGS]...

 Basic Memory - Local-first personal knowledge management system.

╭─ Options ─────────────────────────────────────────────────────────────────────────────────╮
│ --project             -p      TEXT  Specify which project to use                          │
│                                     [env var: BASIC_MEMORY_PROJECT]                       │
│                                     [default: None]                                       │
│ --version             -V            Show version information and exit.                    │
│ --install-completion                Install completion for the current shell.             │
│ --show-completion                   Show completion for the current shell, to copy it or  │
│                                     customize the installation.                           │
│ --help                              Show this message and exit.                           │
╰───────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Commands ────────────────────────────────────────────────────────────────────────────────╮
│ sync      Sync knowledge files with the database.                                         │
│ status    Show sync status between files and database.                                    │
│ reset     Reset database (drop all tables and recreate).                                  │
│ mcp       Run the MCP server for Claude Desktop integration.                              │
│ import    Import data from various sources                                                │
│ tool      Direct access to MCP tools via CLI                                              │
│ project   Manage multiple Basic Memory projects                                           │
╰───────────────────────────────────────────────────────────────────────────────────────────╯
```

## Initial Setup

```bash
# Install Basic Memory
uv install basic-memory

# First sync
basic-memory sync

# Start watching mode
basic-memory sync --watch
```

> **Important**: You need to install Basic Memory via `uv` or `pip` to use the command line tools, see [[Getting Started with Basic Memory#Installation]].

## Regular Usage

```bash
# Check status
basic-memory status

# Import new content
basic-memory import claude conversations

# Sync changes
basic-memory sync

# Sync changes continuously
basic-memory sync --watch
```

## Maintenance Tasks

```bash
# Check system status in detail
basic-memory status --verbose

# Full resync of all files
basic-memory sync

# Import updates to specific folder
basic-memory import claude conversations --folder new
```


## Using stdin with Basic Memory's `write_note` Tool

The `write-note` tool supports reading content from standard input (stdin), allowing for more flexible workflows when creating or updating notes in your Basic Memory knowledge base.

### Use Cases

This feature is particularly useful for:

1. **Piping output from other commands** directly into Basic Memory notes
2. **Creating notes with multi-line content** without having to escape quotes or special characters
3. **Integrating with AI assistants** like Claude Code that can generate content and pipe it to Basic Memory
4. **Processing text data** from files or other sources

### Basic Usage

#### Method 1: Using a Pipe

You can pipe content from another command into `write_note`:

```bash
# Pipe output of a command into a new note
echo "# My Note\n\nThis is a test note" | basic-memory tool write-note --title "Test Note" --folder "notes"

# Pipe output of a file into a new note
cat README.md | basic-memory tool write-note --title "Project README" --folder "documentation"

# Process text through other tools before saving as a note
cat data.txt | grep "important" | basic-memory tool write-note --title "Important Data" --folder "data"
```

#### Method 2: Using Heredoc Syntax

For multi-line content, you can use heredoc syntax:

```bash
# Create a note with heredoc
cat << EOF | basic-memory tool write_note --title "Project Ideas" --folder "projects"
# Project Ideas for Q2

## AI Integration
- Improve recommendation engine
- Add semantic search to product catalog

## Infrastructure
- Migrate to Kubernetes
- Implement CI/CD pipeline
EOF
```

#### Method 3: Input Redirection

You can redirect input from a file:

```bash
# Create a note from file content
basic-memory tool write-note --title "Meeting Notes" --folder "meetings" < meeting_notes.md
```

## Integration with Claude Code

This feature works well with Claude Code in the terminal:

### cli

In a Claude Code session, let Claude know he can use the basic-memory tools, then he can execute them via the cli:

```
⏺ Bash(echo "# Test Note from Claude\n\nThis is a test note created by Claude to test the stdin functionality." | basic-memory tool write-note --title "Claude Test Note" --folder "test" --tags "test" --tags "claude")…
  ⎿  # Created test/Claude Test Note.md (23e00eec)
     permalink: test/claude-test-note

     ## Tags
     - test, claude

```

### MCP

Claude code can also now use mcp tools, so it can use any of the basic-memory tool natively. To install basic-memory in Claude Code:

Run
```
claude mcp add basic-memory basic-memory mcp
```

For example: 

```
➜  ~ claude mcp add basic-memory basic-memory mcp
Added stdio MCP server basic-memory with command: basic-memory mcp to project config
➜  ~ claude mcp list
basic-memory: basic-memory mcp
```

You can then use the `/mcp` command in the REPL:

```
/mcp
  ⎿  MCP Server Status

     • basic-memory: connected
```

## Troubleshooting Common Issues

### Sync Conflicts

If you encounter a file changed during sync error:
1. Check the file referenced in the error message
2. Resolve any conflicts manually
3. Run sync again

### Import Errors

If import fails:
1. Check that the source file is in the correct format
2. Verify permissions on the target directory
3. Use --verbose flag for detailed error information

### Status Issues

If status shows problems:
1. Note any unresolved relations or warnings
2. Run a full sync to attempt automatic resolution
3. Check file permissions if database access errors occur


## Relations
- used_by [[Getting Started with Basic Memory]] (Installation instructions)
- complements [[User Guide]] (How to use Basic Memory)
- relates_to [[Introduction to Basic Memory]] (System overview)