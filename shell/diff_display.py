"""
Diff display utility for showing file changes with rich formatting.
"""

import difflib
from typing import List

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from CLAI.sandbox import ChangedFile, ChangeType


def display_changes(changed_files: List[ChangedFile], console: Console | None = None) -> None:
    """
    Display a formatted diff of all changed files.

    Args:
        changed_files: List of ChangedFile objects from the sandbox
        console: Optional Rich console instance (creates one if not provided)
    """
    if console is None:
        console = Console()

    if not changed_files:
        console.print("[dim]No files were changed during this session.[/dim]")
        return

    console.print()
    console.print(
        Panel.fit(
            "[bold]Changes Summary[/bold]",
            border_style="blue",
        )
    )
    console.print()

    # Group changes by type for summary
    added = [f for f in changed_files if f.change_type == ChangeType.ADDED]
    modified = [f for f in changed_files if f.change_type == ChangeType.MODIFIED]
    deleted = [f for f in changed_files if f.change_type == ChangeType.DELETED]

    # Print summary
    if added:
        console.print(f"[green]  {len(added)} file(s) added[/green]")
    if modified:
        console.print(f"[yellow]  {len(modified)} file(s) modified[/yellow]")
    if deleted:
        console.print(f"[red]  {len(deleted)} file(s) deleted[/red]")

    console.print()

    # Display each file's changes
    for changed_file in changed_files:
        _display_file_diff(changed_file, console)


def _display_file_diff(changed_file: ChangedFile, console: Console) -> None:
    """
    Display the diff for a single file.

    Args:
        changed_file: The ChangedFile object to display
        console: Rich console instance
    """
    path = changed_file.path
    change_type = changed_file.change_type

    # Create header based on change type
    if change_type == ChangeType.ADDED:
        header = Text(f"+ {path}", style="bold green")
        border_style = "green"
    elif change_type == ChangeType.DELETED:
        header = Text(f"- {path}", style="bold red")
        border_style = "red"
    else:  # MODIFIED
        header = Text(f"~ {path}", style="bold yellow")
        border_style = "yellow"

    console.print(Panel(header, border_style=border_style, expand=False))

    # Generate and display diff content
    try:
        diff_lines = _generate_diff(changed_file)
        if diff_lines:
            _print_colored_diff(diff_lines, console)
        else:
            if change_type == ChangeType.DELETED:
                console.print("[dim]  (file deleted)[/dim]")
            elif change_type == ChangeType.ADDED:
                console.print("[dim]  (new file)[/dim]")
            else:
                console.print("[dim]  (binary or unreadable file)[/dim]")
    except Exception as e:
        console.print(f"[dim]  (could not generate diff: {e})[/dim]")

    console.print()


def _generate_diff(changed_file: ChangedFile) -> List[str]:
    """
    Generate unified diff lines for a changed file.

    Args:
        changed_file: The ChangedFile object

    Returns:
        List of diff lines (without the header lines)
    """
    change_type = changed_file.change_type

    # Read original content (for modified and deleted files)
    original_lines: List[str] = []
    if change_type in (ChangeType.MODIFIED, ChangeType.DELETED):
        try:
            with open(changed_file.lower_path, "r", encoding="utf-8", errors="replace") as f:
                original_lines = f.readlines()
        except (OSError, IOError):
            return []

    # Read new content (for modified and added files)
    new_lines: List[str] = []
    if change_type in (ChangeType.MODIFIED, ChangeType.ADDED):
        try:
            with open(changed_file.upper_path, "r", encoding="utf-8", errors="replace") as f:
                new_lines = f.readlines()
        except (OSError, IOError):
            return []

    # Generate unified diff
    diff = difflib.unified_diff(
        original_lines,
        new_lines,
        fromfile=f"a/{changed_file.path}",
        tofile=f"b/{changed_file.path}",
        lineterm="",
    )

    # Skip the header lines (---, +++, @@) but keep the content
    diff_lines = list(diff)
    return diff_lines


def _print_colored_diff(diff_lines: List[str], console: Console) -> None:
    """
    Print diff lines with appropriate coloring.

    Args:
        diff_lines: List of diff lines
        console: Rich console instance
    """
    for line in diff_lines:
        # Remove trailing newline for display
        line = line.rstrip("\n")

        if line.startswith("+++") or line.startswith("---"):
            # File header lines
            console.print(f"[bold]{line}[/bold]")
        elif line.startswith("@@"):
            # Hunk header
            console.print(f"[cyan]{line}[/cyan]")
        elif line.startswith("+"):
            # Added line
            console.print(f"[green]{line}[/green]")
        elif line.startswith("-"):
            # Removed line
            console.print(f"[red]{line}[/red]")
        else:
            # Context line
            console.print(f"[dim]{line}[/dim]")
