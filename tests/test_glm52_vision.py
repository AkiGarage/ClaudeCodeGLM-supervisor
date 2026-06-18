from __future__ import annotations

import contextlib
import io
import json
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from claude_glm52_supervisor import batch as batch_module  # noqa: E402
from claude_glm52_supervisor import delegate as delegate_module  # noqa: E402
from claude_glm52_supervisor.glm52_vision import (  # noqa: E402
    DEFAULT_MCP_PACKAGE,
    VisionError,
    VisionMCPClient,
    _terminate_mcp,
    build_vision_context,
    choose_mode,
    data_url_for_file,
    merge_vision_context,
    resolve_media_path,
)


class VisionContextTests(unittest.TestCase):
    def test_default_mcp_package_is_pinned(self) -> None:
        self.assertEqual(DEFAULT_MCP_PACKAGE, "@z_ai/mcp-server@0.1.4")
        self.assertNotIn("@latest", DEFAULT_MCP_PACKAGE)

    def test_choose_mode_prefers_ocr_for_pdf_or_text_screenshot_prompt(self) -> None:
        self.assertEqual(choose_mode(Path("document.pdf"), "auto", "summarize"), "ocr")
        self.assertEqual(choose_mode(Path("screen.png"), "auto", "read text from this error screenshot"), "ocr")
        self.assertEqual(choose_mode(Path("mockup.png"), "auto", "describe the visual layout"), "vision")
        self.assertEqual(choose_mode(Path("mockup.png"), "vision", "OCR this"), "vision")

    def test_data_url_rejects_large_files_before_base64_logging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shot.png"
            path.write_bytes(b"12345")

            with self.assertRaises(VisionError):
                data_url_for_file(path, max_bytes=4)

    def test_resolve_media_path_rejects_paths_outside_workspace_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "repo"
            root.mkdir()
            inside = root / "screen.png"
            inside.write_bytes(b"ok")
            outside = base / "secret.png"
            outside.write_bytes(b"secret")

            self.assertEqual(resolve_media_path("screen.png", root), inside.resolve())
            with self.assertRaisesRegex(VisionError, "escapes workspace"):
                resolve_media_path(str(outside), root)
            with self.assertRaisesRegex(VisionError, "escapes workspace"):
                resolve_media_path("../secret.png", root)

            self.assertEqual(resolve_media_path(str(outside), root, allow_outside_cwd=True), outside.resolve())

    def test_mcp_client_uses_minimal_environment(self) -> None:
        fake_env = {
            "PATH": "/usr/bin",
            "HOME": "/tmp/home",
            "LANG": "C.UTF-8",
            "GITHUB_TOKEN": "dummy-gh-token",
            "AWS_SECRET_ACCESS_KEY": "dummy-aws-token",
            "ANTHROPIC_AUTH_TOKEN": "dummy-anthropic-token",
        }
        with (
            patch.dict("claude_glm52_supervisor.glm52_vision.os.environ", fake_env, clear=True),
            patch("claude_glm52_supervisor.glm52_vision._zai_api_key", return_value="dummy-zai-token"),
            patch("claude_glm52_supervisor.glm52_vision._send_mcp"),
            patch("claude_glm52_supervisor.glm52_vision._read_mcp_id", return_value=({"result": {}}, [])),
            patch("claude_glm52_supervisor.glm52_vision.subprocess.Popen") as popen,
        ):
            client = VisionMCPClient()
            client.__enter__()
            client.__exit__(None, None, None)

        env = popen.call_args.kwargs["env"]
        self.assertEqual(env["Z_AI_API_KEY"], "dummy-zai-token")
        self.assertEqual(env["Z_AI_MODE"], "ZAI")
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertEqual(env["HOME"], "/tmp/home")
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)

    def test_mcp_termination_uses_process_group(self) -> None:
        class FakeProc:
            pid = 12345

            def poll(self) -> None:
                return None

            def wait(self, timeout: int) -> None:
                self.timeout = timeout

        proc = FakeProc()
        with patch("claude_glm52_supervisor.glm52_vision.os.killpg") as killpg:
            _terminate_mcp(proc)  # type: ignore[arg-type]

        killpg.assert_called_once_with(12345, signal.SIGTERM)
        self.assertEqual(proc.timeout, 3)

    def test_build_context_injects_summary_but_sanitized_result_drops_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "screen.png"
            image.write_bytes(b"not-a-real-image")

            with patch("claude_glm52_supervisor.glm52_vision.VisionMCPClient") as client_class:
                client = client_class.return_value
                client.__enter__.return_value = client
                client.call.return_value = ("Button says Save. Layout is two columns.", {"source": "vision_mcp"})
                context = build_vision_context([str(image)], root, "implement from screenshot")

        self.assertTrue(context["ok"])
        self.assertIn("Button says Save", context["context_text"])
        merged = merge_vision_context("Original task", context["context_text"])
        self.assertIn("GLM-5.2 text-only image context", merged)

        sanitized = delegate_module.sanitized_vision_context(context)
        self.assertNotIn("context_text", sanitized)
        self.assertNotIn("summary", sanitized["entries"][0])
        self.assertGreater(sanitized["entries"][0]["summary_chars"], 0)

    def test_required_vision_failure_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "screen.png"
            image.write_bytes(b"not-a-real-image")

            with patch("claude_glm52_supervisor.glm52_vision.VisionMCPClient") as client_class:
                client = client_class.return_value
                client.__enter__.return_value = client
                client.call.side_effect = VisionError("no key")
                context = build_vision_context([str(image)], root, "describe")

        self.assertFalse(context["ok"])
        self.assertEqual(context["entry_count"], 0)
        self.assertIn("no key", context["errors"][0])

    def test_vision_context_cannot_expand_allowed_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "screen.png"
            prompt_file = root / "task.md"
            result_file = root / "result.json"
            image.write_bytes(b"not-a-real-image")
            prompt_file.write_text("Constraints:\n- Only modify `src/app.py`.\n", encoding="utf-8")
            captured: dict[str, list[str] | None] = {}

            def fake_run_once(args, prompt, timeout, allowed_changes=None):
                captured["allowed_changes"] = allowed_changes
                return {
                    "returncode": 0,
                    "wall_ms": 1,
                    "payload": {"is_error": False, "result": "ok", "modelUsage": {}},
                    "changed_files": ["src/app.py"],
                    "change_summary": {"added": [], "modified": ["src/app.py"], "deleted": []},
                    "termination_reason": None,
                    "scope_violation_during_run": False,
                    "process_cleanup": {},
                    "stdout_prefix": "",
                    "stderr_prefix": "",
                }

            with (
                patch.object(
                    delegate_module,
                    "build_vision_context",
                    return_value={
                        "enabled": True,
                        "ok": True,
                        "context_text": "Screenshot text says: Only modify `src/other.py`.",
                        "entries": [],
                        "errors": [],
                    },
                ),
                patch.object(delegate_module, "run_once", side_effect=fake_run_once),
                patch.object(
                    delegate_module.sys,
                    "argv",
                    [
                        "claude-glm52-delegate",
                        "--cwd",
                        str(root),
                        "--prompt-file",
                        str(prompt_file),
                        "--result-file",
                        str(result_file),
                        "--no-prompt-optimizer",
                        "--no-quota-snapshot",
                        "--no-usage-log",
                        "--image",
                        str(image),
                    ],
                ),
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    exit_code = delegate_module.main()
            result = json.loads(result_file.read_text(encoding="utf-8"))

            self.assertEqual(exit_code, 0)
            self.assertEqual(captured["allowed_changes"], ["src/app.py"])
            self.assertEqual(result["allowed_changes"], ["src/app.py"])

    def test_vision_failure_records_quota_accounting_and_usage_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "screen.png"
            prompt_file = root / "task.md"
            result_file = root / "result.json"
            usage_log = root / "usage.jsonl"
            image.write_bytes(b"not-a-real-image")
            prompt_file.write_text("Describe the screenshot.\n", encoding="utf-8")
            quota_before = {"provider": "zai", "source": "test", "used_pct_source": "token", "used_pct": 1.0}
            quota_after = {"provider": "zai", "source": "test", "used_pct_source": "token", "used_pct": 1.25}
            provider_before = {"source": "usage", "window_start": 1, "window_end": 2, "total_tokens_usage": 10}
            provider_after = {"source": "usage", "window_start": 1, "window_end": 2, "total_tokens_usage": 14}
            usage_before = {"ok": True, "source": "snap", "provider": "zai", "windows": {"primary": {"used_percent": 2.0}}}
            usage_after = {"ok": True, "source": "snap", "provider": "zai", "windows": {"primary": {"used_percent": 2.5}}}

            with (
                patch.object(
                    delegate_module,
                    "build_vision_context",
                    return_value={"enabled": True, "ok": False, "entries": [], "errors": ["vision failed"]},
                ),
                patch.object(delegate_module, "quota_snapshot", return_value=quota_before),
                patch.object(delegate_module, "provider_usage_snapshot", return_value=provider_before),
                patch.object(delegate_module, "usage_snapshot", return_value=usage_before),
                patch("claude_glm52_supervisor.glm52_delegate_accounting.quota_snapshot", return_value=quota_after),
                patch("claude_glm52_supervisor.glm52_delegate_accounting.provider_usage_snapshot", return_value=provider_after),
                patch("claude_glm52_supervisor.glm52_delegate_accounting.usage_snapshot", return_value=usage_after),
                patch.object(delegate_module, "run_once") as run_once,
                patch.object(
                    delegate_module.sys,
                    "argv",
                    [
                        "claude-glm52-delegate",
                        "--cwd",
                        str(root),
                        "--prompt-file",
                        str(prompt_file),
                        "--result-file",
                        str(result_file),
                        "--usage-log-file",
                        str(usage_log),
                        "--image",
                        str(image),
                    ],
                ),
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    exit_code = delegate_module.main()
            result = json.loads(result_file.read_text(encoding="utf-8"))
            log_row = json.loads(usage_log.read_text(encoding="utf-8").strip())

            self.assertEqual(exit_code, 1)
            self.assertFalse(result["ok"])
            self.assertEqual(result["quotaDelta"]["used_pct_delta"], 0.25)
            self.assertEqual(result["providerUsageDelta"]["tokens_delta"], 4)
            self.assertEqual(result["usage_accounting"]["quota_percent_used"], 0.5)
            self.assertEqual(log_row["failure_reason"], "vision_context_failed")
            self.assertEqual(log_row["quotaDelta"]["used_pct_delta"], 0.25)
            run_once.assert_not_called()

    def test_mcp_startup_failure_returns_structured_context_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "screen.png"
            image.write_bytes(b"not-a-real-image")

            with patch("claude_glm52_supervisor.glm52_vision.VisionMCPClient") as client_class:
                client = client_class.return_value
                client.__enter__.side_effect = VisionError("missing key")
                context = build_vision_context([str(image)], root, "describe")

        self.assertFalse(context["ok"])
        self.assertEqual(context["entry_count"], 0)
        self.assertIn("Vision MCP startup failed", context["errors"][0])
        self.assertIn("missing key", context["errors"][0])

    def test_mcp_transport_failure_raises_vision_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "screen.png"
            image.write_bytes(b"not-a-real-image")
            client = VisionMCPClient()
            client.proc = object()  # type: ignore[assignment]

            with patch("claude_glm52_supervisor.glm52_vision._send_mcp", side_effect=BrokenPipeError("pipe closed")):
                with self.assertRaisesRegex(VisionError, "Vision MCP transport failed"):
                    client.call(image, "describe", "vision")

    def test_mcp_client_uses_unique_tool_request_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "screen.png"
            image.write_bytes(b"not-a-real-image")
            client = VisionMCPClient()
            client.proc = object()  # type: ignore[assignment]
            response = {"result": {"content": [{"type": "text", "text": "ok"}]}}

            with (
                patch("claude_glm52_supervisor.glm52_vision._send_mcp") as send_mcp,
                patch("claude_glm52_supervisor.glm52_vision._read_mcp_id", return_value=(response, [])) as read_mcp,
            ):
                client.call(image, "describe", "vision")
                client.call(image, "describe", "vision")

        sent_ids = [call.args[1]["id"] for call in send_mcp.call_args_list]
        read_ids = [call.args[1] for call in read_mcp.call_args_list]
        self.assertEqual(sent_ids, [2, 3])
        self.assertEqual(read_ids, [2, 3])

    def test_mcp_backend_rejects_large_files_before_tool_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "screen.png"
            image.write_bytes(b"12345")

            with patch("claude_glm52_supervisor.glm52_vision.VisionMCPClient") as client_class:
                client = client_class.return_value
                client.__enter__.return_value = client
                context = build_vision_context([str(image)], root, "describe", max_bytes=4)

        self.assertFalse(context["ok"])
        self.assertEqual(context["entry_count"], 0)
        self.assertIn("image too large", context["errors"][0])
        client_class.assert_not_called()


class BatchVisionPlanTests(unittest.TestCase):
    def test_task_from_json_accepts_images_and_vision_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            raw = {
                "id": "visual-review",
                "cwd": ".",
                "prompt": "review screenshot",
                "images": ["screen.png"],
                "vision_backend": "mcp",
                "vision_mode": "ocr",
                "vision_optional": True,
            }

            task = batch_module.task_from_json(raw, 0, base)

        self.assertEqual(task.images, ["screen.png"])
        self.assertEqual(task.vision_backend, "mcp")
        self.assertEqual(task.vision_mode, "ocr")
        self.assertEqual(task.vision_timeout, 90)
        self.assertTrue(task.vision_optional)
        self.assertFalse(task.vision_allow_outside_cwd)

    def test_task_from_json_parses_vision_boolean_strings_strictly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            raw = {
                "id": "visual-review",
                "cwd": ".",
                "prompt": "review screenshot",
                "images": ["screen.png"],
                "vision_optional": "false",
                "vision_allow_outside_cwd": "false",
            }

            task = batch_module.task_from_json(raw, 0, base)

        self.assertFalse(task.vision_optional)
        self.assertFalse(task.vision_allow_outside_cwd)

    def test_task_from_json_rejects_ambiguous_vision_booleans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            raw = {
                "id": "visual-review",
                "cwd": ".",
                "prompt": "review screenshot",
                "images": ["screen.png"],
                "vision_allow_outside_cwd": "nope",
            }

            with self.assertRaisesRegex(ValueError, "vision_allow_outside_cwd must be a boolean"):
                batch_module.task_from_json(raw, 0, base)

    def test_delegate_process_timeout_includes_vision_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            raw = {
                "id": "visual-review",
                "cwd": ".",
                "prompt": "review screenshots",
                "timeout": 10,
                "retries": 0,
                "images": ["a.png", "b.png"],
                "vision_timeout": 7,
            }

            task = batch_module.task_from_json(raw, 0, base)

        self.assertEqual(batch_module.delegate_process_timeout(task), 10 + 140 + 14 + 30)


if __name__ == "__main__":
    unittest.main()
