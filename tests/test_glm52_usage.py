from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from claude_glm52_supervisor.delegate import extract_allowed_changes, scope_violations  # noqa: E402
from claude_glm52_supervisor.glm52_usage import (  # noqa: E402
    aggregate_usage_summaries,
    append_jsonl,
    consumption_summary,
    normalize_quota_payload,
    quota_delta,
    quota_state_summary,
    summarize_model_usage,
    usage_counter_delta,
)
from claude_glm52_supervisor.glm52_usage_snapshots import (  # noqa: E402
    build_usage_accounting,
    derive_quota_usage,
    infer_token_quota_limit,
    normalize_codexbar_usage_snapshot,
    provider_usage_quota_delta,
    usage_snapshot,
    usage_snapshot_from_quota_snapshot,
)


class UsageSummaryTests(unittest.TestCase):
    def test_summarize_model_usage_keeps_ccusage_style_breakdown(self) -> None:
        summary = summarize_model_usage(
            {
                "glm-5.2[1m]": {
                    "inputTokens": 10,
                    "outputTokens": 5,
                    "cacheReadInputTokens": 100,
                    "cacheCreationInputTokens": 7,
                    "costUSD": 0.123456789,
                    "contextWindow": 1000000,
                    "maxOutputTokens": 64000,
                }
            }
        )

        self.assertEqual(summary["inputTokens"], 10)
        self.assertEqual(summary["outputTokens"], 5)
        self.assertEqual(summary["cacheReadInputTokens"], 100)
        self.assertEqual(summary["cacheCreationInputTokens"], 7)
        self.assertEqual(summary["totalTokens"], 122)
        self.assertEqual(summary["models"]["glm-5.2[1m]"]["contextWindow"], 1000000)

    def test_aggregate_usage_summaries_sums_models(self) -> None:
        first = summarize_model_usage({"glm-5.2[1m]": {"inputTokens": 10, "outputTokens": 2}})
        second = summarize_model_usage({"glm-5.2[1m]": {"inputTokens": 3, "outputTokens": 4}})
        total = aggregate_usage_summaries([first, second])

        self.assertEqual(total["inputTokens"], 13)
        self.assertEqual(total["outputTokens"], 6)
        self.assertEqual(total["totalTokens"], 19)
        self.assertNotIn("models", total["models"]["glm-5.2[1m]"])


