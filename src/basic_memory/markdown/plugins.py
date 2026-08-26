"""Markdown-it plugins for Basic Memory markdown parsing."""

import re
from typing import List, Any, Dict

from basic_memory.utils import normalize_project_reference
from markdown_it import MarkdownIt
from markdown_it.rules_inline.backticks import backtick
from markdown_it.token import Token

# Transcript timecodes like [00:00:11] or [1:02:03.500] share the bracket shape of
# observation categories, so indexed transcripts would mint one junk observation per
# spoken line. A category that is purely a clock value is never a semantic category;
# those bracket prefixes stay ordinary content (issue #1219).
_TIMESTAMP_VALUE = r"\d{1,3}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?"
_TIMESTAMP_CATEGORY = re.compile(rf"^{_TIMESTAMP_VALUE}(?:\s+-\s+{_TIMESTAMP_VALUE})?$")
_LINKS_TO_DIRECTIVE = re.compile(r"\s+#bm:links_to\s*$")


def remove_links_to_directive(content: str) -> tuple[str, bool]:
    """Remove an exact terminal ``#bm:links_to`` directive from content."""
    match = _LINKS_TO_DIRECTIVE.search(content)
    if not match:
        return content, False
    return content[: match.start()].rstrip(), True


def _is_task_marker_category(category: str) -> bool:
    """Recognize checkbox-marker shapes the GFM/Obsidian task family uses.

    `[ ]`, `[x]`, and `[-]` are excluded upstream, but the extended vocabulary
    (`[/]` in progress, `[>]` deferred, `[?]` question, uppercase `[X]`) shares the
    bracket shape and would otherwise mint junk one-character categories (#1241).
    Single-character alphanumeric categories other than x/X keep parsing as today.
    """
    if len(category) != 1:
        return False
    return category in {"x", "X"} or not category.isalnum()


def _observation_category_match(content: str) -> re.Match[str] | None:
    """Match ``[category] content``, rejecting timestamp and task-marker shapes."""
    match = re.match(r"^\[([^\[\]()]+)\]\s+(.+)", content)
    if not match:
        return None
    category = match.group(1).strip()
    if _TIMESTAMP_CATEGORY.match(category) or _is_task_marker_category(category):
        return None
    return match


# Observation handling functions
def is_observation(token: Token) -> bool:
    """Check if token looks like our observation format."""

    if token.type != "inline":  # pragma: no cover
        return False
    # Use token.tag which contains the actual content for test tokens, fallback to content
    content = (token.tag or token.content).strip()
    content, _ = remove_links_to_directive(content)
    if not content:  # pragma: no cover
        return False
    # if it's a markdown_task, return false
    if content.startswith("[ ]") or content.startswith("[x]") or content.startswith("[-]"):
        return False

    # Exclude markdown links: [text](url)
    if re.match(r"^\[.*?\]\(.*?\)$", content):
        return False

    # Exclude wiki links: [[text]]
    if re.match(r"^\[\[.*?\]\]$", content):
        return False

    # Check for proper observation format: [category] content
    match = _observation_category_match(content)
    # Check for standalone hashtags (words starting with #)
    # This excludes # in HTML attributes like color="#4285F4"
    has_tags = any(part.startswith("#") for part in content.split())
    return bool(match) or has_tags


def parse_observation(token: Token) -> Dict[str, Any]:
    """Extract observation parts from token."""

    # Use token.tag which contains the actual content for test tokens, fallback to content
    content = (token.tag or token.content).strip()
    content, _ = remove_links_to_directive(content)

    # Parse [category] with regex; a timestamp-shaped prefix is not a category, so a
    # hashtag-promoted transcript line keeps its timecode inside the content instead.
    match = _observation_category_match(content)
    category = None
    if match:
        category = match.group(1).strip()
        content = match.group(2).strip()
    else:
        # Handle empty brackets [] followed by content
        empty_match = re.match(r"^\[\]\s+(.+)", content)
        if empty_match:
            content = empty_match.group(1).strip()

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
            if "#" in part[1:]:
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
def _relation_content(token: Token) -> str:
    """Return the source content of an inline token."""
    return token.tag or token.content


_CODE_SPANS_KEY = "basic_memory_code_spans"
_CODE_SPAN_PARSER = MarkdownIt()


