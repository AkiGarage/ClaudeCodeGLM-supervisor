#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._runtime import default_usage_log, runtime_root


ROOT = runtime_root()
DEFAULT_USAGE_LOG = default_usage_log()
ZAI_QUOTA_URL = "https://api.z.ai/api/monitor/usage/quota/limit"
ZAI_MODEL_USAGE_URL = "https://api.z.ai/api/monitor/usage/model-usage"
GLM52_MODEL_KEYS = ("claude-opus-4-6[1m]", "claude-opus-4-8[1m]", "glm-5.2[1m]")
TOKEN_FIELDS = (
    "inputTokens",
    "outputTokens",
    "cacheReadInputTokens",
    "cacheCreationInputTokens",
    "webSearchRequests",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def redact(text: Any, limit: int = 500) -> str:
    value = str(text)
    value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(cliproxyapi-key-)[A-Za-z0-9._-]+", r"\1[REDACTED]", value)
    value = re.sub(
        r"(?i)(api[_-]?key|token|authorization)([\"':=\s]+)[^,\s\"'}]+",
        r"\1\2[REDACTED]",
        value,
    )
    return value[:limit]


def safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def percent(used: int | None, limit: int | None) -> float | None:
    if used is None or limit is None or limit <= 0:
        return None
    return round((used / limit) * 100, 6)


def empty_usage_summary() -> dict[str, Any]:
    return {
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadInputTokens": 0,
        "cacheCreationInputTokens": 0,
        "webSearchRequests": 0,
        "totalTokens": 0,
        "costUSD": 0.0,
        "models": {},
    }


def empty_model_summary() -> dict[str, Any]:
    return {
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadInputTokens": 0,
        "cacheCreationInputTokens": 0,
        "webSearchRequests": 0,
        "totalTokens": 0,
        "costUSD": 0.0,
        "contextWindow": None,
        "maxOutputTokens": None,
    }


def summarize_model_usage(model_usage: dict[str, Any] | None) -> dict[str, Any]:
    summary = empty_usage_summary()
    if not isinstance(model_usage, dict):
        return summary
    for model_key, raw_usage in sorted(model_usage.items()):
        if not isinstance(raw_usage, dict):
            continue
        row: dict[str, Any] = {}
        for field in TOKEN_FIELDS:
            row[field] = safe_int(raw_usage.get(field)) or 0
            summary[field] += row[field]
        row["costUSD"] = safe_float(raw_usage.get("costUSD")) or 0.0
        row["contextWindow"] = safe_int(raw_usage.get("contextWindow"))
        row["maxOutputTokens"] = safe_int(raw_usage.get("maxOutputTokens"))
        row["totalTokens"] = (
            row["inputTokens"]
            + row["outputTokens"]
            + row["cacheReadInputTokens"]
            + row["cacheCreationInputTokens"]
        )
        summary["costUSD"] += row["costUSD"]
        summary["models"][model_key] = row
    summary["totalTokens"] = (
        summary["inputTokens"]
        + summary["outputTokens"]
        + summary["cacheReadInputTokens"]
        + summary["cacheCreationInputTokens"]
    )
    summary["costUSD"] = round(summary["costUSD"], 12)
    return summary


def aggregate_usage_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    total = empty_usage_summary()
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        for field in TOKEN_FIELDS:
            total[field] += safe_int(summary.get(field)) or 0
        total["costUSD"] += safe_float(summary.get("costUSD")) or 0.0
        models = summary.get("models", {})
        if not isinstance(models, dict):
            continue
        for model_key, row in models.items():
            existing = total["models"].setdefault(model_key, empty_model_summary())
            for field in TOKEN_FIELDS:
                existing[field] += safe_int(row.get(field)) or 0
            existing["costUSD"] += safe_float(row.get("costUSD")) or 0.0
            existing["contextWindow"] = row.get("contextWindow") or existing.get("contextWindow")
            existing["maxOutputTokens"] = row.get("maxOutputTokens") or existing.get("maxOutputTokens")
    for row in total["models"].values():
        row["totalTokens"] = (
            row["inputTokens"]
            + row["outputTokens"]
            + row["cacheReadInputTokens"]
            + row["cacheCreationInputTokens"]
        )
        row["costUSD"] = round(row["costUSD"], 12)
    total["totalTokens"] = (
        total["inputTokens"]
        + total["outputTokens"]
        + total["cacheReadInputTokens"]
        + total["cacheCreationInputTokens"]
    )
    total["costUSD"] = round(total["costUSD"], 12)
    return total


def summarize_attempts(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    summaries = []
    for attempt in attempts:
        payload = attempt.get("payload", {}) if isinstance(attempt, dict) else {}
        summaries.append(summarize_model_usage(payload.get("modelUsage", {})))
    return aggregate_usage_summaries(summaries)


def preferred_model_usage(model_usage: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(model_usage, dict):
        return {}
    for key in GLM52_MODEL_KEYS:
        usage = model_usage.get(key)
        if isinstance(usage, dict):
            return {"modelKey": key, **usage}
    return {}


def normalize_quota_payload(raw: dict[str, Any], provider: str, source: str) -> dict[str, Any]:
    quota = raw.get("quota") if isinstance(raw.get("quota"), dict) else raw
    error = quota.get("error") if isinstance(quota, dict) else "invalid quota payload"
    rolling_used = safe_int(quota.get("rolling_5h_used")) if isinstance(quota, dict) else None
    rolling_limit = safe_int(quota.get("rolling_5h_limit")) if isinstance(quota, dict) else None
    weekly_used = safe_int(quota.get("weekly_used")) if isinstance(quota, dict) else None
    weekly_limit = safe_int(quota.get("weekly_limit")) if isinstance(quota, dict) else None
    token_pct = safe_float(quota.get("token_used_pct")) if isinstance(quota, dict) else None
    token_pct_authoritative = bool(quota.get("token_pct_authoritative", True)) if isinstance(quota, dict) else True
    candidates = [
        ("token_used_pct", token_pct if token_pct_authoritative else None),
        ("rolling_5h_used_pct", percent(rolling_used, rolling_limit)),
        ("weekly_used_pct", percent(weekly_used, weekly_limit)),
    ]
    used_pct_source, used_pct = next(((key, val) for key, val in candidates if val is not None), (None, None))
    return {
        "ok": error in (None, ""),
        "observed_at": utc_now(),
        "source": source,
        "provider": str(raw.get("provider") or provider),
        "plan": quota.get("plan") if isinstance(quota, dict) else None,
        "used_pct": used_pct,
        "used_pct_source": used_pct_source,
        "token_used_pct": token_pct,
        "token_pct_authoritative": token_pct_authoritative,
        "token_pct_note": quota.get("token_pct_note") if isinstance(quota, dict) else None,
        "token_remaining": safe_int(quota.get("token_remaining")) if isinstance(quota, dict) else None,
        "token_limit": safe_int(quota.get("token_limit")) if isinstance(quota, dict) else None,
        "time_used_pct": safe_float(quota.get("time_used_pct")) if isinstance(quota, dict) else None,
        "time_remaining": safe_int(quota.get("time_remaining")) if isinstance(quota, dict) else None,
        "time_limit": safe_int(quota.get("time_limit")) if isinstance(quota, dict) else None,
        "rolling_5h_used": rolling_used,
        "rolling_5h_remaining": safe_int(quota.get("rolling_5h_remaining")) if isinstance(quota, dict) else None,
        "rolling_5h_limit": rolling_limit,
        "weekly_used": weekly_used,
        "weekly_remaining": safe_int(quota.get("weekly_remaining")) if isinstance(quota, dict) else None,
        "weekly_limit": weekly_limit,
        "reset_time": quota.get("reset_time") if isinstance(quota, dict) else None,
        "error": redact(error) if error else None,
        "raw_limits": quota.get("raw_limits") if isinstance(quota, dict) else None,
    }


def _env_or_launchctl(var_name: str) -> str:
    value = os.environ.get(var_name, "")
    if value:
        return value
    launchctl = shutil.which("launchctl") or next(
        (path for path in ("/bin/launchctl", "/usr/bin/launchctl") if Path(path).exists()),
        None,
    )
    if not launchctl:
        return ""
    try:
        proc = subprocess.run(
            [launchctl, "getenv", var_name],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _zai_api_key() -> str:
    for var_name in ("ZAI_API_KEY", "Z_AI_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        value = _env_or_launchctl(var_name)
        if value:
            return value
    return ""


def configured_token_quota_limit() -> int | None:
    for var_name in ("GLM52_TOKEN_QUOTA_LIMIT", "GLM_TOKEN_QUOTA_LIMIT", "ZAI_TOKEN_QUOTA_LIMIT"):
        value = _env_or_launchctl(var_name)
        limit = safe_int(value)
        if limit and limit > 0:
            return limit
    return None


def _limit_summary(limit: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": limit.get("type"),
        "percentage": safe_float(limit.get("percentage")),
        "usage": safe_int(limit.get("usage")),
        "remaining": safe_int(limit.get("remaining")),
        "nextResetTime": safe_int(limit.get("nextResetTime")),
    }


def quota_state_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": snapshot.get("provider"),
        "plan": snapshot.get("plan"),
        "source": snapshot.get("source"),
        "timeRemaining": safe_int(snapshot.get("time_remaining")),
        "timeLimit": safe_int(snapshot.get("time_limit")),
        "timeUsedPct": safe_float(snapshot.get("time_used_pct")),
        "tokenRemaining": safe_int(snapshot.get("token_remaining")),
        "tokenLimit": safe_int(snapshot.get("token_limit")),
        "tokenUsedPct": safe_float(snapshot.get("token_used_pct")),
        "tokenPctAuthoritative": snapshot.get("token_pct_authoritative"),
        "resetTime": safe_int(snapshot.get("reset_time")),
        "error": snapshot.get("error"),
    }


def _zai_quota_payload(timeout: int) -> dict[str, Any]:
    api_key = _zai_api_key()
    if not api_key:
        return {"provider": "zai", "error": "No API key in ZAI_API_KEY, Z_AI_API_KEY, or ANTHROPIC_AUTH_TOKEN"}
    request = urllib.request.Request(
        ZAI_QUOTA_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return {"provider": "zai", "error": f"HTTP {exc.code}"}
    except urllib.error.URLError as exc:
        return {"provider": "zai", "error": f"Connection error: {redact(exc.reason)}"}
    data = json.loads(body)
    if not isinstance(data, dict) or data.get("code") != 200:
        return {"provider": "zai", "error": f"Unexpected Z.AI quota response: {redact(data)}"}
    quota_data = data.get("data", {})
    limits = quota_data.get("limits", []) if isinstance(quota_data, dict) else []
    payload: dict[str, Any] = {
        "provider": "zai",
        "plan": quota_data.get("level") if isinstance(quota_data, dict) else None,
        "raw_limits": [_limit_summary(limit) for limit in limits if isinstance(limit, dict)],
        "error": None,
    }
    for limit in limits:
        if not isinstance(limit, dict):
            continue
        if limit.get("type") == "TOKENS_LIMIT":
            token_limit = safe_int(limit.get("usage"))
            token_remaining = safe_int(limit.get("remaining"))
            payload["token_used_pct"] = safe_float(limit.get("percentage"))
            payload["token_remaining"] = token_remaining
            payload["token_limit"] = token_limit
            payload["reset_time"] = safe_int(limit.get("nextResetTime"))
            payload["token_pct_authoritative"] = token_limit is not None or token_remaining is not None
            if not payload["token_pct_authoritative"]:
                payload["token_pct_note"] = "Z.AI returned TOKENS_LIMIT.percentage without token usage/remaining counts; do not treat it as a delta-safe consumed quota percentage."
        elif limit.get("type") == "TIME_LIMIT":
            payload["time_used_pct"] = safe_float(limit.get("percentage"))
            payload["time_remaining"] = safe_int(limit.get("remaining"))
            payload["time_limit"] = safe_int(limit.get("usage"))
    return payload


def quota_snapshot(provider: str = "zai", enabled: bool = True, timeout: int = 12) -> dict[str, Any]:
    if not enabled:
        return {
            "ok": False,
            "observed_at": utc_now(),
            "source": "disabled",
            "provider": provider,
            "used_pct": None,
            "used_pct_source": None,
            "error": "quota snapshot disabled",
        }
    try:
        return normalize_quota_payload(_zai_quota_payload(timeout), provider=provider, source="zai_api.quota")
    except Exception as exc:  # noqa: BLE001 - quota logging must not fail delegation.
        return {
            "ok": False,
            "observed_at": utc_now(),
            "source": "zai_api.quota",
            "provider": provider,
            "used_pct": None,
            "used_pct_source": None,
            "error": redact(exc),
        }


def _local_day_window() -> tuple[str, str]:
    now = datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    return start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")


def _model_usage_error(message: str, start_time: str, end_time: str) -> dict[str, Any]:
    return {"provider": "zai", "error": message, "window_start": start_time, "window_end": end_time}


def _model_summary_rows(total_usage: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in total_usage.get("modelSummaryList", []):
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "modelName": row.get("modelName"),
                "totalTokens": safe_int(row.get("totalTokens")) or 0,
                "sortOrder": safe_int(row.get("sortOrder")),
            }
        )
    return rows


def _model_usage_record(usage_data: dict[str, Any], start_time: str, end_time: str) -> dict[str, Any]:
    total_usage = usage_data.get("totalUsage") if isinstance(usage_data.get("totalUsage"), dict) else {}
    return {
        "provider": "zai",
        "window_start": start_time,
        "window_end": end_time,
        "granularity": usage_data.get("granularity"),
        "x_time_len": len(usage_data.get("x_time", [])) if isinstance(usage_data.get("x_time"), list) else None,
        "total_tokens_usage": safe_int(total_usage.get("totalTokensUsage")),
        "total_model_call_count": safe_int(total_usage.get("totalModelCallCount")),
        "model_summary": _model_summary_rows(total_usage),
        "error": None,
    }


def _zai_model_usage_payload(timeout: int) -> dict[str, Any]:
    api_key = _zai_api_key()
    if not api_key:
        return {"provider": "zai", "error": "No API key in ZAI_API_KEY, Z_AI_API_KEY, or ANTHROPIC_AUTH_TOKEN"}
    start_time, end_time = _local_day_window()
    query = urllib.parse.urlencode({"startTime": start_time, "endTime": end_time})
    request = urllib.request.Request(
        f"{ZAI_MODEL_USAGE_URL}?{query}",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return _model_usage_error(f"HTTP {exc.code}", start_time, end_time)
    except urllib.error.URLError as exc:
        return _model_usage_error(f"Connection error: {redact(exc.reason)}", start_time, end_time)
    data = json.loads(body)
    if not isinstance(data, dict) or data.get("code") != 200:
        return _model_usage_error(f"Unexpected Z.AI model usage response: {redact(data)}", start_time, end_time)
    usage_data = data.get("data", {})
    if not isinstance(usage_data, dict):
        return _model_usage_error(
            "Unexpected Z.AI model usage response: missing data",
            start_time,
            end_time,
        )
    return _model_usage_record(usage_data, start_time, end_time)


def provider_usage_snapshot(provider: str = "zai", enabled: bool = True, timeout: int = 12) -> dict[str, Any]:
    if not enabled:
        return {
            "ok": False,
            "observed_at": utc_now(),
            "source": "disabled",
            "provider": provider,
            "error": "provider usage snapshot disabled",
        }
    try:
        raw = _zai_model_usage_payload(timeout)
        error = raw.get("error")
        return {
            "ok": error in (None, ""),
            "observed_at": utc_now(),
            "source": "zai_api.model_usage",
            "provider": str(raw.get("provider") or provider),
            "window_start": raw.get("window_start"),
            "window_end": raw.get("window_end"),
            "granularity": raw.get("granularity"),
            "x_time_len": raw.get("x_time_len"),
            "total_tokens_usage": safe_int(raw.get("total_tokens_usage")),
            "total_model_call_count": safe_int(raw.get("total_model_call_count")),
            "model_summary": raw.get("model_summary") if isinstance(raw.get("model_summary"), list) else [],
            "error": redact(error) if error else None,
        }
    except Exception as exc:  # noqa: BLE001 - usage logging must not fail delegation.
        return {
            "ok": False,
            "observed_at": utc_now(),
            "source": "zai_api.model_usage",
            "provider": provider,
            "error": redact(exc),
        }


def quota_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    source = after.get("used_pct_source")
    if not source or source != before.get("used_pct_source"):
        return {"available": False, "used_pct_delta": None, "source": source, "error": "quota pct unavailable"}
    before_pct = safe_float(before.get("used_pct"))
    after_pct = safe_float(after.get("used_pct"))
    if before_pct is None or after_pct is None or after_pct < before_pct:
        return {"available": False, "used_pct_delta": None, "source": source, "error": "quota pct reset or missing"}
    return {
        "available": True,
        "used_pct_delta": round(after_pct - before_pct, 6),
        "source": source,
        "before_used_pct": before_pct,
        "after_used_pct": after_pct,
        "error": None,
    }


def usage_counter_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    source = after.get("source")
    if not source or source != before.get("source"):
        return {"available": False, "tokens_delta": None, "source": source, "error": "provider usage source unavailable"}
    if after.get("window_start") != before.get("window_start") or after.get("window_end") != before.get("window_end"):
        return {"available": False, "tokens_delta": None, "source": source, "error": "provider usage window changed"}
    before_tokens = safe_int(before.get("total_tokens_usage"))
    after_tokens = safe_int(after.get("total_tokens_usage"))
    if before_tokens is None or after_tokens is None or after_tokens < before_tokens:
        return {"available": False, "tokens_delta": None, "source": source, "error": "provider usage counter reset or missing"}
    before_calls = safe_int(before.get("total_model_call_count"))
    after_calls = safe_int(after.get("total_model_call_count"))
    return {
        "available": True,
        "tokens_delta": after_tokens - before_tokens,
        "model_call_count_delta": after_calls - before_calls if before_calls is not None and after_calls is not None else None,
        "source": source,
        "window_start": after.get("window_start"),
        "window_end": after.get("window_end"),
        "before_total_tokens_usage": before_tokens,
        "after_total_tokens_usage": after_tokens,
        "error": None,
    }


def _quota_display_bits(quota_state: dict[str, Any]) -> list[str]:
    bits = []
    if quota_state.get("timeRemaining") is not None and quota_state.get("timeLimit") is not None:
        bits.append(f"ZAI time quota {quota_state['timeRemaining']}/{quota_state['timeLimit']} remaining")
    if quota_state.get("tokenUsedPct") is not None:
        note = " raw/non-authoritative" if quota_state.get("tokenPctAuthoritative") is False else ""
        bits.append(f"ZAI token pct {quota_state['tokenUsedPct']}%{note}")
    return bits


def _provider_usage_state(diff: dict[str, Any], total_tokens: int) -> dict[str, Any]:
    available = diff.get("available") is True
    tokens_delta = safe_int(diff.get("tokens_delta")) if available else None
    after_total = safe_int(diff.get("after_total_tokens_usage")) if available else None
    bits = []
    if tokens_delta is not None and tokens_delta > 0:
        suffix = f" (total {after_total})" if after_total is not None else ""
        bits.append(f"ZAI daily counter +{tokens_delta} tokens{suffix}")
    elif tokens_delta == 0 and after_total is not None:
        bits.append(f"ZAI daily total {after_total} tokens; no immediate counter delta")
    return {
        "available": available,
        "tokens_delta": tokens_delta,
        "after_total": after_total,
        "lag_suspected": available and tokens_delta is not None and tokens_delta < total_tokens,
        "model_call_count_delta": safe_int(diff.get("model_call_count_delta")) if available else None,
        "source": diff.get("source"),
        "display_bits": bits,
    }


def _base_consumption_display(total_tokens: int, estimated_pct_delta: float | None, quota_limit: int | None) -> str:
    if estimated_pct_delta is not None and quota_limit:
        return f"{total_tokens} tokens; estimated quota delta {estimated_pct_delta:.6f}% of configured {quota_limit}"
    return f"{total_tokens} tokens; provider quota pct unavailable"


def consumption_summary(
    usage_summary: dict[str, Any],
    quota_diff: dict[str, Any],
    provider_usage_diff: dict[str, Any] | None = None,
    quota_after: dict[str, Any] | None = None,
    token_quota_limit: int | None = None,
    usage_accounting: dict[str, Any] | None = None,
) -> dict[str, Any]:
    total_tokens = safe_int(usage_summary.get("totalTokens")) or 0
    quota_limit = token_quota_limit if token_quota_limit is not None else configured_token_quota_limit()
    estimated_pct_delta = percent(total_tokens, quota_limit)
    provider_available = quota_diff.get("available") is True
    provider_usage_diff = provider_usage_diff or {}
    quota_state = quota_state_summary(quota_after or {})
    provider_usage = _provider_usage_state(provider_usage_diff, total_tokens)
    display_bits = [
        _base_consumption_display(total_tokens, estimated_pct_delta, quota_limit),
        *_quota_display_bits(quota_state),
        *provider_usage["display_bits"],
    ]
    snapshot_quota_delta = safe_float((usage_accounting or {}).get("quota_percent_used"))
    if snapshot_quota_delta is not None:
        source = (usage_accounting or {}).get("quota_percent_source") or (usage_accounting or {}).get("quota_source") or "usage snapshot"
        display_bits.append(f"usage snapshot quota +{snapshot_quota_delta}% ({source})")
    return {
        "source": "claude_code_model_usage",
        "totalTokens": total_tokens,
        "inputTokens": safe_int(usage_summary.get("inputTokens")) or 0,
        "outputTokens": safe_int(usage_summary.get("outputTokens")) or 0,
        "cacheReadInputTokens": safe_int(usage_summary.get("cacheReadInputTokens")) or 0,
        "cacheCreationInputTokens": safe_int(usage_summary.get("cacheCreationInputTokens")) or 0,
        "costUSD": safe_float(usage_summary.get("costUSD")) or 0.0,
        "providerQuotaPctAvailable": provider_available,
        "providerQuotaPctDelta": quota_diff.get("used_pct_delta") if provider_available else None,
        "providerQuotaPctSource": quota_diff.get("source"),
        "providerUsageCounterAvailable": provider_usage["available"],
        "providerUsageTokensDelta": provider_usage["tokens_delta"],
        "providerUsageModelCallCountDelta": provider_usage["model_call_count_delta"],
        "providerUsageAfterTotalTokens": provider_usage["after_total"],
        "providerUsageLagSuspected": provider_usage["lag_suspected"],
        "providerUsageSource": provider_usage["source"],
        "quotaState": quota_state,
        "snapshotQuotaPctAvailable": snapshot_quota_delta is not None,
        "snapshotQuotaPctDelta": snapshot_quota_delta,
        "snapshotQuotaPctSource": (usage_accounting or {}).get("quota_percent_source") or (usage_accounting or {}).get("quota_source"),
        "estimatedQuotaPctDelta": estimated_pct_delta,
        "estimatedQuotaTokenLimit": quota_limit,
        "display": "; ".join(display_bits),
    }


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
