#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from ._runtime import resource_path, runtime_root
from .glm52_delegate_accounting import append_delegate_usage_log, capture_usage_accounting
from .glm52_usage import (
    DEFAULT_USAGE_LOG,
    provider_usage_snapshot,
    quota_snapshot,
    summarize_attempts,
    utc_now,
)
from .glm52_usage_snapshots import usage_snapshot
from .glm52_vision import (
    DEFAULT_MAX_CONTEXT_CHARS,
    DEFAULT_MAX_IMAGE_BYTES,
    DEFAULT_MCP_PACKAGE,
    DEFAULT_OCR_MODEL,
    DEFAULT_VISION_MODEL,
    DEFAULT_VISION_BACKEND,
    build_vision_context,
    merge_vision_context,
    sanitized_vision_context,
)


ROOT = runtime_root()
DEFAULT_RUNNER = resource_path("claude-glm52-subagent.sh")
TRANSIENT_STATUSES = {429, 500, 502, 503, 504, 529}
PROCESS_GRACE_SECONDS = 2.0
PROCESS_POLL_INTERVAL = 0.25
SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Codex-safe wrapper for Claude Code GLM-5.2 delegation.",
    )
    parser.add_argument("prompt", nargs="*", help="Task packet. Prefer English bounded packets.")
    parser.add_argument("--cwd", default=".", help="Repository/work directory for Claude Code.")
    parser.add_argument("--role", choices=("implement", "review"), default="implement")
    parser.add_argument("--timeout", type=int, default=None, help="Task timeout seconds. Defaults by role.")
    parser.add_argument("--retries", type=int, default=1, help="Retries for transient API failures with no file changes.")
    parser.add_argument("--retry-delay", type=int, default=20, help="Seconds between transient retries.")
    parser.add_argument("--runner", default=str(DEFAULT_RUNNER), help="Path to claude-glm52-subagent.sh.")
    parser.add_argument("--prompt-file", help="Read task packet from a UTF-8 file.")
    parser.add_argument("--result-file", help="Write complete JSON result to this path.")
    parser.add_argument("--stdout", choices=("summary", "full"), default="summary", help="Print compact summary or full JSON.")
    parser.add_argument("--result-max-chars", type=int, default=1200, help="Max result chars in compact stdout.")
    parser.add_argument("--max-output-tokens", type=int, help="Pass CLAUDE_CODE_MAX_OUTPUT_TOKENS through the raw runner.")
    parser.add_argument("--no-prompt-optimizer", action="store_true", help="Do not prepend Codex fast-path instructions.")
    parser.add_argument("--usage-log-file", default=str(DEFAULT_USAGE_LOG), help="Append run usage JSONL to this path.")
    parser.add_argument("--no-usage-log", action="store_true", help="Do not append the usage JSONL record.")
    parser.add_argument("--quota-provider", default="zai", help="Provider name for direct quota snapshots.")
    parser.add_argument("--quota-timeout", type=int, default=12, help="Quota snapshot timeout seconds.")
    parser.add_argument("--no-quota-snapshot", action="store_true", help="Skip direct quota snapshots.")
    parser.add_argument(
        "--usage-snapshot-source",
        "--quota-snapshot-source",
        dest="usage_snapshot_source",
        choices=("auto", "zai-api", "codexbar", "none"),
        default="auto",
        help="Capture ZCode-compatible before/after usage snapshots.",
    )
    parser.add_argument("--codexbar-path", help="Path to CodexBar CLI for codexbar usage snapshots.")
    parser.add_argument("--image", action="append", default=[], help="Local image path to pre-process for GLM-5.2 text-only delegation. PDFs require --vision-backend direct-zai and GLM-OCR resources.")
    parser.add_argument("--vision-backend", choices=("mcp", "direct-zai"), default=DEFAULT_VISION_BACKEND, help="Image preflight backend. Default uses Z.AI Vision MCP under the Coding Plan.")
    parser.add_argument("--vision-mode", choices=("auto", "vision", "ocr"), default="auto", help="How to turn --image inputs into text context.")
    parser.add_argument("--vision-model", default=DEFAULT_VISION_MODEL, help="Z.AI vision model for general image understanding.")
    parser.add_argument("--ocr-model", default=DEFAULT_OCR_MODEL, help="Z.AI OCR/layout parsing model.")
    parser.add_argument("--vision-mcp-package", default=DEFAULT_MCP_PACKAGE, help="Pinned NPM package spec for the Z.AI Vision MCP server.")
    parser.add_argument("--vision-timeout", type=int, default=90, help="Seconds per image/OCR preflight call.")
    parser.add_argument("--vision-max-image-bytes", type=int, default=DEFAULT_MAX_IMAGE_BYTES, help="Reject larger image/PDF inputs.")
    parser.add_argument("--vision-max-context-chars", type=int, default=DEFAULT_MAX_CONTEXT_CHARS, help="Max injected image context chars.")
    parser.add_argument("--vision-optional", action="store_true", help="Continue without image context if vision/OCR preflight fails.")
    parser.add_argument("--vision-allow-outside-cwd", action="store_true", help="Explicitly allow image/PDF paths outside --cwd and mark them in sanitized vision metadata.")
    return parser.parse_args()


def iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file():
            files.append(path)
    return sorted(files)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in iter_files(root):
        try:
            result[str(path.relative_to(root))] = digest(path)
        except OSError:
            continue
    return result


def change_summary(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    before_keys = set(before)
    after_keys = set(after)
    added = sorted(after_keys - before_keys)
    deleted = sorted(before_keys - after_keys)
    modified = sorted(rel for rel in before_keys & after_keys if before[rel] != after[rel])
    return {"added": added, "modified": modified, "deleted": deleted}


def changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    changes = change_summary(before, after)
    return sorted(changes["added"] + changes["modified"] + changes["deleted"])


def extract_allowed_changes(prompt: str) -> list[str]:
    allowed: list[str] = []
    for line in prompt.splitlines():
        lower = line.lower()
        if not any(marker in lower for marker in ("only modify", "only touch", "files allowed", "expected changed")):
            continue
        allowed.extend(re.findall(r"`([^`]+)`", line))
    return sorted(set(path for path in allowed if path and not path.startswith("/")))


def scope_violations(changed: list[str], allowed: list[str]) -> list[str]:
    if not allowed:
        return []
    allowed_set = set(allowed)
    return sorted(path for path in changed if path not in allowed_set)


def child_pids(pid: int) -> list[int]:
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,ppid="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    children: dict[int, list[int]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            child, parent = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        children.setdefault(parent, []).append(child)
    found: list[int] = []
    stack = list(children.get(pid, []))
    while stack:
        child = stack.pop()
        found.append(child)
        stack.extend(children.get(child, []))
    return found


def terminate_process_tree(proc: subprocess.Popen[Any], grace: float = PROCESS_GRACE_SECONDS) -> dict[str, Any]:
    if proc.poll() is not None:
        return {"terminated": False, "killed": False, "returncode": proc.returncode, "child_pids": []}
    descendants = child_pids(proc.pid)
    targets = list(reversed(descendants))
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            pass
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
    except ProcessLookupError:
        pass
    except OSError:
        pass
    killed = False
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        killed = True
        descendants = child_pids(proc.pid)
        targets = list(reversed(descendants))
        for pid in targets:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                pass
        try:
            if hasattr(os, "killpg"):
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass
        except OSError:
            pass
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
    return {"terminated": True, "killed": killed, "returncode": proc.returncode, "child_pids": descendants}


def _read_temp_output(handle: Any) -> str:
    try:
        handle.flush()
        handle.seek(0)
        return handle.read()
    except OSError:
        return ""


def run_process_tree(
    cmd: list[str],
    cwd: Path,
    timeout: float,
    monitor: Any = None,
    poll_interval: float = PROCESS_POLL_INTERVAL,
    grace: float = PROCESS_GRACE_SECONDS,
) -> dict[str, Any]:
    started = time.monotonic()
    proc: subprocess.Popen[Any] | None = None
    with (
        tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as stdout_file,
        tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as stderr_file,
    ):
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(cwd),
                text=True,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
            deadline = started + timeout
            while True:
                returncode = proc.poll()
                if returncode is not None:
                    return {
                        "returncode": returncode,
                        "wall_ms": round((time.monotonic() - started) * 1000),
                        "stdout": _read_temp_output(stdout_file),
                        "stderr": _read_temp_output(stderr_file),
                        "timed_out": False,
                        "termination_reason": None,
                        "cleanup": {"terminated": False, "killed": False, "returncode": returncode, "child_pids": []},
                    }
                if monitor:
                    event = monitor()
                    if event:
                        cleanup = terminate_process_tree(proc, grace=grace)
                        return {
                            "returncode": 125,
                            "wall_ms": round((time.monotonic() - started) * 1000),
                            "stdout": _read_temp_output(stdout_file),
                            "stderr": _read_temp_output(stderr_file),
                            "timed_out": False,
                            "termination_reason": event.get("reason") or "monitor_termination",
                            "monitor_event": event,
                            "cleanup": cleanup,
                        }
                if time.monotonic() >= deadline:
                    cleanup = terminate_process_tree(proc, grace=grace)
                    return {
                        "returncode": 124,
                        "wall_ms": round((time.monotonic() - started) * 1000),
                        "stdout": _read_temp_output(stdout_file),
                        "stderr": _read_temp_output(stderr_file),
                        "timed_out": True,
                        "termination_reason": "timeout",
                        "cleanup": cleanup,
                    }
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            if proc is not None:
                terminate_process_tree(proc, grace=grace)
            raise
        except BaseException:
            if proc is not None:
                terminate_process_tree(proc, grace=grace)
            raise


def scope_monitor_event(root: Path, before: dict[str, str], allowed: list[str]) -> dict[str, Any] | None:
    if not allowed:
        return None
    current = snapshot(root)
    changes = change_summary(before, current)
    changed = sorted(changes["added"] + changes["modified"] + changes["deleted"])
    violations = scope_violations(changed, allowed)
    if not violations:
        return None
    return {
        "reason": "scope_violation_during_run",
        "scope_violations": violations,
        "changed_files": changed,
        "change_summary": changes,
    }


def parse_json(stdout: str, stderr: str) -> dict[str, Any]:
    for text in (stdout.strip(), stderr.strip()):
        if not text:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            continue
    return {"parse_error": True, "stdout_prefix": stdout[:1200], "stderr_prefix": stderr[:1200]}


def transient_error(payload: dict[str, Any]) -> bool:
    status = payload.get("api_error_status")
    result = str(payload.get("result", "")).lower()
    return status in TRANSIENT_STATUSES or "overloaded" in result or "temporarily" in result


def timed_out(returncode: int, payload: dict[str, Any]) -> bool:
    result = str(payload.get("result", "")).lower()
    timeout_markers = (
        "timed out",
        "timeout after",
        "wrapper timeout",
        "request timeout",
        "deadline exceeded",
    )
    return returncode == 124 or any(marker in result for marker in timeout_markers)


def role_timeout(role: str, timeout: int | None) -> int:
    if timeout is not None:
        return timeout
    return 900 if role == "implement" else 300


def emit_early_failure(args: argparse.Namespace, result: dict[str, Any]) -> None:
    result["usageLogFile"], result["usageLogError"] = append_delegate_usage_log(args, result, ROOT)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.result_file:
        Path(args.result_file).parent.mkdir(parents=True, exist_ok=True)
        Path(args.result_file).write_text(text + "\n", encoding="utf-8")
    if args.stdout == "full":
        print(text)
        return
    compact = {
        "ok": result["ok"],
        "role": result["role"],
        "failure_reason": result["failure_reason"],
        "result": result["result"][: args.result_max_chars],
        "visionContext": result.get("visionContext", {}),
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))


