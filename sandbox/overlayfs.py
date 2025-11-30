#!/usr/bin/env python3
"""
Run CLI commands in an overlayfs mount for safe execution with rollback capability.
"""

import os
import subprocess
import tempfile
import shutil
import stat
import glob
from typing import List, Optional, Set, Any
from .sandbox import Sandbox


# Default sensitive paths to hide (absolute paths that will be filtered to base_dir)
DEFAULT_SENSITIVE_PATHS = [
    # # Password and authentication files
    "/etc/shadow",
    "/etc/gshadow",
    "/etc/sudoers",
    "/etc/sudoers.d",
    "/etc/security/opasswd",
    # SSH keys and config
    # "/etc/ssh/ssh_host_*",
    # "/root/.ssh",
    "/home/*/.ssh",
    # # Shell history and secrets
    # "/root/.bash_history",
    # "/root/.zsh_history",
    # "/root/.python_history",
    # "/home/*/.bash_history",
    # "/home/*/.zsh_history",
    # "/home/*/.python_history",
    # # GPG and crypto
    # "/root/.gnupg",
    # "/home/*/.gnupg",
    # # Cloud credentials
    # "/root/.aws",
    # "/root/.azure",
    # "/root/.config/gcloud",
    # "/home/*/.aws",
    # "/home/*/.azure",
    # "/home/*/.config/gcloud",
    # # Environment files that may contain secrets
    # "/etc/environment",
    # # Kubernetes
    # "/root/.kube",
    # "/home/*/.kube",
    # # Docker
    # "/root/.docker/config.json",
    # "/home/*/.docker/config.json",
    # # Password managers and keyrings
    # "/root/.local/share/keyrings",
    # "/home/*/.local/share/keyrings",
    # # Git credentials
    # "/root/.git-credentials",
    # "/home/*/.git-credentials",
    # # netrc files
    # "/root/.netrc",
    # "/home/*/.netrc",
]


