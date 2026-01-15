#!/usr/bin/env python3
"""Test Dataview integration via MCP API."""

import sys
import asyncio
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from basic_memory.mcp.server import BasicMemoryMCP
from basic_memory.config import get_config


async def test_dataview_via_mcp():
    """Test Dataview queries using MCP read_note tool."""
    
    print("=" * 80)
    print("DATAVIEW INTEGRATION TEST - VIA MCP")
    print("=" * 80)
    print()
    
    # Initialize MCP server
    config = get_config()
    print(f"📂 Vault path: {config.vault_path}")
    print(f"🗄️  Database backend: {config.database_backend}")
    print()
    
    mcp = BasicMemoryMCP()
    await mcp.initialize()
    
    print("✅ MCP server initialized")
    print()
    
    # Test 1: Read note WITHOUT Dataview processing
    print("=" * 80)
    print("TEST 1: Read note WITHOUT Dataview processing")
    print("=" * 80)
    print()
    
    try:
        result = await mcp.read_note(
            identifier="Dataview Test",
            project=None,
            page=1,
            page_size=10
        )
        
        print("✅ Note read successfully")
        print(f"Content length: {len(result)} characters")
        print()
        
        # Count Dataview blocks
        dataview_count = result.count("```dataview")
        print(f"Found {dataview_count} Dataview code blocks in raw content")
        print()
        
    except Exception as e:
        print(f"❌ Error reading note: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Test 2: Check if Dataview processing is available
    print("=" * 80)
    print("TEST 2: Check Dataview integration availability")
    print("=" * 80)
    print()
    
    # Check if the integration module exists
    try:
        from basic_memory.dataview.integration import create_dataview_integration
        print("✅ Dataview integration module found")
        
        # Check if MCP has Dataview support
        if hasattr(mcp, 'dataview_integration'):
            print("✅ MCP server has Dataview integration")
        else:
            print("⚠️  MCP server does not have Dataview integration attribute")
            print("   This is expected if Dataview is not yet integrated into MCP tools")
        
        print()
        
    except ImportError as e:
        print(f"❌ Dataview integration module not found: {e}")
        return
    
    # Test 3: Manual Dataview processing
    print("=" * 80)
    print("TEST 3: Manual Dataview processing")
    print("=" * 80)
    print()
    
    try:
        # Get notes from database
        from basic_memory.database import get_session
        from basic_memory.models import Note
        from sqlalchemy import select
        
        async with get_session() as session:
            # Get all notes
            stmt = select(Note)
            result_db = await session.execute(stmt)
            notes = result_db.scalars().all()
            
            print(f"📊 Found {len(notes)} notes in database")
            print()
            
            # Create notes provider
            def notes_provider():
                notes_data = []
                for note in notes:
                    notes_data.append({
                        'id': note.id,
                        'title': note.title,
                        'type': note.type,
                        'folder': note.folder,
                        'content': note.content,
                        'created': note.created.isoformat() if note.created else None,
                        'modified': note.modified.isoformat() if note.modified else None,
                        'file': {
                            'path': f"{note.folder}/{note.title}.md" if note.folder else f"{note.title}.md",
                            'mtime': note.modified.isoformat() if note.modified else None,
                            'ctime': note.created.isoformat() if note.created else None,
                        }
                    })
                return notes_data
            
            # Create integration
            integration = create_dataview_integration(notes_provider)
            print("✅ Dataview integration created")
            print()
            
            # Get test note
            stmt = select(Note).where(Note.title == "Dataview Test")
            result_note = await session.execute(stmt)
            test_note = result_note.scalar_one_or_none()
            
            if not test_note:
                print("❌ Test note 'Dataview Test' not found in database")
                return
            
            print(f"✅ Found test note: {test_note.title} (ID: {test_note.id})")
            print()
            
            # Process the note
            print("=" * 80)
            print("EXECUTING DATAVIEW QUERIES")
            print("=" * 80)
            print()
            
            query_results = integration.process_note(test_note.content, test_note.id)
            
            if not query_results:
                print("❌ No Dataview queries found or processed")
                return
            
            print(f"✅ Processed {len(query_results)} Dataview queries")
            print()
            
            # Display results
            for i, qr in enumerate(query_results, 1):
                print(f"{'=' * 80}")
                print(f"QUERY {i}: {qr['query_id']}")
                print(f"{'=' * 80}")
                print(f"Type: {qr['query_type']}")
                print(f"Line: {qr['line_number']}")
                print(f"Status: {qr['status']}")
                print(f"Execution time: {qr['execution_time_ms']}ms")
                print()
                
                if qr['status'] == 'success':
                    print(f"✅ Results: {qr['result_count']} items")
                    print()
                    
                    # Show first 10 results
                    results_list = qr.get('results', [])
                    for j, item in enumerate(results_list[:10], 1):
                        if isinstance(item, dict):
                            title = item.get('title', item.get('text', str(item)))
                            print(f"  {j}. {title}")
                        else:
                            print(f"  {j}. {item}")
                    
                    if qr['result_count'] > 10:
                        print(f"  ... and {qr['result_count'] - 10} more")
                    
                    print()
                    print(f"Discovered links: {len(qr['discovered_links'])}")
                    if qr['discovered_links']:
                        print("Links:")
                        for link in qr['discovered_links'][:5]:
                            print(f"  - {link.get('target', link)}")
                        if len(qr['discovered_links']) > 5:
                            print(f"  ... and {len(qr['discovered_links']) - 5} more")
                else:
                    print(f"❌ Error: {qr.get('error', 'Unknown error')}")
                
                print()
            
            # Summary
            print("=" * 80)
            print("TEST COMPLETED")
            print("=" * 80)
            print()
            
            success_count = sum(1 for r in query_results if r['status'] == 'success')
            error_count = len(query_results) - success_count
            total_results = sum(r['result_count'] for r in query_results if r['status'] == 'success')
            avg_time = sum(r['execution_time_ms'] for r in query_results) / len(query_results)
            
            print("📊 SUMMARY")
            print(f"  Total queries: {len(query_results)}")
            print(f"  Successful: {success_count}")
            print(f"  Errors: {error_count}")
            print(f"  Total results: {total_results}")
            print(f"  Average execution time: {avg_time:.2f}ms")
            print()
            
            # Validation
            print("=" * 80)
            print("VALIDATION")
            print("=" * 80)
            print()
            
            if success_count == len(query_results):
                print("✅ All queries executed successfully")
            else:
                print(f"⚠️  {error_count} queries failed")
            
            if avg_time < 100:
                print(f"✅ Average execution time is acceptable ({avg_time:.2f}ms < 100ms)")
            else:
                print(f"⚠️  Average execution time is high ({avg_time:.2f}ms >= 100ms)")
            
            if total_results > 0:
                print(f"✅ Queries returned results ({total_results} total items)")
            else:
                print("⚠️  No results returned from queries")
            
            print()
            
    except Exception as e:
        print(f"❌ Error during manual processing: {e}")
        import traceback
        traceback.print_exc()
    
    await mcp.cleanup()


if __name__ == "__main__":
    asyncio.run(test_dataview_via_mcp())
