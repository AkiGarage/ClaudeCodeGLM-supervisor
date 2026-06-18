#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any

from .glm52_usage import configured_token_quota_limit, percent, quota_snapshot, redact, safe_float, safe_int, utc_now


def first_float(*values: Any) -> float | None:
    for value in values:
        parsed = safe_float(value)
        if parsed is not None:
            return parsed
    return None


def iso_from_epoch_millis(value: Any) -> str | None:
    number = safe_float(value)
    if number is None or number <= 0:
        return None
    seconds = number / 1000 if number > 10_000_000_000 else number
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError):
        return None


def _quota_candidate(name: str, value: float | None, line: str) -> dict[str, Any] | None:
    if value is None:
        return None
    return {"name": name, "value": value, "line": line}


def _best_quota_candidate(windows: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    candidates = []
    for name, window in windows.items():
        line_label = window.get("reset_description") or window.get("type") or "quota window"
        candidate = _quota_candidate(name, safe_float(window.get("used_percent")), f"{name}.used_percent ({line_label})")
        if candidate:
            candidates.append(candidate)
    primary = next((candidate for candidate in candidates if candidate["name"] == "primary"), None)
    return primary or (candidates[0] if candidates else None)


def _snapshot_base(
    *,
    ok: bool,
    phase: str,
    source: str,
    provider: str,
    captured_at: str | None = None,
    error_type: str | None = None,
    message: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "ok": ok,
        "phase": phase,
        "source": source,
        "provider": provider,
        "captured_at": captured_at or utc_now(),
    }
    if error_type:
        payload["error_type"] = error_type
    if message:
        payload["message"] = redact(message)
    if extra:
        payload.update(extra)
    return payload


def _quota_candidates(windows: dict[str, dict[str, Any]], label_key: str = "used_percent") -> list[dict[str, Any]]:
    candidates = []
    for name, window in windows.items():
        label = window.get("reset_description") or "quota window"
        candidate = _quota_candidate(name, safe_float(window.get("used_percent")), f"{name}.{label_key} ({label})")
        if candidate:
            candidates.append(candidate)
    return candidates


def normalize_codexbar_usage_snapshot(payload: Any, provider: str, phase: str = "snapshot") -> dict[str, Any]:
    rows = payload if isinstance(payload, list) else [payload] if isinstance(payload, dict) else []
    row = next((item for item in rows if isinstance(item, dict) and item.get("provider") == provider), None)
    if row is None:
        providers = [item.get("provider") for item in rows if isinstance(item, dict) and item.get("provider")]
        return _snapshot_base(
            ok=False,
            phase=phase,
            source="codexbar",
            provider=provider,
            error_type="provider_not_found",
            message=f"CodexBar payload did not include provider {provider}",
            extra={"available_providers": providers, "raw": payload},
        )
    usage = row.get("usage") if isinstance(row, dict) and isinstance(row.get("usage"), dict) else {}
    windows: dict[str, dict[str, Any]] = {}
    for name in ("primary", "secondary", "tertiary"):
        value = usage.get(name)
        if not isinstance(value, dict):
            continue
        windows[name] = {
            "name": name,
            "used_percent": first_float(value.get("usedPercent"), value.get("used_percent")),
            "reset_description": value.get("resetDescription") if isinstance(value.get("resetDescription"), str) else None,
            "resets_at": value.get("resetsAt") if isinstance(value.get("resetsAt"), str) else None,
            "window_minutes": first_float(value.get("windowMinutes"), value.get("window_minutes")),
        }
    best = _best_quota_candidate(windows)
    return {
        "ok": bool(row),
        "phase": phase,
        "source": "codexbar",
        "provider": provider,
        "captured_at": utc_now(),
        "identity": usage.get("identity") if isinstance(usage.get("identity"), dict) else {},
        "codexbar_source": row.get("source") if isinstance(row, dict) else None,
        "updated_at": usage.get("updatedAt") if isinstance(usage.get("updatedAt"), str) else None,
        "windows": windows,
        "best": {
            "tokens_total": None,
            "tokens_line": None,
            "quota_percent": best.get("value") if best else None,
            "quota_percent_line": best.get("line") if best else None,
        },
        "token_candidates": [],
        "quota_percent_candidates": _quota_candidates(windows, "usedPercent"),
        "raw": payload,
    }


def usage_snapshot_from_quota_snapshot(snapshot: dict[str, Any], phase: str = "snapshot") -> dict[str, Any]:
    windows: dict[str, dict[str, Any]] = {}
    for limit in snapshot.get("raw_limits") or []:
        if not isinstance(limit, dict):
            continue
        limit_type = limit.get("type")
        name = "primary" if limit_type == "TOKENS_LIMIT" else "secondary" if limit_type == "TIME_LIMIT" else str(limit_type or f"window_{len(windows) + 1}").lower()
        authoritative = limit_type != "TOKENS_LIMIT" or snapshot.get("token_pct_authoritative") is not False
        windows[name] = {
            "name": name,
            "type": limit_type,
            "used_percent": safe_float(limit.get("percentage")) if authoritative else None,
            "reset_description": "Tokens limit" if limit_type == "TOKENS_LIMIT" else "Time limit" if limit_type == "TIME_LIMIT" else str(limit_type or "quota window"),
            "resets_at": iso_from_epoch_millis(limit.get("nextResetTime")),
            "usage": safe_float(limit.get("usage")),
            "remaining": safe_float(limit.get("remaining")),
            "authoritative": authoritative,
        }
    if not windows:
        _fallback_quota_windows(snapshot, windows)
    best = _best_quota_candidate(windows)
    return {
        "ok": snapshot.get("ok") is True,
        "phase": phase,
        "source": "zai-api",
        "provider": snapshot.get("provider") or "zai",
        "captured_at": snapshot.get("observed_at") or utc_now(),
        "plan": snapshot.get("plan"),
        "windows": windows,
        "best": {
            "tokens_total": None,
            "tokens_line": None,
            "quota_percent": best.get("value") if best else None,
            "quota_percent_line": best.get("line") if best else None,
        },
        "token_candidates": [],
        "quota_percent_candidates": _quota_candidates(windows, "percentage"),
        "raw": {"raw_limits": snapshot.get("raw_limits"), "plan": snapshot.get("plan")},
        "error": snapshot.get("error"),
    }


def _fallback_quota_windows(snapshot: dict[str, Any], windows: dict[str, dict[str, Any]]) -> None:
    token_pct = safe_float(snapshot.get("token_used_pct"))
    time_pct = safe_float(snapshot.get("time_used_pct"))
    token_authoritative = snapshot.get("token_pct_authoritative") is not False
    if token_pct is not None:
        windows["primary"] = {
            "name": "primary",
            "type": "TOKENS_LIMIT",
            "used_percent": token_pct if token_authoritative else None,
            "reset_description": "Tokens limit",
            "resets_at": iso_from_epoch_millis(snapshot.get("reset_time")),
            "usage": safe_float(snapshot.get("token_limit")),
            "remaining": safe_float(snapshot.get("token_remaining")),
            "authoritative": token_authoritative,
        }
    if time_pct is not None:
        windows["secondary"] = {
            "name": "secondary",
            "type": "TIME_LIMIT",
            "used_percent": time_pct,
            "reset_description": "Time limit",
            "resets_at": None,
            "usage": safe_float(snapshot.get("time_limit")),
            "remaining": safe_float(snapshot.get("time_remaining")),
        }


def _codexbar_command(path: str | None = None) -> str:
    return path or os.environ.get("CODEXBAR_PATH") or shutil.which("codexbar") or "codexbar"


def snapshot_has_primary_quota_percent(snapshot: dict[str, Any]) -> bool:
    windows = snapshot.get("windows") if isinstance(snapshot.get("windows"), dict) else {}
    primary = windows.get("primary") if isinstance(windows.get("primary"), dict) else {}
    return safe_float(primary.get("used_percent")) is not None


def codexbar_usage_snapshot(
    provider: str = "zai",
    phase: str = "snapshot",
    timeout: int = 12,
    codexbar_path: str | None = None,
) -> dict[str, Any]:
    command = _codexbar_command(codexbar_path)
    captured_at = utc_now()
    try:
        proc = subprocess.run(
            [command, "usage", "--provider", provider, "--format", "json"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except FileNotFoundError:
        return _snapshot_base(ok=False, phase=phase, source="codexbar", provider=provider, captured_at=captured_at, error_type="not_found", message=f"CodexBar CLI not found: {command}", extra={"command": command})
    except subprocess.TimeoutExpired as exc:
        return _snapshot_base(
            ok=False,
            phase=phase,
            source="codexbar",
            provider=provider,
            captured_at=captured_at,
            error_type="timeout",
            message=f"CodexBar usage timed out after {timeout}s",
            extra={"command": command, "stdout_tail": redact(exc.stdout or "", 1000), "stderr_tail": redact(exc.stderr or "", 1000)},
        )
    if proc.returncode != 0:
        return _snapshot_base(
            ok=False,
            phase=phase,
            source="codexbar",
            provider=provider,
            captured_at=captured_at,
            error_type="command_failed",
            message=f"CodexBar usage failed with exit {proc.returncode}",
            extra={"command": command, "exit_code": proc.returncode, "stdout_tail": redact(proc.stdout, 1000), "stderr_tail": redact(proc.stderr, 1000)},
        )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return _snapshot_base(ok=False, phase=phase, source="codexbar", provider=provider, captured_at=captured_at, error_type="invalid_json", message=str(exc), extra={"command": command, "stdout_tail": redact(proc.stdout, 1000)})
    normalized = normalize_codexbar_usage_snapshot(payload, provider=provider, phase=phase)
    normalized["captured_at"] = captured_at
    normalized["command"] = command
    normalized["stderr_tail"] = redact(proc.stderr, 1000) if proc.stderr else ""
    return normalized


def usage_snapshot(
    provider: str = "zai",
    source: str = "auto",
    phase: str = "snapshot",
    enabled: bool = True,
    timeout: int = 12,
    codexbar_path: str | None = None,
) -> dict[str, Any]:
    mode = str(source or "auto").strip().lower()
    if mode in {"off", "false", "0", "disabled"}:
        mode = "none"
    if not enabled or mode == "none":
        return _snapshot_base(ok=False, phase=phase, source="none", provider=provider, error_type="disabled", message="usage snapshot disabled")
    if mode not in {"auto", "zai-api", "codexbar"}:
        return _snapshot_base(ok=False, phase=phase, source=mode, provider=provider, error_type="invalid_source", message="usage snapshot source must be auto, zai-api, codexbar, or none")
    if mode == "codexbar":
        return codexbar_usage_snapshot(provider=provider, phase=phase, timeout=timeout, codexbar_path=codexbar_path)
    direct = usage_snapshot_from_quota_snapshot(quota_snapshot(provider=provider, enabled=True, timeout=timeout), phase=phase)
    if mode == "zai-api" or (direct.get("ok") and snapshot_has_primary_quota_percent(direct)):
        return direct
    fallback = codexbar_usage_snapshot(provider=provider, phase=phase, timeout=timeout, codexbar_path=codexbar_path)
    if fallback.get("ok"):
        fallback["fallback_from"] = {
            "source": direct.get("source"),
            "error_type": direct.get("error_type") or "snapshot_failed",
            "message": direct.get("message") or direct.get("error"),
        }
        return fallback
    if direct.get("ok"):
        direct["codexbar_fallback_error"] = fallback
        return direct
    fallback["fallback_from"] = direct
    return fallback


def quota_window_delta(before_window: dict[str, Any] | None, after_window: dict[str, Any] | None) -> dict[str, Any]:
    before = safe_float((before_window or {}).get("used_percent"))
    after = safe_float((after_window or {}).get("used_percent"))
    before_reset = (before_window or {}).get("resets_at")
    after_reset = (after_window or {}).get("resets_at")
    reset_changed = bool(before_reset and after_reset and before_reset != after_reset)
    raw_delta = after - before if before is not None and after is not None else None
    return {
        "before_used_percent": before,
        "after_used_percent": after,
        "used_percent_delta": round(raw_delta, 4) if raw_delta is not None and raw_delta >= 0 and not reset_changed else None,
        "reset_changed": reset_changed,
        "before_resets_at": before_reset,
        "after_resets_at": after_reset,
        "reset_description": (after_window or {}).get("reset_description") or (before_window or {}).get("reset_description"),
    }


def derive_quota_usage(before_snapshot: dict[str, Any], after_snapshot: dict[str, Any]) -> dict[str, Any]:
    available = before_snapshot.get("ok") is True and after_snapshot.get("ok") is True
    sources = {item for item in (before_snapshot.get("source"), after_snapshot.get("source")) if item}
    result = {
        "available": available,
        "source": next(iter(sources)) if available and len(sources) == 1 else "mixed" if available else None,
        "provider": after_snapshot.get("provider") or before_snapshot.get("provider"),
        "quota_percent_direction": "used",
        "quota_percent_before": None,
        "quota_percent_after": None,
        "quota_percent_used": None,
        "windows": {},
    }
    if not available:
        return result
    before_windows = before_snapshot.get("windows") if isinstance(before_snapshot.get("windows"), dict) else {}
    after_windows = after_snapshot.get("windows") if isinstance(after_snapshot.get("windows"), dict) else {}
    for name in sorted(set(before_windows) | set(after_windows)):
        result["windows"][name] = quota_window_delta(before_windows.get(name), after_windows.get(name))
    primary = result["windows"].get("primary") or next(iter(result["windows"].values()), None)
    if primary:
        result["quota_percent_before"] = primary.get("before_used_percent")
        result["quota_percent_after"] = primary.get("after_used_percent")
        result["quota_percent_used"] = primary.get("used_percent_delta")
    return result


def normalized_usage_from_summary(usage_summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_tokens": safe_int(usage_summary.get("totalTokens")) or 0,
        "input_tokens": safe_int(usage_summary.get("inputTokens")) or 0,
        "output_tokens": safe_int(usage_summary.get("outputTokens")) or 0,
        "cache_read_tokens": safe_int(usage_summary.get("cacheReadInputTokens")) or 0,
        "cache_write_tokens": safe_int(usage_summary.get("cacheCreationInputTokens")) or 0,
        "web_search_requests": safe_int(usage_summary.get("webSearchRequests")) or 0,
        "cost_usd": safe_float(usage_summary.get("costUSD")) or 0.0,
    }


def _percent_matches(used: int, limit: int, observed_percent: float | None) -> bool:
    if observed_percent is None or limit <= 0:
        return False
    expected = percent(used, limit)
    if expected is None:
        return False
    return abs(expected - observed_percent) <= 0.01


def infer_token_quota_limit(snapshot: dict[str, Any]) -> dict[str, Any]:
    raw_usage = safe_int(snapshot.get("token_limit"))
    raw_remaining = safe_int(snapshot.get("token_remaining"))
    observed_percent = safe_float(snapshot.get("token_used_pct"))
    if raw_usage is None:
        for limit in snapshot.get("raw_limits") or []:
            if isinstance(limit, dict) and limit.get("type") == "TOKENS_LIMIT":
                raw_usage = safe_int(limit.get("usage"))
                raw_remaining = safe_int(limit.get("remaining"))
                observed_percent = safe_float(limit.get("percentage"))
                break
    if raw_usage is None or raw_remaining is None:
        return {"available": False, "token_quota_limit": None, "source": None, "error": "token_quota_limit_unavailable"}

    usage_plus_remaining = raw_usage + raw_remaining
    if usage_plus_remaining > 0 and _percent_matches(raw_usage, usage_plus_remaining, observed_percent):
        return {
            "available": True,
            "token_quota_limit": usage_plus_remaining,
            "source": "zai_api.quota.tokens_limit.usage_plus_remaining",
            "error": None,
        }

    used_from_limit_remaining = raw_usage - raw_remaining
    if raw_usage > 0 and used_from_limit_remaining >= 0 and _percent_matches(used_from_limit_remaining, raw_usage, observed_percent):
        return {
            "available": True,
            "token_quota_limit": raw_usage,
            "source": "zai_api.quota.tokens_limit.limit_minus_remaining",
            "error": None,
        }

    return {"available": False, "token_quota_limit": None, "source": None, "error": "token_quota_limit_shape_unrecognized"}


def provider_usage_quota_delta(provider_usage_diff: dict[str, Any], quota_snapshot_after: dict[str, Any]) -> dict[str, Any]:
    if provider_usage_diff.get("available") is not True:
        return {
            "available": False,
            "quota_percent_used": None,
            "source": provider_usage_diff.get("source"),
            "error": provider_usage_diff.get("error") or "provider_usage_delta_unavailable",
        }
    tokens_delta = safe_int(provider_usage_diff.get("tokens_delta"))
    if tokens_delta is None or tokens_delta < 0:
        return {
            "available": False,
            "quota_percent_used": None,
            "source": provider_usage_diff.get("source"),
            "error": "provider_usage_token_delta_unavailable",
        }
    limit = infer_token_quota_limit(quota_snapshot_after)
    token_quota_limit = safe_int(limit.get("token_quota_limit"))
    quota_percent_used = percent(tokens_delta, token_quota_limit)
    if limit.get("available") is not True or quota_percent_used is None:
        return {
            "available": False,
            "quota_percent_used": None,
            "source": provider_usage_diff.get("source"),
            "error": limit.get("error") or "token_quota_limit_unavailable",
        }
    return {
        "available": True,
        "quota_percent_used": quota_percent_used,
        "source": f"{provider_usage_diff.get('source')}+{limit.get('source')}",
        "tokens_delta": tokens_delta,
        "token_quota_limit": token_quota_limit,
        "token_quota_limit_source": limit.get("source"),
        "error": None,
    }


def build_usage_accounting(
    usage_summary: dict[str, Any],
    before_snapshot: dict[str, Any],
    after_snapshot: dict[str, Any],
    provider_usage_diff: dict[str, Any] | None = None,
    quota_snapshot_after: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalized_usage_from_summary(usage_summary)
    quota = derive_quota_usage(before_snapshot, after_snapshot)
    quota_limit = configured_token_quota_limit()
    estimated_quota_percent = percent(normalized["total_tokens"], quota_limit)
    provider_quota = provider_usage_quota_delta(provider_usage_diff or {}, quota_snapshot_after or {})
    measured_quota_percent = quota["quota_percent_used"]
    measured_quota_source = quota["source"]
    provider_zero_delta_lagged = (
        provider_quota.get("available") is True
        and safe_int(provider_quota.get("tokens_delta")) == 0
        and normalized["total_tokens"] > 0
    )
    snapshot_delta_resolution_limited = (
        measured_quota_percent == 0
        and normalized["total_tokens"] > 0
        and (provider_quota.get("available") is not True or provider_zero_delta_lagged)
    )
    if provider_quota.get("available") is True and not provider_zero_delta_lagged:
        measured_quota_percent = provider_quota.get("quota_percent_used")
        measured_quota_source = provider_quota.get("source")
        snapshot_delta_resolution_limited = False
    elif snapshot_delta_resolution_limited:
        measured_quota_percent = None
    if measured_quota_percent is not None:
        quota_percent_status = "measured"
        quota_percent_source = measured_quota_source
        unavailable_reason = None
        estimated_quota_percent = None
        quota_limit = None
    elif estimated_quota_percent is not None:
        quota_percent_status = "estimated"
        quota_percent_source = "configured_token_quota_limit"
        unavailable_reason = None
    else:
        quota_percent_status = "unavailable"
        quota_percent_source = quota["source"]
        if normalized["total_tokens"] <= 0:
            unavailable_reason = "token_usage_missing"
        elif not quota["available"]:
            unavailable_reason = "usage_snapshot_unavailable"
        elif provider_zero_delta_lagged:
            unavailable_reason = "provider_usage_counter_lagged"
        elif snapshot_delta_resolution_limited:
            unavailable_reason = "quota_percent_resolution_too_coarse"
        else:
            unavailable_reason = "authoritative_quota_delta_unavailable"
    return {
        "tokens_source": "claude_code_model_usage",
        "tokens_used": normalized["total_tokens"],
        "tokens_total": normalized["total_tokens"],
        "input_tokens": normalized["input_tokens"],
        "output_tokens": normalized["output_tokens"],
        "cache_read_tokens": normalized["cache_read_tokens"],
        "cache_write_tokens": normalized["cache_write_tokens"],
        "web_search_requests": normalized["web_search_requests"],
        "cost_usd": normalized["cost_usd"],
        "quota_source": quota["source"],
        "quota_provider": quota["provider"],
        "quota_percent_direction": quota["quota_percent_direction"],
        "quota_percent_before": quota["quota_percent_before"],
        "quota_percent_after": quota["quota_percent_after"],
        "quota_percent_used": measured_quota_percent,
        "quota_percent_status": quota_percent_status,
        "quota_percent_unavailable_reason": unavailable_reason,
        "quota_percent_source": quota_percent_source,
        "estimated_quota_percent_used": estimated_quota_percent,
        "estimated_quota_token_limit": quota_limit,
        "provider_usage_delta_tokens": None,
        "provider_usage_lag_suspected": None,
        "provider_quota_percent_basis_tokens": provider_quota.get("tokens_delta"),
        "provider_quota_token_limit": provider_quota.get("token_quota_limit"),
        "provider_quota_token_limit_source": provider_quota.get("token_quota_limit_source"),
        "provider_quota_percent_error": provider_quota.get("error"),
        "quota_percent_resolution_limited": snapshot_delta_resolution_limited,
        "provider_usage_zero_delta_lagged": provider_zero_delta_lagged,
        "quota_windows": quota["windows"],
    }
