from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.shortcuts import print_formatted_text

if TYPE_CHECKING:
    from ..sandbox import Sandbox


class Prompter:
    """Handles interactive prompting with sandbox isolation."""

    def __init__(self, sandbox: "Sandbox", exit_sequence: str = "/exit"):
        """
        Initialize the Prompter.

        Args:
            sandbox: The sandbox environment to use for command execution
            exit_sequence: The command to exit the interactive session
        """
        self.sandbox = sandbox
        self.exit_sequence = exit_sequence
        self.session = PromptSession()

    def run_interactive_session(self) -> None:
        """
        Run an interactive shell session in sandbox isolation.

        Raises:
            PermissionError: If not running with sufficient privileges for mount operations
            FileNotFoundError: If base_dir doesn't exist
        """

        try:
            self._show_welcome_banner()

            while True:
                try:
                    user_input = self.session.prompt("clai> ").strip()

                    if not user_input:
                        continue

                    if user_input == self.exit_sequence:
                        break

                    command_args = user_input.split()
                    self.sandbox.run_command(command_args)

                except KeyboardInterrupt:
                    print(f"\nUse '{self.exit_sequence}' to exit.")
                    continue
                except EOFError:
                    break
                except Exception as e:
                    print(f"Error: {e}")

            keep_changes = self._prompt_keep_changes()
            self.sandbox.cleanup(keep_changes)

            if keep_changes:
                print("Changes kept.")
            else:
                print("Changes discarded.")

        except Exception:
            if self.sandbox:
                self.sandbox.cleanup(keep_changes=False)
            raise

    def _show_welcome_banner(self) -> None:
        """Display a welcome banner for the CLAI shell."""
        banner = """
    ╔════════════════════════════════════════════════════════════════════════════════╗
    ║                                                                                ║
    ║    ░█████╗░██╗      █████╗ ██╗    ░██████╗██╗  ██╗███████╗░██╗     ░██╗        ║
    ║    ██╔═══╝ ██║     ██╔══██╗██║    ██╔════╝██║  ██║██╔════╝ ██║      ██║        ║
    ║    ██║     ██║     ███████║██║    ╚█████╗ ███████║█████╗   ██║      ██║        ║
    ║    ██║     ██║     ██╔══██║██║     ╚═══██╗██╔══██║██╔══╝   ██║      ██║        ║
    ║    ╚█████╗ ███████╗██║  ██║██║    ██████╔╝██║  ██║███████╗ ███████╗ ███████╗   ║
    ║     ╚════╝ ╚══════╝╚═╝  ╚═╝╚═╝    ╚═════╝ ╚═╝  ╚═╝╚══════╝ ╚══════╝ ╚══════╝   ║
    ║                                                                                ║
    ║                          Command Line AI - Sandboxed Shell                     ║
    ║                                                                                ║
    ║              Welcome to the CLAI interactive shell! Commands are               ║
    ║              executed in a secure sandbox environment.                         ║
    ║                                                                                ║
    ║              Commands:                                                         ║
    ║                • Type commands as you would in a normal shell                  ║
    ║                • Press Ctrl+C to interrupt                                     ║
    ║                • Type '/exit' to quit                                          ║
    ║                                                                                ║
    ╚════════════════════════════════════════════════════════════════════════════════╝
        """
        print(banner)

    def _prompt_keep_changes(self) -> bool:
        """
        Prompt the user whether to keep changes.

        Returns:
            True if changes should be kept, False otherwise
        """
        while True:
            try:
                response = input("Keep changes? (y/n): ").strip().lower()
                if response in ["y", "yes"]:
                    return True
                elif response in ["n", "no"]:
                    return False
                else:
                    print("Please enter 'y' or 'n'.")
            except KeyboardInterrupt:
                print("\nChanges discarded.")
                return False
