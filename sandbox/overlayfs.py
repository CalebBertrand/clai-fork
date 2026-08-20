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
import base64
import shlex
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Set, Any
from .sandbox import Sandbox


class ChangeType(Enum):
    """Type of change detected in the overlay."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass
class ChangedFile:
    """Represents a file that was changed in the overlay."""

    path: str  # Path relative to the lower directory
    change_type: ChangeType
    upper_path: str  # Full path in the upper directory
    lower_path: str  # Full path in the lower directory (original)


# Default sensitive paths to hide (absolute paths that will be filtered to base_dir)
DEFAULT_SENSITIVE_PATHS = [
    # Password and authentication files
    "/etc/shadow",
    "/etc/gshadow",
    "/etc/sudoers",
    "/etc/sudoers.d",
    "/etc/security/opasswd",
    # SSH keys and config
    "/etc/ssh/ssh_host_*",
    "/root/.ssh",
    "/home/*/.ssh",
    # Shell history and secrets
    "/root/.bash_history",
    "/root/.zsh_history",
    "/root/.python_history",
    "/home/*/.bash_history",
    "/home/*/.zsh_history",
    "/home/*/.python_history",
    # GPG and crypto
    "/root/.gnupg",
    "/home/*/.gnupg",
    # Cloud credentials
    "/root/.aws",
    "/root/.azure",
    "/root/.config/gcloud",
    "/home/*/.aws",
    "/home/*/.azure",
    "/home/*/.config/gcloud",
    # Environment files that may contain secrets
    "/etc/environment",
    # Kubernetes
    "/root/.kube",
    "/home/*/.kube",
    # Docker
    "/root/.docker/config.json",
    "/home/*/.docker/config.json",
    # Password managers and keyrings
    "/root/.local/share/keyrings",
    "/home/*/.local/share/keyrings",
    # Git credentials
    "/root/.git-credentials",
    "/home/*/.git-credentials",
    # netrc files
    "/root/.netrc",
    "/home/*/.netrc",
]


def _decode_b64(data: str) -> bytes:
    """Decode a base64 payload from the sandbox, tolerating empty/corrupt input."""
    if not data:
        return b""
    try:
        return base64.b64decode(data)
    except Exception:
        return b""


class OverlayFS(Sandbox):
    """Handles overlayfs mounting and command execution in an isolated environment."""

    def __init__(self, base_dir: str, sensitive_paths: Optional[List[str]] = None):
        """
        Initialize the OverlayFS handler.
        Args:
            base_dir: The base directory to protect with overlayfs
            sensitive_paths: Additional paths to hide (supports glob patterns).
                            If None, uses DEFAULT_SENSITIVE_PATHS when hide_sensitive_files=True
        Raises:
            FileNotFoundError: If base_dir doesn't exist
        """
        if not os.path.exists(base_dir):
            raise FileNotFoundError(f"Base directory does not exist: {base_dir}")

        self.base_dir = os.path.abspath(base_dir)
        self.mounted = False
        self.temp_root = tempfile.mkdtemp(prefix="overlay_")
        self.upper_dir = os.path.join(self.temp_root, "upper")
        self.work_dir = os.path.join(self.temp_root, "work")
        self.merged_dir = os.path.join(self.temp_root, "merged")
        # Where commands actually see the overlay. Privileged mode mounts the
        # merged view under temp_root; unprivileged mode mounts the overlay
        # over base_dir itself inside a private mount namespace, so paths look
        # exactly like the real ones to anything running in the sandbox.
        self.mount_point = self.merged_dir
        self.hidden_paths: Set[str] = set()
        # Track all overlay mounts: list of (upper_dir, lower_dir, mount_point) tuples
        # Used during cleanup to apply changes from each overlay
        self.overlay_mounts: List[tuple[str, str, str]] = []

        # Track whether we're using user namespaces for unprivileged operation
        self.using_userns = False

        # Capture the original user's UID/GID if running under sudo
        # This allows us to run commands as the original user, not root
        self.orig_uid: Optional[int] = None
        self.orig_gid: Optional[int] = None
        self.orig_user: Optional[str] = None
        if os.environ.get("SUDO_UID"):
            self.orig_uid = int(os.environ["SUDO_UID"])
            self.orig_gid = int(os.environ.get("SUDO_GID", self.orig_uid))
            self.orig_user = os.environ.get("SUDO_USER")

        os.makedirs(self.upper_dir)
        os.makedirs(self.work_dir)
        os.makedirs(self.merged_dir)

        # Try to mount overlayfs - first directly (if root), then via user namespace
        self._mount_overlay()

        # Bind-mount submounts (like /home on a separate partition) into the merged view.
        # Overlayfs only sees the root filesystem's content, not other mounted filesystems.
        # For userns mode, submounts are handled inside the namespace setup script.
        if not self.using_userns:
            self._bind_submounts()

        # Hide sensitive paths from the overlay (only needed for privileged mode)
        # In userns mode, the user is already unprivileged and can't access these paths anyway
        if not self.using_userns:
            paths_to_hide = list(DEFAULT_SENSITIVE_PATHS)
            if sensitive_paths:
                paths_to_hide.extend(sensitive_paths)
            self._hide_sensitive_paths(paths_to_hide)

    def _mount_overlay(self) -> None:
        """
        Mount the root overlayfs, trying privileged mount first, then user namespace.

        On modern Linux (5.11+), overlayfs can be mounted inside a user namespace
        without root privileges. This method tries direct mount first (for root),
        then falls back to user namespace mounting for unprivileged users.

        Raises:
            PermissionError: If neither privileged nor unprivileged mounting works
        """
        mount_opts = f"lowerdir=/,upperdir={self.upper_dir},workdir={self.work_dir}"

        # If running as root, use direct mount
        if os.geteuid() == 0:
            try:
                subprocess.run(
                    ["mount", "-t", "overlay", "overlay", "-o", mount_opts, self.merged_dir],
                    check=True,
                    capture_output=True,
                )
                self.mounted = True
                self.overlay_mounts.append((self.upper_dir, "/", self.merged_dir))
                return
            except subprocess.CalledProcessError as e:
                raise PermissionError(
                    f"Failed to mount overlayfs as root: {e.stderr.decode() if e.stderr else str(e)}"
                ) from e

        # Try unprivileged mount using a persistent user namespace
        # This works on Linux 5.11+ with unprivileged user namespaces enabled
        if self._setup_userns_namespace():
            return

        # Neither method worked
        raise PermissionError(
            "Failed to mount overlayfs. This operation requires either:\n"
            "  1. Root privileges (run with sudo), or\n"
            "  2. A Linux kernel 5.11+ with unprivileged user namespaces enabled"
        )

    def _setup_userns_namespace(self) -> bool:
        """
        Set up a persistent user namespace with overlayfs mounted.

        Creates a long-running shell process inside a user+mount namespace,
        mounts the overlay over base_dir inside it, and keeps it alive so that
        successive commands share one shell (and therefore one working
        directory, one set of shell variables, and so on).

        Note: unlike the privileged path, this overlays only base_dir rather
        than the whole root filesystem, so writes outside base_dir are NOT
        captured by the overlay. See run_command() for the full caveat.

        Returns:
            True if setup succeeded, False otherwise
        """
        mount_opts = f"lowerdir={shlex.quote(self.base_dir)},upperdir={self.upper_dir},workdir={self.work_dir},userxattr"

        # Mount the overlay over base_dir itself. We are inside a private mount
        # namespace, so this is invisible to the rest of the system, and it
        # means commands in the sandbox see their real paths rather than an
        # internal /tmp/overlay_* path.
        self.mount_point = self.base_dir

        # Paths used by the command loop to capture each command's streams
        # separately. They live in temp_root, which is outside the overlay.
        out_file = os.path.join(self.temp_root, "cmd_stdout")
        err_file = os.path.join(self.temp_root, "cmd_stderr")

        # Create a shell script that will:
        # 1. Mount an overlay over just the base directory (not /, which fails in userns)
        # 2. cd into the merged view and signal readiness
        # 3. Serve commands from stdin, one per line, in that same shell
        #
        # The command is evaluated in the loop's own shell rather than a
        # subshell so that state such as `cd` persists between commands, which
        # is what makes this behave like a shell rather than a series of
        # unrelated one-shot executions.
        #
        # Protocol, per command: we send one base64-encoded line (or the
        # literal "EXIT"), and read back three lines - the return code, the
        # base64-encoded stdout, and the base64-encoded stderr. base64 keeps
        # arbitrary binary output and embedded newlines on a single line.
        setup_script = f"""
