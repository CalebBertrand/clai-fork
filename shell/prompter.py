import shlex
import sys
from typing import TYPE_CHECKING, Any, List, Optional

from prompt_toolkit import PromptSession
from rich.console import Console

from shell.diff_display import display_changes

if TYPE_CHECKING:
    from llm.translator import Translator
    from sandbox import Sandbox, ChangedFile


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
        self.console = Console()
        # The translator is created lazily: it needs an OpenAI API key, and the
        # plain sandboxed shell must stay usable without one.
        self._translator: Optional["Translator"] = None

    def _get_translator(self) -> "Translator":
        """
        Return the LLM translator, constructing it on first use.

        Raises:
            RuntimeError: If the translator cannot be constructed (e.g. no API key)
        """
        if self._translator is None:
            from llm.translator import Translator

            self._translator = Translator()
        return self._translator

    def _run_command(self, command: "str | List[str]") -> Any:
        """
        Run a command in the sandbox and echo its output to the terminal.

        Args:
            command: A raw shell line, or a list of argv tokens

        Returns:
            The sandbox result dict (returncode, stdout, stderr)
        """
        result = self.sandbox.run_command(command)

        # Print stdout and stderr to terminal
        if result.get("stdout"):
            print(result["stdout"].decode(), end="")
        if result.get("stderr"):
            print(result["stderr"].decode(), end="", file=sys.stderr)

        return result

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
                        # Pass the line through verbatim so quoting, pipes and
                        # redirects behave the way they do in a normal shell.
                        self._run_command(user_input)

                except KeyboardInterrupt:
                    print(f"\nUse '{self.exit_sequence}' to exit.")
                    continue
                except EOFError:
                    break
                except Exception as e:
                    print(f"Error: {e}")

            # Get changed files once and reuse for display and cleanup
            changed_files = self.sandbox.get_changed_files()

            # Show diff before asking about changes
            display_changes(changed_files, self.console)

            # Nothing to decide about if the session made no changes
            keep_changes = bool(changed_files) and self._prompt_keep_changes()
            self.sandbox.cleanup(keep_changes, changed_files)

            if changed_files:
                print("Changes kept." if keep_changes else "Changes discarded.")

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
            translator = self._get_translator()
        except Exception as e:
            print(f"AI prompting is unavailable: {e}")
            print(
                "Set OPENAI_API_KEY (in your environment or a .env file) to use "
                "natural language prompting. Plain shell commands still work."
            )
            return

        try:
            plan = translator.to_plan(nl_prompt)

            print(f"\nExplanation: {plan.get('explain', 'No explanation provided')}")

            if plan.get("needs_clarification", False):
                question = plan.get("question", "Additional clarification needed")
                print(f"\nClarification needed: {question}")
                # Add clarification to conversation history
                translator.add_execution_context(f"Clarification needed: {question}")
                return

            command = plan.get("command", [])
            if command:
                display = shlex.join(command)
                print(f"Executing: {display}")
                result = self._run_command(command)

                execution_info = f"Command executed: {display}"
                returncode = result.get("returncode") if result else None
                if returncode is not None:
                    execution_info += f" (exit code: {returncode})"
                translator.add_execution_context(execution_info)
            else:
                print("No command generated")
                translator.add_execution_context(
                    "No command was generated from the request"
                )

        except Exception as e:
            error_msg = f"AI translation error: {e}"
            print(error_msg)
            try:
                translator.add_execution_context(error_msg)
            except Exception:
                pass

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
            except (KeyboardInterrupt, EOFError):
                # Ctrl+C or Ctrl+D at the prompt: default to the safe answer
                print()
                return False
