#!/usr/bin/env python3
"""
Run CLI commands in an overlayfs mount for safe execution with rollback capability.
"""

import os
import subprocess
import tempfile
import shutil
from typing import List

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
        self.mounted = False
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
                "-t",
                "overlay",
                "overlay",
                "-o",
                f"lowerdir={self.base_dir},upperdir={self.upper_dir},workdir={self.work_dir}",
                self.merged_dir,
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

        fake_bin_dir = os.path.join(self.temp_root, "fake_bin")
        os.makedirs(fake_bin_dir, exist_ok=True)

        pwd_wrapper = os.path.join(fake_bin_dir, "pwd")
        with open(pwd_wrapper, "w") as f:
            f.write(f'#!/bin/sh\necho "{self.base_dir}"\n')
        os.chmod(pwd_wrapper, 0o755)

        env = os.environ.copy()
        env["PWD"] = self.base_dir
        env["OVERLAY_BASE_DIR"] = self.base_dir
        env["PATH"] = f"{fake_bin_dir}:{env.get('PATH', '/usr/bin:/bin')}"

        pwd_func = f'pwd() {{ echo "{self.base_dir}"; }}'
        cmd_str = f'export PWD="{self.base_dir}"; {pwd_func}; {" ".join(command)}'

        return subprocess.run(["bash", "-c", cmd_str], cwd=self.merged_dir, env=env)

    def cleanup(self, keep_changes: bool = False) -> None:
        """
        Clean up the overlay and optionally copy changes to the base directory.

        Args:
            keep_changes: If True, copy changes from upper layer to base directory
        """
        try:
            if keep_changes and self.mounted:
                self._apply_changes_to_base()

        finally:
            if self.mounted:
                try:
                    subprocess.run(
                        ["umount", self.merged_dir], check=True, capture_output=True
                    )
                    self.mounted = False
                except subprocess.CalledProcessError:
                    # Force unmount if regular unmount fails
                    try:
                        subprocess.run(
                            ["umount", "-f", self.merged_dir],
                            check=True,
                            capture_output=True,
                        )
                        self.mounted = False
                    except subprocess.CalledProcessError:
                        pass  # Continue with cleanup even if unmount fails

            # Clean up temporary directories
            if self.temp_root and os.path.exists(self.temp_root):
                try:
                    shutil.rmtree(self.temp_root)
                except OSError:
                    # If removal fails, try to fix permissions and retry
                    self._fix_permissions_and_retry_cleanup()

    def _apply_changes_to_base(self) -> None:
        """Apply changes from upper layer to base directory, handling deletions."""
        import stat

        # Process deletions first (whiteout files)
        for root, dirs, files in os.walk(self.upper_dir):
            rel_path = os.path.relpath(root, self.upper_dir)
            target_dir = (
                os.path.join(self.base_dir, rel_path)
                if rel_path != "."
                else self.base_dir
            )

            for file_name in files:
                src_file = os.path.join(root, file_name)

                # Check if this is a whiteout file (indicates deletion)
                try:
                    file_stat = os.stat(src_file)
                    if stat.S_ISCHR(file_stat.st_mode):
                        # This is a whiteout file, remove corresponding file in base
                        target_file = os.path.join(target_dir, file_name)
                        if os.path.exists(target_file):
                            os.remove(target_file)
                        continue
                except (OSError, PermissionError):
                    # Skip files we can't stat (might be whiteouts we can't read)
                    continue

        # Process additions and modifications
        for root, dirs, files in os.walk(self.upper_dir):
            rel_path = os.path.relpath(root, self.upper_dir)
            target_dir = (
                os.path.join(self.base_dir, rel_path)
                if rel_path != "."
                else self.base_dir
            )

            for dir_name in dirs:
                target_path = os.path.join(target_dir, dir_name)
                os.makedirs(target_path, exist_ok=True)

            for file_name in files:
                src_file = os.path.join(root, file_name)
                dst_file = os.path.join(target_dir, file_name)

                # Skip whiteout files (already handled above)
                try:
                    file_stat = os.stat(src_file)
                    if stat.S_ISCHR(file_stat.st_mode):
                        continue
                except (OSError, PermissionError):
                    continue

                try:
                    shutil.copy2(src_file, dst_file)
                except (OSError, PermissionError):
                    # Skip files we can't copy (might be special files)
                    continue

    def _fix_permissions_and_retry_cleanup(self) -> None:
        """Fix permissions and retry cleanup of temp directories."""
        try:
            # Make everything writable and try again
            for root, dirs, files in os.walk(self.temp_root, topdown=False):
                for name in files:
                    try:
                        os.chmod(os.path.join(root, name), 0o666)
                    except OSError:
                        pass
                for name in dirs:
                    try:
                        os.chmod(os.path.join(root, name), 0o777)
                    except OSError:
                        pass

            shutil.rmtree(self.temp_root)
        except OSError:
            print(f"Warning: Could not remove temporary directory: {self.temp_root}")
