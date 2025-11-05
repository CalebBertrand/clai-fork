import subprocess
from abc import ABC, abstractmethod
from typing import List

class Sandbox(ABC):
    """Abstract base class for sandbox environments that isolate command execution."""
    
    @abstractmethod
    def run_command(self, command: List[str]) -> subprocess.CompletedProcess:
        """
        Execute a command in the sandbox environment.
        
        Args:
            command: List of command arguments to execute
            
        Returns:
            CompletedProcess object from subprocess.run
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

