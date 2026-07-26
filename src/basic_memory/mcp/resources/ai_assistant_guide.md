# AI Assistant Guide for Basic Memory

Basic Memory is a persistent Markdown knowledge base shared by the user and their AI
assistants. Use it for information that should survive this conversation: decisions, discoveries,
plans, preferences, and connected reference material.

## Start usefully

1. Call `recent_activity` to orient in the user's existing notes.
2. If no project is resolved, call `list_memory_projects` and ask which project to use.
3. Preserve the selected route in later calls. Prefer a returned `project_id` over a project name
   because names can collide across cloud workspaces.
4. No recent activity does not prove the knowledge base is empty. If the user appears new, explain
   the shared-memory loop and offer a useful first note. Wait for agreement before creating it.

## Work with the user's knowledge

- Search before creating a note so related knowledge is reused instead of duplicated.
- Read a matching note before updating it, then use `edit_note` for focused changes.
- Use `write_note` for a new note. Use its `directory` parameter to choose the folder.
- Keep notes useful and connected; add observations, tags, or WikiLinks when they improve later
  retrieval, not to satisfy a quota.
- Confirm meaningful writes and preserve the project or `project_id` used for the read.

The tool descriptions and input schemas exposed by this MCP server are authoritative for the
installed tool names and arguments.

## Fetch current documentation

If your client has a web, browser, or fetch tool:

1. Fetch `https://docs.basicmemory.com/llms.txt` for the documentation index.
2. Choose the page relevant to the user's task.
3. Fetch that page's linked `https://docs.basicmemory.com/raw/...md` URL for clean Markdown.

For tool details, the index links directly to the MCP Tools Reference. Fetch
`https://docs.basicmemory.com/llms-full.txt` only when the task genuinely needs the complete
documentation; progressive page-by-page discovery usually uses less context and stays focused.

If no fetch capability is available, use this guide plus the MCP tool schemas.
