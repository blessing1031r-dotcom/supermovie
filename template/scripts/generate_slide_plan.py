#!/usr/bin/env python3
"""SuperMovie Phase 3-C optional script: Anthropic API で slide_plan.json を生成.

Codex Phase 3C design (2026-05-04, CODEX_PHASE3C_LLM_PLAN) 推奨:
- LLM は word index ベースの slide_plan.json を返す (frame は build_slide_data.py が変換)
- ANTHROPIC_API_KEY が無ければ skip (非ゼロ終了しない)
- build_slide_data.py が plan を validate して invalid なら deterministic fallback

Usage:
    ANTHROPIC_API_KEY=sk-ant-... python3 scripts/generate_slide_plan.py \\
        --output slide_plan.json [--model claude-haiku-4-5-20251001]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

# Phase 3 obs migration core: helper を経由して v1 schema + redaction を適用。
# 既存 v0 emit pattern は build_status の **extra で互換維持。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _observability import (
    build_status,
    emit_json as _obs_emit_json,
    resolve_run_context,
    redact_provider_body,
    safe_artifact_path,
)

PROJ = Path(__file__).resolve().parent.parent
PLAN_VERSION = "supermovie.slide_plan.v1"

PROMPT_TEMPLATE = """\
あなたは動画編集者です。SuperMovie パイプラインの slide_plan を返してください。

## 入力
- transcript: 動画のナレーション文字起こし (ms timestamps + words 配列)
- format: {fmt} (短尺=short / 横長=youtube / 正方形=square)
- tone: {tone}

## 制約 (絶対ルール)
1. word index で slide 範囲を返す (startWordIndex / endWordIndex 必須)
2. word index は 0..{n_words_minus_1} の範囲、startWordIndex <= endWordIndex
3. 隣接 slide の word range は overlap しない (前 slide の endWordIndex < 次 slide の startWordIndex)
4. id は 1 から昇順
5. title は {title_max} 文字以内、必須、空不可
6. bullets は最大 {max_bullets} 個、各 bullet text は {bullet_max} 文字以内
7. align は "center" or "left" のみ
8. videoLayer は "visible" / "dimmed" / "hidden" のみ (省略可)

## 出力 (JSON のみ、コードフェンス不要)
{{
  "version": "{plan_version}",
  "slides": [
    {{
      "id": 1,
      "startWordIndex": 0,
      "endWordIndex": 30,
      "title": "短い見出し",
      "subtitle": "任意",
      "bullets": [
        {{ "text": "要点", "emphasis": true }}
      ],
      "align": "left",
      "videoLayer": "visible"
    }}
  ]
}}

## transcript (words 配列、最大 {max_input_words} word のみ抜粋。全 {n_words} 個の最初):
{words_preview}

