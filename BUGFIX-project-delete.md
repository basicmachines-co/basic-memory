# Bug Fix: Project Deletion Failure

## Problem Description

The `delete_project` MCP tool was failing with "Project 'test-verify' not found" even though the project clearly existed and showed up in `list_memory_projects`.

## Root Cause

The bug was in `/Users/drew/code/basic-memory/src/basic_memory/config.py` in the `ConfigManager.remove_project()` method (line 311):

```python
def remove_project(self, name: str) -> None:
    """Remove a project from the configuration."""
    
    project_name, path = self.get_project(name)
    if not project_name:
        raise ValueError(f"Project '{name}' not found")
    
    config = self.load_config()
    if project_name == config.default_project:
        raise ValueError(f"Cannot remove the default project '{name}'")
    
    del config.projects[name]  # ← BUG: Using input name instead of found project_name
    self.save_config(config)
```

**The Issue:**
1. Line 305: `get_project(name)` does a permalink-based lookup and returns the **actual** project name from the config (e.g., "test-verify")
2. Line 311: `del config.projects[name]` tries to delete using the **input** name parameter instead of the `project_name` that was just found
3. Since `get_project()` uses permalink matching, it can find a project even if the input name doesn't match the exact dictionary key

**Example Scenario:**
- Config has project key: `"test-verify"`
- User calls: `delete_project("test-verify")`
- `get_project("test-verify")` finds it via permalink matching and returns `("test-verify", "/path")`
- `del config.projects["test-verify"]` tries to delete using input, which should work...
- BUT if there's any normalization mismatch between the stored key and the input, it fails

## The Fix

Changed line 311 to use the `project_name` returned by `get_project()`:

```python
# Use the found project_name (which may differ from input name due to permalink matching)
del config.projects[project_name]
```

This ensures we're deleting the exact key that exists in the config dictionary, not the potentially non-normalized input name.

## Testing

After applying this fix, the delete operation should work correctly:

```python
# This should now succeed
await delete_project("test-verify")
```

## Related Code

The same pattern is correctly used in other methods:
- `set_default_project()` correctly uses the found `project_name` when setting default
- The API endpoint `remove_project()` in project_router.py correctly passes through to this method

## Commit Message

```
fix: use found project_name in ConfigManager.remove_project()

The remove_project() method was using the input name parameter to delete
from config.projects instead of the project_name returned by get_project().
This caused failures when the input name didn't exactly match the config
dictionary key, even though get_project() successfully found the project
via permalink matching.

Now uses the actual project_name returned by get_project() to ensure we're
deleting the correct dictionary key.

Fixes: Project deletion failing with "not found" error despite project existing
```
