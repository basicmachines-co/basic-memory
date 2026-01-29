"""
Task extractor for TASK queries.

Extracts tasks from markdown content.
"""

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class Task:
    """A task extracted from markdown."""

    text: str
    completed: bool
    line_number: int
    indentation: int = 0
    subtasks: list["Task"] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "text": self.text,
            "completed": self.completed,
            "line": self.line_number,
            "indentation": self.indentation,
            "subtasks": [t.to_dict() for t in self.subtasks] if self.subtasks else [],
        }


class TaskExtractor:
    """Extracts tasks from markdown content."""

    # Regex for task items
    TASK_PATTERN = re.compile(r"^(\s*)[-*]\s+\[([ xX])\]\s+(.+)$")

    @classmethod
    def extract_tasks(cls, content: str) -> list[Task]:
        """
        Extract all tasks from markdown content.

        Args:
            content: Markdown content

        Returns:
            List of Task objects
        """
        tasks = []
        lines = content.split("\n")

        for line_num, line in enumerate(lines, start=1):
            match = cls.TASK_PATTERN.match(line)
            if match:
                indent_str, status, text = match.groups()
                indentation = len(indent_str)
                completed = status.lower() == "x"

                task = Task(
                    text=text.strip(),
                    completed=completed,
                    line_number=line_num,
                    indentation=indentation,
                )
                tasks.append(task)

        return tasks

    @classmethod
    def extract_tasks_from_note(cls, note: dict[str, Any]) -> list[Task]:
        """Extract tasks from a note dictionary."""
        content = note.get("content", "")
        return cls.extract_tasks(content)
