#!/usr/bin/env python3
"""Test script to verify the overlayfs sandbox properly hides sensitive directories."""

import os
import sys
from sandbox.overlayfs import OverlayFS


def test_sensitive_directory_hiding():
    """Test that sensitive directories like ~/.ssh are properly hidden."""

    # Get the user's home directory
    home_dir = os.path.expanduser("~")

    print(f"Testing overlayfs sandbox with base_dir: {home_dir}")
    print("=" * 60)

    # Create the overlayfs sandbox
    try:
        sandbox = OverlayFS(base_dir=home_dir)
        print("✓ Sandbox created successfully")
    except PermissionError as e:
        print(f"✗ Failed to create sandbox: {e}")
        print(
            "  This test requires root privileges. Run with: sudo python3 test_sandbox_security.py"
        )
        return False

    try:
        # Test 1: Try to list ~/.ssh directory
        print("\nTest 1: Attempting to list ~/.ssh directory...")
        result = sandbox.run_command(["ls", "-la", f"{home_dir}/.ssh"])
        stdout = result["stdout"].decode() if result["stdout"] else ""
        stderr = result["stderr"].decode() if result["stderr"] else ""

        if result["returncode"] != 0 or "total 0" in stderr:
            print("✓ .ssh directory is hidden or empty as expected")
        else:
            print("✗ .ssh directory is NOT hidden!")
            print(f"  stdout: {stdout}")
            print(f"  stderr: {stderr}")
            return False

        # Test 2: Try to cd into ~/.ssh
        print("\nTest 2: Attempting to cd into ~/.ssh...")
        result = sandbox.run_command(["cd", f"{home_dir}/.ssh", "&&", "pwd"])
        stderr = result["stderr"].decode() if result["stderr"] else ""

        if "No such file or directory" in stderr or "cannot access" in stderr:
            print("✓ Cannot cd into .ssh directory (blocked as expected)")
        else:
            print("✗ Was able to cd into .ssh directory!")
            return False

        # Test 3: Try to cd via shell expansion
        print("\nTest 3: Attempting to cd ~/.ssh using shell expansion...")
        result = sandbox.run_command(["bash", "-c", "cd ~/.ssh && pwd"])
        stderr = result["stderr"].decode() if result["stderr"] else ""

        if result["returncode"] != 0:
            print("✓ Shell expansion cd to ~/.ssh blocked (failed as expected)")
        else:
            print("✗ Shell expansion allowed access to .ssh!")
            return False

        # Test 4: Try to access via absolute path
        print("\nTest 4: Attempting to test -d on absolute path to .ssh...")
        result = sandbox.run_command(["test", "-d", f"{home_dir}/.ssh"])

        if result["returncode"] != 0:
            print("✓ test -d reports .ssh doesn't exist (correct)")
        else:
            print("✗ test -d reports .ssh exists (should be hidden)!")
            return False

        # Test 5: Try escape attempt - umount
        print("\nTest 5: Attempting escape via umount (should fail due to chroot)...")
        result = sandbox.run_command(
            ["bash", "-c", f"umount {home_dir} && cd ~/.ssh && ls"]
        )
        stderr = result["stderr"].decode() if result["stderr"] else ""

        if (
            "not mounted" in stderr
            or "not found" in stderr
            or "cannot access" in stderr
            or result["returncode"] != 0
        ):
            print("✓ Escape attempt via umount failed (chroot protection working)")
        else:
            print("✗ Escape attempt may have succeeded!")
            stdout = result["stdout"].decode() if result["stdout"] else ""
            print(f"  stdout: {stdout}")
            print(f"  stderr: {stderr}")
            return False

        # Test 6: Verify normal files are still accessible
        print("\nTest 6: Verifying normal files are still accessible...")
        result = sandbox.run_command(["ls", home_dir])

        if result["returncode"] == 0:
            print("✓ Normal directory listing works")
        else:
            print("✗ Cannot access normal files!")
            return False

        print("\n" + "=" * 60)
        print("ALL TESTS PASSED! The sandbox properly hides sensitive directories.")
        return True

    finally:
        # Cleanup
        print("\nCleaning up sandbox...")
        sandbox.cleanup(keep_changes=False)
        print("✓ Cleanup complete")


if __name__ == "__main__":
    # Check if running as root
    if os.geteuid() != 0:
        print("Warning: This script requires root privileges for overlayfs and chroot.")
        print("Please run with: sudo python3 test_sandbox_security.py")
        sys.exit(1)

    success = test_sensitive_directory_hiding()
    sys.exit(0 if success else 1)
