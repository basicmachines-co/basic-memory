#!/usr/bin/env python3
"""
Generate markdown documentation for Basic Memory MCP tools.

This script extracts tool documentation and usage examples from MCP tool files
and generates a comprehensive markdown document that users can easily reference
when creating instructions for their LLMs.
"""

import ast
import inspect
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def extract_function_signature(node: ast.FunctionDef) -> str:
    """Extract a clean function signature from an AST node."""
    args = []
    for arg in node.args.args:
        arg_name = arg.arg
        # Skip self, cls, context parameters
        if arg_name in ["self", "cls", "context"]:
            continue
        # Add type annotation if available
        if arg.annotation:
            type_str = ast.unparse(arg.annotation)
            args.append(f"{arg_name}: {type_str}")
        else:
            args.append(arg_name)
    return f"({', '.join(args)})"


def extract_tool_info(file_path: Path) -> Optional[Dict]:
    """Extract tool information from a Python file."""
    try:
        with open(file_path, "r") as f:
            content = f.read()

        tree = ast.parse(content)

        # Find functions decorated with @mcp.tool
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                # Check if function has @mcp.tool decorator
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call):
                        if isinstance(decorator.func, ast.Attribute):
                            if (
                                decorator.func.attr == "tool"
                                and isinstance(decorator.func.value, ast.Name)
                                and decorator.func.value.id == "mcp"
                            ):
                                # Extract description from decorator
                                description = ""
                                for keyword in decorator.keywords:
                                    if keyword.arg == "description":
                                        if isinstance(keyword.value, ast.Constant):
                                            description = keyword.value.value

                                # Extract docstring
                                docstring = ast.get_docstring(node) or ""

                                # Extract function name and signature
                                func_name = node.name
                                signature = extract_function_signature(node)

                                return {
                                    "name": func_name,
                                    "signature": signature,
                                    "description": description.strip(),
                                    "docstring": docstring.strip(),
                                    "file": file_path.name,
                                }
        return None
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        return None


def format_tool_section(tool_info: Dict) -> str:
    """Format a tool's documentation as a markdown section."""
    lines = []

    # Tool header
    lines.append(f"## {tool_info['name']}")
    lines.append("")

    # Short description from decorator
    if tool_info['description']:
        lines.append(f"**{tool_info['description']}**")
        lines.append("")

    # Function signature
    lines.append("### Function Signature")
    lines.append("```python")
    lines.append(f"{tool_info['name']}{tool_info['signature']}")
    lines.append("```")
    lines.append("")

    # Full docstring with examples
    if tool_info['docstring']:
        lines.append("### Documentation")
        lines.append("")

        # Process docstring to maintain formatting
        docstring = tool_info['docstring']

        # Split into sections
        sections = docstring.split('\n\n')
        in_code_block = False

        for section in sections:
            # Check if this is a code example section
            if 'Examples:' in section or 'Example:' in section:
                lines.append(section)
                lines.append("")
            elif section.strip().startswith('```'):
                lines.append(section)
                lines.append("")
            else:
                # Regular text section
                lines.append(section)
                lines.append("")

    lines.append("---")
    lines.append("")

    return '\n'.join(lines)


def generate_table_of_contents(tools: List[Dict]) -> str:
    """Generate a table of contents for all tools."""
    lines = ["## Table of Contents", ""]

    # Group tools by category
    content_mgmt = []
    knowledge_graph = []
    search = []
    project_mgmt = []
    visualization = []
    other = []

    for tool in tools:
        name = tool['name']
        link = f"[{name}](#{name.replace('_', '-')})"

        # Categorize based on tool name
        if name in ['write_note', 'read_note', 'read_content', 'view_note', 'edit_note', 'move_note', 'delete_note']:
            content_mgmt.append(f"- {link}")
        elif name in ['build_context', 'recent_activity', 'list_directory']:
            knowledge_graph.append(f"- {link}")
        elif name in ['search_notes']:
            search.append(f"- {link}")
        elif name in ['list_memory_projects', 'create_memory_project', 'delete_project', 'get_current_project', 'sync_status']:
            project_mgmt.append(f"- {link}")
        elif name in ['canvas']:
            visualization.append(f"- {link}")
        else:
            other.append(f"- {link}")

    if content_mgmt:
        lines.append("### Content Management")
        lines.extend(content_mgmt)
        lines.append("")

    if knowledge_graph:
        lines.append("### Knowledge Graph Navigation")
        lines.extend(knowledge_graph)
        lines.append("")

    if search:
        lines.append("### Search & Discovery")
        lines.extend(search)
        lines.append("")

    if project_mgmt:
        lines.append("### Project Management")
        lines.extend(project_mgmt)
        lines.append("")

    if visualization:
        lines.append("### Visualization")
        lines.extend(visualization)
        lines.append("")

    if other:
        lines.append("### Other Tools")
        lines.extend(other)
        lines.append("")

    lines.append("---")
    lines.append("")

    return '\n'.join(lines)


def main():
    """Main function to generate tool documentation."""
    # Get the project root
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    tools_dir = project_root / "src" / "basic_memory" / "mcp" / "tools"
    output_file = project_root / "docs" / "mcp-tool-usage.md"

    print(f"Scanning tools directory: {tools_dir}")

    # Extract tool information from all tool files
    tools = []
    for tool_file in sorted(tools_dir.glob("*.py")):
        # Skip __init__.py and utils.py
        if tool_file.name in ["__init__.py", "utils.py", "chatgpt_tools.py"]:
            continue

        print(f"Processing: {tool_file.name}")
        tool_info = extract_tool_info(tool_file)
        if tool_info:
            tools.append(tool_info)
            print(f"  Found tool: {tool_info['name']}")

    # Sort tools alphabetically by name
    tools.sort(key=lambda x: x['name'])

    print(f"\nGenerating documentation for {len(tools)} tools...")

    # Generate the markdown document
    doc_lines = [
        "# Basic Memory MCP Tool Usage Guide",
        "",
        "This document provides comprehensive documentation and usage examples for all Basic Memory MCP tools.",
        "Use this as a reference when creating instructions for your LLM or integrating Basic Memory into your workflows.",
        "",
        f"**Total Tools:** {len(tools)}",
        "",
        "---",
        "",
    ]

    # Add table of contents
    doc_lines.append(generate_table_of_contents(tools))

    # Add individual tool sections
    for tool in tools:
        doc_lines.append(format_tool_section(tool))

    # Add footer
    doc_lines.extend([
        "---",
        "",
        "## Additional Resources",
        "",
        "- [Basic Memory README](../README.md)",
        "- [CLAUDE.md Project Guide](../CLAUDE.md)",
        "- [MCP Server Implementation](../src/basic_memory/mcp/)",
        "",
        "---",
        "",
        "*This documentation was automatically generated from the MCP tool source code.*",
        "*Last updated: (run `just generate-tool-docs` to regenerate)*",
        "",
    ])

    # Write the output file
    output_content = '\n'.join(doc_lines)
    output_file.write_text(output_content)

    print(f"\n✓ Documentation generated: {output_file}")
    print(f"  Total size: {len(output_content)} characters")
    print(f"  Total lines: {len(doc_lines)}")


if __name__ == "__main__":
    main()
