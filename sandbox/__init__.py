"""
Sandbox package for secure command execution with rollback capability.

This package provides sandbox environments that isolate command execution
using overlayfs mounts, allowing for safe testing with rollback capability.
"""

from .overlayfs import Sandbox, OverlayFS

__all__ = ["Sandbox", "OverlayFS"]

