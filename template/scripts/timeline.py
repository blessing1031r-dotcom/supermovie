#!/usr/bin/env python3
"""SuperMovie Phase 3-J: timeline 共通 utility.

Codex Phase 3-I review (CODEX_REVIEW_PHASE3I_AND_NEXT_20260504T215824) Part B
推奨に従い、複数 script (voicevox_narration.py / build_slide_data.py /
build_telop_data.py) で重複していた以下を 1 module に集約:

- read_video_config_fps(): src/videoConfig.ts の FPS = N を一次 source 化
- build_cut_segments_from_vad(): vad_result.json から cut timeline 構築
- ms_to_playback_frame(): cut-aware ms → playback frame 変換
- validate_vad_schema(): vad_result.json の部分破損 (KeyError / TypeError) を
  早期 fail-fast で検出 (Codex Phase 3-I review P1 #2 反映)
- validate_transcript_segment(): transcript_fixed.json segments[].start/end
  の型 / 順序 validation (Codex Phase 3-I review P2 #3 反映)

これにより slide / telop / narration が全て同一 ms→frame mapping を共有し、
videoConfig.FPS を一次 source として Remotion render と同期する
(出典: https://www.remotion.dev/docs/composition)。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

DEFAULT_FPS = 30
FPS_LINE_RE = re.compile(r"^export const FPS\s*=\s*(\d+)\s*;", re.MULTILINE)
CUT_SEGMENT_KEYS = (
    "originalStartMs",
    "originalEndMs",
    "playbackStart",
    "playbackEnd",
)


def read_video_config_fps(proj: Path, default: int = DEFAULT_FPS) -> int:
    """`<proj>/src/videoConfig.ts` の `export const FPS = N;` を読む.

    読めなければ default。`FPS <= 0` も default に倒す (P2 #4 反映)。
    """
    video_config = proj / "src" / "videoConfig.ts"
    if not video_config.exists():
        return default
    try:
        text = video_config.read_text(encoding="utf-8")
    except OSError:
        return default
    m = FPS_LINE_RE.search(text)
    if not m:
        return default
    try:
        fps = int(m.group(1))
    except ValueError:
        return default
    return fps if fps > 0 else default


class VadSchemaError(ValueError):
    """vad_result.json schema 不整合 (Codex P1 #2 反映、fail-fast 用)."""


def _is_timing_number(value: object) -> bool:
    """timestamp/duration numeric guard.

    bool は int subclass だが、timing 値としては typo を隠すため除外する。
    NaN / Infinity も frame mapping を壊すため除外する。
    """
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _validate_fps(fps: object) -> int:
    """frame conversion fps guard."""
    if isinstance(fps, bool) or not isinstance(fps, int):
        raise TypeError(
            f"fps must be positive int (bool / float / str reject), "
            f"got {type(fps).__name__}"
        )
    if fps <= 0:
        raise ValueError(f"fps must be > 0, got {fps!r}")
    return fps


def _validate_ms(value: object, label: str) -> int | float:
    """millisecond/frame mapping numeric guard."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"{label} must be finite int|float (bool / str reject), "
            f"got {type(value).__name__}"
        )
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite int|float, got {value!r}")
    return value


def _validate_frame(value: object, label: str) -> int:
    """playback frame numeric guard."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{label} must be int frame (bool / float / str reject), "
            f"got {type(value).__name__}"
        )
    return value


def _validate_cut_segments_shape(cut_segments: object) -> list[dict]:
    """cut-aware frame conversion shape guard."""
    if not isinstance(cut_segments, list):
        raise TypeError(
            f"cut_segments must be list[dict], got {type(cut_segments).__name__}"
        )
    for i, cs in enumerate(cut_segments):
        if not isinstance(cs, dict):
            raise TypeError(
                f"cut_segments[{i}] must be dict, got {type(cs).__name__}"
            )
        missing = [key for key in CUT_SEGMENT_KEYS if key not in cs]
        if missing:
            raise ValueError(
                f"cut_segments[{i}] missing required key(s): {', '.join(missing)}"
            )
        for key in ("originalStartMs", "originalEndMs"):
            _validate_ms(cs[key], f"cut_segments[{i}].{key}")
        for key in ("playbackStart", "playbackEnd"):
            _validate_frame(cs[key], f"cut_segments[{i}].{key}")
    return cut_segments


def validate_vad_schema(vad: object) -> dict:
    """vad_result.json の最低限 schema を検査して dict を返す.

    必須: dict に `speech_segments` key があり、list、各要素 dict で
    `start` / `end` が int か float、start <= end。
    破損は VadSchemaError。
    """
    if not isinstance(vad, dict):
        raise VadSchemaError(f"vad must be dict, got {type(vad).__name__}")
    segments = vad.get("speech_segments")
    if not isinstance(segments, list):
        raise VadSchemaError(
            f"vad['speech_segments'] must be list, got {type(segments).__name__}"
        )
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            raise VadSchemaError(f"segment[{i}] must be dict, got {type(seg).__name__}")
        for key in ("start", "end"):
            v = seg.get(key)
            if not _is_timing_number(v):
                raise VadSchemaError(
                    f"segment[{i}].{key} must be finite int|float, got {type(v).__name__}"
                )
        if seg["start"] > seg["end"]:
            raise VadSchemaError(
                f"segment[{i}] start={seg['start']} > end={seg['end']}"
            )
    return vad


def build_cut_segments_from_vad(vad: dict, fps: int) -> list[dict]:
    """vad の speech_segments から cut 後 timeline mapping を構築.

    呼び出し前に validate_vad_schema() で検査済みであることを前提とする。
    fps は呼び出し側の videoConfig.FPS を渡す (Phase 3-J: hardcode 撤廃)。
    """
    fps = _validate_fps(fps)
    out: list[dict] = []
    cursor_ms = 0
    for i, seg in enumerate(vad["speech_segments"]):
        s_ms = seg["start"]
        e_ms = seg["end"]
        dur_ms = e_ms - s_ms
        out.append(
            {
                "id": i + 1,
                "originalStartMs": s_ms,
                "originalEndMs": e_ms,
                "playbackStart": round(cursor_ms / 1000 * fps),
                "playbackEnd": round((cursor_ms + dur_ms) / 1000 * fps),
            }
        )
        cursor_ms += dur_ms
    return out


def load_cut_segments(proj: Path, fps: int, fail_fast: bool = False) -> list[dict]:
    """`<proj>/vad_result.json` から cut_segments を構築.

    fail_fast=False (default): 不在 / I/O / schema エラーは [] にして黙過。
    fail_fast=True: 部分破損も VadSchemaError / OSError で raise。

    Codex Phase 3-I review P1 #2 反映: 黙過は legacy 経路へ流れて stale
    narration を出す危険があるので、narration script では fail_fast=True 推奨。
    """
    vad_path = proj / "vad_result.json"
    if not vad_path.exists():
        return []
    try:
        with vad_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        if fail_fast:
            raise
        return []
    try:
        validated = validate_vad_schema(data)
    except VadSchemaError:
        if fail_fast:
            raise
        return []
    return build_cut_segments_from_vad(validated, fps)


def ms_to_playback_frame(
    ms: int | float, fps: int, cut_segments: list[dict]
) -> int | None:
    """元動画の ms を playback frame に変換 (cut-aware).

    cut_segments が空なら直接変換。ms が cut 範囲外 (cut で除外された箇所) は
    None を返す (呼出側が累積 fallback or skip 判断)。
    """
    ms = _validate_ms(ms, "ms")
    fps = _validate_fps(fps)
    cut_segments = _validate_cut_segments_shape(cut_segments)
    if not cut_segments:
        return round(ms / 1000 * fps)
    for cs in cut_segments:
        if cs["originalStartMs"] <= ms <= cs["originalEndMs"]:
            offset_ms = ms - cs["originalStartMs"]
            return cs["playbackStart"] + round(offset_ms / 1000 * fps)
    return None


class TranscriptSegmentError(ValueError):
    """transcript segment の型 / 順序 異常 (Codex P2 #3 反映)."""


def validate_transcript_segment(
    seg: object, idx: int = -1, require_timing: bool = False
) -> dict:
    """transcript_fixed.json の 1 segment の最低限検査.

    必須: dict に `text` (str|None)、`start` / `end` が int|float|None。
    両方 numeric なら start <= end。違反は TranscriptSegmentError。

    require_timing=True: start / end の両方が int|float 必須 (None / 欠落 NG)。
    build_slide_data / build_telop_data など timing 駆動の script で使う
    (Codex Phase 3-J review Part B 設計概要 反映)。
    """
    label = f"segment[{idx}]" if idx >= 0 else "segment"
    if not isinstance(seg, dict):
        raise TranscriptSegmentError(f"{label} must be dict, got {type(seg).__name__}")
    text = seg.get("text")
    if text is not None and not isinstance(text, str):
        raise TranscriptSegmentError(
            f"{label}.text must be str|None, got {type(text).__name__}"
        )
    s = seg.get("start")
    e = seg.get("end")
    for k, v in (("start", s), ("end", e)):
        if v is not None and not _is_timing_number(v):
            raise TranscriptSegmentError(
                f"{label}.{k} must be finite int|float|None, got {type(v).__name__}"
            )
    if require_timing:
        if not _is_timing_number(s):
            raise TranscriptSegmentError(
                f"{label}.start required (finite int|float), got {type(s).__name__}"
            )
        if not _is_timing_number(e):
            raise TranscriptSegmentError(
                f"{label}.end required (finite int|float), got {type(e).__name__}"
            )
    if _is_timing_number(s) and _is_timing_number(e) and s > e:
        raise TranscriptSegmentError(f"{label} start={s} > end={e}")
    return seg


def validate_transcript_segments(
    segments: object, require_timing: bool = False
) -> list[dict]:
    """segments[] 一括検証 (Codex Phase 3-J review Part B 推奨).

    segments が list でない / 各要素が validate に通らない場合 raise。
    require_timing=True で start/end 必須の strict mode (slide / telop 用)。
    """
    if not isinstance(segments, list):
        raise TranscriptSegmentError(
            f"segments must be list, got {type(segments).__name__}"
        )
    return [
        validate_transcript_segment(seg, idx=i, require_timing=require_timing)
        for i, seg in enumerate(segments)
    ]
