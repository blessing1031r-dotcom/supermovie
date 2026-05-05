#!/usr/bin/env python3
"""baseline と new の telop 分割を 5 KPI で比較する.

Codex Phase 2b design (2026-05-04, CODEX_PHASE2B_BUDOUX) Q3 の KPI ゲート:
  hard_word_split_count = 0
  linebreak_inside_preserve_count = 0
  single_char_telops_new <= baseline
  two_char_tail_telops_new <= baseline
  frame_overlap_count = 0

Usage:
    # 1. baseline (旧ロジック) で生成
    python3 scripts/build_telop_data.py --baseline
    cp src/テロップテンプレート/telopData.ts /tmp/telop_baseline.ts

    # 2. new (BudouX) で生成
    python3 scripts/build_telop_data.py
    cp src/テロップテンプレート/telopData.ts /tmp/telop_new.ts

    # 3. 比較
    python3 scripts/compare_telop_split.py /tmp/telop_baseline.ts /tmp/telop_new.ts
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent

# Phase 3 obs migration step 3 (Codex 21:01 verdict S3-6): KPI 比較なので
# v1 status emit 経由で counts に KPI / gate 結果を入れる、cost=null。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _observability import (  # noqa: E402
    build_status,
    emit_json as _obs_emit_json,
    redact_error_message,
    resolve_run_context,
    safe_artifact_path,
)


def parse_telop_data_ts(ts_path: Path) -> list[dict]:
    """telopData.ts から telop 配列を抽出 (簡易 parser)."""
    text = ts_path.read_text(encoding="utf-8")
    # `text: "..."` 部分は \n や 引用符のエスケープがあるので JSON で読む
    items = []
    for m in re.finditer(
        r"\{\s*id:\s*(\d+),\s*startFrame:\s*(\d+),\s*endFrame:\s*(\d+),\s*text:\s*(.+?),\s*style:\s*'(\w+)'",
        text,
        re.S,
    ):
        idn, sf, ef, txt_raw, style = m.groups()
        # txt_raw は JSON 文字列 (例: "abc\\nd") として書かれている
        try:
            txt = json.loads(txt_raw)
        except json.JSONDecodeError:
            txt = txt_raw.strip().strip(",").strip("\"")
        items.append({
            "id": int(idn),
            "startFrame": int(sf),
            "endFrame": int(ef),
            "text": txt,
            "style": style,
        })
    return items


def kpi_metrics(telops: list[dict], words: list[dict], preserve: list[str]) -> dict:
    """telopData から KPI を計算する."""
    out = {
        "telop_count": len(telops),
        "single_char_telops": 0,
        "two_char_tail_telops": 0,  # 改行後の最終行が 2 字以下
        "linebreak_inside_preserve_count": 0,
        "hard_word_split_count": 0,
        "frame_overlap_count": 0,
    }
    # single char telop
    for t in telops:
        plain = t["text"].replace("\n", "")
        if len(plain) <= 1:
            out["single_char_telops"] += 1
        # 改行後の最終行が 2 字以下
        if "\n" in t["text"]:
            last_line = t["text"].split("\n")[-1]
            if len(last_line) <= 2:
                out["two_char_tail_telops"] += 1
            # 改行位置が preserve 内に入っていないか
            line1, line2 = t["text"].split("\n", 1)
            joined = t["text"].replace("\n", "")
            i = len(line1)  # 改行位置
            for p in preserve:
                if not p:
                    continue
                idx = joined.find(p)
                while idx >= 0:
                    end = idx + len(p)
                    if idx < i < end:
                        out["linebreak_inside_preserve_count"] += 1
                        break
                    idx = joined.find(p, idx + 1)
    # frame overlap
    sorted_t = sorted(telops, key=lambda t: t["startFrame"])
    for i in range(len(sorted_t) - 1):
        if sorted_t[i]["endFrame"] > sorted_t[i + 1]["startFrame"]:
            out["frame_overlap_count"] += 1

    # hard_word_split: telop 境界が transcript の word 途中に入った件数
    # word.text を順に連結して各 word の文字 index を取得、
    # telop の text を joined 全文に対して find で一致位置を取得、
    # その境界 (前の telop の終わり + 次の始まり) が word の途中に来ていないか
    out["hard_word_split_count"] = 0
    if not words:
        return out
    full_text = "".join(w.get("text", "") for w in words)
    word_starts = []  # 各 word の full_text 上の開始位置
    cursor = 0
    for w in words:
        word_starts.append(cursor)
        cursor += len(w.get("text", ""))
    # 各 telop の境界の文字位置 (full_text 上)
    cursor = 0
    last_end = 0
    for t in telops:
        plain = t["text"].replace("\n", "")
        idx = full_text.find(plain, last_end)
        if idx < 0:
            continue
        end_pos = idx + len(plain)
        # 次 telop は end_pos 以降から探す, ここで「end_pos が word 途中」をチェック
        for ws_idx, ws in enumerate(word_starts):
            we = ws + len(words[ws_idx].get("text", ""))
            if ws < end_pos < we:
                out["hard_word_split_count"] += 1
                break
        last_end = end_pos
    return out


def main():
    # Phase 3 obs migration step 3: argparse 経由で --json-log 等の obs flags を追加。
    ap = argparse.ArgumentParser(description="baseline と new の telop 分割を 5 KPI で比較する")
    ap.add_argument("baseline", help="baseline telopData.ts path")
    ap.add_argument("new", help="new telopData.ts path")
    ap.add_argument("--json-log", action="store_true",
                    help="末尾に summary を 1 行純 JSON として emit "
                         "(downstream observability、既存 stdout は維持)")
    ap.add_argument("--unsafe-keep-abs-path", action="store_true",
                    help="json tail の artifact path を絶対 path のまま emit (debug 専用)")
    args = ap.parse_args()

    # PR-E: trace context resolve (1 invocation 1 resolve、emit closure に閉じ込め)
    run_ctx = resolve_run_context()

    start_time = time.monotonic()
    baseline_path = Path(args.baseline)
    new_path = Path(args.new)

    # PR-G (error path tail audit): file read / parse failure を tail emit する early-emit closure。
    # KPI 計算前の例外でも `--json-log` 時に v1 tail を返す。
    def _emit_early(v0_status, exit_code, **extra):
        duration_ms = int((time.monotonic() - start_time) * 1000)
        payload = build_status(
            script="compare_telop_split",
            v0_status=v0_status,
            exit_code=exit_code,
            counts={},
            artifacts=[],
            cost=None,
            duration_ms=duration_ms,
            redaction_rules=[],
            run_id=run_ctx["run_id"],
            parent_run_id=run_ctx["parent_run_id"],
            step_id=run_ctx["step_id"],
            **extra,
        )
        _obs_emit_json(args.json_log, payload)
        return exit_code

    try:
        transcript = json.loads((PROJ / "transcript_fixed.json").read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        print(f"ERROR: transcript_fixed.json not found: {e}", file=sys.stderr)
        return _emit_early("transcript_missing", 3, error=redact_error_message(str(e)))
    except (json.JSONDecodeError, OSError) as e:
        print(f"ERROR: transcript_fixed.json parse failed: {e}", file=sys.stderr)
        return _emit_early("transcript_invalid", 3, error=redact_error_message(str(e)))

    typo = (PROJ / "typo_dict.json")
    try:
        typo_dict = json.loads(typo.read_text(encoding="utf-8")) if typo.exists() else {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"ERROR: typo_dict.json parse failed: {e}", file=sys.stderr)
        return _emit_early("typo_dict_invalid", 3, error=redact_error_message(str(e)))
    preserve = typo_dict.get("preserve", [])
    words = transcript.get("words", [])

    try:
        base_telops = parse_telop_data_ts(baseline_path)
        new_telops = parse_telop_data_ts(new_path)
    except (FileNotFoundError, OSError) as e:
        print(f"ERROR: telop ts read failed: {e}", file=sys.stderr)
        return _emit_early("telop_ts_missing", 3, error=redact_error_message(str(e)))
    except Exception as e:
        print(f"ERROR: telop ts parse failed: {e}", file=sys.stderr)
        return _emit_early("telop_ts_invalid", 3, error=redact_error_message(str(e)))

    try:
        base_kpi = kpi_metrics(base_telops, words, preserve)
        new_kpi = kpi_metrics(new_telops, words, preserve)
    except Exception as e:
        print(f"ERROR: kpi calc failed: {e}", file=sys.stderr)
        return _emit_early("kpi_calc_error", 3, error=redact_error_message(str(e)))

    def emit_obs(status, exit_code, gates_result=None):
        """v1 status JSON を --json-log 時のみ emit。category_override で
        kpi-comparison 固定、cost=null (KPI 比較なので provider rate 対象外)。"""
        duration_ms = int((time.monotonic() - start_time) * 1000)
        artifacts = [
            {
                "path": safe_artifact_path(
                    baseline_path,
                    project_root=PROJ,
                    unsafe_keep_abs_path=args.unsafe_keep_abs_path,
                ),
                "kind": "ts",
            },
            {
                "path": safe_artifact_path(
                    new_path,
                    project_root=PROJ,
                    unsafe_keep_abs_path=args.unsafe_keep_abs_path,
                ),
                "kind": "ts",
            },
        ]
        counts = {
            "baseline_kpi": base_kpi,
            "new_kpi": new_kpi,
        }
        if gates_result is not None:
            counts["gates"] = gates_result
        redaction_rules = []
        if not args.unsafe_keep_abs_path:
            redaction_rules.append("abs_path")
        payload = build_status(
            script="compare_telop_split",
            v0_status=status,
            exit_code=exit_code,
            counts=counts,
            artifacts=artifacts,
            cost=None,
            duration_ms=duration_ms,
            category_override="kpi-comparison",
            redaction_rules=redaction_rules,
            run_id=run_ctx["run_id"],
            parent_run_id=run_ctx["parent_run_id"],
            step_id=run_ctx["step_id"],
        )
        return _obs_emit_json(args.json_log, payload)

    print("=== KPI comparison (baseline -> new) ===")
    keys = ["telop_count", "single_char_telops", "two_char_tail_telops",
            "linebreak_inside_preserve_count", "hard_word_split_count", "frame_overlap_count"]
    print(f"{'metric':35} {'baseline':>10} {'new':>6} {'delta':>8}")
    print("-" * 64)
    for k in keys:
        b = base_kpi[k]
        n = new_kpi[k]
        delta = n - b
        sign = "+" if delta > 0 else ""
        print(f"{k:35} {b:>10} {n:>6} {sign}{delta:>7}")

    print()
    print("=== gate evaluation (Codex Phase 2b Q3) ===")
    gates = []
    gates.append(("hard_word_split_count == 0", new_kpi["hard_word_split_count"] == 0))
    gates.append(("linebreak_inside_preserve_count == 0", new_kpi["linebreak_inside_preserve_count"] == 0))
    gates.append(("single_char_telops_new <= baseline", new_kpi["single_char_telops"] <= base_kpi["single_char_telops"]))
    gates.append(("two_char_tail_telops_new <= baseline", new_kpi["two_char_tail_telops"] <= base_kpi["two_char_tail_telops"]))
    gates.append(("frame_overlap_count == 0", new_kpi["frame_overlap_count"] == 0))
    for desc, ok in gates:
        print(f"  {'PASS' if ok else 'FAIL'}: {desc}")

    gates_result = {desc: bool(ok) for desc, ok in gates}
    if all(ok for _, ok in gates):
        print("\nALL PASS")
        # Phase 3 obs migration step 3: v0 status "all_pass" → v1 ok + category_override
        emit_obs("all_pass", 0, gates_result=gates_result)
        sys.exit(0)
    else:
        print("\nSOME FAIL")
        emit_obs("some_fail", 1, gates_result=gates_result)
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main() or 0)
