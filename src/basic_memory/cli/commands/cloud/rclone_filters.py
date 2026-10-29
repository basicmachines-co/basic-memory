"""Convert .bmignore patterns into rclone filter files.

Personal `push`/`pull`/`check` exclude ignored paths with the regular filter.
`prune` uses the inverted filter, which selects only the ignored paths (#1032).
"""

from pathlib import Path

from basic_memory.ignore_utils import create_default_bmignore, get_bmignore_path


def _rclone_exclude_filters(pattern: str) -> list[str]:
    """Return rclone exclude filters for a gitignore-style pattern."""
    if pattern.endswith("/"):
        # Trigger: gitignore-style patterns ending in / are directory-only rules.
        # Why: stripping the slash would also exclude a same-named file.
        # Outcome: rclone keeps the directory rule and excludes recursive contents.
        return [f"- {pattern}", f"- {pattern}**"]

    path_pattern = pattern.removesuffix("/**")

    # Trigger: rclone treats a directory contents filter separately from the
    # directory/file path itself.
    # Why: files like config.json and directory markers like .obsidian must both
    # be excluded, along with anything below matching directories.
    # Outcome: every ignore pattern excludes the direct match and recursive children.
    return [f"- {path_pattern}", f"- {path_pattern}/**"]


def _rclone_include_filters(pattern: str) -> list[str]:
    """Return rclone include filters selecting exactly what a pattern ignores.

    Inverse of _rclone_exclude_filters, used by `bm cloud prune` to target
    already-uploaded ignored files for deletion (#1032). Directory-only
    patterns (trailing /) need just the contents rule: prune deletes files,
    and a bare `+ cache` would also select a same-named *file* that the
    directory-only exclude never hid.
    """
    if pattern.endswith("/"):
        return [f"+ {pattern}**"]

    path_pattern = pattern.removesuffix("/**")
    return [f"+ {path_pattern}", f"+ {path_pattern}/**"]


def convert_bmignore_to_rclone_filters() -> Path:
    """Convert .bmignore patterns to rclone filter format.

    Reads <config dir>/.bmignore (gitignore-style; the config dir honors
    BASIC_MEMORY_CONFIG_DIR) and converts it to <config dir>/.bmignore.rclone
    (rclone filter format).

    Only regenerates if .bmignore has been modified since last conversion.
    The filter only narrows additive transfers and comparisons, so a stale
    cache or the minimal fallback below can never cause a deletion.

    Returns:
        Path to converted rclone filter file
    """
    # Ensure .bmignore exists
    create_default_bmignore()

    bmignore_path = get_bmignore_path()
    # Create rclone filter path: <config dir>/.bmignore -> <config dir>/.bmignore.rclone
    rclone_filter_path = bmignore_path.parent / f"{bmignore_path.name}.rclone"

    # Skip regeneration if rclone file is newer than bmignore
    if rclone_filter_path.exists():
        bmignore_mtime = bmignore_path.stat().st_mtime
        rclone_mtime = rclone_filter_path.stat().st_mtime
        if rclone_mtime >= bmignore_mtime:
            return rclone_filter_path

    # Read .bmignore patterns
    patterns = []
    try:
        with bmignore_path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                # Keep comments and empty lines
                if not line or line.startswith("#"):
                    patterns.append(line)
                    continue

                patterns.extend(_rclone_exclude_filters(line))

    except OSError:
        # If we can't read the file, create a minimal filter
        patterns = ["# Error reading .bmignore, using minimal filters", "- .git", "- .git/**"]

    # Write rclone filter file
    rclone_filter_path.write_text("\n".join(patterns) + "\n")

    return rclone_filter_path


def convert_bmignore_to_rclone_prune_filters() -> Path:
    """Convert .bmignore patterns into an *inverted* rclone filter for prune.

    Where the transfer filter excludes ignored paths, the prune filter includes
    exactly those paths and excludes everything else, so `rclone lsf` /
    `rclone delete` operate on the ignored set alone (#1032).

    Always regenerated (no mtime caching like the exclude converter), and any
    read error propagates instead of falling back to minimal filters: this
    filter sits in front of `rclone delete`, where a stale or guessed pattern
    set is a data loss hazard, and prune runs rarely enough that caching buys
    nothing.

    Returns:
        Path to the generated rclone filter file
    """
    # Ensure .bmignore exists
    create_default_bmignore()

    bmignore_path = get_bmignore_path()
    prune_filter_path = bmignore_path.parent / f"{bmignore_path.name}.rclone-prune"

    patterns = []
    with bmignore_path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            # Keep comments and empty lines
            if not line or line.startswith("#"):
                patterns.append(line)
                continue

            patterns.extend(_rclone_include_filters(line))

    # The terminating exclude-all makes the include rules exhaustive: anything
    # not matched above is protected from deletion. `*` is unanchored in rclone
    # globs, so it matches the final path element of every file at any depth.
    patterns.append("- *")

    prune_filter_path.write_text("\n".join(patterns) + "\n")

    return prune_filter_path
