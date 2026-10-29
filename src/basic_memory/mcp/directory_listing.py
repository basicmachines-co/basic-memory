"""The text rendering of a directory listing page.

The formatter renders any listing page with the next-page call when more pages
exist.
"""

from datetime import datetime

from basic_memory.schemas.directory import DirectoryListResponse


def format_listing_text(
    listing: DirectoryListResponse, *, heading: str, next_call: str | None
) -> str:
    """Render one page of a listing: directories first, then files with their metadata.

    `heading` names what was listed; `next_call` is the exact call that fetches the
    next page (Pagination.next_call), None on the last page.
    """
    nodes = [node.model_dump(mode="json", exclude_none=True) for node in listing.nodes]
    output_lines = [
        heading,
        f"Page {listing.page} (page size {listing.page_size}, {listing.total} total items)",
        "",
    ]

    # The API has already applied the requested stable order. Partitioning
    # preserves that order while keeping directories ahead of files.
    directories = [n for n in nodes if n["type"] == "directory"]
    files = [n for n in nodes if n["type"] == "file"]

    if not nodes:
        output_lines.append("No items on this page.")

    for node in directories:
        output_lines.append(f"📁 {node['name']:<30} {node['directory_path']}")

    if directories and files:
        output_lines.append("")

    for node in files:
        # Reads take the path without the leading slash.
        path_display = node["directory_path"].removeprefix("/")
        title = node.get("title", "")
        updated = node.get("updated_at", "")

        date_str = ""
        if updated:
            try:
                date_str = datetime.fromisoformat(updated.replace("Z", "+00:00")).strftime(
                    "%Y-%m-%d"
                )
            except ValueError:  # pragma: no cover
                date_str = updated[:10] if len(updated) >= 10 else ""

        file_line = f"📄 {node['name']:<30} {path_display}"
        if title and title != node["name"]:
            file_line += f" | {title}"
        if date_str:
            file_line += f" | {date_str}"
        # Web-app deep links are built from the note's external_id; hosted MCP
        # appends a link template that references it, so the id must be visible
        # in the listing the agent actually reads.
        external_id = node.get("external_id")
        if external_id:
            file_line += f" | id: {external_id}"
        output_lines.append(file_line)

    output_lines.append("")
    summary_parts = []
    if directories:
        summary_parts.append(
            f"{len(directories)} director{'y' if len(directories) == 1 else 'ies'}"
        )
    if files:
        summary_parts.append(f"{len(files)} file{'s' if len(files) != 1 else ''}")
    if summary_parts:
        output_lines.append(
            f"Total: {len(directories) + len(files)} items ({', '.join(summary_parts)})"
        )
    else:
        output_lines.append("Total: 0 items")

    if not nodes:
        last_page = (listing.total + listing.page_size - 1) // listing.page_size
        output_lines.append(
            f"Requested page {listing.page} is beyond the available results; "
            f"the last available page is {last_page}."
        )

    if next_call is not None:
        output_lines.extend(["", f"More results available. Call {next_call} to continue."])

    return "\n".join(output_lines)
