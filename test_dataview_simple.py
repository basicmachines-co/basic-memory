#!/usr/bin/env python3
"""Simple test of Dataview integration with real vault data."""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from basic_memory.dataview.integration import create_dataview_integration
from basic_memory.dataview.detector import DataviewDetector


def test_dataview_simple():
    """Test Dataview with minimal setup."""
    
    print("=" * 80)
    print("DATAVIEW INTEGRATION TEST - SIMPLE")
    print("=" * 80)
    print()
    
    # Test content with Dataview queries
    test_content = """---
title: Dataview Test
type: test
---

# Dataview Test

## Test 1: Simple LIST

```dataview
LIST
FROM "1. projects"
LIMIT 5
```

## Test 2: TABLE

```dataview
TABLE type
FROM "3. resources"
LIMIT 5
```

## Test 3: WHERE clause

```dataview
LIST
WHERE type = "project"
LIMIT 3
```
"""
    
    print("📝 Test content prepared")
    print()
    
    # Step 1: Test detector
    print("=" * 80)
    print("STEP 1: Test Dataview Detector")
    print("=" * 80)
    print()
    
    detector = DataviewDetector()
    queries = detector.detect_queries(test_content)
    
    print(f"✅ Found {len(queries)} Dataview queries")
    for i, query in enumerate(queries, 1):
        print(f"  {i}. Line {query.start_line}: {query.block_type} query")
        print(f"     Query: {query.query[:50]}...")
    print()
    
    # Step 2: Test integration with empty notes
    print("=" * 80)
    print("STEP 2: Test Integration (Empty Notes)")
    print("=" * 80)
    print()
    
    # Create integration with no notes
    integration = create_dataview_integration(notes_provider=None)
    print("✅ Integration created")
    print()
    
    # Process the note
    results = integration.process_note(test_content, note_metadata={'id': 1})
    
    print(f"✅ Processed {len(results)} queries")
    print()
    
    # Display results
    for i, result in enumerate(results, 1):
        print(f"{'=' * 80}")
        print(f"QUERY {i}: {result['query_id']}")
        print(f"{'=' * 80}")
        print(f"Type: {result['query_type']}")
        print(f"Status: {result['status']}")
        print(f"Execution time: {result['execution_time_ms']}ms")
        
        if result['status'] == 'success':
            print(f"Results: {result['result_count']} items")
        else:
            print(f"Error: {result.get('error', 'Unknown')}")
        
        print()
    
    # Step 3: Test with mock notes
    print("=" * 80)
    print("STEP 3: Test Integration (Mock Notes)")
    print("=" * 80)
    print()
    
    # Create mock notes
    def mock_notes_provider():
        return [
            {
                'id': 1,
                'title': 'Project Alpha',
                'type': 'project',
                'folder': '1. projects',
                'content': '# Project Alpha\n\nA test project.',
                'created': '2024-01-01T00:00:00',
                'modified': '2024-01-02T00:00:00',
                'file': {
                    'path': '1. projects/Project Alpha.md',
                    'mtime': '2024-01-02T00:00:00',
                    'ctime': '2024-01-01T00:00:00',
                }
            },
            {
                'id': 2,
                'title': 'Project Beta',
                'type': 'project',
                'folder': '1. projects',
                'content': '# Project Beta\n\nAnother test project.',
                'created': '2024-01-03T00:00:00',
                'modified': '2024-01-04T00:00:00',
                'file': {
                    'path': '1. projects/Project Beta.md',
                    'mtime': '2024-01-04T00:00:00',
                    'ctime': '2024-01-03T00:00:00',
                }
            },
            {
                'id': 3,
                'title': 'Reference Doc',
                'type': 'reference',
                'folder': '3. resources',
                'content': '# Reference Doc\n\nA reference document.',
                'created': '2024-01-05T00:00:00',
                'modified': '2024-01-06T00:00:00',
                'file': {
                    'path': '3. resources/Reference Doc.md',
                    'mtime': '2024-01-06T00:00:00',
                    'ctime': '2024-01-05T00:00:00',
                }
            },
        ]
    
    integration_with_notes = create_dataview_integration(notes_provider=mock_notes_provider)
    print("✅ Integration created with mock notes")
    print(f"📊 Mock notes: {len(mock_notes_provider())} items")
    print()
    
    # Process again
    results_with_notes = integration_with_notes.process_note(test_content, note_metadata={'id': 1})
    
    print(f"✅ Processed {len(results_with_notes)} queries")
    print()
    
    # Display results
    for i, result in enumerate(results_with_notes, 1):
        print(f"{'=' * 80}")
        print(f"QUERY {i}: {result['query_id']}")
        print(f"{'=' * 80}")
        print(f"Type: {result['query_type']}")
        print(f"Status: {result['status']}")
        print(f"Execution time: {result['execution_time_ms']}ms")
        
        if result['status'] == 'success':
            print(f"✅ Results: {result['result_count']} items")
            
            # Show results
            results_list = result.get('results', [])
            for j, item in enumerate(results_list[:5], 1):
                if isinstance(item, dict):
                    title = item.get('title', item.get('text', str(item)))
                    print(f"  {j}. {title}")
                else:
                    print(f"  {j}. {item}")
            
            if result['result_count'] > 5:
                print(f"  ... and {result['result_count'] - 5} more")
            
            print(f"\nDiscovered links: {len(result['discovered_links'])}")
        else:
            print(f"❌ Error: {result.get('error', 'Unknown')}")
        
        print()
    
    # Summary
    print("=" * 80)
    print("TEST COMPLETED")
    print("=" * 80)
    print()
    
    success_count = sum(1 for r in results_with_notes if r['status'] == 'success')
    error_count = len(results_with_notes) - success_count
    total_results = sum(r['result_count'] for r in results_with_notes if r['status'] == 'success')
    avg_time = sum(r['execution_time_ms'] for r in results_with_notes) / len(results_with_notes)
    
    print("📊 SUMMARY")
    print(f"  Total queries: {len(results_with_notes)}")
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
    
    all_success = success_count == len(results_with_notes)
    fast_enough = avg_time < 100
    has_results = total_results > 0
    
    if all_success:
        print("✅ All queries executed successfully")
    else:
        print(f"⚠️  {error_count} queries failed")
    
    if fast_enough:
        print(f"✅ Average execution time is acceptable ({avg_time:.2f}ms < 100ms)")
    else:
        print(f"⚠️  Average execution time is high ({avg_time:.2f}ms >= 100ms)")
    
    if has_results:
        print(f"✅ Queries returned results ({total_results} total items)")
    else:
        print("⚠️  No results returned from queries")
    
    print()
    
    if all_success and fast_enough and has_results:
        print("🎉 ALL TESTS PASSED!")
        return 0
    else:
        print("⚠️  SOME TESTS FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(test_dataview_simple())