class QuotaSummaryTests(unittest.TestCase):
    def test_normalize_legacy_glm_quota_percent(self) -> None:
        quota = normalize_quota_payload(
            {"quota": {"token_used_pct": 12.5, "plan": "pro", "error": None}},
            provider="zai",
            source="zai_api.quota",
        )

        self.assertTrue(quota["ok"])
        self.assertEqual(quota["used_pct"], 12.5)
        self.assertEqual(quota["used_pct_source"], "token_used_pct")
        self.assertEqual(quota["plan"], "pro")

    def test_normalize_preserves_sanitized_raw_limits(self) -> None:
        quota = normalize_quota_payload(
            {"raw_limits": [{"type": "TOKENS_LIMIT", "percentage": 12.5}], "error": None},
            provider="zai",
            source="zai_api.quota",
        )

        self.assertEqual(quota["raw_limits"], [{"type": "TOKENS_LIMIT", "percentage": 12.5}])

    def test_zai_raw_percentage_without_counts_is_not_delta_safe(self) -> None:
        quota = normalize_quota_payload(
            {
                "token_used_pct": 1.0,
                "token_pct_authoritative": False,
                "token_pct_note": "percentage without counts",
                "raw_limits": [{"type": "TOKENS_LIMIT", "percentage": 1.0, "usage": None, "remaining": None}],
                "error": None,
            },
            provider="zai",
            source="zai_api.quota",
        )

        self.assertTrue(quota["ok"])
        self.assertIsNone(quota["used_pct"])
        self.assertIsNone(quota["used_pct_source"])
        self.assertEqual(quota["token_used_pct"], 1.0)
        self.assertFalse(quota["token_pct_authoritative"])

    def test_normalize_rolling_quota_percent(self) -> None:
        quota = normalize_quota_payload(
            {"rolling_5h_used": 25, "rolling_5h_limit": 100, "error": None},
            provider="minimax",
            source="unit",
        )

        self.assertTrue(quota["ok"])
        self.assertEqual(quota["used_pct"], 25.0)
        self.assertEqual(quota["used_pct_source"], "rolling_5h_used_pct")

    def test_quota_delta_requires_same_available_source(self) -> None:
        before = {"used_pct": 10.0, "used_pct_source": "token_used_pct"}
        after = {"used_pct": 10.75, "used_pct_source": "token_used_pct"}

        self.assertEqual(quota_delta(before, after)["used_pct_delta"], 0.75)
        self.assertFalse(quota_delta(before, {"used_pct": None, "used_pct_source": None})["available"])

    def test_consumption_summary_records_model_tokens_when_provider_pct_missing(self) -> None:
        usage = summarize_model_usage(
            {"glm-5.2[1m]": {"inputTokens": 100, "outputTokens": 25, "cacheReadInputTokens": 875, "costUSD": 0.01}}
        )
        summary = consumption_summary(
            usage,
            {"available": False, "used_pct_delta": None, "source": None},
            token_quota_limit=100_000,
        )

        self.assertEqual(summary["totalTokens"], 1000)
        self.assertFalse(summary["providerQuotaPctAvailable"])
        self.assertEqual(summary["estimatedQuotaPctDelta"], 1.0)
        self.assertIn("1000 tokens", summary["display"])

    def test_usage_counter_delta_records_zai_daily_tokens(self) -> None:
        before = {
            "source": "zai_api.model_usage",
            "window_start": "2026-06-17 00:00:00",
            "window_end": "2026-06-17 23:59:59",
            "total_tokens_usage": 6_400_000,
            "total_model_call_count": 500,
        }
        after = {
            **before,
            "total_tokens_usage": 6_401_807,
            "total_model_call_count": 501,
        }

        delta = usage_counter_delta(before, after)

        self.assertTrue(delta["available"])
        self.assertEqual(delta["tokens_delta"], 1807)
        self.assertEqual(delta["model_call_count_delta"], 1)

    def test_consumption_summary_includes_quota_state_and_provider_counter(self) -> None:
        usage = summarize_model_usage({"glm-5.2[1m]": {"inputTokens": 1000, "outputTokens": 25}})
        summary = consumption_summary(
            usage,
            {"available": False, "used_pct_delta": None, "source": None},
            provider_usage_diff={
                "available": True,
                "tokens_delta": 1200,
                "model_call_count_delta": 1,
                "after_total_tokens_usage": 6_401_200,
                "source": "zai_api.model_usage",
            },
            quota_after={
                "provider": "zai",
                "plan": "pro",
                "source": "zai_api.quota",
                "time_remaining": 1000,
                "time_limit": 1000,
                "time_used_pct": 0.0,
                "token_used_pct": 1.0,
                "token_pct_authoritative": False,
            },
        )

        self.assertEqual(summary["providerUsageTokensDelta"], 1200)
        self.assertEqual(summary["providerUsageAfterTotalTokens"], 6_401_200)
        self.assertEqual(summary["quotaState"]["timeRemaining"], 1000)
        self.assertIn("ZAI time quota 1000/1000 remaining", summary["display"])

    def test_consumption_summary_marks_zero_provider_delta_as_lagged_total(self) -> None:
        usage = summarize_model_usage({"glm-5.2[1m]": {"inputTokens": 1000, "outputTokens": 25}})
        summary = consumption_summary(
            usage,
            {"available": False, "used_pct_delta": None, "source": None},
            provider_usage_diff={
                "available": True,
                "tokens_delta": 0,
                "model_call_count_delta": 0,
                "after_total_tokens_usage": 6_404_252,
                "source": "zai_api.model_usage",
            },
        )

        self.assertTrue(summary["providerUsageLagSuspected"])
        self.assertIn("ZAI daily total 6404252 tokens", summary["display"])

    def test_quota_state_summary_keeps_remaining_numbers(self) -> None:
        state = quota_state_summary(
            {
                "provider": "zai",
                "plan": "pro",
                "time_remaining": 987,
                "time_limit": 1000,
                "token_remaining": None,
                "token_limit": None,
                "token_used_pct": 1.0,
                "token_pct_authoritative": False,
            }
        )

        self.assertEqual(state["timeRemaining"], 987)
        self.assertEqual(state["timeLimit"], 1000)
        self.assertEqual(state["tokenUsedPct"], 1.0)
        self.assertFalse(state["tokenPctAuthoritative"])

    def test_codexbar_snapshot_normalizes_quota_windows(self) -> None:
        snapshot = normalize_codexbar_usage_snapshot(
            [
                {
                    "provider": "zai",
                    "source": "api",
                    "usage": {
                        "primary": {
                            "usedPercent": 1.25,
                            "resetsAt": "2026-06-17T18:30:44Z",
                            "resetDescription": "5 hours window",
                            "windowMinutes": 300,
                        },
                        "secondary": {
                            "usedPercent": 0.5,
                            "resetsAt": "2026-07-04T05:08:05Z",
                            "resetDescription": "Monthly",
                        },
                        "identity": {"providerID": "zai"},
                        "updatedAt": "2026-06-17T15:07:00Z",
                    },
                }
            ],
            provider="zai",
            phase="before",
        )

        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["source"], "codexbar")
        self.assertEqual(snapshot["best"]["quota_percent"], 1.25)
        self.assertEqual(snapshot["windows"]["primary"]["used_percent"], 1.25)
        self.assertEqual(snapshot["windows"]["secondary"]["reset_description"], "Monthly")

    def test_codexbar_snapshot_rejects_wrong_provider_row(self) -> None:
        snapshot = normalize_codexbar_usage_snapshot(
            [{"provider": "claude", "usage": {"primary": {"usedPercent": 42.0}}}],
            provider="zai",
            phase="before",
        )

        self.assertFalse(snapshot["ok"])
        self.assertEqual(snapshot["error_type"], "provider_not_found")
        self.assertEqual(snapshot["available_providers"], ["claude"])

    def test_zai_quota_snapshot_gets_zcode_style_windows(self) -> None:
        snapshot = usage_snapshot_from_quota_snapshot(
            {
                "ok": True,
                "observed_at": "2026-06-17T12:00:00Z",
                "provider": "zai",
                "plan": "pro",
                "raw_limits": [
                    {"type": "TIME_LIMIT", "percentage": 0.5, "usage": 1000, "remaining": 995, "nextResetTime": 1783141685991},
                    {"type": "TOKENS_LIMIT", "percentage": 2.0, "usage": None, "remaining": None, "nextResetTime": 1781703036040},
                ],
                "token_pct_authoritative": False,
            },
            phase="after",
        )

        self.assertEqual(snapshot["source"], "zai-api")
        self.assertEqual(snapshot["best"]["quota_percent"], 0.5)
        self.assertEqual(snapshot["windows"]["primary"]["type"], "TOKENS_LIMIT")
        self.assertIsNone(snapshot["windows"]["primary"]["used_percent"])
        self.assertEqual(snapshot["windows"]["secondary"]["remaining"], 995.0)

    def test_zai_non_authoritative_token_pct_does_not_become_usage_delta(self) -> None:
        before = usage_snapshot_from_quota_snapshot(
            {
                "ok": True,
                "provider": "zai",
                "raw_limits": [{"type": "TOKENS_LIMIT", "percentage": 1.0, "usage": None, "remaining": None}],
                "token_pct_authoritative": False,
            },
            phase="before",
        )
        after = usage_snapshot_from_quota_snapshot(
            {
                "ok": True,
                "provider": "zai",
                "raw_limits": [{"type": "TOKENS_LIMIT", "percentage": 2.0, "usage": None, "remaining": None}],
                "token_pct_authoritative": False,
            },
            phase="after",
        )

        accounting = build_usage_accounting(summarize_model_usage({"glm-5.2[1m]": {"inputTokens": 1}}), before, after)

        self.assertIsNone(accounting["quota_percent_before"])
        self.assertIsNone(accounting["quota_percent_after"])
        self.assertIsNone(accounting["quota_percent_used"])
        self.assertEqual(accounting["quota_percent_status"], "unavailable")
        self.assertEqual(accounting["quota_percent_unavailable_reason"], "authoritative_quota_delta_unavailable")

    def test_auto_snapshot_uses_codexbar_when_direct_token_percent_is_not_delta_safe(self) -> None:
        with (
            patch(
                "claude_glm52_supervisor.glm52_usage_snapshots.quota_snapshot",
                return_value={
                    "ok": True,
                    "provider": "zai",
                    "raw_limits": [{"type": "TOKENS_LIMIT", "percentage": 1.0, "usage": None, "remaining": None}],
                    "token_pct_authoritative": False,
                },
            ),
            patch(
                "claude_glm52_supervisor.glm52_usage_snapshots.codexbar_usage_snapshot",
                return_value={
                    "ok": True,
                    "source": "codexbar",
                    "provider": "zai",
                    "windows": {"primary": {"used_percent": 1.25, "resets_at": "same"}},
                    "best": {"quota_percent": 1.25},
                },
            ) as codexbar_snapshot,
        ):
            snapshot = usage_snapshot(provider="zai", source="auto", phase="before")

        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["source"], "codexbar")
        self.assertEqual(snapshot["windows"]["primary"]["used_percent"], 1.25)
        self.assertEqual(snapshot["fallback_from"]["source"], "zai-api")
        codexbar_snapshot.assert_called_once()

    def test_auto_snapshot_preserves_direct_success_when_primary_percent_is_available(self) -> None:
        with (
            patch(
                "claude_glm52_supervisor.glm52_usage_snapshots.quota_snapshot",
                return_value={
                    "ok": True,
                    "provider": "zai",
                    "raw_limits": [{"type": "TOKENS_LIMIT", "percentage": 1.0, "usage": 100, "remaining": 9_900}],
                    "token_pct_authoritative": True,
                },
            ),
            patch("claude_glm52_supervisor.glm52_usage_snapshots.codexbar_usage_snapshot") as codexbar_snapshot,
        ):
            snapshot = usage_snapshot(provider="zai", source="auto", phase="before")

        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["source"], "zai-api")
        self.assertEqual(snapshot["windows"]["primary"]["used_percent"], 1.0)
        codexbar_snapshot.assert_not_called()

    def test_usage_accounting_matches_zcode_consumed_fields(self) -> None:
        before = normalize_codexbar_usage_snapshot(
            [{"provider": "zai", "usage": {"primary": {"usedPercent": 1.25, "resetsAt": "same"}}}],
            provider="zai",
            phase="before",
        )
        after = normalize_codexbar_usage_snapshot(
            [{"provider": "zai", "usage": {"primary": {"usedPercent": 1.75, "resetsAt": "same"}}}],
            provider="zai",
            phase="after",
        )
        usage = summarize_model_usage(
            {
                "claude-opus-4-6[1m]": {
                    "inputTokens": 1000,
                    "outputTokens": 234,
                    "cacheReadInputTokens": 456,
                    "cacheCreationInputTokens": 7,
                    "costUSD": 0.03,
                }
            }
        )

        accounting = build_usage_accounting(usage, before, after)

        self.assertEqual(accounting["tokens_source"], "claude_code_model_usage")
        self.assertEqual(accounting["tokens_used"], 1697)
        self.assertEqual(accounting["input_tokens"], 1000)
        self.assertEqual(accounting["output_tokens"], 234)
        self.assertEqual(accounting["cache_read_tokens"], 456)
        self.assertEqual(accounting["cache_write_tokens"], 7)
        self.assertEqual(accounting["quota_source"], "codexbar")
        self.assertEqual(accounting["quota_percent_direction"], "used")
        self.assertEqual(accounting["quota_percent_before"], 1.25)
        self.assertEqual(accounting["quota_percent_after"], 1.75)
        self.assertEqual(accounting["quota_percent_used"], 0.5)
        self.assertEqual(accounting["quota_percent_status"], "measured")
        self.assertEqual(accounting["quota_percent_source"], "codexbar")
        self.assertIsNone(accounting["quota_percent_unavailable_reason"])
        self.assertIsNone(accounting["estimated_quota_percent_used"])
        self.assertIsNone(accounting["estimated_quota_token_limit"])

    def test_usage_accounting_can_label_configured_estimate(self) -> None:
        before = {"ok": True, "source": "zai-api", "provider": "zai", "windows": {"primary": {"used_percent": None}}}
        after = {"ok": True, "source": "zai-api", "provider": "zai", "windows": {"primary": {"used_percent": None}}}
        usage = summarize_model_usage({"glm-5.2[1m]": {"inputTokens": 700, "outputTokens": 300}})

        with patch("claude_glm52_supervisor.glm52_usage_snapshots.configured_token_quota_limit", return_value=10_000):
            accounting = build_usage_accounting(usage, before, after)

        self.assertIsNone(accounting["quota_percent_used"])
        self.assertEqual(accounting["quota_percent_status"], "estimated")
        self.assertEqual(accounting["quota_percent_source"], "configured_token_quota_limit")
        self.assertEqual(accounting["estimated_quota_percent_used"], 10.0)
        self.assertEqual(accounting["estimated_quota_token_limit"], 10_000)

    def test_infers_token_quota_limit_from_used_and_remaining_shape(self) -> None:
        limit = infer_token_quota_limit(
            {
                "raw_limits": [
                    {
                        "type": "TOKENS_LIMIT",
                        "percentage": 2.0,
                        "usage": 1_000,
                        "remaining": 49_000,
                    }
                ]
            }
        )

        self.assertTrue(limit["available"])
        self.assertEqual(limit["token_quota_limit"], 50_000)
        self.assertEqual(limit["source"], "zai_api.quota.tokens_limit.usage_plus_remaining")

    def test_infers_token_quota_limit_from_limit_and_remaining_shape(self) -> None:
        limit = infer_token_quota_limit(
            {
                "raw_limits": [
                    {
                        "type": "TOKENS_LIMIT",
                        "percentage": 2.0,
                        "usage": 50_000,
                        "remaining": 49_000,
                    }
                ]
            }
        )

        self.assertTrue(limit["available"])
        self.assertEqual(limit["token_quota_limit"], 50_000)
        self.assertEqual(limit["source"], "zai_api.quota.tokens_limit.limit_minus_remaining")

    def test_provider_usage_delta_can_measure_quota_percent_from_api_limit(self) -> None:
        quota = provider_usage_quota_delta(
            {
                "available": True,
                "tokens_delta": 1_250,
                "source": "zai_api.model_usage",
            },
            {
                "raw_limits": [
                    {
                        "type": "TOKENS_LIMIT",
                        "percentage": 2.0,
                        "usage": 1_000,
                        "remaining": 49_000,
                    }
                ]
            },
        )

        self.assertTrue(quota["available"])
        self.assertEqual(quota["quota_percent_used"], 2.5)
        self.assertEqual(quota["token_quota_limit"], 50_000)

    def test_usage_accounting_measures_provider_usage_quota_delta(self) -> None:
        before = {"ok": True, "source": "zai-api", "provider": "zai", "windows": {"primary": {"used_percent": None}}}
        after = {"ok": True, "source": "zai-api", "provider": "zai", "windows": {"primary": {"used_percent": None}}}
        usage = summarize_model_usage({"glm-5.2[1m]": {"inputTokens": 700, "outputTokens": 300}})

        accounting = build_usage_accounting(
            usage,
            before,
            after,
            provider_usage_diff={"available": True, "tokens_delta": 1_250, "source": "zai_api.model_usage"},
            quota_snapshot_after={
                "raw_limits": [
                    {
                        "type": "TOKENS_LIMIT",
                        "percentage": 2.0,
                        "usage": 1_000,
                        "remaining": 49_000,
                    }
                ]
            },
        )

        self.assertEqual(accounting["quota_percent_status"], "measured")
        self.assertEqual(accounting["quota_percent_used"], 2.5)
        self.assertEqual(accounting["quota_percent_source"], "zai_api.model_usage+zai_api.quota.tokens_limit.usage_plus_remaining")
        self.assertEqual(accounting["provider_quota_percent_basis_tokens"], 1_250)
        self.assertEqual(accounting["provider_quota_token_limit"], 50_000)
        self.assertIsNone(accounting["estimated_quota_percent_used"])

    def test_provider_zero_delta_with_tokens_is_treated_as_lag(self) -> None:
        before = normalize_codexbar_usage_snapshot(
            [{"provider": "zai", "usage": {"primary": {"usedPercent": 1.0, "resetsAt": "same"}}}],
            provider="zai",
            phase="before",
        )
        after = normalize_codexbar_usage_snapshot(
            [{"provider": "zai", "usage": {"primary": {"usedPercent": 1.0, "resetsAt": "same"}}}],
            provider="zai",
            phase="after",
        )
        usage = summarize_model_usage({"glm-5.2[1m]": {"inputTokens": 700, "outputTokens": 300}})

        accounting = build_usage_accounting(
            usage,
            before,
            after,
            provider_usage_diff={"available": True, "tokens_delta": 0, "source": "zai_api.model_usage"},
            quota_snapshot_after={
                "raw_limits": [
                    {
                        "type": "TOKENS_LIMIT",
                        "percentage": 2.0,
                        "usage": 1_000,
                        "remaining": 49_000,
                    }
                ]
            },
        )

        self.assertIsNone(accounting["quota_percent_used"])
        self.assertEqual(accounting["quota_percent_status"], "unavailable")
        self.assertEqual(accounting["quota_percent_unavailable_reason"], "provider_usage_counter_lagged")
        self.assertTrue(accounting["provider_usage_zero_delta_lagged"])

    def test_provider_usage_quota_delta_rejects_unrecognized_quota_shape(self) -> None:
        quota = provider_usage_quota_delta(
            {"available": True, "tokens_delta": 1_250, "source": "zai_api.model_usage"},
            {"raw_limits": [{"type": "TOKENS_LIMIT", "percentage": 9.0, "usage": 1_000, "remaining": 49_000}]},
        )

        self.assertFalse(quota["available"])
        self.assertEqual(quota["error"], "token_quota_limit_shape_unrecognized")

    def test_zero_display_delta_with_tokens_is_not_measured_quota(self) -> None:
        before = normalize_codexbar_usage_snapshot(
            [{"provider": "zai", "usage": {"primary": {"usedPercent": 1.0, "resetsAt": "same"}}}],
            provider="zai",
            phase="before",
        )
        after = normalize_codexbar_usage_snapshot(
            [{"provider": "zai", "usage": {"primary": {"usedPercent": 1.0, "resetsAt": "same"}}}],
            provider="zai",
            phase="after",
        )
        usage = summarize_model_usage({"glm-5.2[1m]": {"inputTokens": 700, "outputTokens": 300}})

        accounting = build_usage_accounting(usage, before, after)

        self.assertIsNone(accounting["quota_percent_used"])
        self.assertEqual(accounting["quota_percent_status"], "unavailable")
        self.assertEqual(accounting["quota_percent_unavailable_reason"], "quota_percent_resolution_too_coarse")
        self.assertTrue(accounting["quota_percent_resolution_limited"])

    def test_quota_window_reset_prevents_negative_consumption(self) -> None:
        before = {"ok": True, "source": "codexbar", "provider": "zai", "windows": {"primary": {"used_percent": 99.0, "resets_at": "old"}}}
        after = {"ok": True, "source": "codexbar", "provider": "zai", "windows": {"primary": {"used_percent": 1.0, "resets_at": "new"}}}

        quota = derive_quota_usage(before, after)

        self.assertIsNone(quota["quota_percent_used"])
        self.assertTrue(quota["windows"]["primary"]["reset_changed"])

    def test_consumption_summary_includes_snapshot_quota_delta(self) -> None:
        usage = summarize_model_usage({"glm-5.2[1m]": {"inputTokens": 100, "outputTokens": 25}})
        summary = consumption_summary(
            usage,
            {"available": False, "used_pct_delta": None, "source": None},
            usage_accounting={"quota_percent_used": 0.5, "quota_source": "codexbar"},
        )

        self.assertTrue(summary["snapshotQuotaPctAvailable"])
        self.assertEqual(summary["snapshotQuotaPctDelta"], 0.5)
        self.assertIn("usage snapshot quota +0.5% (codexbar)", summary["display"])


class LogTests(unittest.TestCase):
    def test_append_jsonl_writes_one_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "usage.jsonl"
            append_jsonl(path, {"schema": "test", "value": 1})

            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows, [{"schema": "test", "value": 1}])


class ScopePolicyTests(unittest.TestCase):
    def test_extract_allowed_changes_from_only_modify_line(self) -> None:
        prompt = """
Constraints:
- Only modify `src/window_stats.py`.
- Do not edit tests.
"""
        self.assertEqual(extract_allowed_changes(prompt), ["src/window_stats.py"])

    def test_scope_violations_rejects_extra_files_and_deletions(self) -> None:
        allowed = ["src/window_stats.py"]
        changed = ["src/window_stats.py", "task.md", "tests/test_window_stats.py"]
        self.assertEqual(scope_violations(changed, allowed), ["task.md", "tests/test_window_stats.py"])


if __name__ == "__main__":
    unittest.main()