## 全 segments (timestamp 付き):
{segments_preview}
"""

# Phase 3-V post-freeze 第2弾 P2 (Codex CODEX_P2_COST_GUARD_DESIGN_20260505T104428.md):
# Anthropic API cost guard。
# 価格は HARD RULE「根拠なき具体性」回避のため hardcode しない、env / arg 経由のみ。
# max_tokens local safety cap 16384 は API 上限ではなく PR scope の guard。
MAX_TOKENS_DEFAULT = 4096
MAX_TOKENS_CAP = 16384
MAX_INPUT_WORDS_DEFAULT = 200


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _resolve_int(
    cli_val: int | None,
    env_name: str,
    default: int,
    min_val: int,
    max_val: int,
    arg_name: str,
) -> int:
    """CLI > env > default の precedence で int 解決。range 違反は ValueError raise。"""
    if cli_val is not None:
        v = cli_val
        source = f"--{arg_name}"
    else:
        env_str = os.environ.get(env_name)
        if env_str is not None:
            try:
                v = int(env_str)
            except ValueError as e:
                raise ValueError(
                    f"{env_name}={env_str!r} は int に変換できません: {e}"
                ) from e
            source = f"env {env_name}"
        else:
            return default
    if v < min_val or v > max_val:
        raise ValueError(
            f"{source}={v} が範囲外 (許容: {min_val}..{max_val})"
        )
    return v


def _resolve_decimal(
    cli_val: float | None,
    env_name: str,
    *,
    v0_alias: str | None = None,
) -> float | None:
    """CLI > v1 env > v0 alias env > None の precedence で decimal 解決
    (finite + >=0)。範囲違反は ValueError。

    Codex P2 review P2 反映 (CODEX_P2_COST_GUARD_REVIEW:7-9):
    `math.isfinite` を必須化し、nan/inf を禁止 (cost estimate を破壊するため)。

    Codex 21:54 PR-D verdict: env_name を v1 canonical
    (SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK 等) として扱い、v1 が未設定なら
    v0_alias (SUPERMOVIE_RATE_INPUT_PER_MTOK 等) を fallback として参照する。
    両方設定時は v1 が勝つ。docs/OBSERVABILITY.md §Rate Env Var Convention 整合。
    """
    if cli_val is not None:
        v = cli_val
        source = f"--{env_name.lower()}"
    else:
        env_str = os.environ.get(env_name)
        used_env = env_name
        # v1 env が未設定なら v0 alias を fallback
        if env_str is None and v0_alias is not None:
            env_str = os.environ.get(v0_alias)
            used_env = v0_alias
        if env_str is None:
            return None
        try:
            v = float(env_str)
        except ValueError as e:
            raise ValueError(
                f"{used_env}={env_str!r} は decimal に変換できません: {e}"
            ) from e
        source = f"env {used_env}"
    if not math.isfinite(v):
        raise ValueError(f"{source}={v} は finite (nan/inf 禁止)")
    if v < 0:
        raise ValueError(f"{source}={v} は >=0 必須")
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default=str(PROJ / "slide_plan.json"))
    ap.add_argument("--model", default="claude-haiku-4-5-20251001",
                    help="Anthropic model (default: claude-haiku-4-5、cost 最小)")
    # Phase 3-V P2 cost guard (Codex CODEX_P2_COST_GUARD_DESIGN §1):
    ap.add_argument("--max-tokens", type=int, default=None,
                    help=f"max_tokens override (default {MAX_TOKENS_DEFAULT}、cap {MAX_TOKENS_CAP}、"
                         f"env: SUPERMOVIE_MAX_TOKENS)")
    ap.add_argument("--max-input-words", type=int, default=None,
                    help=f"transcript words preview の cap (default {MAX_INPUT_WORDS_DEFAULT}、"
                         f"env: SUPERMOVIE_MAX_INPUT_WORDS)")
    ap.add_argument("--max-input-segments", type=int, default=None,
                    help="transcript segments preview の cap "
                         "(unset: 全量、env: SUPERMOVIE_MAX_INPUT_SEGMENTS)")
    ap.add_argument("--dry-run", action="store_true",
                    help="API を呼ばず prompt 生成 + cost estimate JSON を出して exit 0 "
                         "(API key 不要、env 解決は実行)")
    ap.add_argument("--rate-input", type=float, default=None,
                    help="input cost rate USD/MTok "
                         "(env v1: SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK、"
                         "env v0 alias: SUPERMOVIE_RATE_INPUT_PER_MTOK、"
                         "両 rate 設定時のみ dry-run cost estimate 計算)")
    ap.add_argument("--rate-output", type=float, default=None,
                    help="output cost rate USD/MTok "
                         "(env v1: SUPERMOVIE_RATE_ANTHROPIC_OUTPUT_USD_PER_MTOK、"
                         "env v0 alias: SUPERMOVIE_RATE_OUTPUT_PER_MTOK)")
    # PR-F: pre-API cost abort threshold (Codex 23:04 next priority verdict)
    ap.add_argument("--cost-abort-at", type=float, default=None,
                    help="estimate cost USD upper bound がこの閾値を超えたら API call 前に "
                         "abort (status=cost_guard_aborted, exit 10)。"
                         "env: SUPERMOVIE_COST_USD_ABORT_AT (CLI > env > None=無効)。"
                         "rate 未設定 (estimate=None) 時は閾値設定があっても abort skip "
                         "(cost 不明状態で勝手に止めず通常進行、後方互換)。")
    # Phase 3-V P3 logging 拡張 (Codex CODEX_P2_COST_GUARD_DESIGN §4): voicevox_narration の
    # --json-log と同じ pattern で全 return path を status JSON で観測可能に。
    ap.add_argument("--json-log", action="store_true",
                    help="末尾に summary を 1 行純 JSON として emit "
                         "(downstream observability、既存 stdout は維持)")
    # Phase 3 obs migration core: redaction debug opt-in flags
    # (default は redact、debug 時のみ raw 出力。docs/OBSERVABILITY.md §Redaction Rules)
    ap.add_argument("--unsafe-dump-response", action="store_true",
                    help="provider response body / LLM raw text を stderr に raw で出す "
                         "(default: structured summary、debug 専用)")
    ap.add_argument("--unsafe-keep-abs-path", action="store_true",
                    help="json tail / artifact path を絶対 path のまま emit "
                         "(default: project-root 相対 / <HOME> placeholder、debug 専用)")
    args = ap.parse_args()

    # PR-E (distributed tracing): main 冒頭で 1 回 resolve、全 emission に同 run_ctx を渡す。
    run_ctx = resolve_run_context()

    # Phase 3 obs migration core: 全 return path で v1 schema 経由で emit。
    # dry-run JSON は本 helper を使わず既存 schema を維持 (OBSERVABILITY.md §v0 dry-run JSON legacy)。
    # extra kwargs は build_status で v1 schema に top-level merge され v0 emit pattern と互換。
    # output 等の path field は safe_artifact_path で project-root 相対化 (--unsafe-keep-abs-path で raw)。
    def emit_json(status: str, exit_code: int, **extra) -> int:
        # Apply abs_path redaction to known path-bearing fields
        redaction_rules = []
        if "output" in extra and extra["output"] is not None:
            extra["output"] = safe_artifact_path(
                extra["output"],
                project_root=PROJ,
                unsafe_keep_abs_path=args.unsafe_keep_abs_path,
            )
            if not args.unsafe_keep_abs_path:
                redaction_rules.append("abs_path")
        payload = build_status(
            script="generate_slide_plan",
            v0_status=status,
            exit_code=exit_code,
            redaction_rules=redaction_rules,
            run_id=run_ctx["run_id"],
            parent_run_id=run_ctx["parent_run_id"],
            step_id=run_ctx["step_id"],
            **extra,
        )
        return _obs_emit_json(args.json_log, payload)

    # Codex P2 review P1 反映 (CODEX_P2_COST_GUARD_REVIEW:3-5):
    # API key 未設定 skip は cost guard env 解決より前に判定する。
    # 既存 no-key skip 互換 (env が壊れていても skip 0 で通る) を維持し、
    # dry-run のみ API key 不要で env/arg validation を行う。
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key and not args.dry_run:
        print("INFO: ANTHROPIC_API_KEY 未設定 → slide_plan 生成 skip")
        print("      build_slide_data.py は --plan 無しで deterministic に走ります")
        return emit_json("api_key_skipped", 0)

    # cost guard arg 解決 (CLI > env > default)
    try:
        max_tokens = _resolve_int(
            args.max_tokens, "SUPERMOVIE_MAX_TOKENS",
            MAX_TOKENS_DEFAULT, 1, MAX_TOKENS_CAP, "max-tokens",
        )
        # Codex P2 review P3 反映: design spec は ">=1"、不要な 1M 上限を削除
        # (実装側 hardcode で design と乖離していた箇所を修正、cap は max-tokens のみ保持)
        max_input_words = _resolve_int(
            args.max_input_words, "SUPERMOVIE_MAX_INPUT_WORDS",
            MAX_INPUT_WORDS_DEFAULT, 1, sys.maxsize, "max-input-words",
        )
        # max-input-segments は default unset
        if args.max_input_segments is not None:
            max_input_segments: int | None = args.max_input_segments
            if max_input_segments < 1:
                raise ValueError(f"--max-input-segments={max_input_segments} は >=1 必須")
        else:
            env_seg = os.environ.get("SUPERMOVIE_MAX_INPUT_SEGMENTS")
            if env_seg is not None:
                try:
                    max_input_segments = int(env_seg)
                except ValueError as e:
                    raise ValueError(
                        f"SUPERMOVIE_MAX_INPUT_SEGMENTS={env_seg!r} は int に変換できません: {e}"
                    ) from e
                if max_input_segments < 1:
                    raise ValueError(
                        f"SUPERMOVIE_MAX_INPUT_SEGMENTS={max_input_segments} は >=1 必須"
                    )
            else:
                max_input_segments = None
        # Codex 21:54 PR-D verdict: v1 canonical (SUPERMOVIE_RATE_ANTHROPIC_*) を一次、
        # v0 alias (SUPERMOVIE_RATE_*_PER_MTOK) を後方互換 fallback として読む。
        rate_input = _resolve_decimal(
            args.rate_input,
            "SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK",
            v0_alias="SUPERMOVIE_RATE_INPUT_PER_MTOK",
        )
        rate_output = _resolve_decimal(
            args.rate_output,
            "SUPERMOVIE_RATE_ANTHROPIC_OUTPUT_USD_PER_MTOK",
            v0_alias="SUPERMOVIE_RATE_OUTPUT_PER_MTOK",
        )
        # PR-F: pre-API cost abort threshold。CLI > env、None で無効。
        # rate 未設定 (estimate=None) 時は閾値があっても skip (cost 不明で abort せず通常進行)。
        cost_abort_at = _resolve_decimal(
            args.cost_abort_at,
            "SUPERMOVIE_COST_USD_ABORT_AT",
        )
    except ValueError as e:
        print(f"ERROR: cost guard arg invalid: {e}", file=sys.stderr)
        return emit_json("cost_guard_arg_invalid", 4, error=str(e))

    transcript_path = PROJ / "transcript_fixed.json"
    config_path = PROJ / "project-config.json"
    if not transcript_path.exists() or not config_path.exists():
        print(f"ERROR: transcript_fixed.json or project-config.json missing under {PROJ}", file=sys.stderr)
        return emit_json("inputs_missing", 3)

    transcript = load_json(transcript_path)
    config = load_json(config_path)
    fmt = config.get("format", "short")
    tone = config.get("tone", "プロフェッショナル")
    words = transcript.get("words", [])
    segments = transcript.get("segments", [])
    n_words = len(words)

    title_max = {"youtube": 18, "short": 14, "square": 16}.get(fmt, 14)
    bullet_max = {"youtube": 24, "short": 18, "square": 20}.get(fmt, 18)

    # Phase 3-V P2 cost guard: words / segments cap (CLI/env override 後)
    words_for_prompt = words[:max_input_words]
    segments_for_prompt = (
        segments if max_input_segments is None else segments[:max_input_segments]
    )
    words_preview = "\n".join(
        f"  [{i}] {w.get('text','')!r} ({w.get('start')}ms-{w.get('end')}ms)"
        for i, w in enumerate(words_for_prompt)
    )
    segments_preview = "\n".join(
        f"  seg[{i}] {s.get('start')}-{s.get('end')}ms: {s.get('text','')}"
        for i, s in enumerate(segments_for_prompt)
    )

    prompt = PROMPT_TEMPLATE.format(
        fmt=fmt,
        tone=tone,
        n_words=n_words,
        n_words_minus_1=max(n_words - 1, 0),
        title_max=title_max,
        bullet_max=bullet_max,
        max_bullets=5,
        plan_version=PLAN_VERSION,
        words_preview=words_preview,
        segments_preview=segments_preview,
        max_input_words=max_input_words,
    )

    # PR-F: estimate cost を dry-run / 非 dry-run 両 path で共有するため、ここで一度計算。
    # rate 未設定時は estimate=None で cost-abort スキップ (cost 不明で abort しない)。
    prompt_chars = len(prompt)
    estimated_input_tokens = math.ceil(prompt_chars / 4)
    estimated_output_tokens_upper_bound = max_tokens
    if rate_input is not None and rate_output is not None:
        estimated_cost_usd_upper_bound = (
            estimated_input_tokens / 1_000_000 * rate_input
            + estimated_output_tokens_upper_bound / 1_000_000 * rate_output
        )
    else:
        estimated_cost_usd_upper_bound = None

    # Phase 3-V P2 cost guard --dry-run: API 呼ばず estimate JSON 出力 + exit 0
    # (Codex CODEX_P2_COST_GUARD_DESIGN §1 / §3、HARD RULE「根拠なき具体性」回避で
    # rate hardcode せず env/arg で受領、estimation_method を明示)
    if args.dry_run:
        request_body_bytes_estimate = len(
            json.dumps({
                "model": args.model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }).encode("utf-8")
        )
        dry_run_payload = {
            "status": "dry_run",
            "api_called": False,
            "model": args.model,
            "max_tokens": max_tokens,
            "max_input_words": max_input_words,
            "max_input_segments": max_input_segments,
            "words_in_prompt": len(words_for_prompt),
            "segments_in_prompt": len(segments_for_prompt),
            "prompt_chars": prompt_chars,
            "request_body_bytes_estimate": request_body_bytes_estimate,
            "estimated_input_tokens": estimated_input_tokens,
            "estimated_output_tokens_upper_bound": estimated_output_tokens_upper_bound,
            "estimated_cost_usd_upper_bound": estimated_cost_usd_upper_bound,
            "rate_input_per_mtok": rate_input,
            "rate_output_per_mtok": rate_output,
            "estimation_method": "ceil(prompt_chars/4)",
        }
        print(json.dumps(dry_run_payload, ensure_ascii=False))
        # PR-E: --json-log 時は v1 status tail も emit (run_id propagation、2-emission pattern)。
        # dry-run legacy JSON は本 helper を通さず維持 (OBSERVABILITY.md §v0 dry-run JSON legacy)。
        if args.json_log:
            return emit_json(
                "dry_run", 0,
                model=args.model,
                max_tokens=max_tokens,
                estimated_input_tokens=estimated_input_tokens,
                estimated_output_tokens_upper_bound=estimated_output_tokens_upper_bound,
                estimated_cost_usd_upper_bound=estimated_cost_usd_upper_bound,
            )
        return 0

    # PR-F: pre-API cost abort check。dry-run 後 / 実 API call 前に閾値チェック。
    # rate 未設定 (estimate=None) 時は cost 不明、abort せず通常進行。
    if cost_abort_at is not None and estimated_cost_usd_upper_bound is not None:
        if estimated_cost_usd_upper_bound > cost_abort_at:
            print(
                f"ERROR: estimated cost USD upper bound "
                f"{estimated_cost_usd_upper_bound:.6f} > cost-abort-at {cost_abort_at:.6f}、"
                f"API call abort",
                file=sys.stderr,
            )
            return emit_json(
                "cost_guard_aborted",
                10,
                model=args.model,
                max_tokens=max_tokens,
                estimated_input_tokens=estimated_input_tokens,
                estimated_output_tokens_upper_bound=estimated_output_tokens_upper_bound,
                estimated_cost_usd_upper_bound=estimated_cost_usd_upper_bound,
                cost_abort_at=cost_abort_at,
            )

    # Anthropic API 呼び出し (urllib で SDK 不要に保つ)
    import urllib.request
    import urllib.error
    body = {
        "model": args.model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Phase 3-V P2 cost guard (Codex P2 review §2.5): 429 rate_limit を分離
        # (exit 9、retry-after header 拾って message に含める)。429 以外は exit 4 維持。
        # Phase 3 obs migration core: provider response body を default redact
        # (length + sha256 のみ stderr)、--unsafe-dump-response 指定時のみ raw 出力。
        body = e.read().decode("utf-8", errors="replace")
        body_redacted = redact_provider_body(body, unsafe_dump=args.unsafe_dump_response)
        if e.code == 429:
            retry_after = e.headers.get("retry-after") if e.headers else None
            if args.unsafe_dump_response:
                body_msg = f"body={body[:300]}"
            else:
                body_msg = (
                    f"body=<redacted len={body_redacted['length']} "
                    f"sha256={body_redacted['sha256']}>"
                )
            print(
                f"ERROR: Anthropic API rate_limited (HTTP 429): "
                f"retry-after={retry_after}, {body_msg}",
                file=sys.stderr,
            )
            return emit_json("rate_limited", 9, retry_after=retry_after)
        if args.unsafe_dump_response:
            body_msg = f"body={body[:500]}"
        else:
            body_msg = (
                f"body=<redacted len={body_redacted['length']} "
                f"sha256={body_redacted['sha256']}>"
            )
        print(f"ERROR: Anthropic API HTTP {e.code}: {body_msg}", file=sys.stderr)
        return emit_json("api_http_error", 4, http_status=e.code)

    text = "".join(b.get("text", "") for b in response.get("content", []) if b.get("type") == "text")
    # コードフェンス除去 (LLM が markdown 返した場合)
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    text = text.strip()

    try:
        plan = json.loads(text)
    except json.JSONDecodeError as e:
        # Phase 3 obs migration core: LLM raw text を default redact、--unsafe-dump-response で raw。
        text_redacted = redact_provider_body(text, unsafe_dump=args.unsafe_dump_response)
        if args.unsafe_dump_response:
            raw_msg = f"--- raw ---\n{text[:1000]}"
        else:
            raw_msg = (
                f"--- redacted (use --unsafe-dump-response for raw) ---\n"
                f"len={text_redacted['length']} sha256={text_redacted['sha256']}"
            )
        print(f"ERROR: LLM 応答が JSON parse 失敗: {e}\n{raw_msg}", file=sys.stderr)
        return emit_json("llm_json_invalid", 5, error=str(e))

    out_path = Path(args.output)
    out_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote: {out_path}")
    print(f"slides: {len(plan.get('slides', []))}")
    return emit_json(
        "success", 0,
        model=args.model,
        max_tokens=max_tokens,
        max_input_words=max_input_words,
        max_input_segments=max_input_segments,
        slides=len(plan.get("slides", [])),
        output=str(out_path),
    )


if __name__ == "__main__":
    sys.exit(main())
