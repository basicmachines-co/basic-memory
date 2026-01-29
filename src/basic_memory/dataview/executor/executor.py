"""
Main executor for Dataview queries.

Executes parsed queries against a collection of notes.
"""

from typing import Any

from basic_memory.dataview.ast import DataviewQuery, QueryType, SortDirection
from basic_memory.dataview.errors import DataviewExecutionError
from basic_memory.dataview.executor.expression_eval import ExpressionEvaluator
from basic_memory.dataview.executor.field_resolver import FieldResolver
from basic_memory.dataview.executor.result_formatter import ResultFormatter
from basic_memory.dataview.executor.task_extractor import TaskExtractor


class DataviewExecutor:
    """Executes Dataview queries against note collections."""

    def __init__(self, notes: list[dict[str, Any]]):
        """
        Initialize executor with a collection of notes.

        Args:
            notes: List of note dictionaries
        """
        self.notes = notes
        self.field_resolver = FieldResolver()
        self.formatter = ResultFormatter()

    def execute(self, query: DataviewQuery) -> str:
        """
        Execute a query and return formatted results.

        Args:
            query: Parsed Dataview query

        Returns:
            Formatted result string (markdown)
        """
        # Filter notes by FROM clause
        filtered_notes = self._filter_by_from(query.from_source)

        # Apply WHERE clause
        if query.where_clause:
            filtered_notes = self._filter_by_where(filtered_notes, query.where_clause)

        # Execute based on query type
        if query.query_type == QueryType.TABLE:
            return self._execute_table(filtered_notes, query)
        elif query.query_type == QueryType.LIST:
            return self._execute_list(filtered_notes, query)
        elif query.query_type == QueryType.TASK:
            return self._execute_task(filtered_notes, query)
        else:
            raise DataviewExecutionError(f"Unsupported query type: {query.query_type}")

    def _filter_by_from(self, from_source: str | None) -> list[dict[str, Any]]:
        """Filter notes by FROM clause.
        
        Supports both flat and nested note structures:
        - Flat: {"path": "...", "folder": "...", ...}
        - Nested: {"file": {"path": "...", "folder": "..."}, ...}
        """
        if not from_source:
            return self.notes

        # Simple path matching
        filtered = []
        for note in self.notes:
            # Support both flat and nested structures
            # Try flat structure first (legacy)
            path = note.get("path")
            if path is None:
                # Try nested structure (from sync_service)
                file_info = note.get("file", {})
                path = file_info.get("path", "")
            
            # Match exact path or folder prefix
            if from_source in path or path.startswith(from_source):
                filtered.append(note)

        return filtered

    def _filter_by_where(
        self, notes: list[dict[str, Any]], where_clause: Any
    ) -> list[dict[str, Any]]:
        """Filter notes by WHERE clause."""
        filtered = []
        for note in notes:
            evaluator = ExpressionEvaluator(note)
            try:
                result = evaluator.evaluate(where_clause.expression)
                if result:
                    filtered.append(note)
            except Exception:
                # Skip notes that cause evaluation errors
                continue

        return filtered

    def _execute_table(
        self, notes: list[dict[str, Any]], query: DataviewQuery
    ) -> str:
        """Execute TABLE query."""
        if not query.fields:
            raise DataviewExecutionError("TABLE query requires fields")

        # Extract field names and evaluate expressions
        results = []
        field_names = []

        for field in query.fields:
            field_name = field.alias or self._get_field_name(field.expression)
            field_names.append(field_name)

        for note in notes:
            evaluator = ExpressionEvaluator(note)
            row = {}
            # Always include title for link discovery
            row["title"] = note.get("title", "Untitled")
            row["file.link"] = f"[[{note.get('title', 'Untitled')}]]"
            
            for field in query.fields:
                field_name = field.alias or self._get_field_name(field.expression)
                try:
                    value = evaluator.evaluate(field.expression)
                    row[field_name] = value
                except Exception:
                    row[field_name] = None
            results.append(row)

        # Apply SORT
        if query.sort_clauses:
            results = self._apply_sort(results, query.sort_clauses)

        # Apply LIMIT
        if query.limit:
            results = results[: query.limit]

        return self.formatter.format_table(results, field_names)

    def _execute_list(
        self, notes: list[dict[str, Any]], query: DataviewQuery
    ) -> str:
        """Execute LIST query."""
        results = []

        for note in notes:
            results.append(
                {
                    "file.link": f"[[{note.get('title', 'Untitled')}]]",
                    "title": note.get("title", "Untitled"),
                }
            )

        # Apply SORT
        if query.sort_clauses:
            results = self._apply_sort(results, query.sort_clauses)

        # Apply LIMIT
        if query.limit:
            results = results[: query.limit]

        return self.formatter.format_list(results)

    def _execute_task(
        self, notes: list[dict[str, Any]], query: DataviewQuery
    ) -> str:
        """Execute TASK query."""
        all_tasks = []

        for note in notes:
            tasks = TaskExtractor.extract_tasks_from_note(note)
            all_tasks.extend([t.to_dict() for t in tasks])

        # Apply SORT
        if query.sort_clauses:
            all_tasks = self._apply_sort(all_tasks, query.sort_clauses)

        # Apply LIMIT
        if query.limit:
            all_tasks = all_tasks[: query.limit]

        return self.formatter.format_task_list(all_tasks)

    def _apply_sort(
        self, results: list[dict[str, Any]], sort_clauses: list[Any]
    ) -> list[dict[str, Any]]:
        """Apply SORT clauses to results."""
        for sort_clause in reversed(sort_clauses):
            field = sort_clause.field
            reverse = sort_clause.direction == SortDirection.DESC

            # Handle None values in sorting by placing them at the end
            def sort_key(x):
                value = x.get(field, "")
                # Place None values at the end
                if value is None:
                    return (1, "")  # (1, "") sorts after (0, actual_value)
                return (0, value)

            results = sorted(
                results,
                key=sort_key,
                reverse=reverse,
            )

        return results

    def _get_field_name(self, expression: Any) -> str:
        """Extract field name from expression."""
        from basic_memory.dataview.ast import FieldNode

        if isinstance(expression, FieldNode):
            return expression.field_name or "unknown"
        return "result"
