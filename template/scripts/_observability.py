"""SuperMovie Observability helper (schema v1, redaction v1).

See docs/OBSERVABILITY.md §Status JSON Contract for emission rules.

Design:
- v0 → v1 status mapping (`STATUS_MAP`) - migration policy で normative
- `build_status` で v1 schema payload を組む (extra kwargs は top-level merge で v0 emit 互換)
- `emit_json` は --json-log 経由で 1 line JSON を stdout 末尾に emit
- redaction helpers: `safe_artifact_path` / `user_content_meta` / `redact_provider_body`
- dry-run legacy JSON は本 helper を使わず既存 schema を維持 (OBSERVABILITY.md §v0 dry-run JSON legacy)
"""
import hashlib
import json
import os
from pathlib import Path

SCHEMA_VERSION = 1
REDACTION_VERSION = 1

# v0 → v1 status mapping per docs/OBSERVABILITY.md §Migration Policy.
# Update both this dict and the doc table together.
STATUS_MAP = {
    # success / skip / dry-run
    "success": ("ok", None),
    "api_key_skipped": ("skipped", "api_key_missing"),
    "engine_skipped": ("skipped", "engine_unavailable"),
    "engine_unavailable_strict": ("error", "engine_unavailable"),
    "list_speakers": ("ok", "list_speakers"),
    "dry_run": ("dry_run", None),
    # error variants (slide-plan)
    "cost_guard_arg_invalid": ("error", "cost_guard_arg_invalid"),
    "inputs_missing": ("error", "inputs_missing"),
    "rate_limited": ("error", "rate_limited"),
    "api_http_error": ("error", "api_http_error"),
    "llm_json_invalid": ("error", "llm_json_invalid"),
    # error variants (voicevox)
    "transcript_missing": ("error", "transcript_missing"),
    "transcript_invalid": ("error", "transcript_invalid"),
    "no_chunks": ("error", "no_chunks"),
    "invalid_fps": ("error", "invalid_fps"),
    "stale_cleanup_fail": ("error", "stale_cleanup_fail"),
    "vad_invalid": ("error", "vad_invalid"),
    "no_chunks_succeeded": ("error", "no_chunks_succeeded"),
    "partial_chunks_disallowed": ("error", "partial_chunks_disallowed"),
    "concat_fail": ("error", "concat_fail"),
    "write_narration_data_wave_error": ("error", "write_narration_data_wave_error"),
    "sentinel_write_fail": ("error", "sentinel_write_fail"),
    # compare_telop_split (Codex 21:01 verdict S3-6 KPI comparison、category_override="kpi-comparison")
    "all_pass": ("ok", "kpi-comparison"),
    "some_fail": ("error", "kpi-comparison"),
    # visual_smoke (Codex 21:01 verdict S3-4、category_override="dimension-regression")
    "smoke_ok": ("ok", "dimension-regression"),
    "dimension_mismatch": ("error", "dimension-regression"),
    "env_error": ("error", "env-failure"),
    "grid_failed": ("error", "grid-failure"),
    # visual_smoke early return v0 statuses (Codex 21:14 PR4 review P1 #1 で追加)
    "usage_error_formats_empty": ("error", "usage-error"),
    "usage_error_unknown_format": ("error", "usage-error"),
    "usage_error_frames_empty": ("error", "usage-error"),
    "usage_error_frames_negative": ("error", "usage-error"),
    "usage_error_patch_format": ("error", "usage-error"),
    "env_tool_missing": ("error", "env-failure"),
    "env_main_video_missing": ("error", "env-failure"),
    "env_remotion_cli_missing": ("error", "env-failure"),
    "env_video_config_missing": ("error", "env-failure"),
    "usage_error_frames_invalid": ("error", "usage-error"),
    # preflight_video (PR-B、Codex 21:01 step 3 S3-3、category_override="preflight-source-meta")
    "preflight_ok": ("ok", "preflight-source-meta"),
    "input_not_found": ("error", "input-not-found"),
    "no_video_stream": ("error", "no-video-stream"),
    "ffprobe_failed": ("error", "ffprobe-failed"),
    "risks_not_allowed": ("error", "risks-not-allowed"),
    "format_inference_failed": ("error", "format-inference-failed"),
    # build_slide_data / build_telop_data (PR-C、Codex 21:01 step 3 S3-5 user_content redaction)
    "build_slide_ok": ("ok", "slide-build"),
    "build_telop_ok": ("ok", "telop-build"),
    # build_slide / build_telop error variants (Codex 21:46 PR6 review P1 で error emission 追加)
    "build_slide_inputs_missing": ("error", "inputs-missing"),
    "build_slide_transcript_invalid": ("error", "transcript-invalid"),
    "build_slide_plan_missing": ("error", "plan-missing"),
    "build_slide_plan_invalid": ("error", "plan-invalid"),
    "build_telop_transcript_invalid": ("error", "transcript-invalid"),
}


def map_status(v0_status):
    """Map v0 status name to (v1_status, v1_category).

    Unknown v0 status → ("error", v0_status) defensive default.
    """
    if v0_status in STATUS_MAP:
        return STATUS_MAP[v0_status]
    return ("error", v0_status)


def _hash16(text):
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _lexical_redact(s, home):
    """resolve なしで abs path を placeholder に変換 (defensive fallback)。

    Codex 21:23 PR4 re-review P2 で resolve() 例外時 raw return が漏れる問題 fix。
    純文字列レベルで prefix match → placeholder 化。
    """
    if s.startswith(home):
        return "<HOME>" + s[len(home):]
    tmp_prefixes = ("/tmp/", "/var/tmp/", "/var/folders/", "/private/tmp/", "/private/var/folders/")
    for prefix in tmp_prefixes:
        if s.startswith(prefix):
            return "<TMP>" + s[len(prefix.rstrip("/")):]
    if s.startswith("/"):
        return f"<ABS>/{Path(s).name}"
    return s


