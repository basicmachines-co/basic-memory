"""Markdown-it plugins for Basic Memory markdown parsing."""

from typing import List, Any, Dict
from markdown_it import MarkdownIt
from markdown_it.token import Token


# Observation handling functions
def is_observation(token: Token) -> bool:
    """Check if token looks like our observation format."""
    if token.type != "inline":  # pragma: no cover
        return False

    content = token.content.strip()
    if not content:  # pragma: no cover
        return False

    # if it's a markdown_task, return false
    if content.startswith("[ ]") or content.startswith("[x]") or content.startswith("[-]"):
        return False

    has_category = content.startswith("[") and "]" in content
    has_tags = "#" in content
    return has_category or has_tags


def parse_observation(token: Token) -> Dict[str, Any]:
    """Extract observation parts from token."""
    # Strip bullet point if present
    content = token.content.strip()

    # Parse [category]
    category = None
    if content.startswith("["):
        end = content.find("]")
        if end != -1:
            category = content[1:end].strip() or None  # Convert empty to None
            content = content[end + 1 :].strip()

    # Parse (context)
    context = None
    if content.endswith(")"):
        start = content.rfind("(")
        if start != -1:
            context = content[start + 1 : -1].strip()
            content = content[:start].strip()

    # Extract tags and keep original content
    tags = []
    parts = content.split()
    for part in parts:
        if part.startswith("#"):
            # Handle multiple #tags stuck together
            if "#" in part[1:]:
                # Split on # but keep non-empty tags
                subtags = [t for t in part.split("#") if t]
                tags.extend(subtags)
            else:
                tags.append(part[1:])

    return {
        "category": category,
        "content": content,
        "tags": tags if tags else None,
        "context": context,
    }


# Relation handling functions
def is_explicit_relation(token: Token) -> bool:
    """Check if token looks like our relation format."""
    if token.type != "inline":  # pragma: no cover
        return False

    content = token.content.strip()
    return "[[" in content and "]]" in content


def parse_relation(token: Token) -> Dict[str, Any] | None:
    """Extract relation parts from token."""
    # Remove bullet point if present
    content = token.content.strip()

    # Extract [[target]]
    target = None
    rel_type = "relates_to"  # default
    context = None

    start = content.find("[[")
    end = content.find("]]")

    if start != -1 and end != -1:
        # Get text before link as relation type
        before = content[:start].strip()
        if before:
            rel_type = before

        # Get target
        target = content[start + 2 : end].strip()

        # Look for context after
        after = content[end + 2 :].strip()
        if after.startswith("(") and after.endswith(")"):
            context = after[1:-1].strip() or None

    if not target:  # pragma: no cover
        return None

    return {"type": rel_type, "target": target, "context": context}


def parse_inline_relations(content: str) -> List[Dict[str, Any]]:
    """Find wiki-style links in regular content."""
    relations = []
    start = 0

    while True:
        # Find next outer-most [[
        start = content.find("[[", start)
        if start == -1:  # pragma: no cover
            break

        # Find matching ]]
        depth = 1
        pos = start + 2
        end = -1

        while pos < len(content):
            if content[pos : pos + 2] == "[[":
                depth += 1
                pos += 2
            elif content[pos : pos + 2] == "]]":
                depth -= 1
                if depth == 0:
                    end = pos
                    break
                pos += 2
            else:
                pos += 1

        if end == -1:
            # No matching ]] found
            break

        target = content[start + 2 : end].strip()
        if target:
            relations.append({"type": "links to", "target": target, "context": None})

        start = end + 2

    return relations


def observation_plugin(md: MarkdownIt) -> None:
    """Plugin for parsing observation format:
    - [category] Content text #tag1 #tag2 (context)
    - Content text #tag1 (context)  # No category is also valid
    """

    def observation_rule(state: Any) -> None:
        """Process observations in token stream."""
        tokens = state.tokens
        current_section = None
        in_list_item = False

        for idx in range(len(tokens)):
            token = tokens[idx]

            # Track current section by headings
            if token.type == "heading_open":
                next_token = tokens[idx + 1] if idx + 1 < len(tokens) else None
                if next_token and next_token.type == "inline":
                    current_section = next_token.content.lower()

            # Track list nesting
            elif token.type == "list_item_open":
                in_list_item = True
            elif token.type == "list_item_close":
                in_list_item = False

            # Initialize meta for all tokens
            token.meta = token.meta or {}

            # Parse observations in list items
            if token.type == "inline" and is_observation(token):
                obs = parse_observation(token)
                if obs["content"]:  # Only store if we have content
                    token.meta["observation"] = obs

    # Add the rule after inline processing
    md.core.ruler.after("inline", "observations", observation_rule)


def relation_plugin(md: MarkdownIt) -> None:
    """Plugin for parsing relation formats:

    Explicit relations:
    - relation_type [[target]] (context)

    Implicit relations (links in content):
    Some text with [[target]] reference
    """

    def relation_rule(state: Any) -> None:
        """Process relations in token stream."""
        tokens = state.tokens
        current_section = None
        in_list_item = False

        for idx in range(len(tokens)):
            token = tokens[idx]

            # Track current section by headings
            if token.type == "heading_open":
                next_token = tokens[idx + 1] if idx + 1 < len(tokens) else None
                if next_token and next_token.type == "inline":
                    current_section = next_token.content.lower()

            # Track list nesting
            elif token.type == "list_item_open":
                in_list_item = True
            elif token.type == "list_item_close":
                in_list_item = False

            # Initialize meta for all tokens
            token.meta = token.meta or {}

            # Only process inline tokens
            if token.type == "inline":
                # Check for explicit relations in list items
                if in_list_item and is_explicit_relation(token):
                    rel = parse_relation(token)
                    if rel:
                        token.meta["relations"] = [rel]

                # Always check for inline links in any text
                elif "[[" in token.content:
                    rels = parse_inline_relations(token.content)
                    if rels:
                        token.meta["relations"] = token.meta.get("relations", []) + rels

    # Add the rule after inline processing
    md.core.ruler.after("inline", "relations", relation_rule)
