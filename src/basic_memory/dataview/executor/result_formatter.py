"""
Result formatter for Dataview query results.

Formats query results as markdown tables, lists, etc.
"""

from typing import Any


class ResultFormatter:
    """Formats query results for display."""

    @classmethod
    def format_table(cls, results: list[dict[str, Any]], fields: list[str]) -> str:
        """
        Format results as a markdown table.

        Args:
            results: List of result dictionaries
            fields: List of field names to display

        Returns:
            Markdown table string
        """
        if not results:
            return "_No results_"

        # Build header
        header = "| " + " | ".join(fields) + " |"
        separator = "| " + " | ".join(["---"] * len(fields)) + " |"

        # Build rows
        rows = []
        for result in results:
            row_values = []
            for field in fields:
                value = result.get(field, "")
                # Format value
                if value is None:
                    value = ""
                elif isinstance(value, bool):
                    value = "✓" if value else "✗"
                elif isinstance(value, list):
                    value = ", ".join(str(v) for v in value)
                else:
                    value = str(value)
                row_values.append(value)
            rows.append("| " + " | ".join(row_values) + " |")

        return "\n".join([header, separator] + rows)

    @classmethod
    def format_list(cls, results: list[dict[str, Any]], field: str = "file.link") -> str:
        """
        Format results as a markdown list.

        Args:
            results: List of result dictionaries
            field: Field to display (default: file.link)

        Returns:
            Markdown list string
        """
        if not results:
            return "_No results_"

        lines = []
        for result in results:
            value = result.get(field, result.get("title", "Unknown"))
            lines.append(f"- {value}")

        return "\n".join(lines)

    @classmethod
    def format_task_list(cls, tasks: list[dict[str, Any]]) -> str:
        """
        Format tasks as a markdown task list.

        Args:
            tasks: List of task dictionaries

        Returns:
            Markdown task list string
        """
        if not tasks:
            return "_No tasks_"

        lines = []
        for task in tasks:
            status = "x" if task.get("completed") else " "
            text = task.get("text", "")
            indent = "  " * task.get("indentation", 0)
            lines.append(f"{indent}- [{status}] {text}")

        return "\n".join(lines)
