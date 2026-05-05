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
import math
import os
import re
import uuid
from pathlib import Path

SCHEMA_VERSION = 1
REDACTION_VERSION = 1

# Trace context (run_id active emission, PR-E、Codex 22:40 next priority verdict)。
# `SUPERMOVIE_RUN_ID` 未設定時は uuid4().hex (32 char) を auto-generate、parent / step は env のみ
# (None default、関係性情報なので auto-generate しない)。
TRACE_RUN_ID_ENV = "SUPERMOVIE_RUN_ID"
TRACE_PARENT_RUN_ID_ENV = "SUPERMOVIE_PARENT_RUN_ID"
TRACE_STEP_ID_ENV = "SUPERMOVIE_STEP_ID"
MAX_TRACE_CONTEXT_VALUE_LEN = 128

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
    "cost_guard_aborted": ("error", "cost_guard_aborted"),
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
    # compare_telop_split early-error paths (PR-G error path tail audit)
    "typo_dict_invalid": ("error", "typo_dict_invalid"),
    "telop_ts_missing": ("error", "telop_ts_missing"),
    "telop_ts_invalid": ("error", "telop_ts_invalid"),
    "kpi_calc_error": ("error", "kpi_calc_error"),
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
    # visual_smoke early IO failure paths (PR-G error path tail audit)
    "out_dir_mkdir_error": ("error", "out-dir-mkdir-error"),
    "video_config_read_error": ("error", "video-config-read-error"),
    "video_config_write_error": ("error", "env-failure"),
    "video_config_restore_error": ("error", "env-failure"),
    "summary_write_error": ("error", "summary-write-error"),
    "env_video_config_missing": ("error", "env-failure"),
    "usage_error_frames_invalid": ("error", "usage-error"),
    # preflight_video (PR-B、Codex 21:01 step 3 S3-3、category_override="preflight-source-meta")
    "preflight_ok": ("ok", "preflight-source-meta"),
    "input_not_found": ("error", "input-not-found"),
    "no_video_stream": ("error", "no-video-stream"),
    "ffprobe_failed": ("error", "ffprobe-failed"),
    "risks_not_allowed": ("error", "risks-not-allowed"),
    "format_inference_failed": ("error", "format-inference-failed"),
    # preflight_video write-config error paths (PR-G error path tail audit)
    "write_config_parse_error": ("error", "write-config-parse-error"),
    "write_config_write_error": ("error", "write-config-write-error"),
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


# `/...` 風の POSIX abs path token を抽出。直前が word 文字 (URL の `https:` / `file:` 等の scheme) や
# `:` の場合はマッチしない (URL 破壊回避、Codex 23:33 P2 #1)。
_ABS_PATH_RE = re.compile(r"(?<![A-Za-z0-9_:/])(/[A-Za-z0-9._/\-]+)")