def _record_inline_code_span(state: Any, silent: bool) -> bool:
    """Delegate to MarkdownIt and retain exact source spans for code tokens."""
    start = state.pos
    token_count = len(state.tokens)
    matched = backtick(state, silent)
    if matched and not silent and len(state.tokens) > token_count:
        if state.tokens[-1].type == "code_inline":
            state.env[_CODE_SPANS_KEY].append((start, state.pos))
    return matched


_CODE_SPAN_PARSER.inline.ruler.at("backticks", _record_inline_code_span)


def _inline_code_spans(content: str) -> list[tuple[int, int]]:
    """Return source ranges that MarkdownIt classifies as inline code.

    This delegates delimiter and escape handling to the same MarkdownIt inline
    rule used by the document parser instead of duplicating CommonMark's
    backtick scanner. It also preserves MarkdownIt's linear-time cache for
    unmatched delimiter runs.
    """
    if "`" not in content:
        return []

    spans: list[tuple[int, int]] = []
    _CODE_SPAN_PARSER.inline.parse(content, _CODE_SPAN_PARSER, {_CODE_SPANS_KEY: spans}, [])
    return spans


def _mask_wikilinks_in_inline_code(content: str, code_spans: list[tuple[int, int]]) -> str:
    """Mask only brackets in code spans, retaining all non-link source text."""
    if not code_spans:
        return content

    masked = list(content)
    for start, end in code_spans:
        for position in range(start, end):
            if masked[position] in "[]":
                masked[position] = " "

    return "".join(masked)


def _remove_links_to_directive_outside_inline_code(
    content: str, code_spans: list[tuple[int, int]]
) -> tuple[str, bool]:
    """Remove a terminal directive only when MarkdownIt parsed it as non-code."""
    directive_content = list(content)
    for start, end in code_spans:
        directive_content[start:end] = " " * (end - start)

    match = _LINKS_TO_DIRECTIVE.search("".join(directive_content))
    if not match:
        return content, False
    return content[: match.start()].rstrip(), True


def _relation_parsing_content(token: Token) -> tuple[str, str, list[tuple[int, int]]]:
    """Build source-preserving relation input from MarkdownIt's code spans."""
    source_content = _relation_content(token)
    code_spans = _inline_code_spans(source_content)
    return (
        source_content,
        _mask_wikilinks_in_inline_code(source_content, code_spans),
        code_spans,
    )


def parse_relation_type(content: str) -> str | None:
    """Return the explicit relation label before the first wikilink, if any."""
    before_link = content.partition("[[")[0].strip()
    if not before_link:
        return None

    # Trigger: relation labels that need spaces must be quoted.
    # Why: unquoted multi-word prefixes are indistinguishable from prose
    # containing a wikilink.
    # Outcome: `some_type [[Target]]`, `"some type" [[Target]]`, and
    # `'some type' [[Target]]` are explicit; `some other thing [[Target]]`
    # falls back to inline `links_to` handling.
    quote = before_link[0]
    if quote in {"'", '"'} and before_link.endswith(quote):
        quoted_label = before_link[1:-1].strip()
        return quoted_label or None

    if any(char.isspace() for char in before_link):
        return None
    return before_link


def is_explicit_relation(token: Token) -> bool:
    """Check if token looks like our relation format."""
    if token.type != "inline":  # pragma: no cover
        return False

    _, content, _ = _relation_parsing_content(token)
    if "[[" not in content or "]]" not in content:
        return False
    return _parse_explicit_relation(content) is not None


def _parse_explicit_relation(
    content: str, source_content: str | None = None
) -> Dict[str, Any] | None:
    """Parse ``type [[target]] (context)``, rejecting lines with a prose tail."""
    source_content = source_content or content
    rel_type = parse_relation_type(content)
    if rel_type is None:
        return None

    start = content.find("[[")
    end = content.find("]]", start + 2)
    if start == -1 or end == -1:
        return None

    target = normalize_project_reference(source_content[start + 2 : end].strip())
    if not target:
        return None

    # Trigger: text follows the target that is not a single parenthesized context.
    # Why: an explicit relation line ends at its target or its (context). A prose
    #   tail means the line is a sentence that happens to contain a wikilink; the
    #   old behavior minted a junk type from the word before the link and silently
    #   dropped the tail — including any further [[links]] in it — from the edge
    #   (#1260).
    # Outcome: such lines fall through to inline links_to handling, which keeps
    #   every wikilink on the line as an edge and the sentence intact as content.
    after = content[end + 2 :].strip()
    context = None
    if after:
        if not _is_single_parenthesized(after):
            return None
        source_after = source_content[end + 2 :].strip()
        context = source_after[1:-1].strip() or None

    return {"type": rel_type, "target": target, "context": context}


