from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "work" / "supervisor_duel_eval"))
from claude_glm52_supervisor import batch as batch_module  # noqa: E402
from claude_glm52_supervisor import delegate as delegate_module  # noqa: E402
try:
    from supervisor_runtime import timeout_classification  # noqa: E402
except ModuleNotFoundError:
    timeout_classification = None


def process_running(pid: int) -> bool:
    proc = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat="],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=3,
    )
    if proc.returncode != 0:
        return False
    return "Z" not in proc.stdout


def wait_not_running(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_running(pid):
            return True
        time.sleep(0.1)
    return not process_running(pid)


def read_pid(path: Path) -> int:
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if path.exists() and path.read_text(encoding="utf-8").strip():
            return int(path.read_text(encoding="utf-8").strip())
        time.sleep(0.05)
    raise AssertionError(f"pid file was not written: {path}")


class ProcessCleanupTests(unittest.TestCase):
    def write_child_runner(self, directory: Path, *, child_new_session: bool = False) -> Path:
        runner = directory / "child_runner.py"
        start_new_session = ", start_new_session=True" if child_new_session else ""
        runner.write_text(
            textwrap.dedent(
                f"""
                #!/usr/bin/env python3
                import subprocess
                import sys
                import time
                from pathlib import Path

                marker = Path(sys.argv[1])
                child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"]{start_new_session})
                marker.write_text(str(child.pid), encoding="utf-8")
                time.sleep(30)
                """
            ).lstrip(),
            encoding="utf-8",
        )
        runner.chmod(0o755)
        return runner

    def test_timeout_kills_child_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "child.pid"
            runner = self.write_child_runner(root)

            result = delegate_module.run_process_tree([sys.executable, str(runner), str(marker)], root, timeout=0.5, grace=0.5)
            child_pid = read_pid(marker)

            self.assertTrue(result["timed_out"])
            self.assertEqual(result["returncode"], 124)
            self.assertTrue(wait_not_running(child_pid), f"child process still running: {child_pid}")

    def test_batch_timeout_kills_child_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "child.pid"
            runner = self.write_child_runner(root)

            result = asyncio.run(
                batch_module.run_command([sys.executable, str(runner), str(marker)], root, timeout=0.5)
            )
            child_pid = read_pid(marker)

            self.assertEqual(result["returncode"], 124)
            self.assertTrue(wait_not_running(child_pid), f"batch child process still running: {child_pid}")

    def test_batch_timeout_kills_new_session_child_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "child.pid"
            runner = self.write_child_runner(root, child_new_session=True)

            result = asyncio.run(
                batch_module.run_command([sys.executable, str(runner), str(marker)], root, timeout=0.5)
            )
            child_pid = read_pid(marker)

            self.assertEqual(result["returncode"], 124)
            self.assertTrue(wait_not_running(child_pid), f"batch new-session child still running: {child_pid}")
            self.assertEqual(result["cleanup"]["remaining_child_pids"], [])

    def test_keyboard_interrupt_kills_child_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "child.pid"
            runner = self.write_child_runner(root)

            def interrupt_after_child_starts() -> None:
                if marker.exists():
                    raise KeyboardInterrupt

            with self.assertRaises(KeyboardInterrupt):
                delegate_module.run_process_tree(
                    [sys.executable, str(runner), str(marker)],
                    root,
                    timeout=20,
                    monitor=interrupt_after_child_starts,
                    grace=0.5,
                )
            child_pid = read_pid(marker)
            self.assertTrue(wait_not_running(child_pid), f"child process still running: {child_pid}")

    def test_scope_violation_during_run_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "index.html").write_text("todo\n", encoding="utf-8")
            runner = root / "scope_runner.py"
            runner.write_text(
                textwrap.dedent(
                    """
                    #!/usr/bin/env python3
                    import sys
                    import time
                    from pathlib import Path

                    cwd = Path(sys.argv[sys.argv.index("--cwd") + 1])
                    (cwd / ".writer.py").write_text("out of scope\\n", encoding="utf-8")
                    (cwd / "index.html").write_text("allowed update\\n", encoding="utf-8")
                    time.sleep(30)
                    """
                ).lstrip(),
                encoding="utf-8",
            )
            runner.chmod(0o755)
            args = argparse.Namespace(
                cwd=str(workspace),
                runner=str(runner),
                role="implement",
                max_output_tokens=None,
            )

            started = time.monotonic()
            result = delegate_module.run_once(args, "Files allowed: `index.html`", 10, allowed_changes=["index.html"])

            self.assertLess(time.monotonic() - started, 5)
            self.assertEqual(result["termination_reason"], "scope_violation_during_run")
            self.assertTrue(result["scope_violation_during_run"])
            self.assertIn(".writer.py", result["changed_files"])
            self.assertEqual(result["payload"]["is_error"], True)


class TimeoutClassificationTests(unittest.TestCase):
    @unittest.skipIf(timeout_classification is None, "supervisor duel runtime is not in this public snapshot")
    def test_timeout_classifications_are_distinct(self) -> None:
        assert timeout_classification is not None
        self.assertEqual(timeout_classification(True, [], True, False), "timeout_no_changes")
        self.assertEqual(timeout_classification(True, [".writer.py"], False, False), "timeout_scope_violation")
        self.assertEqual(timeout_classification(True, ["index.html"], True, True), "timeout_valid_artifacts")
        self.assertEqual(timeout_classification(True, ["index.html"], True, False), "timeout_validation_failed")
        self.assertIsNone(timeout_classification(False, ["index.html"], True, True))


if __name__ == "__main__":
    unittest.main()
