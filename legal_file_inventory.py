#!/usr/bin/env python3
"""
Legal File Inventory Generator for Basic Memory

This script generates a comprehensive file inventory for legal documentation purposes,
including copyright assignments and company agreement exhibits.

The inventory includes:
- All source code files and their contributors
- Documentation and configuration files
- License and legal files
- Excludes generated files, dependencies, and temporary files

Usage:
    python legal_file_inventory.py [--output inventory.csv] [--format csv|json|markdown]
"""

import os
import sys
import json
import csv
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Set, Optional, Tuple
import argparse
import hashlib

class FileInventoryGenerator:
    def __init__(self, repo_path: str = "."):
        self.repo_path = Path(repo_path).resolve()
        self.inventory = []
        
        # File patterns to exclude from legal inventory
        self.exclude_patterns = {
            # Version control and git
            '.git', '.gitignore', '.gitmodules',
            
            # Python compiled and cache files
            '__pycache__', '*.pyc', '*.pyo', '*.pyd', '.pytest_cache',
            
            # Virtual environments and dependencies
            '.venv', 'venv', '.env', 'env', 'ENV',
            '*.dist-info', 'site-packages',
            
            # IDE and editor files
            '.idea', '.vscode', '*.swp', '*.swo', '.DS_Store',
            
            # Build and distribution artifacts
            'build', 'dist', 'htmlcov', '.coverage', '.coverage.*',
            '*.egg-info', '.eggs', 'wheels',
            
            # Cache and temporary files
            '.ruff_cache', '.mypy_cache', '.tox',
            'node_modules', '.npm',
            
            # Documentation build artifacts (but keep source docs)
            '.obsidian',
            
            # Lock files (these are generated)
            'uv.lock', 'Pipfile.lock', 'poetry.lock', 'package-lock.json'
        }
        
        # File extensions that are definitely source/authored content
        self.source_extensions = {
            '.py', '.md', '.rst', '.txt', '.toml', '.yaml', '.yml',
            '.json', '.cfg', '.ini', '.conf', '.sh', '.sql',
            '.js', '.ts', '.jsx', '.tsx', '.css', '.scss', '.sass',
            '.html', '.htm', '.xml', '.svg', '.dockerfile', '.Dockerfile'
        }
        
        # License file patterns
        self.license_patterns = {
            'LICENSE', 'LICENCE', 'COPYING', 'COPYRIGHT',
            'license.txt', 'LICENSE.txt', 'LICENSE.md',
            'CITATION.cff', 'CLA.md'
        }

    def run_git_command(self, command: List[str]) -> str:
        """Run a git command and return the output."""
        try:
            result = subprocess.run(
                ['git'] + command,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return ""

    def get_file_contributors(self, file_path: str) -> Dict[str, int]:
        """Get contributors and their line contributions for a file."""
        try:
            blame_output = self.run_git_command(['blame', '--line-porcelain', file_path])
            contributors = {}
            
            current_commit = None
            for line in blame_output.split('\n'):
                if line.startswith('author '):
                    author = line[7:]  # Remove 'author ' prefix
                    contributors[author] = contributors.get(author, 0) + 1
                    
            return contributors
        except:
            return {}

    def get_file_history(self, file_path: str) -> Dict[str, any]:
        """Get file creation date, last modification, and total commits."""
        try:
            # Get creation date (first commit)
            first_commit = self.run_git_command([
                'log', '--follow', '--format=%ad', '--date=iso',
                '--reverse', file_path
            ]).split('\n')[0] if self.run_git_command([
                'log', '--follow', '--format=%ad', '--date=iso', 
                '--reverse', file_path
            ]) else None
            
            # Get last modification date
            last_commit = self.run_git_command([
                'log', '-1', '--format=%ad', '--date=iso', file_path
            ])
            
            # Get total commits for this file
            commit_count = len(self.run_git_command([
                'log', '--follow', '--oneline', file_path
            ]).split('\n')) if self.run_git_command([
                'log', '--follow', '--oneline', file_path
            ]) else 0
            
            return {
                'created': first_commit or 'Unknown',
                'last_modified': last_commit or 'Unknown',
                'commit_count': commit_count
            }
        except:
            return {
                'created': 'Unknown',
                'last_modified': 'Unknown', 
                'commit_count': 0
            }

    def should_exclude_file(self, file_path: Path) -> bool:
        """Determine if a file should be excluded from the inventory."""
        str_path = str(file_path)
        
        # Check if any part of the path matches exclude patterns
        for pattern in self.exclude_patterns:
            if pattern in str_path or file_path.match(pattern):
                return True
        
        # Exclude files in virtual environment paths
        if '/.venv/' in str_path or '/venv/' in str_path:
            return True
            
        # Exclude binary files that are likely dependencies
        if file_path.suffix in {'.so', '.dylib', '.dll', '.pyd'}:
            return True
            
        return False

    def calculate_file_hash(self, file_path: Path) -> str:
        """Calculate SHA-256 hash of file content."""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except:
            return ""

    def categorize_file(self, file_path: Path) -> str:
        """Categorize the file based on its path and extension."""
        str_path = str(file_path).lower()
        
        # License and legal files
        if any(pattern.lower() in file_path.name.lower() for pattern in self.license_patterns):
            return "Legal/License"
        
        # Documentation
        if file_path.suffix.lower() in {'.md', '.rst', '.txt'} and any(
            doc_dir in str_path for doc_dir in ['doc', 'readme', 'changelog', 'contributing']
        ):
            return "Documentation"
        
        # Configuration files
        if file_path.suffix.lower() in {'.toml', '.yaml', '.yml', '.json', '.cfg', '.ini', '.conf'}:
            return "Configuration"
        
        # Source code
        if file_path.suffix.lower() in {'.py', '.js', '.ts', '.jsx', '.tsx'}:
            return "Source Code"
        
        # Tests
        if 'test' in str_path and file_path.suffix.lower() == '.py':
            return "Test Code"
        
        # Build and deployment
        if file_path.name.lower() in {'dockerfile', 'justfile', 'makefile'} or file_path.suffix.lower() in {'.sh'}:
            return "Build/Deployment"
        
        # Database and migrations
        if 'migration' in str_path or 'alembic' in str_path or file_path.suffix.lower() == '.sql':
            return "Database/Migration"
        
        # Templates and resources
        if file_path.suffix.lower() in {'.hbs', '.j2', '.jinja', '.template'}:
            return "Templates/Resources"
        
        return "Other"

    def scan_repository(self):
        """Scan the repository and build the file inventory."""
        print(f"Scanning repository: {self.repo_path}")
        
        for root, dirs, files in os.walk(self.repo_path):
            # Skip excluded directories
            dirs[:] = [d for d in dirs if not any(pattern in d for pattern in self.exclude_patterns)]
            
            for file in files:
                file_path = Path(root) / file
                relative_path = file_path.relative_to(self.repo_path)
                
                # Skip excluded files
                if self.should_exclude_file(relative_path):
                    continue
                
                # Get file stats
                try:
                    stat_info = file_path.stat()
                    file_size = stat_info.st_size
                    modified_time = datetime.fromtimestamp(stat_info.st_mtime)
                except:
                    file_size = 0
                    modified_time = datetime.now()
                
                # Get git information
                contributors = self.get_file_contributors(str(relative_path))
                history = self.get_file_history(str(relative_path))
                
                # Calculate file hash for integrity verification
                file_hash = self.calculate_file_hash(file_path)
                
                # Build inventory entry
                entry = {
                    'file_path': str(relative_path),
                    'full_path': str(file_path),
                    'file_name': file_path.name,
                    'file_extension': file_path.suffix,
                    'file_size_bytes': file_size,
                    'category': self.categorize_file(relative_path),
                    'fs_modified_date': modified_time.isoformat(),
                    'git_created_date': history['created'],
                    'git_last_modified': history['last_modified'],
                    'git_commit_count': history['commit_count'],
                    'contributors': contributors,
                    'primary_author': max(contributors.items(), key=lambda x: x[1])[0] if contributors else 'Unknown',
                    'contributor_count': len(contributors),
                    'total_author_lines': sum(contributors.values()) if contributors else 0,
                    'sha256_hash': file_hash,
                    'scan_timestamp': datetime.now().isoformat()
                }
                
                self.inventory.append(entry)
        
        print(f"Scanned {len(self.inventory)} files")

    def get_summary_statistics(self) -> Dict:
        """Generate summary statistics for the inventory."""
        if not self.inventory:
            return {}
        
        # Collect all contributors
        all_contributors = set()
        for entry in self.inventory:
            all_contributors.update(entry['contributors'].keys())
        
        # Category breakdown
        categories = {}
        for entry in self.inventory:
            cat = entry['category']
            categories[cat] = categories.get(cat, 0) + 1
        
        # File extension breakdown
        extensions = {}
        for entry in self.inventory:
            ext = entry['file_extension'] or 'no_extension'
            extensions[ext] = extensions.get(ext, 0) + 1
        
        # Contributor statistics
        contributor_files = {}
        contributor_lines = {}
        for entry in self.inventory:
            for contributor, lines in entry['contributors'].items():
                contributor_files[contributor] = contributor_files.get(contributor, 0) + 1
                contributor_lines[contributor] = contributor_lines.get(contributor, 0) + lines
        
        return {
            'total_files': len(self.inventory),
            'total_contributors': len(all_contributors),
            'categories': categories,
            'file_extensions': extensions,
            'contributor_file_counts': contributor_files,
            'contributor_line_counts': contributor_lines,
            'scan_date': datetime.now().isoformat(),
            'repository_path': str(self.repo_path)
        }

    def export_csv(self, output_file: str):
        """Export inventory to CSV format."""
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            if not self.inventory:
                return
            
            fieldnames = list(self.inventory[0].keys())
            # Convert complex fields to strings for CSV
            fieldnames = [f for f in fieldnames if f != 'contributors']
            fieldnames.append('contributors_json')
            
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for entry in self.inventory:
                row = {k: v for k, v in entry.items() if k != 'contributors'}
                row['contributors_json'] = json.dumps(entry['contributors'])
                writer.writerow(row)

    def export_json(self, output_file: str):
        """Export inventory to JSON format."""
        export_data = {
            'metadata': self.get_summary_statistics(),
            'files': self.inventory
        }
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)

    def export_markdown(self, output_file: str):
        """Export inventory to Markdown format."""
        stats = self.get_summary_statistics()
        
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("# Basic Memory - Legal File Inventory\n\n")
            f.write(f"**Generated:** {datetime.now().isoformat()}\n\n")
            f.write(f"**Repository:** {stats.get('repository_path', 'Unknown')}\n\n")
            
            # Summary statistics
            f.write("## Summary Statistics\n\n")
            f.write(f"- **Total Files:** {stats.get('total_files', 0)}\n")
            f.write(f"- **Total Contributors:** {stats.get('total_contributors', 0)}\n\n")
            
            # Categories
            if 'categories' in stats:
                f.write("### Files by Category\n\n")
                for category, count in sorted(stats['categories'].items()):
                    f.write(f"- **{category}:** {count} files\n")
                f.write("\n")
            
            # Top contributors
            if 'contributor_file_counts' in stats:
                f.write("### Top Contributors by Files Modified\n\n")
                sorted_contributors = sorted(
                    stats['contributor_file_counts'].items(), 
                    key=lambda x: x[1], 
                    reverse=True
                )[:10]
                for contributor, count in sorted_contributors:
                    f.write(f"- **{contributor}:** {count} files\n")
                f.write("\n")
            
            # Detailed file listing
            f.write("## Detailed File Inventory\n\n")
            f.write("| File Path | Category | Size (bytes) | Primary Author | Contributors |\n")
            f.write("|-----------|----------|--------------|----------------|-------------|\n")
            
            for entry in sorted(self.inventory, key=lambda x: x['file_path']):
                contributors_str = ', '.join(entry['contributors'].keys())[:50]
                if len(contributors_str) == 50:
                    contributors_str += "..."
                
                f.write(f"| {entry['file_path']} | {entry['category']} | "
                       f"{entry['file_size_bytes']} | {entry['primary_author']} | "
                       f"{contributors_str} |\n")

