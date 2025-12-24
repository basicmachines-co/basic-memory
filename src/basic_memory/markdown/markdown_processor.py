from pathlib import Path
from typing import Optional
from collections import OrderedDict

from frontmatter import Post
from loguru import logger


from basic_memory import file_utils
from basic_memory.file_utils import dump_frontmatter
from basic_memory.markdown.entity_parser import EntityParser
from basic_memory.markdown.schemas import EntityMarkdown, Observation, Relation


class DirtyFileError(Exception):
    """Raised when attempting to write to a file that has been modified."""

    pass


class MarkdownProcessor:
    """Process markdown files while preserving content and structure.

    used only for import

    This class handles the file I/O aspects of our markdown processing. It:
    1. Uses EntityParser for reading/parsing files into our schema
    2. Handles writing files with proper frontmatter
    3. Formats structured sections (observations/relations) consistently
    4. Preserves user content exactly as written
    5. Performs atomic writes using temp files

    It does NOT:
    1. Modify the schema directly (that's done by services)
    2. Handle in-place updates (everything is read->modify->write)
    3. Track schema changes (that's done by the database)
    """

    def __init__(self, entity_parser: EntityParser):
        """Initialize processor with base path and parser."""
        self.entity_parser = entity_parser

    async def read_file(self, path: Path) -> EntityMarkdown:
        """Read and parse file into EntityMarkdown schema.

        This is step 1 of our read->modify->write pattern.
        We use EntityParser to handle all the markdown parsing.
        """
        return await self.entity_parser.parse_file(path)

    async def write_file(
        self,
        path: Path,
        markdown: EntityMarkdown,
        expected_checksum: Optional[str] = None,
    ) -> str:
        """Write EntityMarkdown schema back to file.

        This is step 3 of our read->modify->write pattern.
        The entire file is rewritten atomically on each update.

        File Structure:
        ---
        frontmatter fields
        ---
        user content area (preserved exactly)

        ## Observations (if any)
        formatted observations

        ## Relations (if any)
        formatted relations

        Args:
            path: Where to write the file
            markdown: Complete schema to write
            expected_checksum: If provided, verify file hasn't changed

        Returns:
            Checksum of written file

        Raises:
            DirtyFileError: If file has been modified (when expected_checksum provided)
        """
        # Dirty check if needed
        if expected_checksum is not None:
            current_content = path.read_text(encoding="utf-8")
            current_checksum = await file_utils.compute_checksum(current_content)
            if current_checksum != expected_checksum:
                raise DirtyFileError(f"File {path} has been modified")

        # Convert frontmatter to dict
        frontmatter_dict = OrderedDict()
        frontmatter_dict["title"] = markdown.frontmatter.title
        frontmatter_dict["type"] = markdown.frontmatter.type
        frontmatter_dict["permalink"] = markdown.frontmatter.permalink

        metadata = markdown.frontmatter.metadata or {}
        for k, v in metadata.items():
            frontmatter_dict[k] = v

        # Start with user content (or minimal title for new files)
        content = markdown.content or f"# {markdown.frontmatter.title}\n"

        # Add structured sections with proper spacing
        content = content.rstrip()  # Remove trailing whitespace

        # add a blank line if we have semantic content
        if markdown.observations or markdown.relations:
            content += "\n"

        if markdown.observations:
            content += self.format_observations(markdown.observations)
        if markdown.relations:
            content += self.format_relations(markdown.relations)

        # Create Post object for frontmatter
        post = Post(content, **frontmatter_dict)
        final_content = dump_frontmatter(post)

        logger.debug(f"writing file {path} with content:\n{final_content}")

        # Write atomically and return checksum of updated file
        path.parent.mkdir(parents=True, exist_ok=True)
        await file_utils.write_file_atomic(path, final_content)
        return await file_utils.compute_checksum(final_content)

    def format_observations(self, observations: list[Observation]) -> str:
        """Format observations section in standard way.

        Format: - [category] content #tag1 #tag2 (context)
        """
        lines = [f"{obs}" for obs in observations]
        return "\n".join(lines) + "\n"

    def format_relations(self, relations: list[Relation]) -> str:
        """Format relations section in standard way.

        Format: - relation_type [[target]] (context)
        """
        lines = [f"{rel}" for rel in relations]
        return "\n".join(lines) + "\n"
