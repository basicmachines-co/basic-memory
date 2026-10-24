"""Deterministic paginated stdio MCP server with optional durable test notes."""

import json
import os
import sys
import time
from pathlib import Path

SCHEMA = {
    "type": "object",
    "properties": {"project": {"type": "string"}},
    "additionalProperties": True,
}
store_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
notes = json.loads(store_path.read_text()) if store_path and store_path.exists() else {}

for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request["method"]
    params = request.get("params", {})
    match method:
        case "initialize":
            result = {
                "protocolVersion": params["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "bm-test", "version": "1"},
            }
        case "tools/list":
            names = (
                ["bm_write", "bm_project"]
                if not params.get("cursor")
                else ["extra_tool", "bm_read", "bm_search", "bm_context"]
            )
            result = {
                "tools": [
                    {"name": name, "description": name, "inputSchema": SCHEMA} for name in names
                ]
            }
            if not params.get("cursor"):
                result["nextCursor"] = "second"
        case "tools/call":
            arguments = params.get("arguments", {})
            name = params["name"]
            if arguments.get("sleep"):
                time.sleep(10)
            data = {"pid": os.getpid(), "name": name, "arguments": arguments}
            if name == "bm_write" and "title" in arguments:
                path = arguments["directory"] + "/" + arguments["title"] + ".md"
                if path in notes:
                    data = {"error": "ALREADY_EXISTS", "message": "Note already exists"}
                else:
                    notes[path] = {
                        "file_path": path,
                        "content": arguments["content"],
                        "frontmatter": arguments.get("metadata", {}),
                    }
                    if store_path:
                        store_path.write_text(json.dumps(notes))
                    data = {"file_path": path, "action": "created"}
            elif name == "bm_read":
                data = notes.get(
                    arguments["identifier"], {"error": "NOT_FOUND", "message": "Note not found"}
                )
            elif name == "bm_search":
                filters = arguments.get("filters", {})
                data = {
                    "results": [
                        {"file_path": path, "metadata": note["frontmatter"]}
                        for path, note in notes.items()
                        if all(note["frontmatter"].get(k) == v for k, v in filters.items())
                    ]
                }
            result = {
                "content": [{"type": "text", "text": json.dumps(data)}],
                "structuredContent": data,
                "isError": bool(arguments.get("fail") or data.get("error")),
            }
        case _:
            result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
