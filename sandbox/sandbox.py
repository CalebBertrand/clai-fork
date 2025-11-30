import subprocess
from abc import ABC, abstractmethod
from typing import List, Any

class Sandbox(ABC):
    """Abstract base class for sandbox environments that isolate command execution."""
    
    @abstractmethod
    def run_command(self, command: List[str]) -> Any:
        """
        Execute a command in the sandbox environment.
        
        Args:
            command: List of command arguments to execute
            
        Returns:
            Result object with returncode, stdout, stderr
        """
        pass
    
    @abstractmethod
    def cleanup(self, keep_changes: bool = False) -> None:
        """
        Clean up the sandbox and optionally preserve changes.
        
        Args:
            keep_changes: If True, preserve changes made during sandbox session
        """
        pass
    
    @abstractmethod
    def get_pwd(self) -> str:
        """
        Get the current working directory visible in the sandbox.
        
        Returns:
            Current working directory path
        """
        pass

