import sys
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession

from llm.translator import Translator

if TYPE_CHECKING:
    from CLAI.sandbox import Sandbox


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
        self.session: PromptSession = PromptSession()
        self.translator = Translator()

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
                    try:
                        current_dir = self.sandbox.get_pwd()
                        prompt_text = f"clai:{current_dir}> "
                    except Exception:
                        prompt_text = "clai> "

                    user_input = self.session.prompt(prompt_text).strip()

                    if not user_input:
                        continue

                    if user_input == self.exit_sequence:
                        break

                    if user_input.startswith("/") and user_input != self.exit_sequence:
                        self._handle_ai_prompt(user_input[1:])  # Remove the leading /
                    else:
                        command_args = user_input.split()
                        result = self.sandbox.run_command(command_args)

                        # Print stdout and stderr to terminal
                        if result.get("stdout"):
                            print(result["stdout"].decode(), end="")
                        if result.get("stderr"):
                            print(result["stderr"].decode(), end="", file=sys.stderr)

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

    def _handle_ai_prompt(self, nl_prompt: str) -> None:
        """
        Handle natural language prompting mode.

        Args:
            nl_prompt: The natural language prompt from the user
        """
        try:
            plan = self.translator.to_plan(nl_prompt)

            print(f"\nExplanation: {plan.get('explain', 'No explanation provided')}")

            if plan.get("needs_clarification", False):
                question = plan.get("question", "Additional clarification needed")
                print(f"\nClarification needed: {question}")
                # Add clarification to conversation history
                self.translator.add_execution_context(
                    f"Clarification needed: {question}"
                )
                return

            command = plan.get("command", [])
            if command:
                print(f"Executing: {' '.join(command)}")
                result = self.sandbox.run_command(command)
                # Add execution result to conversation history
                execution_info = f"Command executed: {' '.join(command)}"
                if hasattr(result, "returncode"):
                    execution_info += f" (exit code: {result.returncode})"
                self.translator.add_execution_context(execution_info)
            else:
                print("No command generated")
                self.translator.add_execution_context(
                    "No command was generated from the request"
                )

        except Exception as e:
            error_msg = f"AI translation error: {e}"
            print(error_msg)
            self.translator.add_execution_context(error_msg)

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
    ║                • Start with '/' for natural language AI prompting              ║
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