def main():
    parser = argparse.ArgumentParser(
        description="Generate legal file inventory for Basic Memory repository"
    )
    parser.add_argument(
        '--output', '-o',
        default='basic_memory_legal_inventory.csv',
        help='Output file path (default: basic_memory_legal_inventory.csv)'
    )
    parser.add_argument(
        '--format', '-f',
        choices=['csv', 'json', 'markdown'],
        default='csv',
        help='Output format (default: csv)'
    )
    parser.add_argument(
        '--repo-path', '-r',
        default='.',
        help='Path to repository (default: current directory)'
    )
    
    args = parser.parse_args()
    
    # Initialize and run the inventory generator
    generator = FileInventoryGenerator(args.repo_path)
    generator.scan_repository()
    
    # Export in requested format
    if args.format == 'csv':
        generator.export_csv(args.output)
    elif args.format == 'json':
        generator.export_json(args.output)
    elif args.format == 'markdown':
        generator.export_markdown(args.output)
    
    # Print summary
    stats = generator.get_summary_statistics()
    print(f"\n=== Legal File Inventory Complete ===")
    print(f"Repository: {stats.get('repository_path', 'Unknown')}")
    print(f"Total files inventoried: {stats.get('total_files', 0)}")
    print(f"Total contributors identified: {stats.get('total_contributors', 0)}")
    print(f"Output written to: {args.output}")
    
    # Show top contributors
    if 'contributor_file_counts' in stats:
        print(f"\nTop 5 contributors by files modified:")
        sorted_contributors = sorted(
            stats['contributor_file_counts'].items(),
            key=lambda x: x[1],
            reverse=True
        )[:5]
        for i, (contributor, count) in enumerate(sorted_contributors, 1):
            print(f"  {i}. {contributor}: {count} files")

if __name__ == '__main__':
    main()