class OverlayFS(Sandbox):
    """Handles overlayfs mounting and command execution in an isolated environment."""

    def __init__(self, base_dir: str, sensitive_paths: Optional[List[str]] = None):
        """
        Initialize the OverlayFS handler.
        Args:
            base_dir: The base directory to protect with overlayfs
            sensitive_paths: Additional paths to hide (supports glob patterns).
                            If None, uses DEFAULT_SENSITIVE_PATHS when hide_sensitive_files=True
            hide_sensitive_files: If True, hide sensitive system files from the overlay
        Raises:
            FileNotFoundError: If base_dir doesn't exist
        """
        if not os.path.exists(base_dir):
            raise FileNotFoundError(f"Base directory does not exist: {base_dir}")

        self.base_dir = os.path.abspath(base_dir)
        self.current_dir = self.base_dir
        self.mounted = False
        self.temp_root = tempfile.mkdtemp(prefix="overlay_")
        self.upper_dir = os.path.join(self.temp_root, "upper")
        self.work_dir = os.path.join(self.temp_root, "work")
        self.merged_dir = os.path.join(self.temp_root, "merged")
        self.hidden_paths: Set[str] = set()
        # Track all overlay mounts: list of (upper_dir, lower_dir, mount_point) tuples
        # Used during cleanup to apply changes from each overlay
        self.overlay_mounts: List[tuple[str, str, str]] = []

        os.makedirs(self.upper_dir)
        os.makedirs(self.work_dir)
        os.makedirs(self.merged_dir)

        try:
            # Overlay the entire root filesystem so chroot has access to /bin/bash etc.
            # Any writes anywhere in the filesystem will go to the upper layer.
            mount_cmd = [
                "mount",
                "-t",
                "overlay",
                "overlay",
                "-o",
                f"lowerdir=/,upperdir={self.upper_dir},workdir={self.work_dir}",
                self.merged_dir,
            ]
            subprocess.run(mount_cmd, check=True, capture_output=True)
            self.mounted = True
            # Track root overlay: (upper_dir, lower_dir, mount_point)
            self.overlay_mounts.append((self.upper_dir, "/", self.merged_dir))
        except subprocess.CalledProcessError as e:
            if "mount" in str(e.cmd):
                raise PermissionError(
                    "Failed to mount overlayfs. This operation requires root privileges. "
                    "Try running with sudo."
                ) from e
            raise

        # Bind-mount submounts (like /home on a separate partition) into the merged view.
        # Overlayfs only sees the root filesystem's content, not other mounted filesystems.
        self._bind_submounts()

        paths_to_hide = list(DEFAULT_SENSITIVE_PATHS)
        if sensitive_paths:
            paths_to_hide.extend(sensitive_paths)
        self._hide_sensitive_paths(paths_to_hide)

    def run_command(self, command: List[str]) -> Any:
        """
        Execute a command in the overlay environment using chroot isolation.

        Security approach (defense in depth):
        1. The overlayfs overlays the entire root filesystem (/) with an upper layer
        2. Chroot into the merged overlayfs view, which contains /bin/bash and all binaries
        3. All filesystem writes go to the overlay's upper layer, protecting the real filesystem

        Even if a malicious process attempts to unmount from inside the chroot,
        they cannot escape because they're already confined to the merged view.

        Args:
            command: List of command arguments to execute
        Returns:
            Dictionary with returncode, stdout, stderr
        Raises:
            RuntimeError: If overlay is not mounted
        """
        if not self.mounted:
            raise RuntimeError("OverlayFS is not mounted")

        # Prepare environment
        env = os.environ.copy()
        env["PWD"] = self.current_dir
        env["OVERLAY_BASE_DIR"] = self.base_dir

        # Escape single quotes in paths and command for shell safety
        merged_dir_escaped = self.merged_dir.replace("'", "'\\''")
        # Inside the chroot, use the absolute path to current_dir (same as on real system)
        current_dir_escaped = self.current_dir.replace("'", "'\\''")

        # Join command parts, escaping single quotes
        command_escaped = " ".join(part.replace("'", "'\\''") for part in command)

        # Chroot into the merged overlayfs view which contains the full root filesystem.
        # This provides defense in depth:
        # 1. The overlayfs ensures all writes go to the upper layer (protecting the real filesystem)
        # 2. The chroot confines the process to the merged view (even if they try to unmount,
        #    they can't escape because they're already chrooted into the overlay)
        # Note: Submounts (like /home) are overlaid during __init__ via _bind_submounts()
        cmd_str = f"""
        set -e
        chroot '{merged_dir_escaped}' bash -c "cd '{current_dir_escaped}' && {command_escaped} && echo FINAL_PWD:\$(pwd)"
        """

        result = subprocess.run(
            ["unshare", "-m", "bash", "-c", cmd_str],
            env=env,
            capture_output=True,
            text=True,
        )

        # Extract the final working directory from output
        stdout_lines = result.stdout.split("\n") if result.stdout else []
        for i, line in enumerate(stdout_lines):
            if line.startswith("FINAL_PWD:"):
                # Since we overlay the full root, paths inside chroot are absolute
                # and match the real filesystem paths
                final_pwd = line[10:]

                # Validate and normalize the path
                final_pwd = os.path.normpath(final_pwd)
                if final_pwd.startswith(self.base_dir):
                    self.current_dir = final_pwd
                stdout_lines.pop(i)
                break

        return {
            "returncode": result.returncode,
            "stdout": "\n".join(stdout_lines).encode() if stdout_lines else b"",
            "stderr": result.stderr.encode() if result.stderr else b"",
        }

    def _bind_submounts(self) -> None:
        """
        Create overlay mounts for submounts in the merged view.

        Overlayfs only sees the content of the root filesystem itself, not other
        filesystems mounted under it (like /home on a separate partition). This
        method discovers all submounts and creates nested overlays for each,
        ensuring writes are captured in the upper layer (not the real filesystem).
        """
        # Get all mount points except root
        result = subprocess.run(
            ["findmnt", "-rn", "-o", "TARGET"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return

        mount_points = sorted(result.stdout.strip().split("\n"))
        for mnt in mount_points:
            if not mnt or mnt == "/":
                continue
            target = os.path.join(self.merged_dir, mnt.lstrip("/"))
            if os.path.isdir(mnt) and os.path.isdir(target):
                # Create separate upper/work dirs for this submount
                safe_name = mnt.replace("/", "_")
                sub_upper = os.path.join(self.temp_root, f"sub_upper{safe_name}")
                sub_work = os.path.join(self.temp_root, f"sub_work{safe_name}")
                os.makedirs(sub_upper, exist_ok=True)
                os.makedirs(sub_work, exist_ok=True)

                try:
                    subprocess.run(
                        [
                            "mount",
                            "-t",
                            "overlay",
                            "overlay",
                            "-o",
                            f"lowerdir={mnt},upperdir={sub_upper},workdir={sub_work}",
                            target,
                        ],
                        check=True,
                        capture_output=True,
                    )
                    # Track submount overlay: (upper_dir, lower_dir, mount_point)
                    self.overlay_mounts.append((sub_upper, mnt, target))
                except subprocess.CalledProcessError:
                    # Skip mounts that fail (e.g., permission issues)
                    pass

    def _hide_sensitive_paths(self, patterns: List[str]) -> None:
        """
        Hide sensitive paths by creating whiteout files in each overlay's upper layer.

        Loops through each mounted overlay (root and submounts like /home) and
        expands glob patterns within that filesystem, creating whiteouts in the
        corresponding upper directory.

        Args:
            patterns: List of absolute paths or glob patterns to hide
        """
        for upper_dir, lower_dir, _ in self.overlay_mounts:
            for pattern in patterns:
                # Expand glob patterns
                expanded_paths = glob.glob(pattern)
                if not expanded_paths:
                    # Pattern didn't match anything, try as literal path
                    expanded_paths = [pattern]

                for abs_path in expanded_paths:
                    # Check if this path belongs to this overlay
                    if abs_path == lower_dir or abs_path.startswith(
                        lower_dir.rstrip("/") + "/"
                    ):
                        self._create_whiteout_in_overlay(abs_path, upper_dir, lower_dir)

    def _create_whiteout_in_overlay(
        self, abs_path: str, upper_dir: str, lower_dir: str
    ) -> None:
        """
        Create a whiteout file in the specified overlay's upper layer.

        Whiteout files are character devices with device number 0/0. When overlayfs
        sees a whiteout in the upper layer, it hides the corresponding file in the
        lower layer, making it appear as if the file doesn't exist.

        Args:
            abs_path: Absolute path to hide
            upper_dir: The overlay's upper directory
            lower_dir: The overlay's lower directory
        """
        try:
            rel_path = os.path.relpath(abs_path, lower_dir)
            if rel_path.startswith(".."):
                # Path is outside this overlay, skip
                return
        except ValueError:
            # On Windows, relpath can fail for paths on different drives
            return

        if not os.path.exists(abs_path):
            return

        whiteout_path = os.path.join(upper_dir, rel_path)

        parent_dir = os.path.dirname(whiteout_path)
        os.makedirs(parent_dir, exist_ok=True)

        try:
            # If it's a directory, we need to create an opaque directory instead
            if os.path.isdir(abs_path):
                self._create_opaque_dir(whiteout_path, abs_path, upper_dir, lower_dir)
            else:
                if os.path.exists(whiteout_path):
                    os.remove(whiteout_path)
                os.mknod(whiteout_path, stat.S_IFCHR | 0o000, os.makedev(0, 0))
                self.hidden_paths.add(abs_path)
        except PermissionError:
            pass
        except OSError as e:
            # Handle other OS errors gracefully
            if e.errno not in (17,):  # 17 = EEXIST
                pass

    def _create_opaque_dir(
        self, whiteout_path: str, abs_path: str, upper_dir: str, lower_dir: str
    ) -> None:
        """
        Create an opaque directory to hide an entire directory tree.

        An opaque directory has the trusted.overlay.opaque xattr set to 'y',
        which tells overlayfs to hide all contents from the lower layer.

        Args:
            whiteout_path: Path in upper layer
            abs_path: Absolute path being hidden
            upper_dir: The overlay's upper directory
            lower_dir: The overlay's lower directory
        """
        import subprocess

        os.makedirs(whiteout_path, exist_ok=True)

        try:
            subprocess.run(
                ["setfattr", "-n", "trusted.overlay.opaque", "-v", "y", whiteout_path],
                check=True,
                capture_output=True,
            )
            self.hidden_paths.add(abs_path)
        except (subprocess.CalledProcessError, FileNotFoundError):
            self._create_whiteouts_recursive(abs_path, upper_dir, lower_dir)

    def _create_whiteouts_recursive(
        self, abs_path: str, upper_dir: str, lower_dir: str
    ) -> None:
        """
        Recursively create whiteout files for all contents of a directory.

        This is a fallback when xattr-based opaque directories aren't available.

        Args:
            abs_path: Absolute path of the directory to hide
            upper_dir: The overlay's upper directory
            lower_dir: The overlay's lower directory
        """
        try:
            for entry in os.listdir(abs_path):
                entry_abs_path = os.path.join(abs_path, entry)
                entry_rel_path = os.path.relpath(entry_abs_path, lower_dir)

                if os.path.isdir(entry_abs_path):
                    self._create_whiteouts_recursive(
                        entry_abs_path, upper_dir, lower_dir
                    )
                else:
                    whiteout_path = os.path.join(upper_dir, entry_rel_path)
                    parent_dir = os.path.dirname(whiteout_path)
                    os.makedirs(parent_dir, exist_ok=True)

                    try:
                        if os.path.exists(whiteout_path):
                            os.remove(whiteout_path)
                        os.mknod(whiteout_path, stat.S_IFCHR | 0o000, os.makedev(0, 0))
                        self.hidden_paths.add(entry_abs_path)
                    except (PermissionError, OSError):
                        pass
        except PermissionError:
            # Can't read the directory
            pass

    def cleanup(self, keep_changes: bool = False) -> None:
        """
        Clean up all overlays and optionally copy changes to their base directories.
        Args:
            keep_changes: If True, copy changes from each upper layer to its base directory
        """
        try:
            if keep_changes and self.mounted:
                # Apply changes from each overlay's upper layer to its lower layer
                for upper_dir, lower_dir, _ in self.overlay_mounts:
                    self._apply_overlay_changes(upper_dir, lower_dir)
        finally:
            if self.mounted:
                # Unmount in reverse order (submounts first, then root)
                for _, _, mount_point in reversed(self.overlay_mounts):
                    try:
                        subprocess.run(
                            ["umount", mount_point], check=True, capture_output=True
                        )
                    except subprocess.CalledProcessError:
                        # Force unmount if regular unmount fails
                        try:
                            subprocess.run(
                                ["umount", "-f", mount_point],
                                check=True,
                                capture_output=True,
                            )
                        except subprocess.CalledProcessError:
                            pass  # Continue with cleanup even if unmount fails
                self.mounted = False
                self.overlay_mounts.clear()

            # Clean up temporary directories
            if self.temp_root and os.path.exists(self.temp_root):
                try:
                    shutil.rmtree(self.temp_root)
                except OSError:
                    # If removal fails, try to fix permissions and retry
                    self._fix_permissions_and_retry_cleanup()

    def _apply_overlay_changes(self, upper_dir: str, lower_dir: str) -> None:
        """
        Apply changes from an overlay's upper layer to its lower (base) directory.

        Args:
            upper_dir: The overlay's upper directory containing changes
            lower_dir: The original lower directory to apply changes to
        """
        import stat

        # Process deletions first (whiteout files)
        for root, dirs, files in os.walk(upper_dir):
            rel_path = os.path.relpath(root, upper_dir)
            target_dir = (
                os.path.join(lower_dir, rel_path) if rel_path != "." else lower_dir
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
        for root, dirs, files in os.walk(upper_dir):
            rel_path = os.path.relpath(root, upper_dir)
            target_dir = (
                os.path.join(lower_dir, rel_path) if rel_path != "." else lower_dir
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

    def get_pwd(self) -> str:
        """
        Get the current working directory visible in the sandbox.

        Returns:
            Current working directory path
        """
        return self.current_dir

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