def _is_single_parenthesized(text: str) -> bool:
    """Whether the text is one balanced ``(...)`` group and nothing more.

    Checking only the first and last characters would accept
    ``(primary) and [[Beta]] (secondary)`` as a single context and silently
    drop the Beta link — the corruption class the prose-tail rule exists to
    prevent — so the opening paren must close exactly at the final character.
    """
    if not text.startswith("("):
        return False
    depth = 0
    for position, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return position == len(text) - 1
    return False


def parse_relation(token: Token) -> Dict[str, Any] | None:
    """Extract relation parts from token."""
    source_content, content, _ = _relation_parsing_content(token)
    return _parse_explicit_relation(content, source_content)


def _is_escaped(content: str, position: int) -> bool:
    """Whether the character at ``position`` is escaped by an odd slash run."""
    slash_count = 0
    position -= 1
    while position >= 0 and content[position] == "\\":
        slash_count += 1
        position -= 1
    return slash_count % 2 == 1


def parse_inline_relations(content: str, source_content: str | None = None) -> List[Dict[str, Any]]:
    """Find wiki-style links, extracting targets from the original source."""
    source_content = content if source_content is None else source_content
    relations = []
    start = 0

    while True:
        # Find next outer-most [[
        start = content.find("[[", start)
        if start == -1:  # pragma: no cover
            break
        if _is_escaped(content, start):
            start += 2
            continue

        # Find matching ]]
        depth = 1
        pos = start + 2
        end = -1

        while pos < len(content):
            if content[pos : pos + 2] == "[[" and not _is_escaped(content, pos):
                depth += 1
                pos += 2
            elif content[pos : pos + 2] == "]]" and not _is_escaped(content, pos):
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

        target = normalize_project_reference(source_content[start + 2 : end].strip())
        if target:
            relations.append({"type": "links_to", "target": target, "context": None})

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
        # Track blockquote nesting so Obsidian callouts (`> [!info] Title`)
        # don't get parsed as observations with category `!info`.
        blockquote_depth = 0

        for idx in range(len(tokens)):
            token = tokens[idx]

            # Initialize meta for all tokens
            token.meta = token.meta or {}

            if token.type == "blockquote_open":
                blockquote_depth += 1
                continue
            if token.type == "blockquote_close":
                blockquote_depth -= 1
                continue

            # Skip parsing inside blockquotes — that's Obsidian callout
            # territory, not Basic Memory observation syntax.
            if blockquote_depth > 0:
                continue

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
    - "multi word relation type" [[target]] (context)
    - 'multi word relation type' [[target]] (context)

    Implicit relations (links in content):
    Some text with [[target]] reference
    """

    def relation_rule(state: Any) -> None:
        """Process relations in token stream."""
        tokens = state.tokens
        in_list_item = False

        for idx in range(len(tokens)):
            token = tokens[idx]

            # Track list nesting
            if token.type == "list_item_open":
                in_list_item = True
            elif token.type == "list_item_close":
                in_list_item = False

            # Initialize meta for all tokens
            token.meta = token.meta or {}

            # Only process inline tokens
            if token.type == "inline":
                source_content = _relation_content(token)
                if "[[" not in source_content:
                    continue

                code_spans = _inline_code_spans(source_content)
                relation_content = _mask_wikilinks_in_inline_code(source_content, code_spans)
                content_without_directive, has_directive = (
                    _remove_links_to_directive_outside_inline_code(relation_content, code_spans)
                )

                # Check for explicit relations in list items
                if in_list_item and not has_directive:
                    rel = _parse_explicit_relation(relation_content, source_content)
                    if rel:
                        token.meta["relations"] = [rel]
                        continue

                # Always check for inline links in any text
                rels = parse_inline_relations(content_without_directive, source_content)
                if rels:
                    token.meta["relations"] = token.meta.get("relations", []) + rels

    # Add the rule after inline processing
    md.core.ruler.after("inline", "relations", relation_rule)
