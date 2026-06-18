#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._runtime import default_delegate_path, runtime_root
from .glm52_usage import aggregate_usage_summaries, summarize_model_usage


ROOT = runtime_root()
DEFAULT_DELEGATE = default_delegate_path()
TRANSIENT_STATUSES = {429, 500, 502, 503, 504, 529}
GLM52_MODEL_KEYS = ("claude-opus-4-6[1m]", "claude-opus-4-8[1m]", "glm-5.2[1m]")


@dataclass(frozen=True)
class Task:
    task_id: str
    cwd: Path
    role: str
    prompt_file: Path | None
    prompt: str
    timeout: int
    retries: int
    retry_delay: int
    max_output_tokens: int | None
    images: list[str]
    vision_backend: str
    vision_mode: str
    vision_timeout: int
    vision_optional: bool
    vision_allow_outside_cwd: bool
    result_file: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run independent Claude Code GLM-5.2 delegation packets with bounded parallelism.",
    )
    parser.add_argument("--plan-file", required=True, help="JSON file with a top-level tasks array.")
    parser.add_argument("--result-file", help="Write batch JSON result to this path.")
    parser.add_argument(
        "--delegate",
        default=str(DEFAULT_DELEGATE) if DEFAULT_DELEGATE else "",
        help="Path to claude-glm52-delegate.py. Defaults to the package module when installed.",
    )
    parser.add_argument("--concurrency", type=int, default=2, help="Parallel workers. Keep 1-3 for CLIProxyAPI.")
    parser.add_argument("--preflight-timeout", type=int, default=120, help="Health probe timeout seconds.")
    parser.add_argument("--no-preflight", action="store_true", help="Skip the lightweight provider health probe.")
    parser.add_argument(
        "--continue-on-provider-error",
        action="store_true",
        help="Continue scheduling after provider-like failures with no useful edits.",
    )
    parser.add_argument("--stdout", choices=("summary", "full"), default="summary")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def strict_bool(raw: dict[str, Any], key: str, task_id: str, default: bool = False) -> bool:
    value = raw.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ValueError(f"{task_id}: {key} must be a boolean")


def task_from_json(raw: dict[str, Any], index: int, base: Path) -> Task:
    task_id = str(raw.get("id") or f"task-{index + 1}")
    cwd = Path(str(raw["cwd"])).expanduser()
    if not cwd.is_absolute():
        cwd = (base / cwd).resolve()
    prompt_file = None
    if raw.get("prompt_file"):
        prompt_file = Path(str(raw["prompt_file"])).expanduser()
        if not prompt_file.is_absolute():
            prompt_file = (base / prompt_file).resolve()
    prompt = str(raw.get("prompt", ""))
    if prompt_file is None and not prompt.strip():
        raise ValueError(f"{task_id}: prompt or prompt_file is required")
    result_file = Path(str(raw.get("result_file") or (cwd / "delegate-result.json"))).expanduser()
    if not result_file.is_absolute():
        result_file = (base / result_file).resolve()
    role = str(raw.get("role", "implement"))
    if role not in {"implement", "review"}:
        raise ValueError(f"{task_id}: role must be implement or review")
    raw_images = raw.get("images", raw.get("image_paths", []))
    if isinstance(raw_images, str):
        images = [raw_images]
    elif isinstance(raw_images, list):
        images = [str(item) for item in raw_images]
    else:
        raise ValueError(f"{task_id}: images must be a string or array when provided")
    vision_backend = str(raw.get("vision_backend", "mcp"))
    if vision_backend not in {"mcp", "direct-zai"}:
        raise ValueError(f"{task_id}: vision_backend must be mcp or direct-zai")
    vision_mode = str(raw.get("vision_mode", "auto"))
    if vision_mode not in {"auto", "vision", "ocr"}:
        raise ValueError(f"{task_id}: vision_mode must be auto, vision, or ocr")
    return Task(
        task_id=task_id,
        cwd=cwd,
        role=role,
        prompt_file=prompt_file,
        prompt=prompt,
        timeout=int(raw.get("timeout", 900 if role == "implement" else 300)),
        retries=int(raw.get("retries", 1)),
        retry_delay=int(raw.get("retry_delay", 10)),
        max_output_tokens=int(raw["max_output_tokens"]) if raw.get("max_output_tokens") is not None else None,
        images=images,
        vision_backend=vision_backend,
        vision_mode=vision_mode,
        vision_timeout=int(raw.get("vision_timeout", 90)),
        vision_optional=strict_bool(raw, "vision_optional", task_id),
        vision_allow_outside_cwd=strict_bool(raw, "vision_allow_outside_cwd", task_id),
        result_file=result_file,
    )


