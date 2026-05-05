#!/usr/bin/env python3
"""SuperMovie Phase 3-B: transcript_fixed.json + project-config.json → src/Slides/slideData.ts.

Codex Phase 3B design (2026-05-04, CODEX_PHASE3B_NEXT) 推奨: A 一択 + deterministic first.

入力:
    <PROJECT>/transcript_fixed.json  - segments[] / words[]
    <PROJECT>/project-config.json    - format / tone
    (任意) <PROJECT>/vad_result.json - cut segments の元データ (cut 後 frame 変換用)

出力:
    <PROJECT>/src/Slides/slideData.ts - SlideSegment[]

Usage:
    python3 scripts/build_slide_data.py [--mode topic|segment]

  --mode topic    (default): 連続 segments をグループ化して 1 slide に
  --mode segment           : 1 transcript segment = 1 slide (シンプル)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Phase 3 obs migration step 3 PR-C (Codex 21:01 step 3 verdict S3-5):
# build_slide_data の title/telop raw text を default redact、--unsafe-show-user-content で raw。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _observability import (  # noqa: E402
    build_status,
    emit_json as _obs_emit_json,
    safe_artifact_path,
    user_content_meta,
)

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from timeline import (  # noqa: E402
    TranscriptSegmentError,
    build_cut_segments_from_vad as _bcs_raw,
    ms_to_playback_frame as _msf_raw,
    read_video_config_fps,
    validate_transcript_segment,
    validate_vad_schema,
)

FPS = read_video_config_fps(PROJ)  # Phase 3-J: timeline 共通化、videoConfig.FPS と同期
SILENCE_THRESHOLD_MS = 1500  # 1.5 秒以上の無音で話題区切り
TITLE_MAX_CHARS = {"youtube": 18, "short": 14, "square": 16}
BULLET_MAX_CHARS = {"youtube": 24, "short": 18, "square": 20}
MAX_BULLETS_PER_SLIDE = 5
MAX_SEGMENTS_PER_SLIDE = 5  # silence 検出失敗時の機械 fallback


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def build_cut_segments_from_vad(vad: dict | None) -> list[dict]:
    """Phase 3-J: timeline.build_cut_segments_from_vad を FPS 注入 wrapper.

    旧 inline 実装は timeline.py に集約した。validate を経由して schema 破損は
    raise (build_slide_data は cut が壊れていれば slide も壊れるため fail-fast)。
    """
    if not vad:
        return []
    return _bcs_raw(validate_vad_schema(vad), FPS)


def ms_to_playback_frame(ms: int, cut_segments: list[dict]) -> int | None:
    """Phase 3-J: timeline.ms_to_playback_frame を FPS 注入 wrapper."""
    return _msf_raw(ms, FPS, cut_segments)


def truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 1] + "…"


def group_topics(segments: list[dict], threshold_ms: int = SILENCE_THRESHOLD_MS) -> list[list[dict]]:
    """隣接 segments の間隔 >= threshold_ms で話題区切り."""
    if not segments:
        return []
    groups: list[list[dict]] = [[segments[0]]]
    for prev, cur in zip(segments, segments[1:]):
        gap_ms = cur["start"] - prev["end"]
        if gap_ms >= threshold_ms or len(groups[-1]) >= MAX_SEGMENTS_PER_SLIDE:
            groups.append([cur])
        else:
            groups[-1].append(cur)
    return groups


def style_for_tone(tone: str) -> dict:
    table = {
        "プロフェッショナル": {"align": "center", "bg": "rgba(20, 26, 44, 0.92)", "emphasis_ratio": 0.2},
        "エンタメ": {"align": "left", "bg": "#101a2c", "emphasis_ratio": 0.4},
        "カジュアル": {"align": "left", "bg": "rgba(40, 30, 60, 0.9)", "emphasis_ratio": 0.3},
        "教育的": {"align": "left", "bg": "#0f2540", "emphasis_ratio": 0.4},
    }
    return table.get(tone, table["プロフェッショナル"])


def build_slides_topic_mode(segments: list[dict], cut_segments: list[dict],
                            fmt: str, tone: str) -> list[dict]:
    title_max = TITLE_MAX_CHARS.get(fmt, TITLE_MAX_CHARS["short"])
    bullet_max = BULLET_MAX_CHARS.get(fmt, BULLET_MAX_CHARS["short"])
    style = style_for_tone(tone)
    cut_total = cut_segments[-1]["playbackEnd"] if cut_segments else None

    slides: list[dict] = []
    groups = group_topics(segments)
    for group_idx, group in enumerate(groups):
        first = group[0]
        last = group[-1]

        pb_start = ms_to_playback_frame(first["start"], cut_segments)
        pb_end = ms_to_playback_frame(last["end"], cut_segments)
        if pb_start is None or pb_end is None:
            continue
        if cut_total is not None:
            pb_end = min(pb_end, cut_total)
        if pb_end <= pb_start:
            continue

        title = truncate(first["text"], title_max)
        subtitle = truncate(last["text"], title_max + 6) if len(group) > 1 and last is not first else None
        if subtitle == title:
            subtitle = None

        bullets: list[dict] = []
        bullets_source = group[1:-1] if len(group) >= 3 else group
        for i, seg in enumerate(bullets_source[:MAX_BULLETS_PER_SLIDE]):
            text = truncate(seg["text"], bullet_max)
            emphasis = (i == 0 and style["emphasis_ratio"] >= 0.4) or (
                style["emphasis_ratio"] >= 0.3 and i == len(bullets_source) // 2
            )
            bullets.append({"text": text, "emphasis": emphasis})

        slides.append({
            "id": group_idx + 1,
            "startFrame": pb_start,
            "endFrame": pb_end,
            "title": title,
            "subtitle": subtitle,
            "bullets": bullets if bullets else None,
            "align": style["align"],
            "backgroundColor": style["bg"],
            "videoLayer": "visible",
        })
    return slides


def build_slides_segment_mode(segments: list[dict], cut_segments: list[dict],
                              fmt: str, tone: str) -> list[dict]:
    title_max = TITLE_MAX_CHARS.get(fmt, TITLE_MAX_CHARS["short"])
    style = style_for_tone(tone)
    cut_total = cut_segments[-1]["playbackEnd"] if cut_segments else None

    slides: list[dict] = []
    for i, seg in enumerate(segments):
        pb_start = ms_to_playback_frame(seg["start"], cut_segments)
        pb_end = ms_to_playback_frame(seg["end"], cut_segments)
        if pb_start is None or pb_end is None:
            continue
        if cut_total is not None:
            pb_end = min(pb_end, cut_total)
        if pb_end <= pb_start:
            continue
        slides.append({
            "id": i + 1,
            "startFrame": pb_start,
            "endFrame": pb_end,
            "title": truncate(seg["text"], title_max),
            "align": style["align"],
            "backgroundColor": style["bg"],
            "videoLayer": "visible",
        })
    return slides


def render_slide_data_ts(slides: list[dict]) -> str:
    lines = [
        "import type { SlideSegment } from './types';",
        "",
        "// 自動生成: scripts/build_slide_data.py",
        f"// {len(slides)} slides を transcript_fixed.json から生成",
        "",
        "export const slideData: SlideSegment[] = [",
    ]
    for s in slides:
        parts = [
            f"id: {s['id']}",
            f"startFrame: {s['startFrame']}",
            f"endFrame: {s['endFrame']}",
            f"title: {json.dumps(s['title'], ensure_ascii=False)}",
        ]
        if s.get("subtitle"):
            parts.append(f"subtitle: {json.dumps(s['subtitle'], ensure_ascii=False)}")
        if s.get("bullets"):
            bullets_ts = ", ".join(
                "{ text: " + json.dumps(b["text"], ensure_ascii=False)
                + (", emphasis: true" if b.get("emphasis") else "")
                + " }"
                for b in s["bullets"]
            )
            parts.append(f"bullets: [{bullets_ts}]")
        if s.get("align"):
            parts.append(f"align: '{s['align']}'")
        if s.get("backgroundColor"):
            parts.append(f"backgroundColor: {json.dumps(s['backgroundColor'], ensure_ascii=False)}")
        if s.get("videoLayer"):
            parts.append(f"videoLayer: '{s['videoLayer']}'")
        lines.append("  { " + ", ".join(parts) + " },")
    lines.append("];")
    lines.append("")
    return "\n".join(lines)


PLAN_VERSION = "supermovie.slide_plan.v1"
ALLOWED_ALIGN = ("center", "left")
ALLOWED_VIDEO_LAYER = ("visible", "dimmed", "hidden")


def validate_slide_plan(plan: dict, words: list[dict], cut_total_frames: int | None,
                        fmt: str) -> list[str]:
    """Codex Phase 3-C validate (Q4) を実装。invalid なら理由を返す (空 list = OK)."""
    title_max = TITLE_MAX_CHARS.get(fmt, TITLE_MAX_CHARS["short"])
    bullet_max = BULLET_MAX_CHARS.get(fmt, BULLET_MAX_CHARS["short"])
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["plan is not a dict"]
    if plan.get("version") != PLAN_VERSION:
        errors.append(f"version mismatch (expect {PLAN_VERSION})")
    slides = plan.get("slides")
    if not isinstance(slides, list):
        errors.append("slides is not a list")
        return errors
    n_words = len(words)
    last_end_idx = -1
    last_id = 0
    for i, s in enumerate(slides):
        if not isinstance(s, dict):
            errors.append(f"slides[{i}] not a dict")
            continue
        sid = s.get("id")
        if not isinstance(sid, int) or sid <= last_id:
            errors.append(f"slides[{i}].id must be ascending int (got {sid})")
        else:
            last_id = sid
        sw = s.get("startWordIndex")
        ew = s.get("endWordIndex")
        if not (isinstance(sw, int) and isinstance(ew, int)
                and 0 <= sw <= ew < n_words):
            errors.append(f"slides[{i}] startWordIndex/endWordIndex out of range (got {sw}/{ew}, words={n_words})")
            continue
        if sw <= last_end_idx:
            errors.append(f"slides[{i}] word range overlaps previous (sw={sw} <= prev end {last_end_idx})")
        last_end_idx = ew
        title = s.get("title")
        if not isinstance(title, str) or not title.strip():
            errors.append(f"slides[{i}].title empty")
        elif len(title) > title_max:
            errors.append(f"slides[{i}].title too long ({len(title)} > {title_max})")
        bullets = s.get("bullets") or []
        if not isinstance(bullets, list) or len(bullets) > MAX_BULLETS_PER_SLIDE:
            errors.append(f"slides[{i}].bullets exceeds {MAX_BULLETS_PER_SLIDE}")
        else:
            for j, b in enumerate(bullets):
                bt = b.get("text") if isinstance(b, dict) else None
                if not isinstance(bt, str) or not bt.strip():
                    errors.append(f"slides[{i}].bullets[{j}] empty text")
                elif len(bt) > bullet_max:
                    errors.append(f"slides[{i}].bullets[{j}] too long ({len(bt)} > {bullet_max})")
        align = s.get("align")
        if align is not None and align not in ALLOWED_ALIGN:
            errors.append(f"slides[{i}].align invalid ({align})")
        video_layer = s.get("videoLayer")
        if video_layer is not None and video_layer not in ALLOWED_VIDEO_LAYER:
            errors.append(f"slides[{i}].videoLayer invalid ({video_layer})")
    return errors


def build_slides_from_plan(plan: dict, words: list[dict], cut_segments: list[dict],
                           fmt: str, tone: str) -> list[dict]:
    """validated plan を SlideSegment dict 列に変換 (frame は script 側で計算)."""
    style = style_for_tone(tone)
    cut_total = cut_segments[-1]["playbackEnd"] if cut_segments else None
    slides: list[dict] = []
    for s in plan["slides"]:
        sw = s["startWordIndex"]
        ew = s["endWordIndex"]
        ms_start = words[sw].get("start", 0)
        ms_end = words[ew].get("end", 0)
        pb_start = ms_to_playback_frame(ms_start, cut_segments)
        pb_end = ms_to_playback_frame(ms_end, cut_segments)
        if pb_start is None or pb_end is None or pb_end <= pb_start:
            continue
        if cut_total is not None:
            pb_end = min(pb_end, cut_total)
        slides.append({
            "id": s["id"],
            "startFrame": pb_start,
            "endFrame": pb_end,
            "title": s["title"],
            "subtitle": s.get("subtitle"),
            "bullets": s.get("bullets") or None,
            "align": s.get("align") or style["align"],
            "backgroundColor": s.get("backgroundColor") or style["bg"],
            "videoLayer": s.get("videoLayer") or "visible",
        })
    return slides


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["topic", "segment"], default="topic")
    ap.add_argument("--plan", help="optional slide_plan.json (LLM 生成)")
    ap.add_argument("--strict-plan", action="store_true",
                    help="--plan の validate 失敗時に exit 2 (default は warning + deterministic fallback)")
    ap.add_argument("--json-log", action="store_true",
                    help="末尾に summary を 1 行純 JSON として emit "
                         "(downstream observability、既存 stdout は維持)")
    ap.add_argument("--unsafe-show-user-content", action="store_true",
                    help="title/bullets raw text を stdout に raw で出す "
                         "(default: length / sha256 only、debug 専用)")
    ap.add_argument("--unsafe-keep-abs-path", action="store_true",
                    help="json tail の artifact path を絶対 path のまま emit (debug 専用)")
    args = ap.parse_args()
    start_time = time.monotonic()

    # Codex 21:46 PR6 review P1 fix: 全 error path も `--json-log` で
    # v1 status JSON tail emit する。1 invocation 1 emission contract 維持。
    def _emit_error(v0_status, exit_code, *, category=None, **extra):
        duration_ms = int((time.monotonic() - start_time) * 1000)
        payload = build_status(
            script="build_slide_data",
            v0_status=v0_status,
            exit_code=exit_code,
            counts={},
            artifacts=[],
            cost=None,
            duration_ms=duration_ms,
            category_override=category,
            redaction_rules=[],
            **extra,
        )
        _obs_emit_json(args.json_log, payload)
        return exit_code

    transcript_path = PROJ / "transcript_fixed.json"
    config_path = PROJ / "project-config.json"
    if not transcript_path.exists() or not config_path.exists():
        msg = f"missing input: transcript_fixed.json or project-config.json under {PROJ}"
        print(f"ERROR: {msg}", file=sys.stderr)
        sys.exit(_emit_error("build_slide_inputs_missing", 3))

    transcript = load_json(transcript_path)
    config = load_json(config_path)
    fmt = config.get("format", "short")
    tone = config.get("tone", "プロフェッショナル")
    segments = transcript.get("segments", [])
    words = transcript.get("words", [])

    # Phase 3-K (Codex Phase 3-I review P2 #3 拡張): build_slide_data でも
    # transcript の壊れたデータを早期検出 (start>end / 型不正)。
    # Phase 3-L: require_timing=True で start/end 必須化 (slide は timing 駆動、
    # ms_to_playback_frame 呼出箇所 4 件すべてで start/end を使用するため)。
    for i, seg in enumerate(segments):
        try:
            validate_transcript_segment(seg, idx=i, require_timing=True)
        except TranscriptSegmentError as e:
            print(f"ERROR: transcript validation failed: {e}", file=sys.stderr)
            sys.exit(_emit_error("build_slide_transcript_invalid", 3, error=str(e)))

    vad_path = PROJ / "vad_result.json"
    vad = load_json(vad_path) if vad_path.exists() else None
    cut_segments = build_cut_segments_from_vad(vad)
    cut_total_frames = cut_segments[-1]["playbackEnd"] if cut_segments else None

    used_plan = False
    if args.plan:
        plan_path = Path(args.plan)
        if not plan_path.exists():
            msg = f"--plan path not found: {plan_path}"
            if args.strict_plan:
                print(f"ERROR: {msg}", file=sys.stderr)
                sys.exit(_emit_error("build_slide_plan_missing", 2, plan_path=str(plan_path)))
            print(f"WARN: {msg} → deterministic fallback")
        else:
            plan = load_json(plan_path)
            errors = validate_slide_plan(plan, words, cut_total_frames, fmt)
            if errors:
                if args.strict_plan:
                    print("ERROR: plan validation failed:")
                    for e in errors:
                        print(f"  - {e}")
                    sys.exit(_emit_error(
                        "build_slide_plan_invalid", 2,
                        validation_errors=list(errors),
                    ))
                print("WARN: plan validation failed, deterministic fallback:")
                for e in errors:
                    print(f"  - {e}")
            else:
                slides = build_slides_from_plan(plan, words, cut_segments, fmt, tone)
                used_plan = True
                print(f"=== plan accepted ({len(plan.get('slides', []))} slides) ===")

    if not used_plan:
        if args.mode == "topic":
            slides = build_slides_topic_mode(segments, cut_segments, fmt, tone)
        else:
            slides = build_slides_segment_mode(segments, cut_segments, fmt, tone)

    out_path = PROJ / "src" / "Slides" / "slideData.ts"
    backup = PROJ / "src" / "Slides" / "slideData.backup.ts"
    if out_path.exists() and not backup.exists():
        backup.write_text(out_path.read_text(encoding="utf-8"), encoding="utf-8")
    ts = render_slide_data_ts(slides)
    out_path.write_text(ts, encoding="utf-8")

    mode_label = "plan" if used_plan else f"deterministic-{args.mode}"
    print(f"=== slideData.ts 生成 (mode={mode_label}) ===")
    print(f"path: {out_path}")
    print(f"input segments: {len(segments)}")
    print(f"output slides: {len(slides)}")
    # Codex 21:01 step 3 S3-5 fix: default は title raw を出さず length/hash で表示。
    # --unsafe-show-user-content で raw (debug 用)。docs/OBSERVABILITY.md §Redaction Rules 整合。
    for s in slides:
        bullets_count = len(s.get("bullets") or [])
        title = s["title"]
        if args.unsafe_show_user_content:
            title_repr = f"'{title}'"
        else:
            meta = user_content_meta(title)
            title_repr = f"<redacted len={meta['length']} sha256={meta['sha256']}>"
        print(f"  [{s['id']:2}] f{s['startFrame']:5}-{s['endFrame']:5} {title_repr} (bullets={bullets_count})")

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
        script="build_slide_data",
        v0_status="build_slide_ok",
        exit_code=0,
        counts={
            "input_segments": len(segments),
            "output_slides": len(slides),
            "used_plan": used_plan,
        },
        artifacts=artifacts,
        cost=None,
        duration_ms=duration_ms,
        category_override="slide-build",
        redaction_rules=redaction_rules,
        mode=mode_label,
    )
    _obs_emit_json(args.json_log, payload)


if __name__ == "__main__":
    main()
