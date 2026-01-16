# Extract Conversation into memories/ using Basic Memory MCP

## Goal
Extract important information from a referenced conversation into the following folders using the available tools:

### Folders
- `plans` - action plans, roadmaps, to-do lists
- `knowledge` - stable reference facts, policies, domain docs
- `decisions` - architecture / product choices & rationale
- `lessons_learned` - retrospectives, what-went-well, mistakes to avoid

### Tasks
1.  **Scan the `conversation_log`** for the following:
    -   `#Plans` - any clearly stated tasks, next steps, or roadmaps
    -   `#Knowledge` - stable facts, policies, formulas, specs
    -   `#Decisions` - explicit choices made and their justification
    -   `#Lessons Learned` - mistakes, insights, or heuristics expressed

2.  **For each extracted item:**
    -   Choose a clear, concise `title`.
    -   Run `write_note` with the `folder` set to the target directory.

3.  **Link and relate:**
    -   Use wiki links (`[[Other Note]]`) to connect to existing notes when relevant.

4.  **Verify and report:**
    -   After all writes/edits, call `sync_status()` to confirm memory health.
    -   Provide a summary list of the notes you created or updated.
To run the extraction process, you reference the extractor prompt and the target conversation in the chat input. Using XML tags helps the agent clearly identify the conversation file.

@extract-conversation.md

<conversation>
@script-generator-2025-05-29-09-37-troubleshooting-server-errors-and-api-issues.md
</conversation>