def optimize_prompt(role: str, prompt: str) -> str:
    if role == "review":
        prefix = """\
Codex fast-path contract:
- Work in English unless reviewing Japanese text.
- Review only source/tests/diff named by the task.
- Stay inside the current working directory. Never search from / or ~.
- Ignore delegation artifacts like delegate-result.json, review-result.json, *_result.json, and task-packet files unless explicitly requested.
- Run at most one relevant validation command if allowed.
- If validation is denied, do not retry variants; state the denial once.
- Output findings only: severity, file evidence, fix idea, residual risk.
- Keep the final answer under 12 concise bullets.
"""
    else:
        prefix = """\
Codex fast-path contract:
- Work in English unless the task is specifically about Japanese text.
- Read only the relevant files named by the task before editing.
- Stay inside the current working directory. Never search from / or ~.
- Do not delete files. Do not edit task.md or tests unless explicitly requested.
- Prefer one direct implementation pass.
- Run the requested validation once after editing.
- If tests conflict with the written spec, state the minimal blocker and stop.
- Do not include long traces, tables, or exhaustive reasoning.
- Keep the final answer under 10 concise bullet lines.
"""
    return f"{prefix}\n{prompt.strip()}\n"


def run_once(args: argparse.Namespace, prompt: str, timeout: int, allowed_changes: list[str] | None = None) -> dict[str, Any]:
    cwd = Path(args.cwd).expanduser().resolve()
    runner = Path(args.runner).expanduser().resolve()
    before = snapshot(cwd)
    cmd = [str(runner)]
    if args.role != "implement":
        cmd.extend(["--role", args.role])
    if args.max_output_tokens:
        cmd.extend(["--max-output-tokens", str(args.max_output_tokens)])
    cmd.extend(["--timeout", str(timeout), "--cwd", str(cwd), prompt])

    monitor = None
    if args.role == "implement" and allowed_changes:
        monitor = lambda: scope_monitor_event(cwd, before, allowed_changes)
    process = run_process_tree(cmd, ROOT, timeout + 120, monitor=monitor)
    returncode = process["returncode"]
    stdout = process["stdout"]
    stderr = process["stderr"]
    wall_ms = process["wall_ms"]
    if process.get("timed_out"):
        stderr = f"{stderr}\nwrapper timeout after {timeout + 120}s".strip()
    payload = parse_json(stdout, stderr)
    monitor_event = process.get("monitor_event") if isinstance(process.get("monitor_event"), dict) else {}
    if process.get("termination_reason") == "scope_violation_during_run":
        payload["is_error"] = True
        payload["result"] = "scope_violation_during_run: " + ", ".join(monitor_event.get("scope_violations", []))
    after = snapshot(cwd)
    changes = change_summary(before, after)
    changed = sorted(changes["added"] + changes["modified"] + changes["deleted"])
    return {
        "cmd": cmd[:5] + ["..."],
        "returncode": returncode,
        "wall_ms": wall_ms,
        "payload": payload,
        "changed_files": changed,
        "change_summary": changes,
        "termination_reason": process.get("termination_reason"),
        "scope_violation_during_run": process.get("termination_reason") == "scope_violation_during_run",
        "process_cleanup": process.get("cleanup"),
        "stdout_prefix": stdout[:1200],
        "stderr_prefix": stderr[:1200],
    }


