#!/usr/bin/env python3
"""
Functional tests for the overlayfs sandbox and the shell plumbing.

These run unprivileged, via user namespaces, so no sudo is needed:
    python3 test_sandbox.py

For the privileged sensitive-path hiding tests, see test_sandbox_security.py.
"""

import os
import shutil
import sys
import tempfile
from typing import Callable, List, Tuple

from sandbox.overlayfs import ChangeType, OverlayFS


class Failure(AssertionError):
    """Raised when a check inside a test does not hold."""


def check(condition: bool, message: str) -> None:
    """Assert a condition, raising Failure with a readable message."""
    if not condition:
        raise Failure(message)


def make_base() -> str:
    """Create a throwaway directory tree for a single test to sandbox."""
    base = tempfile.mkdtemp(prefix="clai_test_")
    with open(os.path.join(base, "notes.txt"), "w") as f:
        f.write("alpha\nbeta\ngamma\n")
    with open(os.path.join(base, "doomed.txt"), "w") as f:
        f.write("delete me\n")
    os.makedirs(os.path.join(base, "subdir"))
    with open(os.path.join(base, "subdir", "inner.txt"), "w") as f:
        f.write("nested\n")
    return base


def read(path: str) -> str:
    """Read a file as text."""
    with open(path) as f:
        return f.read()


def test_shell_state_persists_between_commands(base: str) -> None:
    """cd and shell variables set by one command are visible to the next."""
    sb = OverlayFS(base_dir=base)
    try:
        sb.run_command("cd subdir")
        check(
            sb.run_command("pwd")["stdout"] == f"{base}/subdir\n".encode(),
            "cd did not persist to the following command",
        )
        check(
            sb.run_command("cat inner.txt")["stdout"] == b"nested\n",
            "relative path did not resolve against the persisted cwd",
        )
        sb.run_command("cd ..")
        sb.run_command("MYVAR=kept")
        check(
            sb.run_command("echo $MYVAR")["stdout"] == b"kept\n",
            "shell variable did not persist between commands",
        )
    finally:
        sb.cleanup(keep_changes=False)


def test_paths_look_real_inside_the_sandbox(base: str) -> None:
    """The sandbox must not leak its internal /tmp/overlay_* path."""
    sb = OverlayFS(base_dir=base)
    try:
        pwd = sb.run_command("pwd")["stdout"].decode().strip()
        check(pwd == base, f"pwd reported {pwd!r}, expected {base!r}")
        check(sb.get_pwd() == base, f"get_pwd() reported {sb.get_pwd()!r}")
    finally:
        sb.cleanup(keep_changes=False)


def test_quoting_and_shell_grammar(base: str) -> None:
    """Quotes, pipes and redirects survive the trip into the sandbox."""
    sb = OverlayFS(base_dir=base)
    try:
        r = sb.run_command("sed -i 's/beta/BETA TWO/' notes.txt")
        check(r["returncode"] == 0, f"quoted sed failed: {r['stderr']!r}")
        check(
            sb.run_command("cat notes.txt")["stdout"] == b"alpha\nBETA TWO\ngamma\n",
            "quoted sed expression was mangled",
        )

        sb.run_command("echo 'made this' > made.txt")
        check(
            sb.run_command("cat made.txt")["stdout"] == b"made this\n",
            "redirect did not write the expected content",
        )

        check(
            sb.run_command("cat notes.txt | wc -l")["stdout"] == b"3\n",
            "pipe did not work",
        )

        # An argv list is quoted rather than re-split, so a spaced argument
        # stays a single argument.
        r = sb.run_command(["grep", "-c", "BETA TWO", "notes.txt"])
        check(r["returncode"] == 0 and r["stdout"] == b"1\n", f"argv form failed: {r}")
    finally:
        sb.cleanup(keep_changes=False)


def test_streams_and_exit_codes(base: str) -> None:
    """stdout, stderr and the exit code come back separately and accurately."""
    sb = OverlayFS(base_dir=base)
    try:
        r = sb.run_command("cat no_such_file.txt")
        check(r["returncode"] != 0, "missing file should give a non-zero exit code")
        check(r["stdout"] == b"", f"stdout should be empty, got {r['stdout']!r}")
        check(b"No such file" in r["stderr"], f"stderr missing: {r['stderr']!r}")

        r = sb.run_command("true")
        check(r["returncode"] == 0, "true should exit 0")
        check(r["stdout"] == b"", f"true should print nothing, got {r['stdout']!r}")

        r = sb.run_command("echo out; echo err >&2")
        check(r["stdout"] == b"out\n", f"stdout wrong: {r['stdout']!r}")
        check(r["stderr"] == b"err\n", f"stderr wrong: {r['stderr']!r}")
    finally:
        sb.cleanup(keep_changes=False)


