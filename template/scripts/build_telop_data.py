#!/usr/bin/env python3
"""SuperMovie subtitles phase: transcript_fixed.json → telopData.ts (BudouX 分割版).

Codex Phase 2b design (2026-05-04, CODEX_PHASE2B_BUDOUX) 推奨:
  C' = BudouX first + optional LLM plan.

設計差分 (Phase 1 build_telop_data.py との違い):
  - 文字数比例で句読点・助詞境界を探す split_segment_text() を廃止
  - 代わりに BudouX (scripts/budoux_split.mjs) で意味単位の phrases を取得
  - phrases を「max_chars 以内になる範囲で連結」しながら 1 telop 化
  - 単語途中切れ・1字単独 telop が発生しにくい

Usage:
    python3 scripts/build_telop_data.py [--baseline]

  --baseline を付けると BudouX 不使用の旧ロジックで生成する (KPI 比較用).

入力: transcript_fixed.json + vad_result.json + (optional) typo_dict.json
出力: src/テロップテンプレート/telopData.ts
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
# Phase 3 obs migration step 3 PR-C (Codex 21:01 step 3 verdict S3-5):
# build_telop_data の telop raw text を default redact、--unsafe-show-user-content で raw。
from _observability import (  # noqa: E402
    build_status,
    emit_json as _obs_emit_json,
    resolve_run_context,
    safe_artifact_path,
    user_content_meta,
)
from timeline import (  # noqa: E402
    TranscriptSegmentError,
    VadSchemaError,
    ms_to_playback_frame as _msf_raw,
    read_video_config_fps,
    validate_transcript_segment,
    validate_vad_schema,
)

FPS = read_video_config_fps(PROJ)  # Phase 3-J: timeline 共通化、videoConfig.FPS と同期
# Phase 1 短尺 (short) format 既定値、後段で project-config.json から読むよう拡張可能
MAX_CHARS = 24
MAX_CHARS_PER_LINE = 12
LINE_BREAK_THRESHOLD = 10
MIN_DURATION_FRAMES = round(1.5 * FPS)
MAX_DURATION_FRAMES = round(5.0 * FPS)


# ---------------- BudouX phrase 連結 (新ロジック) ----------------
def split_segment_text_budoux(seg_text: str, phrases: list[str], max_chars: int = MAX_CHARS) -> tuple[list[str], list[list[str]]]:
    """BudouX phrases を max_chars 以内で連結して telop 列にする。
    1 phrase が max_chars を超える場合のみ強制分割する。
    返り値: (parts: 各 telop の文字列, parts_phrases: 各 telop に含まれる phrase 列)"""
    parts: list[str] = []
    parts_phrases: list[list[str]] = []
    buf = ""
    buf_phrases: list[str] = []
    for p in phrases:
        if not p:
            continue
        if len(p) > max_chars:
            if buf:
                parts.append(buf)
                parts_phrases.append(buf_phrases)
                buf = ""
                buf_phrases = []
            for i in range(0, len(p), max_chars):
                chunk = p[i:i + max_chars]
                parts.append(chunk)
                parts_phrases.append([chunk])
            continue
        if len(buf) + len(p) <= max_chars:
            buf += p
            buf_phrases.append(p)
        else:
            parts.append(buf)
            parts_phrases.append(buf_phrases)
            buf = p
            buf_phrases = [p]
    if buf:
        parts.append(buf)
        parts_phrases.append(buf_phrases)
    return parts, parts_phrases


# ---------------- Phase 1 旧ロジック (baseline 比較用) ----------------
def split_segment_text_legacy(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    split_priority = ["。", "！", "？", "、", "ので", "けど", "から", "って", "ように", "として"]
    parts = [text]
    while True:
        new_parts = []
        changed = False
        for p in parts:
            if len(p) <= max_chars:
                new_parts.append(p)
                continue
            best = None
            for kw in split_priority:
                for m in re.finditer(re.escape(kw), p):
                    end = m.end()
                    if end == 0 or end >= len(p):
                        continue
                    if end <= max_chars:
                        if best is None or end > best:
                            best = end
            if best is None:
                best = max_chars
            new_parts.append(p[:best])
            new_parts.append(p[best:])
            changed = True
        parts = new_parts
        if not changed:
            break
    return parts


# ---------------- 改行 (phase 1 と同じ、preserve / ASCII word 保護) ----------------
def _is_inside_preserve(text: str, i: int, preserve: list[str]) -> bool:
    for p in preserve:
        if not p:
            continue
        start = 0
        while True:
            idx = text.find(p, start)
            if idx < 0:
                break
            end = idx + len(p)
            if idx < i < end:
                return True
            start = idx + 1
    return False


def _is_inside_word(text: str, i: int) -> bool:
    if i <= 0 or i >= len(text):
        return False
    prev_ch = text[i - 1]
    cur_ch = text[i]
    return (prev_ch.isascii() and prev_ch.isalnum()) and (cur_ch.isascii() and cur_ch.isalnum())


def _candidate_score(text, i, target, breakpoints, particles_after):
    score = 0
    prev = text[i - 1]
    if prev in breakpoints:
        score += 100
    for p in particles_after:
        if text[max(0, i - len(p)):i] == p:
            score += 50
            break
    score -= abs(i - target)
    return score


def _phrase_boundaries(phrases: list[str]) -> set[int]:
    """phrases から累積文字数 = 切り位置候補集合を返す."""
    out = set()
    cum = 0
    for p in phrases:
        cum += len(p)
        out.add(cum)
    return out


def insert_linebreak(text, max_per_line=MAX_CHARS_PER_LINE, threshold=LINE_BREAK_THRESHOLD,
                    preserve=None, phrases=None):
    """改行挿入。tier 優先順位:
      tier 0 (phrase aware): phrase 境界のみ + max_per_line ±2 内に収まる位置
      tier 1: phrase 境界 + max_per_line を超えても 2 行目が 3 字以上
      tier 2: phrase 不問 + max_per_line 内
      tier 3: 諦めて max_per_line 直後
    `phrases` を渡すと BudouX 文節境界が最優先される (Codex Phase 2b)。
    """
    preserve = preserve or []
    if len(text) <= threshold or "\n" in text:
        return text
    breakpoints = ["、", "。", "！", "？"]
    particles_after = ["を", "に", "で", "が", "は", "と", "から", "けど", "ので", "って", "ような", "として"]
    n = len(text)
    target = n // 2
    phrase_pos = _phrase_boundaries(phrases) if phrases else set()

    def forbidden(i):
        return _is_inside_preserve(text, i, preserve) or _is_inside_word(text, i)

    # tier 0: phrase 境界 ∩ tier1 範囲 (両行 max_per_line 以内 + 各行 2 字以上)
    tier0 = [i for i in phrase_pos
             if 1 <= i < n and not forbidden(i)
             and len(text[:i]) <= max_per_line and len(text[i:]) <= max_per_line
             and len(text[i:]) >= 2]
    # tier 1: phrase 境界 ∩ ゆるめ (max_per_line 超過許容、2 行目 3 字以上)
    tier1 = [i for i in phrase_pos
             if 1 <= i < n and not forbidden(i)
             and len(text[i:]) >= 3]
    # tier 2: phrase 不問 + max_per_line 内
    tier2 = [i for i in range(1, n) if not forbidden(i)
             and len(text[:i]) <= max_per_line and len(text[i:]) <= max_per_line]
    # tier 3: phrase 不問 + 両行 2 字以上
    tier3 = [i for i in range(1, n) if not forbidden(i)
             and len(text[:i]) >= 2 and len(text[i:]) >= 2]

    for tier in (tier0, tier1, tier2, tier3):
        if tier:
            candidates = tier
            break
    else:
        return text[:max_per_line] + "\n" + text[max_per_line:]

    best = max(candidates, key=lambda i: _candidate_score(text, i, target, breakpoints, particles_after))
    return text[:best] + "\n" + text[best:]


# ---------------- BudouX 呼び出し ----------------
def call_budoux(seg_texts: list[str]) -> list[list[str]]:
    """proj/scripts/budoux_split.mjs を呼んで各 segment の phrases を返す."""
    proj = PROJ
    payload = {"segments": [{"id": i, "text": t} for i, t in enumerate(seg_texts)]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fin:
        json.dump(payload, fin, ensure_ascii=False)
        fin_path = fin.name
    fout_path = fin_path.replace(".json", "_out.json")
    script = proj / "scripts" / "budoux_split.mjs"
    if not script.exists():
        raise FileNotFoundError(f"budoux_split.mjs not found at {script}")
    res = subprocess.run(
        ["node", str(script), "--in", fin_path, "--out", fout_path],
        cwd=str(proj),
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"budoux_split.mjs failed: {res.stderr}")
    out = json.loads(Path(fout_path).read_text(encoding="utf-8"))
    return [s.get("phrases", []) for s in out["segments"]]


# ---------------- VAD / cut ----------------
def build_cut_segments_from_vad(vad):
    """vad の speech_segments から cut timeline 構築 (Phase 3-K: validate 経由).

    Codex Phase 3-J review P2 #2 反映: build_slide_data / voicevox_narration と
    同じ validate_vad_schema を経由して、3 script で壊れた VAD の扱いを揃える。
    """
    validate_vad_schema(vad)
    speech = vad["speech_segments"]
    out = []
    cursor_ms = 0
    for i, seg in enumerate(speech):
        s_ms = seg["start"]
        e_ms = seg["end"]
        dur_ms = e_ms - s_ms
        out.append({
            "id": i + 1,
            "originalStartMs": s_ms,
            "originalEndMs": e_ms,
            "playbackStart": round(cursor_ms / 1000 * FPS),
            "playbackEnd": round((cursor_ms + dur_ms) / 1000 * FPS),
        })
        cursor_ms += dur_ms
    return out


def find_cut_segment_for_ms(ms, cut_segments):
    """build_telop 固有の用途で使われる helper (line 353-354 の fallback search 等)、
    timeline には移さず local 維持 (Codex Phase 3-M consultation 候補 ii、
    cut boundary clamp 用途で残置妥当)。"""
    for cs in cut_segments:
        if cs["originalStartMs"] <= ms <= cs["originalEndMs"]:
            return cs
    return None


def ms_to_playback_frame(ms, cut_segments):
    """Phase 3-M (Codex Phase 3-L 次点指摘 ii): timeline.ms_to_playback_frame
    に委譲。FPS 注入 wrapper、build_telop 固有挙動 (cut_segments 不在 → None)
    との差は run-time に main() が必ず cut_segments を提供するため不変。
    """
    return _msf_raw(ms, FPS, cut_segments)


# ---------------- 本体 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", action="store_true", help="BudouX を使わず Phase 1 旧ロジックで生成")
    ap.add_argument("--json-log", action="store_true",
                    help="末尾に summary を 1 行純 JSON として emit "
                         "(downstream observability、既存 stdout は維持)")
    ap.add_argument("--unsafe-show-user-content", action="store_true",
                    help="telop raw text を stdout に raw で出す "
                         "(default: length / sha256 only、debug 専用)")
    ap.add_argument("--unsafe-keep-abs-path", action="store_true",
                    help="json tail の artifact path を絶対 path のまま emit (debug 専用)")
    args = ap.parse_args()
    start_time = time.monotonic()

    # PR-E: trace context resolve、_emit_error / 本走 emit 両方で共有
    run_ctx = resolve_run_context()

    # Codex 21:46 PR6 review P1 fix: error path も `--json-log` で
    # v1 status JSON tail emit する。1 invocation 1 emission contract 維持。
    def _emit_error(v0_status, exit_code, *, category=None, **extra):
        duration_ms = int((time.monotonic() - start_time) * 1000)
        payload = build_status(
            script="build_telop_data",
            v0_status=v0_status,
            exit_code=exit_code,
            counts={},
            artifacts=[],
            cost=None,
            duration_ms=duration_ms,
            category_override=category,
            redaction_rules=[],
            run_id=run_ctx["run_id"],
            parent_run_id=run_ctx["parent_run_id"],
            step_id=run_ctx["step_id"],
            **extra,
        )
        _obs_emit_json(args.json_log, payload)
        return exit_code

    transcript = json.loads((PROJ / "transcript_fixed.json").read_text(encoding="utf-8"))
    vad = json.loads((PROJ / "vad_result.json").read_text(encoding="utf-8"))
    typo = (PROJ / "typo_dict.json")
    typo_dict = json.loads(typo.read_text(encoding="utf-8")) if typo.exists() else {}
    preserve = typo_dict.get("preserve", [])
    cut_segments = build_cut_segments_from_vad(vad)
    cut_total_frames = cut_segments[-1]["playbackEnd"] if cut_segments else 0

    words = transcript["words"]
    segments = transcript["segments"]

    # Phase 3-K (Codex Phase 3-I review P2 #3 拡張): build_telop でも transcript
    # 壊れたデータを早期検出。
    # Phase 3-L: require_timing=True で start/end 必須化 (telop は ms→frame
    # 変換で start/end を必須使用するため)。
    for i, seg in enumerate(segments):
        try:
            validate_transcript_segment(seg, idx=i, require_timing=True)
        except TranscriptSegmentError as e:
            print(f"ERROR: transcript validation failed: {e}", file=_sys.stderr)
            _sys.exit(_emit_error("build_telop_transcript_invalid", 3, error=str(e)))

    # 分割 phase: BudouX 呼出 (一括)
    seg_parts: list[list[str]] = []
    seg_parts_phrases: list[list[list[str]]] = []  # part 毎の phrase リスト (insert_linebreak で使用)
    if args.baseline:
        for s in segments:
            parts = split_segment_text_legacy(s["text"], MAX_CHARS)
            seg_parts.append(parts)
            seg_parts_phrases.append([[] for _ in parts])  # baseline は phrase なし
    else:
        try:
            phrases_list = call_budoux([s["text"] for s in segments])
            for i, s in enumerate(segments):
                parts, parts_phrases = split_segment_text_budoux(s["text"], phrases_list[i], MAX_CHARS)
                seg_parts.append(parts)
                seg_parts_phrases.append(parts_phrases)
        except Exception as e:
            print(f"WARN: BudouX 失敗 → legacy fallback: {e}")
            for s in segments:
                parts = split_segment_text_legacy(s["text"], MAX_CHARS)
                seg_parts.append(parts)
                seg_parts_phrases.append([[] for _ in parts])

    telop_segments = []
    weaknesses = []
    telop_id = 1

    for seg_idx, (seg, parts, parts_phrases) in enumerate(zip(segments, seg_parts, seg_parts_phrases)):
        if not parts:
            continue
        # 1-2 字を直前にマージ (文末「す」等の単独 telop 抑制)、phrases も追従
        merged: list[str] = []
        merged_phrases: list[list[str]] = []
        for p_idx, p in enumerate(parts):
            ph = parts_phrases[p_idx] if p_idx < len(parts_phrases) else []
            if merged and len(p) <= 2:
                merged[-1] = merged[-1] + p
                merged_phrases[-1] = (merged_phrases[-1] or []) + ph
            else:
                merged.append(p)
                merged_phrases.append(ph)
        parts = merged
        parts_phrases = merged_phrases

        seg_total_chars = sum(len(p) for p in parts)
        cum_chars = 0
        for p_idx, part_text in enumerate(parts):
            part_phrases = parts_phrases[p_idx] if p_idx < len(parts_phrases) else []
            part_chars = len(part_text)
            ratio_start = cum_chars / max(seg_total_chars, 1)
            ratio_end = (cum_chars + part_chars) / max(seg_total_chars, 1)
            seg_dur_ms = seg["end"] - seg["start"]
            ms_start = seg["start"] + round(seg_dur_ms * ratio_start)
            ms_end = seg["start"] + round(seg_dur_ms * ratio_end)

            # cut 境界またぎ防止
            cs_start = find_cut_segment_for_ms(ms_start, cut_segments)
            cs_end = find_cut_segment_for_ms(ms_end, cut_segments)
            if cs_start and cs_end and cs_start is not cs_end:
                ms_end = cs_start["originalEndMs"]
                weaknesses.append({"type": "telop_cut_boundary_clamp", "telop_text": part_text})

            pb_start = ms_to_playback_frame(ms_start, cut_segments)
            pb_end = ms_to_playback_frame(ms_end, cut_segments)
            if pb_start is None or pb_end is None:
                fallback = next((cs for cs in cut_segments if ms_start <= cs["originalEndMs"]), None)
                if fallback:
                    pb_start = fallback["playbackStart"]
                    pb_end = min(fallback["playbackEnd"], (pb_start or 0) + MAX_DURATION_FRAMES)
                else:
                    weaknesses.append({"type": "telop_outside_cut", "telop_text": part_text})
                    cum_chars += part_chars
                    continue

            duration = pb_end - pb_start
            if duration < MIN_DURATION_FRAMES:
                pb_end = pb_start + MIN_DURATION_FRAMES
            if duration > MAX_DURATION_FRAMES:
                pb_end = pb_start + MAX_DURATION_FRAMES
            if pb_end > cut_total_frames:
                pb_end = cut_total_frames
            if pb_start >= cut_total_frames:
                weaknesses.append({"type": "telop_after_cut_total", "telop_text": part_text})
                cum_chars += part_chars
                continue
            if telop_segments and pb_start < telop_segments[-1]["endFrame"]:
                pb_start = telop_segments[-1]["endFrame"]
                if pb_end <= pb_start:
                    pb_end = min(pb_start + MIN_DURATION_FRAMES, cut_total_frames)
                    if pb_end <= pb_start:
                        weaknesses.append({"type": "telop_overlap_unresolvable"})
                        cum_chars += part_chars
                        continue

            wrapped = insert_linebreak(part_text, preserve=preserve, phrases=part_phrases)
            telop_segments.append({
                "id": telop_id,
                "startFrame": pb_start,
                "endFrame": pb_end,
                "text": wrapped,
                "style": "normal",
                "templateId": "WhiteBlueTeleopV2",
                "template": 2,
                "animation": "fadeOnly",
            })
            telop_id += 1
            cum_chars += part_chars

    ts_lines = [
        "import type { TelopSegment } from './telopTypes';",
        "import { FPS as CONFIG_FPS } from '../videoConfig';",
        "import { CUT_TOTAL_FRAMES } from '../cutData';",
        "",
        "// 自動生成: scripts/build_telop_data.py" + (" (--baseline)" if args.baseline else " (BudouX)"),
        "// transcript_fixed.json の segments を BudouX phrase 単位で分割し、",
        "// cutData 経由で playback frame に変換した TelopSegment[]",
        "",
        "export const FPS = CONFIG_FPS;",
        "export const TOTAL_FRAMES = CUT_TOTAL_FRAMES;",
        "",
        "export const telopData: TelopSegment[] = [",
    ]
    for t in telop_segments:
        ts_lines.append(
            f"  {{ id: {t['id']}, startFrame: {t['startFrame']}, endFrame: {t['endFrame']}, "
            f"text: {json.dumps(t['text'], ensure_ascii=False)}, "
            f"style: '{t['style']}', templateId: '{t['templateId']}', "
            f"template: {t['template']}, animation: '{t['animation']}' }},"
        )
    ts_lines.append("];")
    ts_lines.append("")

    out_path = PROJ / "src" / "テロップテンプレート" / "telopData.ts"
    out_path.write_text("\n".join(ts_lines), encoding="utf-8")

    mode_label = "baseline" if args.baseline else "BudouX"
    print(f"=== telopData.ts 生成 ({mode_label}) ===")
    # PR-I (human stdout path leak audit、Codex 00:08): default redact、--unsafe-keep-abs-path で raw。
    print(f"path: {safe_artifact_path(out_path, project_root=PROJ, unsafe_keep_abs_path=args.unsafe_keep_abs_path)}")
    print(f"telop count: {len(telop_segments)}")
    print(f"weaknesses: {len(weaknesses)}")
    print()
    # Codex 21:01 step 3 S3-5 fix: default は telop raw を出さず length/hash で表示。
    # --unsafe-show-user-content で raw (debug 用)。docs/OBSERVABILITY.md §Redaction Rules 整合。
    for t in telop_segments:
        if args.unsafe_show_user_content:
            text_oneline = t["text"].replace("\n", "↵")
            text_repr = f"'{text_oneline}'"
        else:
            meta = user_content_meta(t["text"])
            text_repr = f"<redacted len={meta['length']} sha256={meta['sha256']}>"
        print(f"  [{t['id']:2}] f{t['startFrame']:5}-{t['endFrame']:5} {text_repr}")

    # Phase 3 obs migration step 3 PR-C: v1 status JSON tail emit (--json-log 時のみ)
    duration_ms = int((time.monotonic() - start_time) * 1000)
    artifacts = [{
        "path": safe_artifact_path(
            out_path, project_root=PROJ,
            unsafe_keep_abs_path=args.unsafe_keep_abs_path,
        ),
        "kind": "ts",
    }]
    redaction_rules = ["user_content"]
    if not args.unsafe_keep_abs_path:
        redaction_rules.append("abs_path")
    payload = build_status(
        script="build_telop_data",
        v0_status="build_telop_ok",
        exit_code=0,
        counts={
            "telop_count": len(telop_segments),
            "weaknesses": len(weaknesses),
        },
        artifacts=artifacts,
        cost=None,
        duration_ms=duration_ms,
        category_override="telop-build",
        redaction_rules=redaction_rules,
        run_id=run_ctx["run_id"],
        parent_run_id=run_ctx["parent_run_id"],
        step_id=run_ctx["step_id"],
        mode=mode_label,
    )
    _obs_emit_json(args.json_log, payload)


if __name__ == "__main__":
    main()
