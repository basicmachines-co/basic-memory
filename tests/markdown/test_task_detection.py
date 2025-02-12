"""Test how markdown-it handles task lists."""

from markdown_it import MarkdownIt


def test_task_token_type():
    """Verify how markdown-it parses task list items."""
    md = MarkdownIt()
    content = """
    - [ ] Unchecked task
    - [x] Completed task 
    - [-] In progress task
    """

    tokens = md.parse(content)
    for token in tokens:
        print(f"{token.type}: {token.content}")