def test_changes_are_detected(base: str) -> None:
    """Adds, modifications and deletes are all reported."""
    sb = OverlayFS(base_dir=base)
    try:
        sb.run_command("echo new > added.txt")
        sb.run_command("echo more >> notes.txt")
        sb.run_command("rm doomed.txt")
        sb.run_command("mkdir -p deep/dir && echo z > deep/dir/z.txt")

        found = {(c.change_type.value, c.path) for c in sb.get_changed_files()}
        for expected in [
            (ChangeType.ADDED.value, "added.txt"),
            (ChangeType.MODIFIED.value, "notes.txt"),
            (ChangeType.DELETED.value, "doomed.txt"),
            (ChangeType.ADDED.value, "deep/dir/z.txt"),
        ]:
            check(expected in found, f"missing change {expected}; got {sorted(found)}")
    finally:
        sb.cleanup(keep_changes=False)


def test_discard_leaves_base_untouched(base: str) -> None:
    """Discarding rolls the base directory back to exactly how it started."""
    sb = OverlayFS(base_dir=base)
    try:
        sb.run_command("echo new > added.txt")
        sb.run_command("echo more >> notes.txt")
        sb.run_command("rm doomed.txt")
    finally:
        sb.cleanup(keep_changes=False)

    check(not os.path.exists(f"{base}/added.txt"), "added file leaked into the base dir")
    check(os.path.exists(f"{base}/doomed.txt"), "deleted file was not restored")
    check(read(f"{base}/notes.txt") == "alpha\nbeta\ngamma\n", "base file was modified")


def test_keep_applies_changes(base: str) -> None:
    """Keeping writes adds, modifications and deletes through to the base dir."""
    sb = OverlayFS(base_dir=base)
    try:
        sb.run_command("echo new > added.txt")
        sb.run_command("echo more >> notes.txt")
        sb.run_command("rm doomed.txt")
        sb.run_command("mkdir -p deep/dir && echo z > deep/dir/z.txt")
        changed = sb.get_changed_files()
    finally:
        sb.cleanup(keep_changes=True, changed_files=changed)

    check(read(f"{base}/added.txt") == "new\n", "added file was not applied")
    check(read(f"{base}/notes.txt").endswith("more\n"), "modification was not applied")
    check(not os.path.exists(f"{base}/doomed.txt"), "deletion was not applied")
    check(read(f"{base}/deep/dir/z.txt") == "z\n", "nested add was not applied")


def test_temp_dirs_are_removed(base: str) -> None:
    """cleanup() must not leave its scratch directory behind."""
    sb = OverlayFS(base_dir=base)
    temp_root = sb.temp_root
    check(os.path.isdir(temp_root), "temp root was not created")
    sb.run_command("echo x > x.txt")
    sb.cleanup(keep_changes=False)
    check(not os.path.exists(temp_root), f"temp root {temp_root} was left behind")


def test_diff_display_handles_markup(base: str) -> None:
    """File content containing square brackets must not be eaten as Rich markup."""
    import io

    from rich.console import Console

    from shell.diff_display import display_changes

    sb = OverlayFS(base_dir=base)
    try:
        sb.run_command("echo '[bold]not markup[/bold]' > bracket.txt")
        changed = sb.get_changed_files()
        buf = io.StringIO()
        display_changes(changed, Console(file=buf, width=100, no_color=True))
        out = buf.getvalue()
        check("[bold]not markup[/bold]" in out, f"markup was swallowed:\n{out}")
    finally:
        sb.cleanup(keep_changes=False)


def test_shell_starts_without_an_api_key() -> None:
    """The sandboxed shell must be usable with no OPENAI_API_KEY set."""
    from shell import Prompter

    saved = {k: os.environ.pop(k, None) for k in ("OPENAI_API_KEY", "OPENAI_ADMIN_KEY")}
    try:
        base = make_base()
        sb = OverlayFS(base_dir=base)
        try:
            # Constructing the Prompter must not require credentials.
            Prompter(sandbox=sb)
        finally:
            sb.cleanup(keep_changes=False)
            shutil.rmtree(base, ignore_errors=True)
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


TESTS: List[Tuple[str, Callable[..., None]]] = [
    ("shell state persists between commands", test_shell_state_persists_between_commands),
    ("paths look real inside the sandbox", test_paths_look_real_inside_the_sandbox),
    ("quoting and shell grammar", test_quoting_and_shell_grammar),
    ("streams and exit codes", test_streams_and_exit_codes),
    ("changes are detected", test_changes_are_detected),
    ("discard leaves base untouched", test_discard_leaves_base_untouched),
    ("keep applies changes", test_keep_applies_changes),
    ("temp dirs are removed", test_temp_dirs_are_removed),
    ("diff display handles markup", test_diff_display_handles_markup),
    ("shell starts without an api key", test_shell_starts_without_an_api_key),
]


def main() -> int:
    """Run every test, reporting a pass/fail line for each."""
    passed = 0
    failed: List[str] = []

    for name, fn in TESTS:
        needs_base = fn.__code__.co_argcount > 0
        base = make_base() if needs_base else None
        try:
            fn(base) if base else fn()
        except Exception as e:
            failed.append(name)
            print(f"FAIL  {name}\n        {type(e).__name__}: {e}")
        else:
            passed += 1
            print(f"ok    {name}")
        finally:
            if base:
                shutil.rmtree(base, ignore_errors=True)

    print(f"\n{passed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
