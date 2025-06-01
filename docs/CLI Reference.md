---
title: CLI Reference
type: note
permalink: docs/cli-reference
---

# CLI Reference

Basic Memory provides command line tools for managing your knowledge base. This reference covers the available commands and their options.

## Core Commands

### auth (New in v0.13.0)

Manage OAuth authentication for secure remote access:

```bash
# Test authentication setup
basic-memory auth test-auth

# Register OAuth client
basic-memory auth register-client
```

Supports multiple authentication providers:
- **Basic Provider**: For development and testing
- **Supabase Provider**: For production deployments
- **External Providers**: GitHub, Google integration framework

See [[OAuth Authentication Guide]] for complete setup instructions.

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
### import (Enhanced in v0.13.0)

Imports external knowledge sources with support for project targeting:

```bash
# Claude conversations
basic-memory import claude conversations

# Claude projects
basic-memory import claude projects

# ChatGPT history
basic-memory import chatgpt

# Memory JSON format
basic-memory import memory-json /path/to/memory.json

# Import to specific project (v0.13.0)
basic-memory --project=work import claude conversations
```

**New in v0.13.0:**
- **Project Targeting**: Import directly to specific projects
- **Real-time Sync**: Imported content available immediately
- **Unified Database**: All imports stored in centralized database

> **Note**: Changes sync automatically - no manual sync required in v0.13.0.
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


### project (Enhanced in v0.13.0)

Manage multiple projects with the new unified database architecture. Projects can now be switched instantly during conversations without restart.
  
```bash  
# List all configured projects with status
basic-memory project list  
  
# Create a new project
basic-memory project create work ~/work-basic-memory  
  
# Set the default project  
basic-memory project set-default work  
  
# Delete a project (doesn't delete files)  
basic-memory project delete personal  
  
# Show detailed project statistics
basic-memory project info
```  

**New in v0.13.0:**
- **Unified Database**: All projects share a single database for better performance
- **Instant Switching**: Switch projects during conversations without restart
- **Enhanced Commands**: Updated project commands with better status information
- **Project Statistics**: Detailed info about entities, observations, and relations 

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

### tool (Enhanced in v0.13.0)

Direct access to MCP tools via CLI with new editing and file management capabilities:

```bash
# Create notes
basic-memory tool write-note --title "My Note" --content "Content here"

# Edit notes incrementally (v0.13.0)
echo "New content" | basic-memory tool edit-note --title "My Note" --operation append

# Move notes (v0.13.0)
basic-memory tool move-note --identifier "My Note" --destination "archive/my-note.md"

# Search notes
basic-memory tool search-notes --query "authentication"

# Project management (v0.13.0)
basic-memory tool list-projects
basic-memory tool switch-project --project-name "work"
```

**New in v0.13.0:**
- **edit-note**: Incremental editing (append, prepend, find/replace, section replace)
- **move-note**: File management with database consistency
- **Project tools**: list-projects, switch-project, get-current-project
- **Cross-project operations**: Use `--project` flag with any tool

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
│ auth      OAuth authentication management (v0.13.0)                                      │
│ sync      Sync knowledge files with the database                                          │
│ status    Show sync status between files and database                                     │
│ reset     Reset database (drop all tables and recreate)                                   │
│ mcp       Run the MCP server for Claude Desktop integration                               │
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

## Version Management (New in v0.13.0)

Basic Memory v0.13.0 introduces automatic version management and multiple installation options:

```bash
# Stable releases
pip install basic-memory

# Beta/pre-releases
pip install basic-memory --pre

# Latest development builds (auto-published)
pip install basic-memory --pre --force-reinstall

# Check current version
basic-memory --version
```

**Version Types:**
- **Stable**: `0.13.0` (manual git tags)
- **Beta**: `0.13.0b1` (manual git tags) 
- **Development**: `0.12.4.dev26+468a22f` (automatic from commits)

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