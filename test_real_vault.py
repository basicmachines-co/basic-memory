#!/usr/bin/env python3
"""Test Dataview with real vault data."""

import sys
import sqlite3
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from basic_memory.dataview.integration import create_dataview_integration

def test_with_real_vault():
    """Test Dataview with the user's real vault."""
    
    # Connect to the real database
    db_path = Path.home() / ".basic-memory" / "basic_memory.db"
    vault_path = Path.home() / "basic-memory"
    
    if not db_path.exists():
        print(f"❌ Database not found at {db_path}")
        return
    
    if not vault_path.exists():
        print(f"❌ Vault not found at {vault_path}")
        return
    
    print(f"📂 Vault path: {vault_path}")
    print(f"🗄️  Database: {db_path}")
    print()
    
    conn = sqlite3.connect(str(db_path))
    
    # Create notes provider function
    def notes_provider():
        """Fetch all notes from database."""
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, title, type, folder, content, created, modified
            FROM notes
        """)
        
        notes = []
        for row in cursor.fetchall():
            note_id, title, note_type, folder, content, created, modified = row
            notes.append({
                'id': note_id,
                'title': title,
                'type': note_type,
                'folder': folder,
                'content': content,
                'created': created,
                'modified': modified,
                'file': {
                    'path': f"{folder}/{title}.md" if folder else f"{title}.md",
                    'mtime': modified,
                    'ctime': created,
                }
            })
        return notes
    
    # Create integration
    integration = create_dataview_integration(notes_provider)
    
    # Read the test note
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, title, content 
        FROM notes 
        WHERE title = 'Dataview Test'
    """)
    
    row = cursor.fetchone()
    
    if not row:
        print("❌ Test note 'Dataview Test' not found")
        print("Please ensure the note exists at: 0. inbox/Dataview Test.md")
        
        # Show available notes
        cursor.execute("SELECT COUNT(*) FROM notes")
        count = cursor.fetchone()[0]
        print(f"\nTotal notes in database: {count}")
        
        if count > 0:
            cursor.execute("SELECT title FROM notes LIMIT 5")
            print("\nSample notes:")
            for (title,) in cursor.fetchall():
                print(f"  - {title}")
        
        conn.close()
        return
    
    note_id, title, content = row
    print(f"✅ Found test note: {title} (ID: {note_id})")
    print()
    print("=" * 80)
    print("EXECUTING DATAVIEW QUERIES")
    print("=" * 80)
    print()
    
    # Process the note
    try:
        results = integration.process_note(content, note_id)
    except Exception as e:
        print(f"❌ Error processing note: {e}")
        import traceback
        traceback.print_exc()
        conn.close()
        return
    
    if not results:
        print("❌ No Dataview queries found in the note")
        conn.close()
        return
    
    print(f"✅ Found {len(results)} Dataview queries")
    print()
    
    # Display results
    for i, result in enumerate(results, 1):
        print(f"{'=' * 80}")
        print(f"QUERY {i}: {result['query_id']}")
        print(f"{'=' * 80}")
        print(f"Type: {result['query_type']}")
        print(f"Line: {result['line_number']}")
        print(f"Status: {result['status']}")
        print(f"Execution time: {result['execution_time_ms']}ms")
        print()
        
        if result['status'] == 'success':
            print(f"✅ Results: {result['result_count']} items")
            print()
            
            # Show first 10 results
            results_list = result.get('results', [])
            for j, item in enumerate(results_list[:10], 1):
                # Handle different result formats
                if isinstance(item, dict):
                    title = item.get('title', item.get('text', str(item)))
                    print(f"  {j}. {title}")
                else:
                    print(f"  {j}. {item}")
            
            if result['result_count'] > 10:
                print(f"  ... and {result['result_count'] - 10} more")
            
            print()
            print(f"Discovered links: {len(result['discovered_links'])}")
            if result['discovered_links']:
                print("Links:")
                for link in result['discovered_links'][:5]:
                    print(f"  - {link}")
                if len(result['discovered_links']) > 5:
                    print(f"  ... and {len(result['discovered_links']) - 5} more")
        else:
            print(f"❌ Error: {result.get('error', 'Unknown error')}")
        
        print()
    
    conn.close()
    
    print("=" * 80)
    print("TEST COMPLETED")
    print("=" * 80)
    print()
    
    # Summary
    success_count = sum(1 for r in results if r['status'] == 'success')
    error_count = len(results) - success_count
    total_results = sum(r['result_count'] for r in results if r['status'] == 'success')
    avg_time = sum(r['execution_time_ms'] for r in results) / len(results)
    
    print("📊 SUMMARY")
    print(f"  Total queries: {len(results)}")
    print(f"  Successful: {success_count}")
    print(f"  Errors: {error_count}")
    print(f"  Total results: {total_results}")
    print(f"  Average execution time: {avg_time:.2f}ms")

if __name__ == "__main__":
    test_with_real_vault()
