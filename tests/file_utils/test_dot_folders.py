"""Test that all dot folders are ignored."""

import pytest
from basic_memory.file_utils.gitignore import should_ignore_file


@pytest.mark.asyncio
async def test_dot_folders_ignored(tmp_path):
    """Test that all dot folders and their contents are ignored."""
    # Create test dot folders and files
    dot_folders = [
        '.git',
        '.vscode', 
        '.idea',
        '.custom_folder',
        '.another.folder',
        '.config'
    ]
    
    # Create each dot folder with some contents
    for folder in dot_folders:
        folder_path = tmp_path / folder
        folder_path.mkdir()
        (folder_path / 'test.txt').write_text('test')
        
    # Create a regular folder for comparison
    normal_folder = tmp_path / 'normal_folder'
    normal_folder.mkdir()
    (normal_folder / 'test.txt').write_text('test')
    
    # Test that all dot folders and their contents are ignored
    for folder in dot_folders:
        folder_path = tmp_path / folder
        assert should_ignore_file(str(folder_path), tmp_path), f"Should ignore {folder}"
        assert should_ignore_file(str(folder_path / 'test.txt'), tmp_path), f"Should ignore files in {folder}"
        
    # Test that normal folder is not ignored
    assert not should_ignore_file(str(normal_folder), tmp_path), "Should not ignore normal folder"
    assert not should_ignore_file(str(normal_folder / 'test.txt'), tmp_path), "Should not ignore files in normal folder"
