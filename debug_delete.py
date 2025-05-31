#!/usr/bin/env python3
"""Debug script for delete_note issue."""

import asyncio
import tempfile
from pathlib import Path
from fastmcp import Client
from basic_memory.mcp.server import create_mcp_server


async def debug_delete():
    """Debug the delete issue step by step."""
    
    # Create temporary directory for test
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Using temp directory: {temp_dir}")
        
        # Initialize MCP server with temp directory
        server = await create_mcp_server(Path(temp_dir))
        
        async with Client(server) as client:
            print("=== STEP 1: Create note ===")
            create_result = await client.call_tool(
                "write_note",
                {
                    "title": "Debug Note",
                    "folder": "test",
                    "content": "# Debug Note\n\nThis is a test note.",
                    "tags": "debug,test",
                },
            )
            print(f"Create result: {create_result[0].text}")
            
            print("\n=== STEP 2: Verify note exists ===")
            try:
                read_result = await client.call_tool(
                    "read_note",
                    {"identifier": "Debug Note"},
                )
                print(f"Read result: Found note with content length {len(read_result[0].text)}")
            except Exception as e:
                print(f"Read failed: {e}")
                
            print("\n=== STEP 3: Delete note ===")
            delete_result = await client.call_tool(
                "delete_note",
                {"identifier": "Debug Note"},
            )
            print(f"Delete result: {delete_result[0].text}")
            
            print("\n=== STEP 4: Try to read deleted note ===")
            try:
                read_result2 = await client.call_tool(
                    "read_note",
                    {"identifier": "Debug Note"},
                )
                print(f"ERROR: Note still found after deletion! Content length: {len(read_result2[0].text)}")
                print(f"Content preview: {read_result2[0].text[:200]}...")
            except Exception as e:
                print(f"Good: Read failed as expected: {e}")
                
            print("\n=== STEP 5: Check filesystem ===")
            test_file = Path(temp_dir) / "test" / "Debug Note.md"
            if test_file.exists():
                print(f"ERROR: File still exists at {test_file}")
                print(f"File content: {test_file.read_text()}")
            else:
                print(f"Good: File deleted from filesystem")


if __name__ == "__main__":
    asyncio.run(debug_delete())