def main() -> int:
    args = parse_args()
    started_at = utc_now()
    overall_started = time.monotonic()
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    else:
        prompt = " ".join(args.prompt)
    if not prompt.strip():
        print("prompt or --prompt-file is required", file=sys.stderr)
        return 2
    allowed_changes = extract_allowed_changes(prompt)
    snapshot_enabled = not args.no_quota_snapshot and args.usage_snapshot_source != "none"
    quota_before = quota_snapshot(
        provider=args.quota_provider,
        enabled=snapshot_enabled,
        timeout=args.quota_timeout,
    )
    provider_usage_before = provider_usage_snapshot(
        provider=args.quota_provider,
        enabled=snapshot_enabled,
        timeout=args.quota_timeout,
    )
    usage_snapshot_before = usage_snapshot(
        provider=args.quota_provider,
        source=args.usage_snapshot_source,
        phase="before",
        enabled=snapshot_enabled,
        timeout=args.quota_timeout,
        codexbar_path=args.codexbar_path,
    )
    vision_context: dict[str, Any] = {"enabled": False, "ok": True, "entries": [], "errors": []}
    if args.image:
        vision_context = build_vision_context(
            args.image,
            cwd=Path(args.cwd).expanduser().resolve(),
            user_prompt=prompt,
            mode=args.vision_mode,
            backend=args.vision_backend,
            vision_model=args.vision_model,
            ocr_model=args.ocr_model,
            mcp_package=args.vision_mcp_package,
            timeout=args.vision_timeout,
            max_bytes=args.vision_max_image_bytes,
            max_context_chars=args.vision_max_context_chars,
            optional=args.vision_optional,
            allow_outside_cwd=args.vision_allow_outside_cwd,
        )
        if not vision_context.get("ok"):
            ended_at = utc_now()
            usage_summary = summarize_attempts([])
            accounting = capture_usage_accounting(
                args,
                usage_summary,
                quota_before,
                provider_usage_before,
                usage_snapshot_before,
                snapshot_enabled,
            )
            result = {
                "ok": False,
                "role": args.role,
                "failure_reason": "vision_context_failed",
                "result": "image vision/OCR preflight failed; GLM-5.2 is text-only and delegation was not started",
                "changed_files": [],
                "policy_ok": True,
                "scope_violations": [],
                "usageSummary": usage_summary,
                **accounting,
                "visionContext": sanitized_vision_context(vision_context),
                "started_at": started_at,
                "ended_at": ended_at,
                "overall_wall_ms": round((time.monotonic() - overall_started) * 1000),
            }
            emit_early_failure(args, result)
            return 1
        prompt = merge_vision_context(prompt, str(vision_context.get("context_text", "")))
    if not args.no_prompt_optimizer:
        prompt = optimize_prompt(args.role, prompt)

    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    timeout = role_timeout(args.role, args.timeout)
    attempts = []
    max_attempts = max(1, args.retries + 1)
    for index in range(max_attempts):
        attempt = run_once(args, prompt, timeout, allowed_changes=allowed_changes)
        attempts.append(attempt)
        payload = attempt["payload"]
        success = attempt["returncode"] == 0 and payload.get("is_error") is False
        if success:
            break
        can_retry = (
            index + 1 < max_attempts
            and transient_error(payload)
            and not attempt["changed_files"]
        )
        if not can_retry:
            break
        time.sleep(args.retry_delay)

    final = attempts[-1]
    payload = final["payload"]
    final_transient_error = transient_error(payload)
    final_timed_out = timed_out(final["returncode"], payload)
    deleted_files = final.get("change_summary", {}).get("deleted", [])
    scope_errors = scope_violations(final["changed_files"], allowed_changes)
    policy_ok = not deleted_files and not scope_errors
    ok = final["returncode"] == 0 and payload.get("is_error") is False and not final_timed_out and policy_ok
    safe_to_retry = (final_transient_error or final_timed_out) and not final["changed_files"]
    usage_summary = summarize_attempts(attempts)
    accounting = capture_usage_accounting(
        args,
        usage_summary,
        quota_before,
        provider_usage_before,
        usage_snapshot_before,
        snapshot_enabled,
    )
    ended_at = utc_now()
    overall_wall_ms = round((time.monotonic() - overall_started) * 1000)
    result = {
        "ok": ok,
        "role": args.role,
        "timeout": timeout,
        "attempt_count": len(attempts),
        "transient_retry_used": len(attempts) > 1,
        "final_returncode": final["returncode"],
        "final_api_error_status": payload.get("api_error_status"),
        "final_is_error": payload.get("is_error"),
        "final_transient_error": final_transient_error,
        "final_timed_out": final_timed_out,
        "failure_reason": final.get("termination_reason"),
        "scope_violation_during_run": final.get("scope_violation_during_run", False),
        "safe_to_retry_later": safe_to_retry,
        "policy_ok": policy_ok,
        "scope_violations": scope_errors,
        "allowed_changes": allowed_changes,
        "changed_files": final["changed_files"],
        "change_summary": final.get("change_summary", {}),
        "result": str(payload.get("result", ""))[:4000],
        "modelUsage": payload.get("modelUsage", {}),
        "usageSummary": usage_summary,
        **accounting,
        "visionContext": sanitized_vision_context(vision_context),
        "overall_wall_ms": overall_wall_ms,
        "started_at": started_at,
        "ended_at": ended_at,
        "attempts": attempts,
        "prompt_sha256": prompt_sha256,
        "prompt_chars": len(prompt),
    }
    result["usageLogFile"], result["usageLogError"] = append_delegate_usage_log(args, result, ROOT)
    compact = {
        "ok": result["ok"],
        "role": result["role"],
        "timeout": result["timeout"],
        "attempt_count": result["attempt_count"],
        "transient_retry_used": result["transient_retry_used"],
        "final_returncode": result["final_returncode"],
        "final_api_error_status": result["final_api_error_status"],
        "final_is_error": result["final_is_error"],
        "final_transient_error": result["final_transient_error"],
        "final_timed_out": result["final_timed_out"],
        "failure_reason": result["failure_reason"],
        "scope_violation_during_run": result["scope_violation_during_run"],
        "safe_to_retry_later": result["safe_to_retry_later"],
        "policy_ok": result["policy_ok"],
        "scope_violations": result["scope_violations"],
        "changed_files": result["changed_files"],
        "change_summary": result["change_summary"],
        "result": result["result"][: args.result_max_chars],
        "modelUsage": result["modelUsage"],
        "usageSummary": result["usageSummary"],
        "usage_normalized": result["usage_normalized"],
        "usage_accounting": result["usage_accounting"],
        "usage_snapshots": result["usage_snapshots"],
        "consumptionSummary": result["consumptionSummary"],
        "quotaDelta": result["quotaDelta"],
        "quotaState": result["quotaState"],
        "providerUsageDelta": result["providerUsageDelta"],
        "visionContext": result["visionContext"],
        "usageLogFile": result["usageLogFile"],
        "usageLogError": result["usageLogError"],
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.result_file:
        Path(args.result_file).parent.mkdir(parents=True, exist_ok=True)
        Path(args.result_file).write_text(text + "\n", encoding="utf-8")
    if args.stdout == "full":
        print(text)
    else:
        print(json.dumps(compact, ensure_ascii=False, indent=2))
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
