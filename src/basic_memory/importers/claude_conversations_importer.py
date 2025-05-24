"""Claude conversations import service for Basic Memory."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from basic_memory.markdown.schemas import EntityFrontmatter, EntityMarkdown
from basic_memory.importers.base import Importer
from basic_memory.schemas.importer import ChatImportResult
from basic_memory.importers.utils import clean_filename, format_timestamp

logger = logging.getLogger(__name__)


class ClaudeConversationsImporter(Importer[ChatImportResult]):
    """Service for importing Claude conversations."""

    async def import_data(
        self, source_data, destination_folder: str, **kwargs: Any
    ) -> ChatImportResult:
        """Import conversations from Claude JSON export.

        Args:
            source_data: Path to the Claude conversations.json file.
            destination_folder: Destination folder within the project.
            **kwargs: Additional keyword arguments.

        Returns:
            ChatImportResult containing statistics and status of the import.
        """
        try:
            # Ensure the destination folder exists
            folder_path = self.ensure_folder_exists(destination_folder)

            conversations = source_data

            # Process each conversation
            messages_imported = 0
            chats_imported = 0

            for chat in conversations:
                # Convert to entity
                entity = self._format_chat_content(
                    base_path=folder_path,
                    name=chat["name"],
                    messages=chat["chat_messages"],
                    created_at=chat["created_at"],
                    modified_at=chat["updated_at"],
                )

                # Write file
                file_path = self.base_path / Path(f"{entity.frontmatter.metadata['permalink']}.md")
                await self.write_entity(entity, file_path)

                chats_imported += 1
                messages_imported += len(chat["chat_messages"])

            return ChatImportResult(
                import_count={"conversations": chats_imported, "messages": messages_imported},
                success=True,
                conversations=chats_imported,
                messages=messages_imported,
            )

        except Exception as e:  # pragma: no cover
            logger.exception("Failed to import Claude conversations")
            return self.handle_error("Failed to import Claude conversations", e)  # pyright: ignore [reportReturnType]

    def _format_chat_content(
        self,
        base_path: Path,
        name: str,
        messages: List[Dict[str, Any]],
        created_at: str,
        modified_at: str,
    ) -> EntityMarkdown:
        """Convert chat messages to Basic Memory entity format.

        Args:
            base_path: Base path for the entity.
            name: Chat name.
            messages: List of chat messages.
            created_at: Creation timestamp.
            modified_at: Modification timestamp.

        Returns:
            EntityMarkdown instance representing the conversation.
        """
        # Generate permalink
        date_prefix = datetime.fromisoformat(created_at.replace("Z", "+00:00")).strftime("%Y%m%d")
        clean_title = clean_filename(name)
        permalink = f"{base_path.name}/{date_prefix}-{clean_title}"

        # Format content
        content = self._format_chat_markdown(
            name=name,
            messages=messages,
            created_at=created_at,
            modified_at=modified_at,
            permalink=permalink,
        )

        # Create entity
        entity = EntityMarkdown(
            frontmatter=EntityFrontmatter(
                metadata={
                    "type": "conversation",
                    "title": name,
                    "created": created_at,
                    "modified": modified_at,
                    "permalink": permalink,
                }
            ),
            content=content,
        )

        return entity

    def _format_chat_markdown(
        self,
        name: str,
        messages: List[Dict[str, Any]],
        created_at: str,
        modified_at: str,
        permalink: str,
    ) -> str:
        """Format chat as clean markdown.

        Args:
            name: Chat name.
            messages: List of chat messages.
            created_at: Creation timestamp.
            modified_at: Modification timestamp.
            permalink: Permalink for the entity.

        Returns:
            Formatted markdown content.
        """
        # Start with frontmatter and title
        lines = [
            f"# {name}\n",
        ]

        # Add messages
        for msg in messages:
            # Format timestamp
            ts = format_timestamp(msg["created_at"])

            # Add message header
            lines.append(f"### {msg['sender'].title()} ({ts})")

            # Handle message content
            content = msg.get("text", "")
            if msg.get("content"):
                content = " ".join(c.get("text", "") for c in msg["content"])
            lines.append(content)

            # Handle attachments
            attachments = msg.get("attachments", [])
            for attachment in attachments:
                if "file_name" in attachment:
                    lines.append(f"\n**Attachment: {attachment['file_name']}**")
                    if "extracted_content" in attachment:
                        lines.append("```")
                        lines.append(attachment["extracted_content"])
                        lines.append("```")

            # Add spacing between messages
            lines.append("")

        return "\n".join(lines)
