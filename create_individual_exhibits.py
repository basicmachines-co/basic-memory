#!/usr/bin/env python3
"""
Create individual Exhibit A files for copyright assignments
"""

import json
from pathlib import Path
from datetime import datetime


def create_individual_exhibits():
    """Create individual Exhibit A files for each contributor."""

    # Read the JSON inventory
    inventory_file = Path("legal_inventory_main/basic_memory_inventory_20250730_101521.json")

    if not inventory_file.exists():
        print(f"Error: {inventory_file} not found")
        return

    with open(inventory_file, "r") as f:
        data = json.load(f)

    files = data["files"]

    # Create output directory
    output_dir = Path("legal_exhibits")
    output_dir.mkdir(exist_ok=True)

    # Contributors we need exhibits for (based on copyright assignments)
    target_contributors = {
        "jope-bm": 'Joseph "Joe" [Last Name]',  # Need to get his full name
        "Drew Cain": "Drew Cain",
    }

    print("Creating individual contributor exhibits...")

    for contributor_key, full_name in target_contributors.items():
        # Find files for this contributor
        contributor_files = []

        for file_info in files:
            # Check if this contributor is listed in the file's contributors
            for contrib in file_info.get("contributors", []):
                if contributor_key in contrib["name"]:
                    contributor_files.append(file_info)
                    break

        if not contributor_files:
            print(f"No files found for {contributor_key}")
            continue

        # Sort files by path
        contributor_files.sort(key=lambda x: x["path"])

        # Create exhibit markdown
        exhibit_content = f"""# Exhibit A - Assigned Works
## Copyright Assignment: {full_name} to Basic Memory LLC

**Date:** [To be filled]
**Assignor:** {full_name}
**Assignee:** Basic Memory LLC

## Summary
- **Total Files:** {len(contributor_files)}
- **Total Size:** {sum(f["size_bytes"] for f in contributor_files):,} bytes
- **Categories:** {", ".join(set(f["category"] for f in contributor_files))}

## Detailed File List

"""

        # Group by category
        categories = {}
        for file_info in contributor_files:
            category = file_info["category"]
            if category not in categories:
                categories[category] = []
            categories[category].append(file_info)

        # Add files by category
        for category, category_files in sorted(categories.items()):
            exhibit_content += f"### {category.replace('_', ' ').title()}\n\n"

            for file_info in category_files:
                exhibit_content += f"**{file_info['path']}**\n"
                exhibit_content += f"- Size: {file_info['size_bytes']:,} bytes\n"
                exhibit_content += f"- Modified: {file_info['modified_time'][:10]}\n"
                exhibit_content += f"- Primary Author: {file_info['primary_author']}\n"

                # Show all contributors for this file
                if len(file_info["contributors"]) > 1:
                    contributors_list = ", ".join([c["name"] for c in file_info["contributors"]])
                    exhibit_content += f"- All Contributors: {contributors_list}\n"

                exhibit_content += f"- SHA-256: `{file_info['sha256_hash']}`\n\n"

        # Add verification section
        exhibit_content += f"""
## Verification
This exhibit lists all files in the Basic Memory repository where {full_name} is identified as a contributor based on git commit history analysis.

**Analysis Date:** {datetime.now().strftime("%Y-%m-%d")}
**Repository State:** Basic Memory main branch
**Method:** Git history analysis via `git log --follow` for each file

## Legal Representation
{full_name} hereby represents and warrants that they are the author of the contributions listed above and have the right to assign copyright in these works to Basic Memory LLC.

---

*This exhibit is attached to and forms part of the Copyright Assignment Agreement between {full_name} and Basic Memory LLC.*
"""

        # Write exhibit file
        safe_name = contributor_key.replace(" ", "_").replace("-", "_").lower()
        exhibit_file = output_dir / f"exhibit_a_{safe_name}.md"

        with open(exhibit_file, "w") as f:
            f.write(exhibit_content)

        print(f"Created exhibit for {full_name}: {exhibit_file}")
        print(f"  - {len(contributor_files)} files")
        print(f"  - {sum(f['size_bytes'] for f in contributor_files):,} bytes")

    # Create overall summary exhibit (for Paul's assignment to Basic Machines LLC)
    create_overall_summary_exhibit(data, output_dir)


def create_overall_summary_exhibit(data, output_dir):
    """Create overall summary exhibit for Company Agreement."""

    files = data["files"]
    summary = data["summary"]
    contributors = data["contributors"]

    summary_content = f"""# Basic Memory Repository - Complete IP Inventory
## For Basic Memory LLC Company Agreement

**Analysis Date:** {summary["scan_date"][:10]}
**Repository:** {summary["repository_path"]}

## Executive Summary
- **Total Files:** {summary["total_files"]:,}
- **Total Size:** {summary["total_size_bytes"]:,} bytes
- **Contributors:** {summary["contributor_count"]}
- **Primary Author:** Paul Hernandez ({len(contributors.get("Paul Hernandez", {}).get("files", []))} files)

## File Categories
"""

    for category, count in sorted(summary["categories"].items()):
        summary_content += f"- **{category.replace('_', ' ').title()}:** {count} files\n"

    summary_content += """

## Contributor Summary
"""

    for contrib in summary["top_contributors"]:
        summary_content += f"- **{contrib['name']}** ({contrib['email']}): {contrib['file_count']} files, {contrib['commit_count']} commits\n"

    summary_content += """

## Legal Significance
This inventory represents the complete codebase of Basic Memory as licensed from Basic Machines LLC to Basic Memory LLC under the copyright license agreement dated [DATE].

### IP Rights Chain
1. **Paul Hernandez** → Basic Machines LLC (copyright assignment)
2. **Basic Machines LLC** → Basic Memory LLC (15% royalty license)
3. **Co-founders** → Basic Memory LLC (direct copyright assignments)

### Due Diligence Documentation
This comprehensive file inventory serves as:
- **Company Agreement Exhibit:** Original codebase licensed to Basic Memory LLC
- **Acquisition Documentation:** Complete IP inventory for due diligence
- **Copyright Verification:** Establishes chain of title for all repository contents

## Repository Contents by Category

"""

    # Add sample files by category (first 10 in each category)
    for category in sorted(summary["categories"].keys()):
        category_files = [f for f in files if f["category"] == category][:10]
        if category_files:
            summary_content += f"### {category.replace('_', ' ').title()} (Sample)\n\n"
            for file_info in category_files:
                summary_content += f"- `{file_info['path']}` ({file_info['size_bytes']:,} bytes)\n"

            if len([f for f in files if f["category"] == category]) > 10:
                remaining = len([f for f in files if f["category"] == category]) - 10
                summary_content += f"- *... and {remaining} more files*\n"
            summary_content += "\n"

    summary_content += f"""
---

*This inventory was generated automatically from git repository analysis and represents the complete intellectual property foundation of Basic Memory as of {summary["scan_date"][:10]}.*
"""

    # Write summary file
    summary_file = output_dir / "basic_memory_complete_inventory.md"
    with open(summary_file, "w") as f:
        f.write(summary_content)

    print(f"Created complete inventory summary: {summary_file}")


if __name__ == "__main__":
    create_individual_exhibits()
