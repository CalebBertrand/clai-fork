from typing import TYPE_CHECKING

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
    
    def run_interactive_session(self) -> None:
        """
        Run an interactive shell session in sandbox isolation.
        
        Raises:
            PermissionError: If not running with sufficient privileges for mount operations
            FileNotFoundError: If base_dir doesn't exist
        """
        
        try:
            print(f"Interactive sandbox session started. Type '{self.exit_sequence}' to exit.")
            
            while True:
                try:
                    user_input = input("clai> ").strip()
                    
                    if user_input == self.exit_sequence:
                        break
                    
                    if not user_input:
                        continue
                    
                    # Split input into command arguments
                    command_args = user_input.split()
                    
                    # Execute the command in the sandbox
                    self.sandbox.run_command(command_args)
                    
                except KeyboardInterrupt:
                    print(f"\nUse '{self.exit_sequence}' to exit.")
                    continue
                except Exception as e:
                    print(f"Error: {e}")

            # Ask user if they want to keep changes
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
    
    def _prompt_keep_changes(self) -> bool:
        """
        Prompt the user whether to keep changes.
        
        Returns:
            True if changes should be kept, False otherwise
        """
        while True:
            try:
                response = input("Keep changes? (y/n): ").strip().lower()
                if response in ['y', 'yes']:
                    return True
                elif response in ['n', 'no']:
                    return False
                else:
                    print("Please enter 'y' or 'n'.")
            except KeyboardInterrupt:
                print("\nChanges discarded.")
                return False