def load_tasks(plan_file: Path) -> list[Task]:
    plan = read_json(plan_file)
    raw_tasks = plan.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("plan file must contain a non-empty tasks array")
    base = plan_file.parent.resolve()
    tasks = [task_from_json(raw, index, base) for index, raw in enumerate(raw_tasks)]
    seen: set[str] = set()
    for task in tasks:
        if task.task_id in seen:
            raise ValueError(f"duplicate task id: {task.task_id}")
        seen.add(task.task_id)
    return tasks


def delegate_process_timeout(task: Task) -> int:
    attempts = max(1, task.retries + 1)
    vision_budget = len(task.images) * task.vision_timeout
    return vision_budget + (task.timeout + 140) * attempts + task.retry_delay * max(0, task.retries) + 30


def descendant_pids(pid: int) -> list[int]:
    try:
        proc = subprocess.run(
            ["pgrep", "-P", str(pid)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    children = [int(line) for line in proc.stdout.splitlines() if line.strip().isdigit()]
    descendants = children[:]
    for child in children:
        descendants.extend(descendant_pids(child))
    return descendants


def process_running(pid: int) -> bool:
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and "Z" not in proc.stdout


async def wait_for_pids_to_exit(pids: list[int], timeout: float) -> list[int]:
    deadline = time.monotonic() + timeout
    live = [pid for pid in pids if process_running(pid)]
    while live and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        live = [pid for pid in live if process_running(pid)]
    return live


def signal_pid_and_group(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except OSError:
        pass
    try:
        os.kill(pid, sig)
    except OSError:
        pass


async def terminate_process_tree(proc: asyncio.subprocess.Process) -> dict[str, Any]:
    children = descendant_pids(proc.pid)
    for pid in reversed(children):
        signal_pid_and_group(pid, signal.SIGTERM)
    signal_pid_and_group(proc.pid, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=2)
    except asyncio.TimeoutError:
        signal_pid_and_group(proc.pid, signal.SIGKILL)
        await proc.wait()
    remaining = await wait_for_pids_to_exit(children, 2)
    for pid in reversed(remaining):
        signal_pid_and_group(pid, signal.SIGKILL)
    remaining = await wait_for_pids_to_exit(remaining, 2)
    return {"child_pids": children, "remaining_child_pids": remaining}


async def run_command(command: list[str], cwd: Path, timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        returncode = proc.returncode
    except asyncio.TimeoutError:
        cleanup = {}
        if proc is not None:
            cleanup = await terminate_process_tree(proc)
        stdout_b = b""
        stderr_b = f"batch command timed out after {timeout}s".encode()
        returncode = 124
    else:
        cleanup = {}
    return {
        "returncode": returncode,
        "wall_ms": round((time.monotonic() - started) * 1000),
        "stdout": stdout_b.decode("utf-8", errors="replace")[-3000:],
        "stderr": stderr_b.decode("utf-8", errors="replace")[-3000:],
        "cleanup": cleanup,
    }


def model_usage(parsed: dict[str, Any]) -> dict[str, Any]:
    all_usage = parsed.get("modelUsage", {})
    if not isinstance(all_usage, dict):
        return {}
    for key in GLM52_MODEL_KEYS:
        usage = all_usage.get(key)
        if isinstance(usage, dict):
            return {"modelKey": key, **usage}
    return {}


def provider_unhealthy(parsed: dict[str, Any], command_result: dict[str, Any]) -> bool:
    changed = parsed.get("changed_files", [])
    status = parsed.get("final_api_error_status")
    transient = parsed.get("final_transient_error") or status in TRANSIENT_STATUSES
    timed_out = parsed.get("final_timed_out") or command_result.get("returncode") == 124
    return bool((transient or timed_out) and not changed)


def compact_task_result(task: Task, command_result: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    usage = model_usage(parsed)
    usage_summary = parsed.get("usageSummary")
    if not isinstance(usage_summary, dict):
        usage_summary = summarize_model_usage(parsed.get("modelUsage", {}))
    timed_out = parsed.get("final_timed_out") or command_result["returncode"] == 124
    return {
        "id": task.task_id,
        "role": task.role,
        "cwd": str(task.cwd),
        "ok": parsed.get("ok") is True and not timed_out,
        "policy_ok": parsed.get("policy_ok"),
        "scope_violations": parsed.get("scope_violations", []),
        "provider_unhealthy": provider_unhealthy(parsed, command_result),
        "returncode": command_result["returncode"],
        "wall_ms": parsed.get("attempts", [{}])[-1].get("wall_ms", command_result["wall_ms"]),
        "attempt_count": parsed.get("attempt_count"),
        "api_error_status": parsed.get("final_api_error_status"),
        "timed_out": timed_out,
        "changed_files": parsed.get("changed_files", []),
        "change_summary": parsed.get("change_summary", {}),
        "result_file": str(task.result_file),
        "result_prefix": str(parsed.get("result", ""))[:1000],
        "usageSummary": usage_summary,
        "quotaDelta": parsed.get("quotaDelta", {}),
        "visionContext": parsed.get("visionContext", {}),
        "glm52": {
            "inputTokens": usage.get("inputTokens"),
            "outputTokens": usage.get("outputTokens"),
            "cacheReadInputTokens": usage.get("cacheReadInputTokens"),
            "costUSD": usage.get("costUSD"),
            "contextWindow": usage.get("contextWindow"),
            "maxOutputTokens": usage.get("maxOutputTokens"),
            "modelKey": usage.get("modelKey"),
        },
    }


async def run_delegate(task: Task, delegate: Path | None) -> dict[str, Any]:
    task.result_file.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(delegate)] if delegate else [
        sys.executable,
        "-m",
        "claude_glm52_supervisor.delegate",
    ]
    command.extend(
        [
            "--cwd",
            str(task.cwd),
            "--role",
            task.role,
            "--timeout",
            str(task.timeout),
            "--retries",
            str(task.retries),
            "--retry-delay",
            str(task.retry_delay),
            "--result-max-chars",
            "900",
            "--result-file",
            str(task.result_file),
        ]
    )
    if task.max_output_tokens:
        command.extend(["--max-output-tokens", str(task.max_output_tokens)])
    for image in task.images:
        command.extend(["--image", image])
    if task.images:
        command.extend(["--vision-backend", task.vision_backend])
        command.extend(["--vision-mode", task.vision_mode])
        command.extend(["--vision-timeout", str(task.vision_timeout)])
    if task.vision_optional:
        command.append("--vision-optional")
    if task.vision_allow_outside_cwd:
        command.append("--vision-allow-outside-cwd")
    if task.prompt_file is not None:
        command.extend(["--prompt-file", str(task.prompt_file)])
    else:
        command.append(task.prompt)
    command_result = await run_command(command, ROOT, delegate_process_timeout(task))
    parsed = read_json(task.result_file) if task.result_file.exists() else {}
    result = compact_task_result(task, command_result, parsed)
    result["stdout_prefix"] = command_result["stdout"][:1200]
    result["stderr_prefix"] = command_result["stderr"][:1200]
    return result


async def run_preflight(delegate: Path | None, cwd: Path, timeout: int) -> dict[str, Any]:
    result_file = cwd / ".glm52-batch-preflight.json"
    task = Task(
        task_id="preflight",
        cwd=cwd,
        role="review",
        prompt_file=None,
        prompt="Return exactly: ok",
        timeout=timeout,
        retries=0,
        retry_delay=0,
        max_output_tokens=None,
        images=[],
        vision_backend="mcp",
        vision_mode="auto",
        vision_timeout=90,
        vision_optional=False,
        vision_allow_outside_cwd=False,
        result_file=result_file,
    )
    result = await run_delegate(task, delegate)
    try:
        result_file.unlink()
    except OSError:
        pass
    return result


async def run_tasks(
    tasks: list[Task],
    delegate: Path | None,
    concurrency: int,
    continue_on_provider_error: bool,
) -> tuple[list[dict[str, Any]], str | None]:
    queue: asyncio.Queue[Task] = asyncio.Queue()
    for task in tasks:
        queue.put_nowait(task)
    results: list[dict[str, Any]] = []
    stop_reason: str | None = None
    lock = asyncio.Lock()

    async def worker() -> None:
        nonlocal stop_reason
        while True:
            async with lock:
                if stop_reason and not continue_on_provider_error:
                    return
            try:
                task = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            print(f"batch {task.task_id}: starting", flush=True)
            row = await run_delegate(task, delegate)
            print(f"batch {task.task_id}: {'ok' if row['ok'] else 'failed'}", flush=True)
            async with lock:
                results.append(row)
                if row["provider_unhealthy"] and not continue_on_provider_error:
                    stop_reason = (
                        f"Stopped after {task.task_id}: provider-like failure with no useful edits. "
                        "Already-running workers may have completed; no new tasks were scheduled."
                    )
                if row.get("policy_ok") is False and not continue_on_provider_error:
                    stop_reason = (
                        f"Stopped after {task.task_id}: worker violated Codex scope policy. "
                        "Inspect scope_violations/change_summary before retrying."
                    )
            queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(max(1, concurrency))]
    await asyncio.gather(*workers)
    order = {task.task_id: index for index, task in enumerate(tasks)}
    results.sort(key=lambda row: order[row["id"]])
    return results, stop_reason


async def async_main() -> int:
    args = parse_args()
    if args.concurrency < 1 or args.concurrency > 6:
        print("--concurrency must be between 1 and 6", file=sys.stderr)
        return 2
    plan_file = Path(args.plan_file).expanduser().resolve()
    delegate = Path(args.delegate).expanduser().resolve() if args.delegate else None
    tasks = load_tasks(plan_file)
    preflight = None
    stop_reason = None
    if not args.no_preflight:
        print("batch preflight: starting", flush=True)
        preflight = await run_preflight(delegate, tasks[0].cwd, args.preflight_timeout)
        print(f"batch preflight: {'ok' if preflight['ok'] else 'failed'}", flush=True)
        if preflight["provider_unhealthy"]:
            stop_reason = "Provider health probe failed before batch scheduling."
    results: list[dict[str, Any]] = []
    if stop_reason is None:
        results, stop_reason = await run_tasks(tasks, delegate, args.concurrency, args.continue_on_provider_error)
    task_usage_summary = aggregate_usage_summaries([row.get("usageSummary", {}) for row in results])
    preflight_usage_summary = preflight.get("usageSummary", {}) if isinstance(preflight, dict) else {}
    total_usage_summary = aggregate_usage_summaries([preflight_usage_summary, task_usage_summary])
    payload = {
        "ok": bool(results) and all(row["ok"] for row in results) and stop_reason is None,
        "plan_file": str(plan_file),
        "concurrency": args.concurrency,
        "preflight": preflight,
        "stop_reason": stop_reason,
        "usageSummary": {
            "preflight": preflight_usage_summary,
            "tasks": task_usage_summary,
            "total": total_usage_summary,
        },
        "results": results,
    }
    if args.result_file:
        result_path = Path(args.result_file).expanduser().resolve()
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.stdout == "full":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        compact = {
            "ok": payload["ok"],
            "concurrency": args.concurrency,
            "preflight_ok": None if preflight is None else preflight["ok"],
            "stop_reason": stop_reason,
            "task_total_tokens": task_usage_summary["totalTokens"],
            "task_output_tokens": task_usage_summary["outputTokens"],
            "task_cost_usd": task_usage_summary["costUSD"],
            "results": [
                {
                    "id": row["id"],
                    "ok": row["ok"],
                    "provider_unhealthy": row["provider_unhealthy"],
                    "policy_ok": row.get("policy_ok"),
                    "scope_violations": row.get("scope_violations", []),
                    "changed_files": row["changed_files"],
                    "change_summary": row.get("change_summary", {}),
                    "wall_ms": row["wall_ms"],
                    "totalTokens": row["usageSummary"]["totalTokens"],
                    "contextWindow": row["glm52"]["contextWindow"],
                }
                for row in results
            ],
        }
        print(json.dumps(compact, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


def main() -> int:
    try:
        return asyncio.run(async_main())
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"batch plan error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
