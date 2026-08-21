"""ChatGPT import service for Basic Memory."""

import logging
from datetime import datetime
from typing import override, Any, Dict, List, Optional, Set

from basic_memory.markdown.schemas import EntityFrontmatter, EntityMarkdown
from basic_memory.importers.base import Importer
from basic_memory.schemas.importer import ChatImportResult
from basic_memory.importers.utils import clean_filename, format_timestamp

logger = logging.getLogger(__name__)

# One day past the Unix epoch: deterministic, still obviously a sentinel, and
# never a pre-epoch value in any local timezone — Windows' CRT raises OSError
# converting epoch-adjacent times through fromtimestamp/astimezone.
UNKNOWN_DATE_SENTINEL = 86400.0


class ChatGPTImporter(Importer[ChatImportResult]):
    """Service for importing ChatGPT conversations."""

    @override
    def handle_error(  # pragma: no cover
        self, message: str, error: Optional[Exception] = None
    ) -> ChatImportResult:
        """Return a failed ChatImportResult with an error message."""
        error_msg = f"{message}: {error}" if error else message
        return ChatImportResult(
            import_count={},
            success=False,
            error_message=error_msg,
            conversations=0,
            messages=0,
        )

    @override
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
            await self.ensure_folder_exists(destination_folder)
            conversations = source_data

            # Process each conversation
            messages_imported = 0
            chats_imported = 0

            for chat in conversations:
                created_at, modified_at = self._resolve_timestamps(chat)
                date_prefix = datetime.fromtimestamp(created_at).astimezone().strftime("%Y%m%d")
                clean_title = clean_filename(chat["title"])
                relative_path = (
                    f"{destination_folder}/{date_prefix}-{clean_title}"
                    if destination_folder
                    else f"{date_prefix}-{clean_title}"
                )
                permalink, file_path = self.build_import_paths(relative_path)

                # Convert to entity
                entity = self._format_chat_content(chat, permalink, created_at, modified_at)

                # Write file using relative path - FileService handles base_path
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
            return self.handle_error("Failed to import ChatGPT conversations", e)

    def _resolve_timestamps(self, conversation: Dict[str, Any]) -> tuple[float, float]:
        """Resolve conversation timestamps, tolerating absent fields.

        OpenAI's export format does not guarantee `create_time` or `update_time`
        on every conversation object (#1276). Fall back in order: the earliest
        message timestamp, the conversation's `update_time`, an epoch sentinel.
        Every rung must stay stable across re-exports — the resolved date names
        the output file (`YYYYMMDD-title.md`), so a value that changes between
        exports would make the next import write a duplicate note under a new
        name instead of updating the original. That is why the earliest message
        time outranks `update_time` (existing messages keep their timestamps
        while `update_time` advances whenever the conversation continues) and
        why the last resort is a fixed sentinel whose obviously-wrong 1970
        prefix reads as "date unknown" rather than faking a plausible one.

        Args:
            conversation: ChatGPT conversation data.

        Returns:
            Tuple of (created_at, modified_at) unix timestamps.
        """
        created_at = conversation.get("create_time")
        modified_at = conversation.get("update_time")
        if created_at is None:
            message_times = [
                node["message"]["create_time"]
                for node in conversation.get("mapping", {}).values()
                if node.get("message") and node["message"].get("create_time") is not None
            ]
            created_at = min(message_times) if message_times else None
        if created_at is None:
            created_at = modified_at
        if created_at is None:
            created_at = UNKNOWN_DATE_SENTINEL
        if modified_at is None:
            modified_at = created_at
        return created_at, modified_at

    def _format_chat_content(
        self, conversation: Dict[str, Any], permalink: str, created_at: float, modified_at: float
    ) -> EntityMarkdown:  # pragma: no cover
        """Convert chat conversation to Basic Memory entity.

        Args:
            conversation: ChatGPT conversation data.
            permalink: Permalink for the entity.
            created_at: Resolved creation timestamp.
            modified_at: Resolved modification timestamp.

        Returns:
            EntityMarkdown instance representing the conversation.
        """
        root_id = None
        # Find root message
        for node_id, node in conversation["mapping"].items():
            if node.get("parent") is None:
                root_id = node_id
                break

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
                    "permalink": permalink,
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
        """Traverse message tree iteratively to handle deep conversations.

        Args:
            mapping: Message mapping.
            root_id: Root message ID.
            seen: Set of seen message IDs.

        Returns:
            List of message data.
        """
        messages = []
        if not root_id:
            return messages

        # Use iterative approach with stack to avoid recursion depth issues
        stack = [root_id]

        while stack:
            node_id = stack.pop()
            if not node_id:
                continue

            node = mapping.get(node_id)
            if not node:
                continue

            # Process current node if it has a message and hasn't been seen
            if node["id"] not in seen and node.get("message"):
                seen.add(node["id"])
                messages.append(node["message"])

            # Add children to stack in reverse order to maintain conversation flow
            children = node.get("children", [])
            for child_id in reversed(children):
                stack.append(child_id)

        return messages
