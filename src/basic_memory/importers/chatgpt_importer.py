"""ChatGPT import service for Basic Memory."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from basic_memory.markdown.schemas import EntityFrontmatter, EntityMarkdown
from basic_memory.importers.base import Importer
from basic_memory.schemas.importer import ChatImportResult
from basic_memory.importers.utils import clean_filename, format_timestamp

logger = logging.getLogger(__name__)


class ChatGPTImporter(Importer[ChatImportResult]):
    """Service for importing ChatGPT conversations."""

    async def import_data(
        self, source_data, destination_folder: str, **kwargs: Any
    ) -> ChatImportResult:
        """Import conversations from ChatGPT JSON export.

        Args:
            source_path: Path to the ChatGPT conversations.json file.
            destination_folder: Destination folder within the project.
            **kwargs: Additional keyword arguments.

        Returns:
            ChatImportResult containing statistics and status of the import.
        """
        try:  # pragma: no cover
            # Ensure the destination folder exists
            self.ensure_folder_exists(destination_folder)
            conversations = source_data

            # Process each conversation
            messages_imported = 0
            chats_imported = 0

            for chat in conversations:
                # Convert to entity
                entity = self._format_chat_content(destination_folder, chat)

                # Write file
                file_path = self.base_path / f"{entity.frontmatter.metadata['permalink']}.md"
                await self.write_entity(entity, file_path)

                # Count messages
                msg_count = sum(
                    1
                    for node in chat["mapping"].values()
                    if node.get("message")
                    and not node.get("message", {})
                    .get("metadata", {})
                    .get("is_visually_hidden_from_conversation")
                )

                chats_imported += 1
                messages_imported += msg_count

            return ChatImportResult(
                import_count={"conversations": chats_imported, "messages": messages_imported},
                success=True,
                conversations=chats_imported,
                messages=messages_imported,
            )

        except Exception as e:  # pragma: no cover
            logger.exception("Failed to import ChatGPT conversations")
            return self.handle_error("Failed to import ChatGPT conversations", e)  # pyright: ignore [reportReturnType]

    def _format_chat_content(
        self, folder: str, conversation: Dict[str, Any]
    ) -> EntityMarkdown:  # pragma: no cover
        """Convert chat conversation to Basic Memory entity.

        Args:
            folder: Destination folder name.
            conversation: ChatGPT conversation data.

        Returns:
            EntityMarkdown instance representing the conversation.
        """
        # Extract timestamps
        created_at = conversation["create_time"]
        modified_at = conversation["update_time"]

        root_id = None
        # Find root message
        for node_id, node in conversation["mapping"].items():
            if node.get("parent") is None:
                root_id = node_id
                break

        # Generate permalink
        date_prefix = datetime.fromtimestamp(created_at).strftime("%Y%m%d")
        clean_title = clean_filename(conversation["title"])

        # Format content
        content = self._format_chat_markdown(
            title=conversation["title"],
            mapping=conversation["mapping"],
            root_id=root_id,
            created_at=created_at,
            modified_at=modified_at,
        )

        # Create entity
        entity = EntityMarkdown(
            frontmatter=EntityFrontmatter(
                metadata={
                    "type": "conversation",
                    "title": conversation["title"],
                    "created": format_timestamp(created_at),
                    "modified": format_timestamp(modified_at),
                    "permalink": f"{folder}/{date_prefix}-{clean_title}",
                }
            ),
            content=content,
        )

        return entity

    def _format_chat_markdown(
        self,
        title: str,
        mapping: Dict[str, Any],
        root_id: Optional[str],
        created_at: float,
        modified_at: float,
    ) -> str:  # pragma: no cover
        """Format chat as clean markdown.

        Args:
            title: Chat title.
            mapping: Message mapping.
            root_id: Root message ID.
            created_at: Creation timestamp.
            modified_at: Modification timestamp.

        Returns:
            Formatted markdown content.
        """
        # Start with title
        lines = [f"# {title}\n"]

        # Traverse message tree
        seen_msgs: Set[str] = set()
        messages = self._traverse_messages(mapping, root_id, seen_msgs)

        # Format each message
        for msg in messages:
            # Skip hidden messages
            if msg.get("metadata", {}).get("is_visually_hidden_from_conversation"):
                continue

            # Get author and timestamp
            author = msg["author"]["role"].title()
            ts = format_timestamp(msg["create_time"]) if msg.get("create_time") else ""

            # Add message header
            lines.append(f"### {author} ({ts})")

            # Add message content
            content = self._get_message_content(msg)
            if content:
                lines.append(content)

            # Add spacing
            lines.append("")

        return "\n".join(lines)

    def _get_message_content(self, message: Dict[str, Any]) -> str:  # pragma: no cover
        """Extract clean message content.

        Args:
            message: Message data.

        Returns:
            Cleaned message content.
        """
        if not message or "content" not in message:
            return ""

        content = message["content"]
        if content.get("content_type") == "text":
            return "\n".join(content.get("parts", []))
        elif content.get("content_type") == "code":
            return f"```{content.get('language', '')}\n{content.get('text', '')}\n```"
        return ""

    def _traverse_messages(
        self, mapping: Dict[str, Any], root_id: Optional[str], seen: Set[str]
    ) -> List[Dict[str, Any]]:  # pragma: no cover
        """Traverse message tree and return messages in order.

        Args:
            mapping: Message mapping.
            root_id: Root message ID.
            seen: Set of seen message IDs.

        Returns:
            List of message data.
        """
        messages = []
        node = mapping.get(root_id) if root_id else None

        while node:
            if node["id"] not in seen and node.get("message"):
                seen.add(node["id"])
                messages.append(node["message"])

            # Follow children
            children = node.get("children", [])
            for child_id in children:
                child_msgs = self._traverse_messages(mapping, child_id, seen)
                messages.extend(child_msgs)

            break  # Don't follow siblings

        return messages
