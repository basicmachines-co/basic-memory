"""Deterministic paginated stdio MCP server for real transport tests."""

import json
import os
import sys
import time

SCHEMA = {
    "type": "object",
    "properties": {"project": {"type": "string"}},
    "additionalProperties": True,
}

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
                ["write_note", "recent_activity"] if not params.get("cursor") else ["extra_tool"]
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
            if arguments.get("sleep"):
                time.sleep(10)
            result = {
                "content": [{"type": "text", "text": json.dumps(arguments)}],
                "structuredContent": {
                    "pid": os.getpid(),
                    "name": params["name"],
                    "arguments": arguments,
                },
                "isError": bool(arguments.get("fail")),
            }
        case _:
            result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
