"""Tests for TaskExtractor."""

import pytest

from basic_memory.dataview.executor.task_extractor import Task, TaskExtractor


class TestTaskExtractorBasic:
    """Test basic task extraction."""

    def test_extract_single_task(self):
        """Test extracting single task."""
        content = "- [ ] Task 1"
        tasks = TaskExtractor.extract_tasks(content)
        assert len(tasks) == 1
        assert tasks[0].text == "Task 1"
        assert tasks[0].completed is False

    def test_extract_completed_task(self):
        """Test extracting completed task."""
        content = "- [x] Done task"
        tasks = TaskExtractor.extract_tasks(content)
        assert len(tasks) == 1
        assert tasks[0].completed is True

    def test_extract_completed_task_uppercase(self):
        """Test extracting completed task with uppercase X."""
        content = "- [X] Done task"
        tasks = TaskExtractor.extract_tasks(content)
        assert len(tasks) == 1
        assert tasks[0].completed is True

    def test_extract_multiple_tasks(self):
        """Test extracting multiple tasks."""
        content = """- [ ] Task 1
- [x] Task 2
- [ ] Task 3"""
        tasks = TaskExtractor.extract_tasks(content)
        assert len(tasks) == 3
        assert tasks[0].text == "Task 1"
        assert tasks[1].text == "Task 2"
        assert tasks[2].text == "Task 3"


class TestTaskExtractorIndentation:
    """Test task extraction with indentation."""

    def test_extract_indented_task(self):
        """Test extracting indented task."""
        content = "  - [ ] Subtask"
        tasks = TaskExtractor.extract_tasks(content)
        assert len(tasks) == 1
        assert tasks[0].indentation == 2

    def test_extract_nested_tasks(self):
        """Test extracting nested tasks."""
        content = """- [ ] Task 1
  - [ ] Subtask 1.1
  - [ ] Subtask 1.2
- [ ] Task 2"""
        tasks = TaskExtractor.extract_tasks(content)
        assert len(tasks) == 4
        assert tasks[0].indentation == 0
        assert tasks[1].indentation == 2
        assert tasks[2].indentation == 2
        assert tasks[3].indentation == 0

    def test_extract_deeply_nested_tasks(self):
        """Test extracting deeply nested tasks."""
        content = """- [ ] Task 1
  - [ ] Subtask 1.1
    - [ ] Subtask 1.1.1"""
        tasks = TaskExtractor.extract_tasks(content)
        assert len(tasks) == 3
        assert tasks[0].indentation == 0
        assert tasks[1].indentation == 2
        assert tasks[2].indentation == 4


class TestTaskExtractorLineNumbers:
    """Test line number tracking."""

    def test_track_line_numbers(self):
        """Test tracking line numbers."""
        content = """Line 1
- [ ] Task 1
Line 3
- [ ] Task 2"""
        tasks = TaskExtractor.extract_tasks(content)
        assert len(tasks) == 2
        assert tasks[0].line_number == 2
        assert tasks[1].line_number == 4

    def test_track_line_numbers_with_content(self, markdown_with_tasks):
        """Test tracking line numbers in real content."""
        tasks = TaskExtractor.extract_tasks(markdown_with_tasks)
        assert all(task.line_number > 0 for task in tasks)


class TestTaskExtractorAlternativeSyntax:
    """Test alternative task syntax."""

    def test_extract_task_with_asterisk(self):
        """Test extracting task with asterisk."""
        content = "* [ ] Task with asterisk"
        tasks = TaskExtractor.extract_tasks(content)
        assert len(tasks) == 1
        assert tasks[0].text == "Task with asterisk"

    def test_extract_mixed_syntax(self):
        """Test extracting tasks with mixed syntax."""
        content = """- [ ] Task with dash
* [ ] Task with asterisk"""
        tasks = TaskExtractor.extract_tasks(content)
        assert len(tasks) == 2


class TestTaskExtractorFromNote:
    """Test extracting tasks from note dictionary."""

    def test_extract_from_note(self, note_with_frontmatter):
        """Test extracting tasks from note."""
        tasks = TaskExtractor.extract_tasks_from_note(note_with_frontmatter)
        assert len(tasks) == 2
        assert tasks[0].text == "Task 1"
        assert tasks[0].completed is False
        assert tasks[1].text == "Task 2"
        assert tasks[1].completed is True

    def test_extract_from_note_no_content(self):
        """Test extracting from note without content."""
        note = {"id": 1}
        tasks = TaskExtractor.extract_tasks_from_note(note)
        assert len(tasks) == 0


class TestTaskExtractorToDict:
    """Test Task.to_dict() method."""

    def test_task_to_dict(self):
        """Test converting task to dictionary."""
        task = Task(
            text="Test task",
            completed=False,
            line_number=5,
            indentation=2,
        )
        result = task.to_dict()
        assert result["text"] == "Test task"
        assert result["completed"] is False
        assert result["line"] == 5
        assert result["indentation"] == 2
        assert result["subtasks"] == []


class TestTaskExtractorEdgeCases:
    """Test edge cases."""

    def test_extract_from_empty_content(self):
        """Test extracting from empty content."""
        tasks = TaskExtractor.extract_tasks("")
        assert len(tasks) == 0

    def test_extract_from_content_without_tasks(self):
        """Test extracting from content without tasks."""
        content = "# Heading\n\nSome text\n\n- Regular list item"
        tasks = TaskExtractor.extract_tasks(content)
        assert len(tasks) == 0

    def test_extract_task_with_special_characters(self):
        """Test extracting task with special characters."""
        content = "- [ ] Task with @mention and #tag"
        tasks = TaskExtractor.extract_tasks(content)
        assert len(tasks) == 1
        assert tasks[0].text == "Task with @mention and #tag"

    def test_extract_task_with_link(self):
        """Test extracting task with link."""
        content = "- [ ] Task with [[link]]"
        tasks = TaskExtractor.extract_tasks(content)
        assert len(tasks) == 1
        assert "[[link]]" in tasks[0].text

    def test_ignore_incomplete_task_syntax(self):
        """Test ignoring incomplete task syntax."""
        content = """- [ ] Valid task
- [] Invalid task
- [ Invalid task"""
        tasks = TaskExtractor.extract_tasks(content)
        assert len(tasks) == 1
        assert tasks[0].text == "Valid task"
