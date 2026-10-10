"""Print user and external text literally inside Rich markup.

CLI output interpolates project names, paths, exception messages, and server
responses into markup strings such as ``f"[red]Error: {e}[/red]"``. Rich reads
any ``[word]`` in that text as a tag: ``proj [x]`` prints as ``proj ``, a value
like ``a[/b]c`` raises ``MarkupError`` and crashes the command, and ``[bold]``
restyles the line. Wrap such values with ``literal`` at the interpolation.
"""

from rich.markup import escape


def literal(value: object) -> str:
    """Return ``value`` as text that Rich prints verbatim inside a markup string.

    Accepts any object because the values printed this way are often exceptions,
    paths, or untyped JSON fields, and ``rich.markup.escape`` only accepts ``str``.
    """
    return escape(str(value))
