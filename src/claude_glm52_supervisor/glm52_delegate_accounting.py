#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Any

from .glm52_usage import (
    append_jsonl,
    consumption_summary,
    provider_usage_snapshot,
    quota_delta,
    quota_snapshot,
    quota_state_summary,
    usage_counter_delta,
)
from .glm52_usage_snapshots import build_usage_accounting, normalized_usage_from_summary, usage_snapshot


def resolve_log_path(raw_path: str, root: Path) -> Path:
    path = Path(raw_path).expanduser()
    return path if path.is_absolute() else root / path


def capture_usage_accounting(
    args: Any,
    usage_summary: dict[str, Any],
    quota_before: dict[str, Any],
    provider_usage_before: dict[str, Any],
    usage_snapshot_before: dict[str, Any],
    snapshot_enabled: bool,
) -> dict[str, Any]:
    quota_after = quota_snapshot(provider=args.quota_provider, enabled=snapshot_enabled, timeout=args.quota_timeout)
    provider_usage_after = provider_usage_snapshot(provider=args.quota_provider, enabled=snapshot_enabled, timeout=args.quota_timeout)
    usage_snapshot_after = usage_snapshot(
        provider=args.quota_provider,
        source=args.usage_snapshot_source,
        phase="after",
        enabled=snapshot_enabled,
        timeout=args.quota_timeout,
        codexbar_path=args.codexbar_path,
    )
    usage_normalized = normalized_usage_from_summary(usage_summary)
    quota_diff = quota_delta(quota_before, quota_after)
    provider_usage_diff = usage_counter_delta(provider_usage_before, provider_usage_after)
    usage_accounting = build_usage_accounting(
        usage_summary,
        usage_snapshot_before,
        usage_snapshot_after,
        provider_usage_diff=provider_usage_diff,
        quota_snapshot_after=quota_after,
    )
    usage_accounting["provider_usage_delta_tokens"] = (
        provider_usage_diff.get("tokens_delta") if provider_usage_diff.get("available") is True else None
    )
    usage_accounting["provider_usage_lag_suspected"] = (
        provider_usage_diff.get("available") is True
        and usage_accounting["provider_usage_delta_tokens"] is not None
        and usage_accounting["provider_usage_delta_tokens"] < (usage_summary.get("totalTokens") or 0)
    )
    usage_accounting["provider_usage_source"] = provider_usage_diff.get("source")
    quota_state = quota_state_summary(quota_after)
    return {
        "usage_normalized": usage_normalized,
        "usage_accounting": usage_accounting,
        "usage_snapshots": {"before": usage_snapshot_before, "after": usage_snapshot_after},
        "consumptionSummary": consumption_summary(
            usage_summary,
            quota_diff,
            provider_usage_diff=provider_usage_diff,
            quota_after=quota_after,
            usage_accounting=usage_accounting,
        ),
        "quotaBefore": quota_before,
        "quotaAfter": quota_after,
        "quotaDelta": quota_diff,
        "quotaState": quota_state,
        "providerUsageBefore": provider_usage_before,
        "providerUsageAfter": provider_usage_after,
        "providerUsageDelta": provider_usage_diff,
    }


def append_delegate_usage_log(args: Any, result: dict[str, Any], root: Path) -> tuple[str | None, str | None]:
    if args.no_usage_log:
        return None, None
    usage_log_path = resolve_log_path(args.usage_log_file, root)
    record = {
        "schema": "glm52.usage.v1",
        "event": "delegate_run",
        "started_at": result.get("started_at"),
        "ended_at": result.get("ended_at"),
        "overall_wall_ms": result.get("overall_wall_ms"),
        "cwd": str(Path(args.cwd).expanduser().resolve()),
        "role": result.get("role"),
        "ok": result.get("ok"),
        "timeout": result.get("timeout"),
        "attempt_count": result.get("attempt_count"),
        "transient_retry_used": result.get("transient_retry_used"),
        "final_returncode": result.get("final_returncode"),
        "final_api_error_status": result.get("final_api_error_status"),
        "final_timed_out": result.get("final_timed_out"),
        "failure_reason": result.get("failure_reason"),
        "scope_violation_during_run": result.get("scope_violation_during_run"),
        "safe_to_retry_later": result.get("safe_to_retry_later"),
        "policy_ok": result.get("policy_ok"),
        "scope_violations": result.get("scope_violations"),
        "allowed_changes": result.get("allowed_changes"),
        "changed_files": result.get("changed_files"),
        "change_summary": result.get("change_summary"),
        "prompt_sha256": result.get("prompt_sha256"),
        "prompt_chars": result.get("prompt_chars"),
        "result_file": str(Path(args.result_file).expanduser().resolve()) if args.result_file else None,
        "usageSummary": result.get("usageSummary"),
        "usage_normalized": result.get("usage_normalized"),
        "usage_accounting": result.get("usage_accounting"),
        "usage_snapshots": result.get("usage_snapshots"),
        "consumptionSummary": result.get("consumptionSummary"),
        "quotaBefore": result.get("quotaBefore"),
        "quotaAfter": result.get("quotaAfter"),
        "quotaDelta": result.get("quotaDelta"),
        "quotaState": result.get("quotaState"),
        "providerUsageBefore": result.get("providerUsageBefore"),
        "providerUsageAfter": result.get("providerUsageAfter"),
        "providerUsageDelta": result.get("providerUsageDelta"),
        "visionContext": result.get("visionContext", {}),
    }
    try:
        append_jsonl(usage_log_path, record)
        return str(usage_log_path), None
    except OSError as exc:
        return str(usage_log_path), str(exc)
