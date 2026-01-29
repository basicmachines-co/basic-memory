"""
Integration layer for Dataview in MCP tools.

This module provides the bridge between MCP tools (read_note, search_notes, build_context)
and the Dataview query execution engine.
"""

import time
from typing import Any, Dict, List, Optional

from loguru import logger

from basic_memory.dataview.detector import DataviewDetector
from basic_memory.dataview.errors import (
    DataviewError,
    DataviewExecutionError,
    DataviewParseError,
    DataviewSyntaxError,
)
from basic_memory.dataview.executor.executor import DataviewExecutor
from basic_memory.dataview.lexer import DataviewLexer
from basic_memory.dataview.parser import DataviewParser


class DataviewIntegration:
    """
    Integrate Dataview execution into MCP tools.
    
    This class handles:
    - Detection of Dataview queries in markdown content
    - Parsing and execution of queries
    - Error handling and result formatting
    - Performance tracking
    """

    def __init__(self, notes_provider: Optional[callable] = None):
        """
        Initialize the Dataview integration.
        
        Args:
            notes_provider: Optional callable that returns list of notes for query execution.
                           If None, queries will be executed with empty note collection.
        """
        self.notes_provider = notes_provider
        self.detector = DataviewDetector()

    def process_note(
        self, note_content: str, note_metadata: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Process a note and execute all Dataview queries found in it.
        
        Args:
            note_content: Markdown content of the note
            note_metadata: Optional metadata about the note (id, title, path, etc.)
            
        Returns:
            List of dataview_results dictionaries, one per query found
        """
        # Detect Dataview blocks
        blocks = self.detector.detect_queries(note_content)

        if not blocks:
            return []

        logger.debug(f"Found {len(blocks)} Dataview queries in note")

        results = []
        for idx, block in enumerate(blocks, 1):
            result = self._execute_query(
                query_id=f"dv-{idx}",
                query_text=block.query,
                line_number=block.start_line + 1,  # Convert to 1-based
                block_type=block.block_type,
            )
            results.append(result)

        return results

    def _execute_query(
        self, query_id: str, query_text: str, line_number: int, block_type: str = "codeblock"
    ) -> Dict[str, Any]:
        """
        Execute a single Dataview query.
        
        Args:
            query_id: Unique identifier for this query
            query_text: The Dataview query text
            line_number: Line number where query appears in source
            block_type: Type of block ("codeblock" or "inline")
            
        Returns:
            Dictionary with query results and metadata
        """
        start_time = time.time()

        try:
            # Parse query using class method
            query_ast = DataviewParser.parse(query_text)

            # Get notes for execution
            notes = self._get_notes_for_query()

            # Execute query and get structured results
            executor = DataviewExecutor(notes)
            result_markdown, structured_results = self._execute_and_extract_results(
                executor, query_ast
            )

            # Calculate execution time
            execution_time_ms = int((time.time() - start_time) * 1000)

            return {
                "query_id": query_id,
                "query_type": str(query_ast.query_type.value),
                "query_source": self._format_query_source(query_text, block_type),
                "line_number": line_number,
                "status": "success",
                "result_markdown": result_markdown,
                "result_count": len(structured_results),
                "discovered_links": self._extract_discovered_links(structured_results),
                "execution_time_ms": execution_time_ms,
                "results": structured_results,
            }

        except (DataviewSyntaxError, DataviewParseError) as e:
            # Syntax/parse error
            execution_time_ms = int((time.time() - start_time) * 1000)
            logger.warning(f"Dataview syntax error in query {query_id}: {e}")
            return {
                "query_id": query_id,
                "query_type": "unknown",
                "query_source": self._format_query_source(query_text, block_type),
                "line_number": line_number,
                "status": "error",
                "error": str(e),
                "error_type": "syntax",
                "discovered_links": [],
                "result_count": 0,
                "execution_time_ms": execution_time_ms,
            }

        except (DataviewExecutionError, DataviewError) as e:
            # Execution error
            execution_time_ms = int((time.time() - start_time) * 1000)
            logger.warning(f"Dataview execution error in query {query_id}: {e}")
            return {
                "query_id": query_id,
                "query_type": "unknown",
                "query_source": self._format_query_source(query_text, block_type),
                "line_number": line_number,
                "status": "error",
                "error": str(e),
                "error_type": "execution",
                "discovered_links": [],
                "result_count": 0,
                "execution_time_ms": execution_time_ms,
            }

        except Exception as e:
            # Unexpected error
            execution_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Unexpected error executing Dataview query {query_id}: {e}", exc_info=True)
            return {
                "query_id": query_id,
                "query_type": "unknown",
                "query_source": self._format_query_source(query_text, block_type),
                "line_number": line_number,
                "status": "error",
                "error": f"Unexpected error: {str(e)}",
                "error_type": "unexpected",
                "discovered_links": [],
                "result_count": 0,
                "execution_time_ms": execution_time_ms,
            }

    def _get_notes_for_query(self) -> List[Dict[str, Any]]:
        """Get notes collection for query execution."""
        if self.notes_provider:
            try:
                return self.notes_provider()
            except Exception as e:
                logger.warning(f"Failed to get notes from provider: {e}")
                return []
        return []

    def _execute_and_extract_results(
        self, executor: DataviewExecutor, query_ast
    ) -> tuple[str, List[Dict[str, Any]]]:
        """
        Execute query and extract both markdown and structured results.
        
        This method duplicates the executor logic to get structured results
        before they're formatted to markdown.
        """
        from basic_memory.dataview.ast import QueryType
        
        # Filter notes (same as executor)
        filtered_notes = executor._filter_by_from(query_ast.from_source)
        if query_ast.where_clause:
            filtered_notes = executor._filter_by_where(filtered_notes, query_ast.where_clause)
        
        # Execute based on query type and get structured results
        if query_ast.query_type == QueryType.TABLE:
            # Get structured results before formatting
            results = []
            field_names = []
            
            for field in query_ast.fields:
                field_name = field.alias or executor._get_field_name(field.expression)
                field_names.append(field_name)
            
            for note in filtered_notes:
                from basic_memory.dataview.executor.expression_eval import ExpressionEvaluator
                evaluator = ExpressionEvaluator(note)
                row = {}
                # Always include title for link discovery
                row["title"] = note.get("title", "Untitled")
                row["file.link"] = f"[[{note.get('title', 'Untitled')}]]"
                row["file.path"] = note.get("file", {}).get("path", "")
                row["type"] = "table_row"
                
                for field in query_ast.fields:
                    field_name = field.alias or executor._get_field_name(field.expression)
                    try:
                        value = evaluator.evaluate(field.expression)
                        row[field_name] = value
                    except Exception:
                        row[field_name] = None
                results.append(row)
            
            # Apply SORT
            if query_ast.sort_clauses:
                results = executor._apply_sort(results, query_ast.sort_clauses)
            
            # Apply LIMIT
            if query_ast.limit:
                results = results[: query_ast.limit]
            
            # Format to markdown
            markdown = executor.formatter.format_table(results, field_names)
            return markdown, results
            
        elif query_ast.query_type == QueryType.LIST:
            results = []
            for note in filtered_notes:
                results.append({
                    "type": "list_item",
                    "file.link": f"[[{note.get('title', 'Untitled')}]]",
                    "title": note.get("title", "Untitled"),
                    "file.path": note.get("file", {}).get("path", ""),
                })
            
            # Apply SORT
            if query_ast.sort_clauses:
                results = executor._apply_sort(results, query_ast.sort_clauses)
            
            # Apply LIMIT
            if query_ast.limit:
                results = results[: query_ast.limit]
            
            markdown = executor.formatter.format_list(results)
            return markdown, results
            
        elif query_ast.query_type == QueryType.TASK:
            # For tasks, use executor's method
            markdown = executor._execute_task(filtered_notes, query_ast)
            # Parse markdown to get structured results
            results = self._parse_result_markdown(markdown, query_ast.query_type)
            return markdown, results
        
        else:
            # Fallback: execute normally and parse markdown
            markdown = executor.execute(query_ast)
            results = self._parse_result_markdown(markdown, query_ast.query_type)
            return markdown, results

    def _format_query_source(self, query_text: str, block_type: str) -> str:
        """Format query source for display."""
        if block_type == "inline":
            return f"`= {query_text}`"
        else:
            return f"```dataview\n{query_text}\n```"

    def _parse_result_markdown(self, markdown: str, query_type) -> List[Dict[str, Any]]:
        """
        Parse result markdown into structured data.
        
        This is a simple parser that extracts basic structure from the markdown output.
        """
        from basic_memory.dataview.ast import QueryType

        results = []

        if not markdown or not markdown.strip():
            return results

        lines = markdown.strip().split("\n")

        if query_type == QueryType.LIST:
            # Parse list items
            for line in lines:
                line = line.strip()
                if line.startswith("- "):
                    # Extract wikilink if present
                    link_text = line[2:].strip()
                    if link_text.startswith("[[") and "]]" in link_text:
                        end_idx = link_text.index("]]")
                        title = link_text[2:end_idx]
                        results.append({"type": "list_item", "title": title, "raw": line})
                    else:
                        results.append({"type": "list_item", "title": link_text, "raw": line})

        elif query_type == QueryType.TABLE:
            # Parse table rows (skip header and separator)
            in_table = False
            headers = []
            for line in lines:
                line = line.strip()
                if line.startswith("|") and line.endswith("|"):
                    if not in_table:
                        # First row is headers
                        headers = [h.strip() for h in line.split("|")[1:-1]]
                        in_table = True
                    elif line.startswith("|---") or line.startswith("| ---"):
                        # Skip separator
                        continue
                    else:
                        # Data row
                        values = [v.strip() for v in line.split("|")[1:-1]]
                        if len(values) == len(headers):
                            row = dict(zip(headers, values))
                            row["type"] = "table_row"
                            results.append(row)

        elif query_type == QueryType.TASK:
            # Parse task items
            for line in lines:
                line = line.strip()
                if line.startswith("- [ ]") or line.startswith("- [x]"):
                    completed = line.startswith("- [x]")
                    text = line[5:].strip()
                    results.append({"type": "task", "completed": completed, "text": text, "raw": line})

        return results

    def _extract_discovered_links(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Extract discovered links from query results.
        
        These links can be used for graph traversal and context building.
        """
        links = []

        for result in results:
            result_type = result.get("type")

            if result_type == "task":
                # Extract task info
                link = {
                    "target": result.get("text", ""),
                    "type": "task",
                    "metadata": {
                        "completed": result.get("completed", False),
                    },
                }
                links.append(link)

            elif result_type == "list_item":
                # Extract note reference
                # Try file.path first (most reliable)
                file_path = result.get("file.path", "")
                target = file_path if file_path else result.get("title", "")
                
                if target:
                    link = {
                        "target": target,
                        "type": "note",
                        "metadata": {},
                    }
                    links.append(link)

            elif result_type == "table_row":
                # For table rows, always extract title or file.link
                # These fields are now always present in results (added by executor)
                target = None
                
                # Try file.path first (most reliable)
                file_path = result.get("file.path", "")
                if file_path:
                    target = file_path
                
                # Fallback to file.link (has wikilink format)
                if not target and "file.link" in result:
                    clean_value = result["file.link"].strip()
                    if clean_value.startswith("[[") and clean_value.endswith("]]"):
                        target = clean_value[2:-2]
                    else:
                        target = clean_value
                
                # Fallback to title
                if not target and "title" in result:
                    target = result["title"]
                
                # Fallback to other common fields
                if not target:
                    for key in ("name", "file", "path"):
                        if key in result and result[key]:
                            target = result[key]
                            break
                
                if target:
                    link = {
                        "target": target,
                        "type": "note",
                        "metadata": {k: v for k, v in result.items() if k not in ("type", "title", "file.link")},
                    }
                    links.append(link)

        return links


def create_dataview_integration(notes_provider: Optional[callable] = None) -> DataviewIntegration:
    """
    Factory function to create DataviewIntegration instance.
    
    Args:
        notes_provider: Optional callable that returns list of notes for query execution
        
    Returns:
        Configured DataviewIntegration instance
    """
    return DataviewIntegration(notes_provider)
