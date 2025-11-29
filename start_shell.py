#!/usr/bin/env python3
"""
Simple script to start the CLAI shell in the current directory.
Run with: python3 -m start_shell
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from CLAI.sandbox.overlayfs import OverlayFS
from CLAI.shell import Prompter


def main() -> None:
    """Start the interactive shell in the current directory."""
    current_dir = os.getcwd()
    
    try:
        overlayfs = OverlayFS(base_dir=current_dir)
        prompter = Prompter(sandbox=overlayfs)
        prompter.run_interactive_session()
    except PermissionError as e:
        print(f"Error: {e}")
        print("You may need to run this script with sudo privileges.")
        return


if __name__ == "__main__":
    main()