# Mount overlay over the base directory
mount -t overlay overlay -o {mount_opts} {shlex.quote(self.mount_point)} || exit 1

# Start inside the overlaid directory; this cwd persists across commands
cd {shlex.quote(self.mount_point)} || exit 1

echo "READY"

while IFS= read -r cmd_b64; do
    if [ "$cmd_b64" = "EXIT" ]; then
        exit 0
    fi
    cmd=$(printf '%s' "$cmd_b64" | base64 -d)
    # Redirect stdin from /dev/null so a command cannot consume the command
    # stream we are reading from.
    eval "$cmd" > {shlex.quote(out_file)} 2> {shlex.quote(err_file)} < /dev/null
    rc=$?
    printf '%s\\n' "$rc"
    base64 -w0 < {shlex.quote(out_file)}; printf '\\n'
    base64 -w0 < {shlex.quote(err_file)}; printf '\\n'
done
"""

        try:
            import select

            # Start the namespace process
            self._ns_process = subprocess.Popen(
                ["unshare", "-rm", "bash", "-c", setup_script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            ns_stdout = self._ns_process.stdout
            if ns_stdout is None:
                self._ns_process.kill()
                return False

            # Wait for the READY signal (with timeout)
            ready = select.select([ns_stdout], [], [], 10)
            if not ready[0]:
                self._ns_process.kill()
                return False

            if ns_stdout.readline().strip() != "READY":
                self._ns_process.kill()
                return False

            self.mounted = True
            self.using_userns = True
            self.overlay_mounts.append(
                (self.upper_dir, self.base_dir, self.mount_point)
            )
            return True

        except (FileNotFoundError, OSError):
            return False

    @staticmethod
    def _to_shell_line(command: "str | List[str]") -> str:
        """
        Normalise a command into a single shell line.

        A string is passed through untouched, so the caller can use the full
        shell grammar (pipes, redirects, quoting, &&). A list is treated as an
        argv vector and joined with shlex.quote, so arguments containing
        spaces or metacharacters survive intact rather than being re-split.

        Args:
            command: A raw shell line, or a list of argv tokens
        Returns:
            A shell line ready to be evaluated by bash
        """
        if isinstance(command, str):
            return command
        return shlex.join(command)

    def run_command(self, command: "str | List[str]") -> Any:
        """
        Execute a command in the overlay environment.

        Accepts either a raw shell line (evaluated by bash, so pipes,
        redirects and quoting all work) or a list of argv tokens (joined with
        proper quoting).

        Isolation depends on how the overlay was mounted:

        Privileged mode (running as root) - the overlay covers the entire root
        filesystem, the command runs chrooted into the merged view, and every
        write anywhere on the filesystem lands in the upper layer. If running
        under sudo, commands execute as the original user, not root.

        Unprivileged mode (user namespace) - the overlay covers base_dir only.
        Writes under base_dir are captured by the upper layer and can be
        reviewed and rolled back, but paths OUTSIDE base_dir are the real
        filesystem and are not protected. There is no chroot in this mode
        because the merged view contains only base_dir, not a root filesystem
        to chroot into.

        Args:
            command: A raw shell line, or a list of argv tokens
        Returns:
            Dictionary with returncode, stdout, stderr
        Raises:
            RuntimeError: If overlay is not mounted
        """
        if not self.mounted:
            raise RuntimeError("OverlayFS is not mounted")

        if self.using_userns:
            return self._run_command_userns(command)
        else:
            return self._run_command_privileged(command)

    def _run_command_privileged(self, command: "str | List[str]") -> Any:
        """
        Execute a command using privileged chroot isolation.

        Used when running as root. Creates a new mount namespace per command
        and uses chroot for isolation.
        """
        env = os.environ.copy()
        env["OVERLAY_BASE_DIR"] = self.base_dir

        # Build the inner script that will run inside the chroot
        # Using base64 encoding avoids all nested quoting issues
        inner_script = self._to_shell_line(command)
        encoded_script = base64.b64encode(inner_script.encode()).decode()

        # Build the command to run inside the chroot
        # If we're running under sudo, drop privileges to the original user
        if self.orig_uid is not None and self.orig_user:
            # Use su to drop privileges to the original user
            # This ensures commands run as the user who invoked sudo, not as root
            chroot_inner_cmd = (
                f'su -s /bin/bash {shlex.quote(self.orig_user)} -c '
                f'"eval $(echo {encoded_script} | base64 -d)"'
            )
        else:
            chroot_inner_cmd = f'"eval $(echo {encoded_script} | base64 -d)"'

        # Chroot into the merged overlayfs view which contains the full root filesystem.
        # This provides defense in depth:
        # 1. The overlayfs ensures all writes go to the upper layer (protecting the real filesystem)
        # 2. The chroot confines the process to the merged view (even if they try to unmount,
        #    they can't escape because they're already chrooted into the overlay)
        # 3. Commands run as the original user, not root (when invoked via sudo)
        # Note: Submounts (like /home) are overlaid during __init__ via _bind_submounts()
        cmd_str = f"""