def safe_artifact_path(path, *, project_root=None, repo_root=None, unsafe_keep_abs_path=False):
    """Convert path to safe form (relative to project/repo root or placeholder).

    Returns string. None input → None.
    unsafe_keep_abs_path=True bypasses sanitization (debug-only flag).

    Codex 21:14 PR4 review P1 #2 fix: project/repo root に該当しない absolute path も
    `<HOME>` / `<TMP>` / `<ABS>` placeholder に正規化する (旧実装は raw return で
    `/tmp/...` 等が漏れていた)。
    Codex 21:23 PR4 re-review P2 fix: resolve() 例外時の fallback も lexical redaction
    で raw absolute を漏らさない (旧実装は raw `s` 返却していた)。
    """
    if path is None:
        return None
    s = str(path)
    if unsafe_keep_abs_path:
        return s
    home = os.path.expanduser("~")
    try:
        p = Path(s).expanduser().resolve() if Path(s).is_absolute() else Path(s).resolve()
    except (OSError, RuntimeError):
        # resolve fail (broken symlink / circular link 等): lexical redaction で defensive
        return _lexical_redact(s, home)
    for root in (project_root, repo_root):
        if root is None:
            continue
        try:
            root_resolved = Path(root).expanduser().resolve()
            return str(p.relative_to(root_resolved))
        except (ValueError, OSError):
            continue
    # 通常 path: lexical redaction (HOME / TMP / ABS placeholder)
    return _lexical_redact(s, home)


def user_content_meta(text):
    """Return safe metadata for user content (length + sha256 prefix).

    Used in json tail / external log when raw is forbidden.
    Returns None for None input. Coerces non-str to str.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    return {
        "length": len(text),
        "sha256": _hash16(text),
    }


def redact_provider_body(body, *, unsafe_dump=False, max_preview=80):
    """Redact provider response body.

    Default: returns structured summary (length + sha256 + truncated preview).
    unsafe_dump=True returns raw body verbatim (debug-only flag).
    None input → None.
    """
    if body is None:
        return None
    if unsafe_dump:
        return {"kind": "raw", "body": body}
    if isinstance(body, str):
        return {
            "kind": "summary",
            "length": len(body),
            "sha256": _hash16(body),
            "preview_length": min(len(body), max_preview),
        }
    return {"kind": "summary", "type": type(body).__name__}


def build_status(*, script, v0_status, exit_code, counts=None, artifacts=None,
                 cost=None, redaction_rules=None,
                 duration_ms=None, category_override=None,
                 run_id=None, parent_run_id=None, step_id=None,
                 **extra):
    """Build v1 schema-conforming status payload.

    Schema fields per docs/OBSERVABILITY.md §Common Fields.
    Extra kwargs are merged at top level to preserve v0 emit pattern
    (model, max_tokens, slides, output, etc. flow through unchanged).

    Args:
      script: emitting script name (for `script` field).
      v0_status: v0 status string. Looked up in STATUS_MAP. Unknown → ("error", v0_status).
      exit_code: process exit code.
      counts: dict of domain-specific counters (slides=N, frames=N, etc.).
      artifacts: list of {"path": str, "kind": str} dicts.
      cost: dict per cost contract, or None when not applicable.
      redaction_rules: applied redaction rule names (e.g. ["abs_path", "user_content"]).
      duration_ms: process / phase duration in milliseconds (Codex Step 3 S3-7 で
        common field として明示要求、default None で省略時は payload に含めない)。
      category_override: set v1 category explicitly, bypassing STATUS_MAP lookup
        (Codex Step 3 S3-7 で v0_status 名を category として使い回せない script 用)。
        Used by compare_telop_split / visual_smoke / preflight where category is
        domain-specific (kpi-comparison / dimension-regression / preflight-source-meta).
      run_id / parent_run_id / step_id: distributed tracing reservation.
    """
    v1_status, v1_category = map_status(v0_status)
    if category_override is not None:
        v1_category = category_override
    payload = {
        "schema_version": SCHEMA_VERSION,
        "script": script,
        "status": v1_status,
        "ok": v1_status in ("ok", "skipped", "dry_run"),
        "exit_code": exit_code,
        "category": v1_category,
        "counts": counts or {},
        "artifacts": artifacts or [],
        "cost": cost,
        "redaction": {
            "applied_rules": list(redaction_rules) if redaction_rules else [],
            "version": REDACTION_VERSION,
        },
    }
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if run_id is not None:
        payload["run_id"] = run_id
    if parent_run_id is not None:
        payload["parent_run_id"] = parent_run_id
    if step_id is not None:
        payload["step_id"] = step_id
    # v0 compat: extras (e.g. model / max_tokens / output / slides) are merged at top level.
    # Reserved schema keys above always win.
    reserved = set(payload.keys())
    for k, v in extra.items():
        if k not in reserved:
            payload[k] = v
    return payload


def emit_json(enabled, payload):
    """Emit single-line JSON to stdout tail when enabled (--json-log).

    Returns exit_code from payload for chained `return emit_json(...)` pattern.
    print() is used (not sys.stdout.write) to keep newline + flush behavior identical
    to existing v0 emit sites.
    """
    if enabled:
        print(json.dumps(payload, ensure_ascii=False))
    return int(payload.get("exit_code", 0))
