#!/usr/bin/env python3
"""
Test script for the overlayfs runner functionality.
run `python3 -m sandbox.test_runner` from the repo root
"""

import os
from sandbox.overlayfs import OverlayFS
from shell import Prompter


def main() -> None:
    """Example usage of the interactive overlayfs runner."""

    test_dir = os.getcwd()
    # os.makedirs(test_dir, exist_ok=True)

    test_file = os.path.join(test_dir, "test.txt")
    with open(test_file, "w") as f:
        f.write("Original content\n")

    # print(f"Original content: {open(test_file).read()}")
    # print("\n--- Starting interactive overlay session ---")

    try:
        overlayfs = OverlayFS(base_dir=test_dir)
        prompter = Prompter(sandbox=overlayfs)
        prompter.run_interactive_session()
    except PermissionError as e:
        print(f"Error: {e}")
        return

    # print(f"\nFinal content: {open(test_file).read()}")


if __name__ == "__main__":
    main()
