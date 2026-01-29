"""
Detector for Dataview queries in markdown content.
"""

import re
from dataclasses import dataclass


@dataclass
class DataviewBlock:
    """A detected Dataview query block."""

    query: str
    start_line: int
    end_line: int
    block_type: str  # "codeblock" or "inline"

    def __repr__(self) -> str:
        return f"DataviewBlock(type={self.block_type}, lines={self.start_line}-{self.end_line})"


class DataviewDetector:
    """Detects Dataview queries in markdown content."""

    # Regex patterns
    CODEBLOCK_START = re.compile(r"^```dataview\s*$", re.MULTILINE)
    CODEBLOCK_END = re.compile(r"^```\s*$", re.MULTILINE)
    INLINE_QUERY = re.compile(r"`=\s*(.+?)\s*`")

    @classmethod
    def detect_queries(cls, content: str) -> list[DataviewBlock]:
        """
        Detect all Dataview queries in markdown content.

        Returns:
            List of DataviewBlock objects containing query text and location.
        """
        blocks = []

        # Detect codeblock queries
        blocks.extend(cls._detect_codeblocks(content))

        # Detect inline queries
        blocks.extend(cls._detect_inline_queries(content))

        return blocks

    @classmethod
    def _detect_codeblocks(cls, content: str) -> list[DataviewBlock]:
        """Detect ```dataview codeblocks."""
        blocks = []
        lines = content.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i]

            # Check for dataview codeblock start
            if cls.CODEBLOCK_START.match(line):
                start_line = i
                query_lines = []
                i += 1

                # Collect query lines until we hit the closing ```
                while i < len(lines):
                    if cls.CODEBLOCK_END.match(lines[i]):
                        end_line = i
                        query = "\n".join(query_lines)
                        blocks.append(
                            DataviewBlock(
                                query=query,
                                start_line=start_line,
                                end_line=end_line,
                                block_type="codeblock",
                            )
                        )
                        break
                    query_lines.append(lines[i])
                    i += 1

            i += 1

        return blocks

    @classmethod
    def _detect_inline_queries(cls, content: str) -> list[DataviewBlock]:
        """Detect inline `= ...` queries."""
        blocks = []
        lines = content.split("\n")

        for line_num, line in enumerate(lines):
            for match in cls.INLINE_QUERY.finditer(line):
                query = match.group(1)
                blocks.append(
                    DataviewBlock(
                        query=query,
                        start_line=line_num,
                        end_line=line_num,
                        block_type="inline",
                    )
                )

        return blocks

    @classmethod
    def has_dataview_queries(cls, content: str) -> bool:
        """Check if content contains any Dataview queries."""
        return bool(cls.detect_queries(content))

    @classmethod
    def extract_query_text(cls, content: str) -> list[str]:
        """Extract just the query text from all detected queries."""
        blocks = cls.detect_queries(content)
        return [block.query for block in blocks]