set -e
chroot {shlex.quote(self.merged_dir)} bash -c {chroot_inner_cmd}
"""

        result = subprocess.run(
            ["unshare", "-m", "bash", "-c", cmd_str],
            env=env,
            capture_output=True,
            text=True,
        )

        return {
            "returncode": result.returncode,
            "stdout": result.stdout.encode() if result.stdout else b"",
            "stderr": result.stderr.encode() if result.stderr else b"",
        }

    def _run_command_userns(self, command: "str | List[str]") -> Any:
        """
        Execute a command in the persistent user namespace.

        Used for unprivileged operation. Sends the command to the long-running
        shell inside the user namespace, which evaluates it in its own shell so
        that working directory and other shell state persist between calls.
        """
        if not hasattr(self, "_ns_process") or self._ns_process.poll() is not None:
            raise RuntimeError("User namespace process is not running")

        stdin = self._ns_process.stdin
        stdout = self._ns_process.stdout
        if stdin is None or stdout is None:
            raise RuntimeError("User namespace process has no command channel")

        cmd_str = self._to_shell_line(command)
        encoded_cmd = base64.b64encode(cmd_str.encode()).decode()

        # Send command to the namespace process
        stdin.write(encoded_cmd + "\n")
        stdin.flush()

        # Read the response: return code, base64 stdout, base64 stderr
        rc_line = stdout.readline()
        if not rc_line:
            raise RuntimeError("User namespace process exited unexpectedly")
        out_b64 = stdout.readline().strip()
        err_b64 = stdout.readline().strip()

        try:
            returncode = int(rc_line.strip())
        except ValueError:
            returncode = 1

        return {
            "returncode": returncode,
            "stdout": _decode_b64(out_b64),
            "stderr": _decode_b64(err_b64),
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

    def cleanup(
        self, keep_changes: bool = False, changed_files: List[ChangedFile] | None = None
    ) -> None:
        """
        Clean up all overlays and optionally copy changes to their base directories.

        Args:
            keep_changes: If True, copy changes from each upper layer to its base directory
            changed_files: Optional pre-computed list of changed files to avoid re-traversing.
                          If None and keep_changes=True, will traverse to find changes.
        """
        try:
            if keep_changes and self.mounted:
                if changed_files is not None:
                    # Use provided list to apply changes without re-traversing
                    self._apply_changes_from_list(changed_files)
                else:
                    # Fall back to traversing each overlay
                    for upper_dir, lower_dir, _ in self.overlay_mounts:
                        self._apply_overlay_changes(upper_dir, lower_dir)
        finally:
            # Terminate the user namespace process if running
            if hasattr(self, "_ns_process") and self._ns_process.poll() is None:
                try:
                    if self._ns_process.stdin is not None:
                        self._ns_process.stdin.write("EXIT\n")
                        self._ns_process.stdin.flush()
                    self._ns_process.wait(timeout=5)
                except Exception:
                    self._ns_process.kill()

            if self.mounted and not self.using_userns:
                # Unmount in reverse order (submounts first, then root)
                # Only needed for privileged mode; userns mounts die with the process
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

    def _apply_changes_from_list(self, changed_files: List[ChangedFile]) -> None:
        """
        Apply changes using a pre-computed list of ChangedFile objects.

        This avoids re-traversing the filesystem when we already have the list
        of changed files (e.g., from displaying the diff to the user).

        Args:
            changed_files: List of ChangedFile objects to apply
        """
        # Process deletions first
        for cf in changed_files:
            if cf.change_type == ChangeType.DELETED:
                try:
                    if os.path.exists(cf.lower_path):
                        os.remove(cf.lower_path)
                except (OSError, PermissionError):
                    continue

        # Process additions and modifications
        for cf in changed_files:
            if cf.change_type in (ChangeType.ADDED, ChangeType.MODIFIED):
                try:
                    # Ensure parent directory exists
                    parent_dir = os.path.dirname(cf.lower_path)
                    os.makedirs(parent_dir, exist_ok=True)
                    shutil.copy2(cf.upper_path, cf.lower_path)
                except (OSError, PermissionError):
                    continue

    def get_pwd(self) -> str:
        """
        Get the current working directory visible in the sandbox.

        The merged overlay lives under a temporary directory, so the raw path
        is translated back to the equivalent path under base_dir. That keeps
        the shell prompt showing where the user thinks they are rather than
        an internal /tmp/overlay_* path.

        Returns:
            Current working directory path
        """
        result = self.run_command(["pwd"])
        if result["returncode"] != 0:
            return self.base_dir

        pwd = str(result["stdout"].decode(errors="replace")).strip()
        if not pwd:
            return self.base_dir

        merged = self.mount_point.rstrip("/")
        if pwd == merged:
            return self.base_dir
        if pwd.startswith(merged + "/"):
            return os.path.join(self.base_dir, pwd[len(merged) + 1 :])
        # cwd is outside the overlay (the user cd'd away); report it as-is
        return pwd

    def get_changed_files(self) -> List[ChangedFile]:
        """
        Get a list of all files changed in the overlay.

        Traverses each overlay's upper directory to find added, modified,
        and deleted files. This list can be reused by cleanup() to avoid
        re-traversing the filesystem.

        Returns:
            List of ChangedFile objects representing all changes
        """
        changed_files: List[ChangedFile] = []

        for upper_dir, lower_dir, _ in self.overlay_mounts:
            changed_files.extend(self._get_changes_for_overlay(upper_dir, lower_dir))

        return changed_files

    def _get_changes_for_overlay(
        self, upper_dir: str, lower_dir: str
    ) -> List[ChangedFile]:
        """
        Get changed files for a single overlay mount.

        Args:
            upper_dir: The overlay's upper directory containing changes
            lower_dir: The original lower directory

        Returns:
            List of ChangedFile objects for this overlay
        """
        changes: List[ChangedFile] = []

        for root, dirs, files in os.walk(upper_dir):
            rel_root = os.path.relpath(root, upper_dir)
            if rel_root == ".":
                rel_root = ""

            for file_name in files:
                rel_path = os.path.join(rel_root, file_name) if rel_root else file_name
                upper_path = os.path.join(root, file_name)
                lower_path = os.path.join(lower_dir, rel_path)

                # Skip files that were hidden by us (sensitive paths)
                # These are whiteouts we created, not user deletions
                if lower_path in self.hidden_paths:
                    continue

                # Check if this is a whiteout file (indicates deletion)
                try:
                    file_stat = os.lstat(upper_path)
                    if stat.S_ISCHR(file_stat.st_mode):
                        # Whiteout file - this file was deleted
                        # Only report if the original file exists
                        if os.path.exists(lower_path):
                            changes.append(
                                ChangedFile(
                                    path=rel_path,
                                    change_type=ChangeType.DELETED,
                                    upper_path=upper_path,
                                    lower_path=lower_path,
                                )
                            )
                        continue
                except (OSError, PermissionError):
                    continue

                # Regular file - check if it's new or modified
                if os.path.exists(lower_path):
                    change_type = ChangeType.MODIFIED
                else:
                    change_type = ChangeType.ADDED

                changes.append(
                    ChangedFile(
                        path=rel_path,
                        change_type=change_type,
                        upper_path=upper_path,
                        lower_path=lower_path,
                    )
                )

        return changes

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
