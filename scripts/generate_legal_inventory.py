#!/usr/bin/env python3
"""
Legal File Inventory Generator for Basic Memory

Generates comprehensive file inventory with contributor information
for legal documentation, copyright assignments, and due diligence.

Usage:
    python scripts/generate_legal_inventory.py [options]

Output formats:
    - CSV: Detailed spreadsheet for analysis
    - JSON: Structured data for integration
    - Markdown: Human-readable legal report
"""

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


class LegalInventoryGenerator:
    """Generate comprehensive file inventory for legal documentation."""
    
    # Files to exclude from legal inventory
    EXCLUDED_PATTERNS = {
        # Generated/compiled files
        '*.pyc', '*.pyo', '*.pyd', '__pycache__',
        '*.so', '*.dylib', '*.dll',
        
        # Build/cache directories
        'build/', 'dist/', '.eggs/', '*.egg-info/',
        '.coverage', '.pytest_cache/', '.mypy_cache/',
        '.ruff_cache/', '.tox/', 'venv/', '.venv/', 'env/', '.env/',
        'node_modules/', '.npm/', '.yarn/',
        
        # IDE and editor files
        '.vscode/', '.idea/', '*.swp', '*.swo', '*~',
        '.DS_Store', 'Thumbs.db',
        
        # Version control
        '.git/', '.gitignore',
        
        # OS generated
        'desktop.ini', '*.tmp', '*.temp'
    }
    
    # File categories for legal classification
    FILE_CATEGORIES = {
        'source_code': ['.py', '.pyx', '.pyi'],
        'documentation': ['.md', '.rst', '.txt'],
        'configuration': ['.toml', '.yaml', '.yml', '.json', '.ini', '.cfg'],
        'legal': ['LICENSE', 'COPYING', 'COPYRIGHT', '.md'],
        'build_deployment': ['Dockerfile', 'Makefile', 'justfile', '.sh'],
        'database': ['.sql', '.sqlite', '.db'],
        'templates': ['.j2', '.jinja2', '.hbs', '.handlebars'],
        'data': ['.csv', '.json', '.xml'],
        'other': []  # Catch-all for uncategorized files
    }
    
    def __init__(self, repo_path: str = "."):
        """Initialize generator with repository path."""
        self.repo_path = Path(repo_path).resolve()
        self.file_inventory: List[Dict] = []
        self.contributors: Dict[str, Dict] = defaultdict(lambda: {
            'email': '', 'commits': 0, 'lines_added': 0, 'files': set()
        })
        
    def should_exclude_file(self, file_path: Path) -> bool:
        """Check if file should be excluded from inventory."""
        # Check if file is tracked by git (more efficient than check-ignore)
        try:
            rel_path = str(file_path.relative_to(self.repo_path))
            result = subprocess.run([
                'git', 'ls-files', '--error-unmatch', rel_path
            ], capture_output=True, cwd=self.repo_path)
            
            # If git ls-files returns non-zero, file is not tracked (likely ignored)
            if result.returncode != 0:
                return True
                
        except Exception:
            # Fallback to manual exclusion patterns if git fails
            pass
        
        # Additional manual exclusions for safety
        file_str = str(file_path.relative_to(self.repo_path))
        
        for pattern in self.EXCLUDED_PATTERNS:
            if pattern.endswith('/'):
                if any(part == pattern[:-1] for part in file_path.parts):
                    return True
            elif '*' in pattern:
                import fnmatch
                if fnmatch.fnmatch(file_str, pattern):
                    return True
            else:
                if file_path.name == pattern or file_str == pattern:
                    return True
        return False
    
    def categorize_file(self, file_path: Path) -> str:
        """Categorize file based on extension and name."""
        suffix = file_path.suffix.lower()
        name = file_path.name.upper()
        
        # Check legal files by name first
        if any(legal in name for legal in ['LICENSE', 'COPYING', 'COPYRIGHT', 'CLA']):
            return 'legal'
            
        # Check by extension
        for category, extensions in self.FILE_CATEGORIES.items():
            if suffix in extensions:
                return category
                
        return 'other'
    
    def get_file_hash(self, file_path: Path) -> str:
        """Generate SHA-256 hash of file content."""
        try:
            with open(file_path, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except (IOError, OSError):
            return "ERROR_READING_FILE"
    
    def get_git_contributors(self, file_path: Path) -> List[Dict]:
        """Get contributor information for a specific file."""
        try:
            rel_path = file_path.relative_to(self.repo_path)
            
            # Get contributors with line counts
            result = subprocess.run([
                'git', 'log', '--follow', '--pretty=format:%an|%ae|%ad|%H',
                '--date=short', '--', str(rel_path)
            ], capture_output=True, text=True, cwd=self.repo_path)
            
            if result.returncode != 0:
                return []
                
            contributors = []
            seen = set()
            
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue
                    
                parts = line.split('|')
                if len(parts) >= 4:
                    name, email, date, commit_hash = parts[:4]
                    
                    # Normalize author names/emails
                    normalized_name = self.normalize_author_name(name, email)
                    
                    if normalized_name not in seen:
                        contributors.append({
                            'name': normalized_name,
                            'email': email,
                            'first_contribution': date,
                            'commit_hash': commit_hash
                        })
                        seen.add(normalized_name)
                        
            return contributors
            
        except Exception as e:
            print(f"Warning: Could not get git info for {file_path}: {e}")
            return []
    
    def normalize_author_name(self, name: str, email: str) -> str:
        """Normalize author names to handle multiple emails for same person."""
        # Known mappings for Basic Memory team
        name_mappings = {
            'phernandez': 'Paul Hernandez',
            'Paul Hernandez': 'Paul Hernandez',
            'drew-cain': 'Drew Cain',
            'Drew Cain': 'Drew Cain'
        }
        
        # Handle GitHub bot accounts
        if 'bot' in name.lower() or 'claude' in name.lower():
            return f"{name} (AI Assistant)"
            
        return name_mappings.get(name, name)
    
    def get_file_stats(self, file_path: Path) -> Dict:
        """Get comprehensive file statistics."""
        try:
            stat = file_path.stat()
            rel_path = file_path.relative_to(self.repo_path)
            
            # Basic file info
            file_info = {
                'path': str(rel_path),
                'name': file_path.name,
                'size_bytes': stat.st_size,
                'modified_time': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                'category': self.categorize_file(file_path),
                'sha256_hash': self.get_file_hash(file_path)
            }
            
            # Git information
            contributors = self.get_git_contributors(file_path)
            file_info['contributors'] = contributors
            file_info['primary_author'] = contributors[0]['name'] if contributors else 'Unknown'
            file_info['contributor_count'] = len(contributors)
            
            # Update global contributor stats
            for contrib in contributors:
                name = contrib['name']
                self.contributors[name]['email'] = contrib['email']
                self.contributors[name]['files'].add(str(rel_path))
                
            return file_info
            
        except Exception as e:
            print(f"Error processing {file_path}: {e}")
            return None
    
    def scan_repository(self) -> None:
        """Scan repository and build file inventory."""
        print(f"Scanning repository: {self.repo_path}")
        
        # Get all git-tracked files first (much more efficient)
        try:
            result = subprocess.run([
                'git', 'ls-files'
            ], capture_output=True, text=True, cwd=self.repo_path)
            
            if result.returncode == 0:
                tracked_files = [self.repo_path / f for f in result.stdout.strip().split('\n') if f]
                print(f"Found {len(tracked_files)} git-tracked files")
                
                for file_path in tracked_files:
                    if file_path.is_file():
                        file_info = self.get_file_stats(file_path)
                        if file_info:
                            self.file_inventory.append(file_info)
            else:
                print("Warning: Could not get git tracked files, falling back to directory scan")
                self._fallback_scan()
                
        except Exception as e:
            print(f"Warning: Git command failed ({e}), falling back to directory scan")
            self._fallback_scan()
        
        # Get global git stats
        self._get_global_git_stats()
        
        print(f"Processed {len(self.file_inventory)} files")
        print(f"Found {len(self.contributors)} contributors")
    
    def _fallback_scan(self) -> None:
        """Fallback directory scan if git commands fail."""
        for file_path in self.repo_path.rglob('*'):
            if file_path.is_file() and not self.should_exclude_file(file_path):
                file_info = self.get_file_stats(file_path)
                if file_info:
                    self.file_inventory.append(file_info)
    
    def _get_global_git_stats(self) -> None:
        """Get global contributor statistics from git."""
        try:
            # Get commit counts per author
            result = subprocess.run([
                'git', 'shortlog', '-sn', '--all'
            ], capture_output=True, text=True, cwd=self.repo_path)
            
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if line.strip():
                        parts = line.strip().split('\t', 1)
                        if len(parts) == 2:
                            count, name = parts
                            normalized_name = self.normalize_author_name(name, '')
                            self.contributors[normalized_name]['commits'] = int(count)
                            
        except Exception as e:
            print(f"Warning: Could not get global git stats: {e}")
    
    def generate_summary(self) -> Dict:
        """Generate inventory summary statistics."""
        total_files = len(self.file_inventory)
        total_size = sum(f['size_bytes'] for f in self.file_inventory)
        
        # Category breakdown
        categories = defaultdict(int)
        for file_info in self.file_inventory:
            categories[file_info['category']] += 1
        
        # Top contributors
        top_contributors = sorted(
            self.contributors.items(),
            key=lambda x: len(x[1]['files']),
            reverse=True
        )[:10]
        
        return {
            'scan_date': datetime.now().isoformat(),
            'repository_path': str(self.repo_path),
            'total_files': total_files,
            'total_size_bytes': total_size,
            'categories': dict(categories),
            'contributor_count': len(self.contributors),
            'top_contributors': [
                {
                    'name': name,
                    'file_count': len(stats['files']),
                    'commit_count': stats['commits'],
                    'email': stats['email']
                }
                for name, stats in top_contributors
            ]
        }
    
    def export_csv(self, output_path: str) -> None:
        """Export inventory to CSV format."""
        with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = [
                'path', 'name', 'category', 'size_bytes', 'modified_time',
                'primary_author', 'contributor_count', 'contributors_list',
                'sha256_hash'
            ]
            
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            
            for file_info in sorted(self.file_inventory, key=lambda x: x['path']):
                contributors_list = '; '.join([
                    f"{c['name']} ({c['email']})" for c in file_info['contributors']
                ])
                
                writer.writerow({
                    'path': file_info['path'],
                    'name': file_info['name'],
                    'category': file_info['category'],
                    'size_bytes': file_info['size_bytes'],
                    'modified_time': file_info['modified_time'],
                    'primary_author': file_info['primary_author'],
                    'contributor_count': file_info['contributor_count'],
                    'contributors_list': contributors_list,
                    'sha256_hash': file_info['sha256_hash']
                })
        
        print(f"CSV export saved to: {output_path}")
    
    def export_json(self, output_path: str) -> None:
        """Export inventory to JSON format."""
        # Convert sets to lists for JSON serialization
        contributors_serializable = {}
        for name, stats in self.contributors.items():
            contributors_serializable[name] = {
                'email': stats['email'],
                'commits': stats['commits'],
                'lines_added': stats['lines_added'],
                'files': list(stats['files'])
            }
        
        data = {
            'summary': self.generate_summary(),
            'files': self.file_inventory,
            'contributors': contributors_serializable
        }
        
        with open(output_path, 'w', encoding='utf-8') as jsonfile:
            json.dump(data, jsonfile, indent=2, ensure_ascii=False)
        
        print(f"JSON export saved to: {output_path}")
    
    def export_markdown(self, output_path: str) -> None:
        """Export inventory to Markdown format for legal documentation."""
        summary = self.generate_summary()
        
        with open(output_path, 'w', encoding='utf-8') as mdfile:
            mdfile.write("# Basic Memory - Legal File Inventory\n\n")
            
            # Summary section
            mdfile.write("## Summary\n\n")
            mdfile.write(f"**Scan Date:** {summary['scan_date']}\n")
            mdfile.write(f"**Repository:** {summary['repository_path']}\n")
            mdfile.write(f"**Total Files:** {summary['total_files']:,}\n")
            mdfile.write(f"**Total Size:** {summary['total_size_bytes']:,} bytes\n")
            mdfile.write(f"**Contributors:** {summary['contributor_count']}\n\n")
            
            # Category breakdown
            mdfile.write("## File Categories\n\n")
            for category, count in sorted(summary['categories'].items()):
                mdfile.write(f"- **{category.replace('_', ' ').title()}:** {count} files\n")
            mdfile.write("\n")
            
            # Top contributors
            mdfile.write("## Contributors\n\n")
            for contrib in summary['top_contributors']:
                mdfile.write(f"- **{contrib['name']}** ({contrib['email']}): ")
                mdfile.write(f"{contrib['file_count']} files, {contrib['commit_count']} commits\n")
            mdfile.write("\n")
            
            # Detailed file listing by category
            mdfile.write("## Detailed File Inventory\n\n")
            
            for category in sorted(summary['categories'].keys()):
                category_files = [f for f in self.file_inventory if f['category'] == category]
                if not category_files:
                    continue
                    
                mdfile.write(f"### {category.replace('_', ' ').title()}\n\n")
                
                for file_info in sorted(category_files, key=lambda x: x['path']):
                    mdfile.write(f"**{file_info['path']}**\n")
                    mdfile.write(f"- Primary Author: {file_info['primary_author']}\n")
                    mdfile.write(f"- Contributors: {file_info['contributor_count']}\n")
                    mdfile.write(f"- Size: {file_info['size_bytes']:,} bytes\n")
                    
                    if file_info['contributors']:
                        contributors_str = ', '.join([c['name'] for c in file_info['contributors']])
                        mdfile.write(f"- All Contributors: {contributors_str}\n")
                    
                    mdfile.write(f"- SHA-256: `{file_info['sha256_hash']}`\n\n")
        
        print(f"Markdown export saved to: {output_path}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate legal file inventory for Basic Memory repository"
    )
    parser.add_argument(
        '--repo-path', '-r',
        default='.',
        help='Path to repository (default: current directory)'
    )
    parser.add_argument(
        '--output-dir', '-o',
        default='./legal_inventory',
        help='Output directory for reports (default: ./legal_inventory)'
    )
    parser.add_argument(
        '--formats', '-f',
        nargs='+',
        choices=['csv', 'json', 'markdown', 'all'],
        default=['all'],
        help='Output formats to generate (default: all)'
    )
    
    args = parser.parse_args()
    
    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)
    
    # Generate inventory
    generator = LegalInventoryGenerator(args.repo_path)
    generator.scan_repository()
    
    # Determine formats to export
    formats = args.formats
    if 'all' in formats:
        formats = ['csv', 'json', 'markdown']
    
    # Export in requested formats
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    if 'csv' in formats:
        generator.export_csv(output_dir / f'basic_memory_inventory_{timestamp}.csv')
    
    if 'json' in formats:
        generator.export_json(output_dir / f'basic_memory_inventory_{timestamp}.json')
    
    if 'markdown' in formats:
        generator.export_markdown(output_dir / f'basic_memory_inventory_{timestamp}.md')
    
    print(f"\nLegal inventory generation complete!")
    print(f"Output saved to: {output_dir}")


if __name__ == '__main__':
    main()