#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import mimetypes
import os
import select
import signal
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .glm52_usage import _zai_api_key, redact, utc_now


ZAI_CHAT_URL = "https://api.z.ai/api/paas/v4/chat/completions"
ZAI_LAYOUT_URL = "https://api.z.ai/api/paas/v4/layout_parsing"
DEFAULT_VISION_MODEL = "glm-5v-turbo"
DEFAULT_OCR_MODEL = "glm-ocr"
DEFAULT_VISION_BACKEND = "mcp"
DEFAULT_MCP_PACKAGE = "@z_ai/mcp-server@0.1.4"
DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_CONTEXT_CHARS = 6000
DEFAULT_MAX_CHARS_PER_IMAGE = 2400
MCP_ENV_ALLOWLIST = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NPM_CONFIG_CACHE",
    "PATH",
    "TEMP",
    "TMP",
    "TMPDIR",
    "npm_config_cache",
}

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"}
DOCUMENT_SUFFIXES = {".pdf"}
SUPPORTED_SUFFIXES = IMAGE_SUFFIXES | DOCUMENT_SUFFIXES


class VisionError(RuntimeError):
    pass


def resolve_media_path(raw_path: str, cwd: Path, allow_outside_cwd: bool = False) -> Path:
    workspace = cwd.expanduser().resolve()
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = workspace / path
    path = path.resolve()
    if not allow_outside_cwd and not path.is_relative_to(workspace):
        raise VisionError(f"image path escapes workspace: {raw_path}")
    if not path.exists():
        raise VisionError(f"image not found: {raw_path}")
    if not path.is_file():
        raise VisionError(f"image path is not a file: {raw_path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise VisionError(f"unsupported image/document type: {path.suffix or '(none)'}")
    return path


def media_display_path(path: Path, cwd: Path) -> str:
    try:
        return str(path.relative_to(cwd))
    except ValueError:
        return path.name


def validate_media_size(path: Path, max_bytes: int = DEFAULT_MAX_IMAGE_BYTES) -> int:
    size = path.stat().st_size
    if size > max_bytes:
        raise VisionError(f"image too large: {size} bytes; max is {max_bytes}")
    return size


def data_url_for_file(path: Path, max_bytes: int = DEFAULT_MAX_IMAGE_BYTES) -> str:
    validate_media_size(path, max_bytes=max_bytes)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    api_key = _zai_api_key()
    if not api_key:
        raise VisionError("missing Z.AI API key in ZAI_API_KEY, Z_AI_API_KEY, or ANTHROPIC_AUTH_TOKEN")
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise VisionError(f"Z.AI vision HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise VisionError(f"Z.AI vision connection error: {redact(exc.reason)}") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise VisionError(f"invalid Z.AI vision response: {redact(body)}") from exc
    if not isinstance(parsed, dict):
        raise VisionError(f"unexpected Z.AI vision response: {redact(parsed)}")
    return parsed


def _send_mcp(proc: subprocess.Popen[str], payload: dict[str, Any]) -> None:
    if proc.stdin is None:
        raise VisionError("Vision MCP stdin unavailable")
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()


def _read_mcp_id(proc: subprocess.Popen[str], request_id: int, timeout: int) -> tuple[dict[str, Any] | None, list[str]]:
    deadline = time.monotonic() + timeout
    stderr_lines: list[str] = []
    while time.monotonic() < deadline:
        streams = [stream for stream in (proc.stdout, proc.stderr) if stream is not None]
        ready, _, _ = select.select(streams, [], [], 0.2)
        for stream in ready:
            line = stream.readline()
            if not line:
                continue
            if stream is proc.stderr:
                stderr_lines.append(line.rstrip())
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == request_id:
                return message, stderr_lines
        if proc.poll() is not None:
            break
    return None, stderr_lines


def _mcp_tool_text(result: dict[str, Any]) -> str:
    if result.get("isError"):
        raise VisionError(f"Vision MCP tool error: {redact(result)}")
    text_parts = []
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            text_parts.append(str(item.get("text", "")))
    text = "\n".join(part for part in text_parts if part).strip()
    if not text:
        raise VisionError(f"Vision MCP returned no text: {redact(result)}")
    return text


def _terminate_mcp(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    pid = getattr(proc, "pid", None)
    try:
        if isinstance(pid, int):
            os.killpg(pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            if isinstance(pid, int):
                os.killpg(pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass


def minimal_mcp_env(api_key: str) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if value and (key in MCP_ENV_ALLOWLIST or key.startswith("LC_"))
    }
    env["Z_AI_API_KEY"] = api_key
    env["Z_AI_MODE"] = "ZAI"
    return env


class VisionMCPClient:
    def __init__(self, timeout: int = 90, package: str = DEFAULT_MCP_PACKAGE) -> None:
        self.timeout = timeout
        self.package = package
        self.proc: subprocess.Popen[str] | None = None
        self.next_request_id = 2

    def __enter__(self) -> "VisionMCPClient":
        api_key = _zai_api_key()
        if not api_key:
            raise VisionError("missing Z.AI API key in ZAI_API_KEY, Z_AI_API_KEY, or ANTHROPIC_AUTH_TOKEN")
        self.proc = subprocess.Popen(
            ["npx", "-y", self.package],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=minimal_mcp_env(api_key),
            bufsize=1,
            start_new_session=True,
        )
        _send_mcp(self.proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "glm52-vision-preflight", "version": "0.1"}}})
        initialized, stderr_lines = _read_mcp_id(self.proc, 1, self.timeout)
        if not initialized or "result" not in initialized:
            raise VisionError(f"Vision MCP initialize failed: {redact(initialized or stderr_lines)}")
        _send_mcp(self.proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.proc is not None:
            _terminate_mcp(self.proc)

    def call(self, path: Path, user_prompt: str, mode: str) -> tuple[str, dict[str, Any]]:
        if path.suffix.lower() in DOCUMENT_SUFFIXES:
            raise VisionError("Vision MCP backend does not support PDFs; use direct-zai only when GLM-OCR API resources are available")
        if self.proc is None:
            raise VisionError("Vision MCP client is not initialized")
        tool_name = "extract_text_from_screenshot" if mode == "ocr" else "analyze_image"
        arguments = {"image_source": str(path), "prompt": compact_instruction(user_prompt, mode)}
        if mode == "ocr":
            arguments["programming_language"] = ""
        request_id = self.next_request_id
        self.next_request_id += 1
        try:
            _send_mcp(self.proc, {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": {"name": tool_name, "arguments": arguments}})
            response, stderr_lines = _read_mcp_id(self.proc, request_id, self.timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise VisionError(f"Vision MCP transport failed: {redact(exc)}") from exc
        if not response or "result" not in response:
            raise VisionError(f"Vision MCP tool call failed: {redact(response or stderr_lines)}")
        return _mcp_tool_text(response["result"]), {"source": "vision_mcp", "tool": tool_name}


def call_vision_mcp(
    path: Path,
    user_prompt: str,
    mode: str,
    timeout: int = 90,
    package: str = DEFAULT_MCP_PACKAGE,
) -> tuple[str, dict[str, Any]]:
    with VisionMCPClient(timeout=timeout, package=package) as client:
        return client.call(path, user_prompt, mode)


def _extract_chat_text(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise VisionError(f"missing Z.AI vision choices: {redact(response)}")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        texts = [item.get("text", "") for item in content if isinstance(item, dict)]
        text = "\n".join(part for part in texts if part).strip()
        if text:
            return text
    raise VisionError(f"missing Z.AI vision text: {redact(response)}")


def _extract_ocr_text(response: dict[str, Any]) -> str:
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    if not isinstance(data, dict):
        raise VisionError(f"unexpected Z.AI OCR response: {redact(response)}")
    text = data.get("md_results")
    if isinstance(text, str) and text.strip():
        return text.strip()
    results = data.get("md_results")
    if isinstance(results, list):
        text = "\n\n".join(str(item) for item in results if item).strip()
        if text:
            return text
    raise VisionError(f"missing Z.AI OCR text: {redact(response)}")


def compact_instruction(user_prompt: str, mode: str) -> str:
    task_hint = user_prompt.strip()
    if len(task_hint) > 1200:
        task_hint = task_hint[:1200] + "\n[truncated]"
    if mode == "ocr":
        return (
            "Extract the visible text, UI labels, code, tables, and error messages. "
            "Keep structure, omit decorative commentary, and return concise Markdown.\n\n"
            f"Downstream coding task context:\n{task_hint}"
        )
    return (
        "Analyze this image for a downstream coding agent that cannot see pixels. "
        "Return concise Markdown with: visible text/OCR, UI layout, key objects, colors, "
        "state/errors, coordinates only when useful, and uncertainties. Avoid speculation.\n\n"
        f"Downstream coding task context:\n{task_hint}"
    )


def call_zai_vision(
    path: Path,
    user_prompt: str,
    model: str = DEFAULT_VISION_MODEL,
    timeout: int = 90,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> tuple[str, dict[str, Any]]:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url_for_file(path, max_bytes=max_bytes)}},
                    {"type": "text", "text": compact_instruction(user_prompt, "vision")},
                ],
            }
        ],
        "thinking": {"type": "disabled"},
        "max_tokens": 1200,
    }
    response = _post_json(ZAI_CHAT_URL, payload, timeout=timeout)
    return _extract_chat_text(response), response.get("usage", {}) if isinstance(response.get("usage"), dict) else {}


def call_zai_ocr(
    path: Path,
    model: str = DEFAULT_OCR_MODEL,
    timeout: int = 90,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> tuple[str, dict[str, Any]]:
    payload = {
        "model": model,
        "file": data_url_for_file(path, max_bytes=max_bytes),
        "return_crop_images": False,
        "need_layout_visualization": False,
    }
    response = _post_json(ZAI_LAYOUT_URL, payload, timeout=timeout)
    usage = response.get("usage")
    if not isinstance(usage, dict) and isinstance(response.get("data"), dict):
        usage = response["data"].get("usage")
    return _extract_ocr_text(response), usage if isinstance(usage, dict) else {}


def choose_mode(path: Path, requested_mode: str, user_prompt: str) -> str:
    if requested_mode in {"vision", "ocr"}:
        return requested_mode
    if path.suffix.lower() in DOCUMENT_SUFFIXES:
        return "ocr"
    prompt = user_prompt.lower()
    ocr_hints = (
        "ocr",
        "extract text",
        "read text",
        "terminal",
        "stack trace",
        "error screenshot",
        "invoice",
        "receipt",
        "table",
        "文字",
        "テキスト",
        "スクショ",
        "エラー",
        "表",
    )
    return "ocr" if any(hint in prompt for hint in ocr_hints) else "vision"


def truncate_text(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[: max(0, limit - 24)].rstrip() + "\n[truncated for GLM-5.2]", True


def build_vision_context(
    image_paths: list[str],
    cwd: Path,
    user_prompt: str,
    mode: str = "auto",
    backend: str = DEFAULT_VISION_BACKEND,
    vision_model: str = DEFAULT_VISION_MODEL,
    ocr_model: str = DEFAULT_OCR_MODEL,
    mcp_package: str = DEFAULT_MCP_PACKAGE,
    timeout: int = 90,
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    optional: bool = False,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    started_at = utc_now()
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    prepared: list[dict[str, Any]] = []
    mcp_client: VisionMCPClient | None = None
    try:
        for index, raw_path in enumerate(image_paths, start=1):
            try:
                path = resolve_media_path(raw_path, cwd, allow_outside_cwd=allow_outside_cwd)
                size = validate_media_size(path, max_bytes=max_bytes)
                selected_mode = choose_mode(path, mode, user_prompt)
                prepared.append({"index": index, "raw_path": raw_path, "path": path, "mode": selected_mode, "size": size})
            except VisionError as exc:
                message = f"{raw_path}: {exc}"
                errors.append(message)
                if not optional:
                    break
        if backend == "mcp" and prepared and (not errors or optional):
            try:
                mcp_client = VisionMCPClient(timeout=timeout, package=mcp_package)
                mcp_client.__enter__()
            except (VisionError, OSError, subprocess.SubprocessError) as exc:
                errors.append(f"Vision MCP startup failed: {redact(exc)}")
        mcp_ready = backend != "mcp" or mcp_client is not None
        if (not errors or optional) and mcp_ready:
            for item in prepared:
                raw_path = str(item["raw_path"])
                path = item["path"]
                index = int(item["index"])
                selected_mode = str(item["mode"])
                size = int(item["size"])
                try:
                    assert isinstance(path, Path)
                    if backend == "mcp":
                        assert mcp_client is not None
                        text, usage = mcp_client.call(path, user_prompt, selected_mode)
                        model_name = "zai-vision-mcp"
                    elif selected_mode == "ocr":
                        text, usage = call_zai_ocr(path, model=ocr_model, timeout=timeout, max_bytes=max_bytes)
                        model_name = ocr_model
                    else:
                        text, usage = call_zai_vision(path, user_prompt, model=vision_model, timeout=timeout, max_bytes=max_bytes)
                        model_name = vision_model
                    compact_text, truncated = truncate_text(text, DEFAULT_MAX_CHARS_PER_IMAGE)
                    entries.append(
                        {
                            "index": index,
                            "path": media_display_path(path, cwd),
                            "mode": selected_mode,
                            "backend": backend,
                            "model": model_name,
                            "bytes": size,
                            "outside_workspace": not path.is_relative_to(cwd.expanduser().resolve()),
                            "summary": compact_text,
                            "truncated": truncated,
                            "usage": usage,
                        }
                    )
                except VisionError as exc:
                    message = f"{raw_path}: {exc}"
                    errors.append(message)
                    if not optional:
                        break
    finally:
        if mcp_client is not None:
            mcp_client.__exit__(None, None, None)
    ok = not errors or optional
    context_text = ""
    if entries:
        parts = [
            "GLM-5.2 text-only image context:",
            "These notes were produced by a separate vision/OCR preflight. Treat them as evidence, not pixel access.",
        ]
        for entry in entries:
            parts.append(
                "\n".join(
                    [
                        f"Image {entry['index']} ({entry['path']}; {entry['mode']}; {entry['model']}):",
                        str(entry["summary"]).strip(),
                    ]
                )
            )
        context_text, _ = truncate_text("\n\n".join(parts), max_context_chars)
    return {
        "enabled": bool(image_paths),
        "ok": ok,
        "optional": optional,
        "started_at": started_at,
        "ended_at": utc_now(),
        "mode": mode,
        "allow_outside_cwd": allow_outside_cwd,
        "entry_count": len(entries),
        "entries": entries,
        "errors": errors,
        "context_text": context_text,
    }


def merge_vision_context(prompt: str, context_text: str) -> str:
    if not context_text.strip():
        return prompt
    return f"{context_text.strip()}\n\n---\n\nOriginal GLM-5.2 task packet:\n{prompt.strip()}\n"


def sanitized_vision_context(context: dict[str, Any]) -> dict[str, Any]:
    clean = {key: value for key, value in context.items() if key != "context_text"}
    entries = []
    for entry in clean.get("entries", []):
        if not isinstance(entry, dict):
            continue
        safe_entry = {key: value for key, value in entry.items() if key != "summary"}
        safe_entry["summary_chars"] = len(str(entry.get("summary", "")))
        entries.append(safe_entry)
    clean["entries"] = entries
    return clean
