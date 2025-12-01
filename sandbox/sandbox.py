from abc import ABC, abstractmethod
from typing import List, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .overlayfs import ChangedFile


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
    def cleanup(
        self, keep_changes: bool = False, changed_files: List["ChangedFile"] | None = None
    ) -> None:
        """
        Clean up the sandbox and optionally preserve changes.

        Args:
            keep_changes: If True, preserve changes made during sandbox session
            changed_files: Optional pre-computed list of changed files to avoid re-traversing
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

    @abstractmethod
    def get_changed_files(self) -> List["ChangedFile"]:
        """
        Get a list of all files changed in the sandbox.

        Returns:
            List of ChangedFile objects representing all changes
        """
        pass

