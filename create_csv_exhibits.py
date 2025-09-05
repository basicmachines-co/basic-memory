#!/usr/bin/env python3
"""
Create CSV exhibits for individual contributors
"""

import json
import csv
from pathlib import Path


def create_csv_exhibits():
    """Create CSV Exhibit A files for each contributor."""

    # Read the JSON inventory
    inventory_files = list(Path("legal_inventory_main").glob("*.json"))
    if not inventory_files:
        print("Error: No JSON inventory files found")
        return

    inventory_file = inventory_files[0]  # Use the most recent one
    print(f"Using inventory file: {inventory_file}")

    with open(inventory_file, "r") as f:
        data = json.load(f)

    files = data["files"]

    # Create output directory
    output_dir = Path("legal_exhibits")
    output_dir.mkdir(exist_ok=True)

    # Contributors we need exhibits for
    target_contributors = {"jope-bm": "joe_exhibit_a.csv", "Drew Cain": "drew_cain_exhibit_a.csv"}

    print("Creating CSV exhibits for contributors...")

    for contributor_key, filename in target_contributors.items():
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

        # Create CSV file
        csv_file = output_dir / filename

        with open(csv_file, "w", newline="", encoding="utf-8") as csvfile:
            fieldnames = [
                "file_path",
                "file_name",
                "category",
                "size_bytes",
                "modified_date",
                "primary_author",
                "all_contributors",
                "sha256_hash",
            ]

            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            for file_info in contributor_files:
                contributors_list = "; ".join([c["name"] for c in file_info["contributors"]])

                writer.writerow(
                    {
                        "file_path": file_info["path"],
                        "file_name": file_info["name"],
                        "category": file_info["category"],
                        "size_bytes": file_info["size_bytes"],
                        "modified_date": file_info["modified_time"][:10],
                        "primary_author": file_info["primary_author"],
                        "all_contributors": contributors_list,
                        "sha256_hash": file_info["sha256_hash"],
                    }
                )

        print(f"Created CSV exhibit for {contributor_key}: {csv_file}")
        print(f"  - {len(contributor_files)} files")
        print(f"  - {sum(f['size_bytes'] for f in contributor_files):,} bytes")


if __name__ == "__main__":
    create_csv_exhibits()
