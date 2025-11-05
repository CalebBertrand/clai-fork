#!/usr/bin/env python3
"""
Run CLI commands in an overlayfs mount for safe execution with rollback capability.
"""

import os
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Optional, Tuple, List
from abc import ABC, abstractmethod

from ..shell import Prompter
from .sandbox import Sandbox


class OverlayFS(Sandbox):
    """Handles overlayfs mounting and command execution in an isolated environment."""
    
    def __init__(self, base_dir: str):
        """
        Initialize the OverlayFS handler.
        
        Args:
            base_dir: The base directory to protect with overlayfs
            
        Raises:
            FileNotFoundError: If base_dir doesn't exist
        """
        if not os.path.exists(base_dir):
            raise FileNotFoundError(f"Base directory does not exist: {base_dir}")
        
        self.base_dir = base_dir
        self.temp_root = None
        self.upper_dir = None
        self.work_dir = None
        self.merged_dir = None
        self.mounted = False
        
        # Create temporary directories for overlayfs layers
        self.temp_root = tempfile.mkdtemp(prefix="overlay_")
        self.upper_dir = os.path.join(self.temp_root, "upper")
        self.work_dir = os.path.join(self.temp_root, "work")
        self.merged_dir = os.path.join(self.temp_root, "merged")
        
        os.makedirs(self.upper_dir)
        os.makedirs(self.work_dir)
        os.makedirs(self.merged_dir)
        
        try:
            mount_cmd = [
                "mount",
                "-t", "overlay",
                "overlay",
                "-o", f"lowerdir={self.base_dir},upperdir={self.upper_dir},workdir={self.work_dir}",
                self.merged_dir
            ]
            subprocess.run(mount_cmd, check=True, capture_output=True)
            self.mounted = True
        except subprocess.CalledProcessError as e:
            if "mount" in str(e.cmd):
                raise PermissionError(
                    "Failed to mount overlayfs. This operation requires root privileges. "
                    "Try running with sudo."
                ) from e
            raise
    
    def run_command(self, command: List[str]) -> subprocess.CompletedProcess:
        """
        Execute a command in the overlay environment.
        
        Args:
            command: List of command arguments to execute
            
        Returns:
            CompletedProcess object from subprocess.run
            
        Raises:
            RuntimeError: If overlay is not mounted
        """
        if not self.mounted:
            raise RuntimeError("OverlayFS is not mounted")
        
        return subprocess.run(command, cwd=self.merged_dir)
    
    def cleanup(self, keep_changes: bool = False) -> None:
        """
        Clean up the overlay and optionally copy changes to the base directory.
        
        Args:
            keep_changes: If True, copy changes from upper layer to base directory
        """
        try:
            if keep_changes and self.mounted:
                # Copy changes from upper_dir to base_dir
                for root, dirs, files in os.walk(self.upper_dir):
                    rel_path = os.path.relpath(root, self.upper_dir)
                    target_dir = os.path.join(self.base_dir, rel_path) if rel_path != "." else self.base_dir

                    for dir_name in dirs:
                        target_path = os.path.join(target_dir, dir_name)
                        os.makedirs(target_path, exist_ok=True)

                    for file_name in files:
                        src_file = os.path.join(root, file_name)
                        dst_file = os.path.join(target_dir, file_name)
                        shutil.copy2(src_file, dst_file)
                        
            if self.mounted:
                subprocess.run(["umount", self.merged_dir], check=True, capture_output=True)
                self.mounted = False
                
        finally:
            if self.temp_root and os.path.exists(self.temp_root):
                shutil.rmtree(self.temp_root)


if __name__ == "__main__":
    main()
