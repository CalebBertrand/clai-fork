#!/usr/bin/env python3
"""
Start the CLAI shell, sandboxing a directory.

Run from the repo root with:
    python3 start_shell.py [directory]

or use ./start.sh, which activates the venv and sandboxes your current
working directory.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sandbox.overlayfs import OverlayFS
from shell import Prompter


def main() -> None:
    """Start the interactive shell in the requested directory."""
    base_dir = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

    try:
        overlayfs = OverlayFS(base_dir=base_dir)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except PermissionError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    prompter = Prompter(sandbox=overlayfs)
    prompter.run_interactive_session()


if __name__ == "__main__":
    main()