# Windows abs path token (drive letter + `:` + `\` or `/`)。SuperMovie は Darwin/Linux 主だが、
# CI / cross-platform エラー文字列 / Windows tool 経由で leak する可能性に対する defense-in-depth (PR-K、Codex 00:36)。
# 例: `C:\Users\name\foo`、`D:/Projects/bar`。直前が word 文字なら非 match (URL 等で誤発火しない)。
_WIN_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z]:[\\\/][^\s'\"]+)")


def redact_secret(value, *, last_n=4, mask_char="*"):
    """Secret value (API key / token / credential) を contract 通り redact する。

    PR-H (Codex 23:18 medium / 23:58 approve): docs/OBSERVABILITY.md §Redaction Rules
    secret class の last-4 mask rule (`sk-...XXXX`) を helper として実装。
    helper module には PR #3 で `safe_artifact_path` / `user_content_meta` /
    `redact_provider_body` が入ったが secret 専用 helper は欠落していた。

    Behavior:
      - non-string (None / int / etc.) → そのまま return (caller 側で type check 不要)
      - 長さ <= last_n+1 → 全 mask (`****`、value 露出回避)
      - 長さ > last_n+1 → mask + 末尾 last_n char ("sk-1234567890abc" → "****abc" 等)

    Codex 23:58 verdict: helper 化 + unit test 化で contract 閉じる。
    """
    if not isinstance(value, str):
        return value
    if not value:
        return value
    # PR-H fix iter (Codex 00:02 P1 #1): last_n <= 0 は全 mask に倒す。
    # Python slice の `value[-0:]` = `value[0:]` で full string leak になるため、
    # custom param で 0 や負値が渡されても fail-closed (全 mask) で安全側に。
    if last_n <= 0:
        return mask_char * len(value)
    if len(value) <= last_n + 1:
        return mask_char * len(value)
    masked_len = len(value) - last_n
    return (mask_char * masked_len) + value[-last_n:]


def redact_error_message(msg):
    """Error message 文字列内の絶対 path token を `_lexical_redact` で安全化する。

    PR-G review P1 #2 (Codex 23:25): error=str(e) で abs_path が tail JSON に raw 漏れする
    contract 違反を防ぐため、regex で `/...` 風の token を抽出し `<HOME>` / `<TMP>` / `<ABS>`
    placeholder に置換する。引用符 (`'/foo'` / `"/foo"`) や直前文字 (`:`、`=`) があっても
    動作するよう、char-class で前後 boundary を判定する。

    URL 破壊回避: `https://...` の `://` 配下は scheme の `:` が直前にあるため非 match。
    純粋な絶対 path のみ redact 対象。

    PR-K (Codex 00:36 approve): Windows path (`C:\\...`、`D:/...`) も `_WIN_PATH_RE` で
    `<ABS>/<basename>` に置換、cross-platform error string leak への defense-in-depth。
    """
    if not isinstance(msg, str):
        return msg
    home = str(Path.home())

    def _sub_posix(m):
        path = m.group(1)
        return _lexical_redact(path, home)

    def _sub_win(m):
        path = m.group(1)
        # Windows path は drive letter 含めて全置換、basename だけ残す (`C:\Users\foo` → `<ABS>/foo`)
        # OSError str 等で `\\` escaped で来るケースもあるため、最後 `\\` または `/` token を basename とする。
        for sep in ("\\", "/"):
            if sep in path:
                basename = path.rsplit(sep, 1)[-1]
                if basename:
                    return f"<ABS>/{basename}"
        return "<ABS>"

    msg = _ABS_PATH_RE.sub(_sub_posix, msg)
    msg = _WIN_PATH_RE.sub(_sub_win, msg)
    return msg


def safe_artifact_path(path, *, project_root=None, repo_root=None, unsafe_keep_abs_path=False):
    """Convert path to safe form (relative to project/repo root or placeholder).

    Returns string. None input → None.
    unsafe_keep_abs_path=True bypasses sanitization (debug-only flag).

    Codex 21:14 PR4 review P1 #2 fix: project/repo root に該当しない absolute path も
    `<HOME>` / `<TMP>` / `<ABS>` placeholder に正規化する (旧実装は raw return で
    `/tmp/...` 等が漏れていた)。
    Codex 21:23 PR4 re-review P2 fix: resolve() 例外時の fallback も lexical redaction
    で raw absolute を漏らさない (旧実装は raw `s` 返却していた)。
    Codex 02:14 PR-V verdict AF fix: `~/...` 入力を早期 expanduser で吸収。
    旧実装は `Path(s).is_absolute()` ガードで `~` を `expanduser` せず、
    後段 `_lexical_redact(s, home)` も `s` 原文 (`~/...`) を `<HOME>` に
    変換できず literal `~/...` がそのまま漏れていた。`s.startswith("~")`
    時のみ `os.path.expanduser` を通し、相対 path 入力 (`public/main.mp4`
    等) の as-is passthrough は維持する。
    """
    if path is None:
        return None
    s = str(path)
    if unsafe_keep_abs_path:
        return s
    home = os.path.expanduser("~")
    s_expanded = os.path.expanduser(s) if s.startswith("~") else s
    # Codex 02:18 PR-V re-review P2 fix: `~unknownuser/...` 等の存在しない
    # user 名は `os.path.expanduser` で展開されず literal のまま残るため、
    # `<ABS>/<basename>` placeholder に落として user 名 + path 構造の漏れを
    # 防ぐ (resolve 後も relative_to 不可、lexical_redact も `/` 始まりでない
    # ので素通りしてしまう経路の最終ガード)。
    if s_expanded.startswith("~"):
        return f"<ABS>/{Path(s_expanded).name}"
    try:
        p = Path(s_expanded).resolve()
    except (OSError, RuntimeError):
        # resolve fail (broken symlink / circular link 等): lexical redaction で defensive
        return _lexical_redact(s_expanded, home)
    for root in (project_root, repo_root):
        if root is None:
            continue
        try:
            root_resolved = Path(root).expanduser().resolve()
            return str(p.relative_to(root_resolved))
        except (ValueError, OSError):
            continue
    # 通常 path: lexical redaction (HOME / TMP / ABS placeholder)
    return _lexical_redact(s_expanded, home)


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


def _coerce_finite_or_none(v):
    """Return v if it's a finite real number, else None。

    PR-AA (Codex 02:46 verdict AM) defense: NaN / Inf / -Inf / 非数値型を
    None に正規化することで cost payload に non-finite を漏らさず、
    json.dumps 既定の non-standard `NaN` / `Infinity` token 出力 +
    json.dumps(allow_nan=False) 突発 ValueError を回避する。None 入力は
    そのまま None 通過 (rate 未設定時の通常経路を壊さない)。
    """
    if v is None:
        return None
    try:
        if isinstance(v, bool):
            # bool は int subclass なので isfinite True、ただし currency 数値
            # としては不適切 → None 扱い
            return None
        if math.isfinite(v):
            return v
    except (TypeError, ValueError):
        pass
    return None


def compute_rate_missing(estimate):
    """Cost rate_missing discriminator helper (PR-O、Codex 01:12)。

    `estimate is None ⇔ rate_missing=true` の判定式を一箇所に集約 (PR-O)。
    PR-AA (Codex 02:46 verdict AM) 拡張: NaN / Inf も rate_missing 扱いに
    する。caller の rate guard (generate_slide_plan argparse type=...) を
    すり抜けて非 finite が helper まで届いた場合に、json output / downstream
    consumer の double-counting / NaN 算術伝播 を防ぐ defense-in-depth layer。

    Args:
      estimate: float (rate 設定済 finite) | None | NaN | Inf | -Inf

    Returns:
      bool: True iff estimate is None or non-finite (rate_missing).
    """
    if estimate is None:
        return True
    return _coerce_finite_or_none(estimate) is None


def build_cost_payload(estimate, rate_input, rate_output, *,
                       currency="USD",
                       tokens_input=None, tokens_output=None,
                       rate_source="env:SUPERMOVIE_RATE_<PROVIDER>_<DIR>_USD_PER_MTOK"):
    """Nested cost payload builder per docs/OBSERVABILITY.md §Cost JSON Shape (PR-S)。

    PR-N で top-level discriminator (estimated_cost_usd_upper_bound / rate_missing 等) を
    導入したが、docs §Cost JSON Shape は nested `cost` object を future canonical と定義。
    本 helper で nested form を emission に追加、downstream parser が nested cost.* で
    consume できるよう migrate (top-level extras は backward compat で残す)。

    PR-AA (Codex 02:46 verdict AM) defense: estimate / rate_input / rate_output に
    NaN / Inf / -Inf / 非数値が混入した場合、None に正規化して payload に出す。
    結果として `rate_missing=True` も維持され、`json.dumps` が non-standard token
    (`NaN` / `Infinity`) を出力する経路を完全 closure。CLI / env 側の rate guard
    (math.isfinite + ValueError reject) が efficient だが、helper を独立 caller
    から呼ぶ場合や CLI guard をすり抜ける将来 path への defense-in-depth。

    Args:
      estimate: float | None。rate 未設定時は None。NaN / Inf は None 正規化。
      rate_input / rate_output: float | None。MTok 単価。NaN / Inf は None 正規化。
      currency: str default "USD"。
      tokens_input / tokens_output: int | None。実 API 呼び出し前は None。
      rate_source: str。env var convention の placeholder。

    Returns:
      dict: nested cost object per Cost JSON Shape contract。`rate_missing` は
      `compute_rate_missing(estimate)` で算出 (single source of truth)。
    """
    estimate = _coerce_finite_or_none(estimate)
    rate_input = _coerce_finite_or_none(rate_input)
    rate_output = _coerce_finite_or_none(rate_output)
    return {
        "currency": currency,
        "estimate": estimate,
        "rate_source": rate_source,
        "rate_input_usd_per_mtok": rate_input,
        "rate_output_usd_per_mtok": rate_output,
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "rate_missing": compute_rate_missing(estimate),
    }


# PR-X (Codex 02:31 verdict AH): legacy top-level cost extras deprecation gate。
# PR-N で top-level cost discriminator (estimated_cost_usd_upper_bound / rate_missing /
# cost_abort_at 等) を入れたが PR-S で nested `cost` object を canonical に移行。
# 現行は backward compat で dual emission を続けているが、env-gated deprecation
# warning を追加して downstream consumer に migration を促せるようにする。
# default off (env 未設定 / "0" で warning 抑制)。"1" の時のみ stderr に warning。
WARN_LEGACY_COST_EXTRAS_ENV = "SUPERMOVIE_OBSERVABILITY_WARN_LEGACY_COST_EXTRAS"
LEGACY_COST_EXTRAS_KEYS = (
    "estimated_input_tokens",
    "estimated_output_tokens_upper_bound",
    "estimated_cost_usd_upper_bound",
    "cost_abort_at",
    "rate_missing",
)


def warn_legacy_cost_extras(payload, *, stream=None):
    """Emit deprecation warning to stderr when nested `cost` and legacy
    top-level cost extras coexist in payload, gated by env.

    Default off: env unset or "0" → no-op (returns False)。
    "1" 時のみ payload に nested `cost` (truthy) と legacy keys (any of
    `LEGACY_COST_EXTRAS_KEYS`) が併存していたら 1 行の deprecation warning
    を `stream` (default: sys.stderr) に書き出す。

    stdout JSON contract は触らない (warning は stderr のみ)。

    Args:
      payload: build_status() の出力 dict (nested `cost` + extras top-level merge 済)。
      stream: deprecation warning の書き込み先。default は import 時 sys.stderr。

    Returns:
      bool: True iff warning が emit された (test 用 sentinel)。
    """
    import sys as _sys

    if os.environ.get(WARN_LEGACY_COST_EXTRAS_ENV) != "1":
        return False
    if not payload.get("cost"):
        return False
    legacy_present = sorted(
        k for k in LEGACY_COST_EXTRAS_KEYS if k in payload
    )
    if not legacy_present:
        return False
    target_stream = stream if stream is not None else _sys.stderr
    print(
        f"WARNING: deprecated top-level cost extras alongside nested `cost` "
        f"object: {legacy_present}. These top-level keys will be removed in a "
        f"future release; consume `cost.*` instead "
        f"(set {WARN_LEGACY_COST_EXTRAS_ENV}=0 to silence).",
        file=target_stream,
    )
    return True


def _normalize_redaction_rules(rules):
    """Validate redaction_rules input and return sorted unique str list。

    PR-AD (Codex 03:09 verdict AQ-改) defense:
      - None / empty list/tuple → []
      - list / tuple of str → sorted set
      - bare str → TypeError (caller probably forgot list wrap、`'abs_path'`
        を渡すと iter で char 分解されて `['_','a','b','h','p','s','t']`
        になる silent drift を防ぐ)
      - 非 str 要素 (None / int / dict / etc.) を含む → TypeError fail-loud
      - その他 (dict / set / int / etc.) → TypeError

    旧実装 `sorted(set(redaction_rules)) if redaction_rules else []` は
    None/empty で [] になるが、`[None]` / `[1]` を silent pass、
    `["a", None]` で「'<' not supported between instances of 'str' and
    'NoneType'」の意味不明 TypeError、bare str で char 分解という
    schema drift を起こしていた。
    """
    if rules is None:
        return []
    # bare str 早期 reject (iter で char 分解される silent drift を防ぐ)
    if isinstance(rules, str):
        raise TypeError(
            f"redaction_rules must be list/tuple of str, got bare str "
            f"({rules!r}); did you mean [{rules!r}]?"
        )
    if not isinstance(rules, (list, tuple)):
        raise TypeError(
            f"redaction_rules must be list/tuple of str or None, got "
            f"{type(rules).__name__} ({rules!r})"
        )
    for item in rules:
        if not isinstance(item, str):
            raise TypeError(
                f"redaction_rules entries must be str, got "
                f"{type(item).__name__} ({item!r}) in {rules!r}"
            )
    return sorted(set(rules))


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
    # PR-AF (Codex 03:21 verdict AU) defense: cost は v1 contract の canonical
    # nested object (docs/OBSERVABILITY.md §Cost JSON Shape)、None / dict 以外を
    # 渡されたら schema drift + warn_legacy_cost_extras() の truthiness 判定 drift
    # を引き起こす。caller の型違いを fail-loud で reject、payload 構築側責務に
    # 固定。bool は dict subclass ではないが、念のため None と dict のみ受理する
    # 厳密な isinstance 検査。
    if cost is not None and not isinstance(cost, dict):
        raise TypeError(
            f"build_status: cost must be dict or None, got "
            f"{type(cost).__name__} ({cost!r})"
        )
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
            # PR-Q (Codex 01:30 AC approve): redaction.applied_rules を helper 側で正規化。
            # caller (generate_slide_plan / voicevox / build_slide / build_telop / etc.) で
            # `redaction_rules.append(...)` を繰り返す pattern が多く、重複や順序が
            # non-deterministic だった。helper で sorted(set(...)) に正規化、downstream
            # diff / regression test の安定性を確保。empty も空 list で固定。
            # PR-AD (Codex 03:09 AQ-改 approve): `_normalize_redaction_rules()` 経由で
            # 非 str 要素 / bare str / dict-set の silent pass / char 分解を fail-loud reject。
            "applied_rules": _normalize_redaction_rules(redaction_rules),
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

    PR-AC (Codex 03:01 verdict AO) defense: `payload["exit_code"]` は v1 schema
    の core 字段で int 限定 contract。旧実装 `int(payload.get("exit_code", 0))`
    は str "2" / float 1.5 / bool True を silent coerce、None / "abc" で
    uncaught TypeError/ValueError を投げる weak 動作で、schema drift の
    silent 通過 + caller の責務曖昧化を起こしていた。新実装は missing 時の
    default 0 はそのまま、type mismatch (bool / str / float / None / その他) を
    explicit TypeError で fail-loud、payload 構築側の責務として固定する。
    """
    exit_code = payload.get("exit_code", 0)
    # bool は int subclass なので isinstance(v, int) を通り抜けるため先に reject
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise TypeError(
            f"emit_json: payload['exit_code'] must be int (not bool), "
            f"got {type(exit_code).__name__} ({exit_code!r})"
        )
    if enabled:
        print(json.dumps(payload, ensure_ascii=False))
    return exit_code


class TraceContextError(ValueError):
    """env value invalid for trace context (length cap exceeded etc.)."""


def _validate_trace_value(name, value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise TraceContextError(f"{name} must be str, got {type(value).__name__}")
    if len(value) > MAX_TRACE_CONTEXT_VALUE_LEN:
        raise TraceContextError(
            f"env {name}={value[:16]}... exceeds MAX_TRACE_CONTEXT_VALUE_LEN={MAX_TRACE_CONTEXT_VALUE_LEN}"
        )
    return value


def resolve_run_context(*,
                        run_id_env=TRACE_RUN_ID_ENV,
                        parent_env=TRACE_PARENT_RUN_ID_ENV,
                        step_env=TRACE_STEP_ID_ENV,
                        generate_if_missing=True):
    """Resolve run_id / parent_run_id / step_id from env, fallback to uuid4 hex.

    precedence:
      1. `SUPERMOVIE_RUN_ID` 設定 + 非空 → そのまま使用 (cap 検証のみ)
      2. 未設定 + generate_if_missing=True → uuid.uuid4().hex (32 char) auto-generate
      3. 未設定 + generate_if_missing=False → None
      parent / step は env のみ、未設定なら None (auto-generate しない)

    `MAX_TRACE_CONTEXT_VALUE_LEN` (default 128) 超は TraceContextError raise (truncation せず error)。

    Returns: dict with keys "run_id" / "parent_run_id" / "step_id" (str | None)
    """
    raw_run_id = os.environ.get(run_id_env) or None
    raw_parent = os.environ.get(parent_env) or None
    raw_step = os.environ.get(step_env) or None

    run_id = _validate_trace_value(run_id_env, raw_run_id)
    if run_id is None and generate_if_missing:
        run_id = uuid.uuid4().hex

    parent = _validate_trace_value(parent_env, raw_parent)
    step = _validate_trace_value(step_env, raw_step)

    return {"run_id": run_id, "parent_run_id": parent, "step_id": step}
