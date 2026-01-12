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

            # Execute query
            executor = DataviewExecutor(notes)
            result_markdown = executor.execute(query_ast)

            # Calculate execution time
            execution_time_ms = int((time.time() - start_time) * 1000)

            # Parse results to extract structured data
            structured_results = self._parse_result_markdown(result_markdown, query_ast.query_type)

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
                title = result.get("title", "")
                if title:
                    link = {
                        "target": title,
                        "type": "note",
                        "metadata": {},
                    }
                    links.append(link)

            elif result_type == "table_row":
                # Extract first column as potential link
                # (often file.link or title)
                for key, value in result.items():
                    if key in ("file.link", "title", "name") and value:
                        # Clean wikilink syntax if present
                        clean_value = value.strip()
                        if clean_value.startswith("[[") and clean_value.endswith("]]"):
                            clean_value = clean_value[2:-2]

                        link = {
                            "target": clean_value,
                            "type": "note",
                            "metadata": {k: v for k, v in result.items() if k != "type"},
                        }
                        links.append(link)
                        break  # Only extract one link per row

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
