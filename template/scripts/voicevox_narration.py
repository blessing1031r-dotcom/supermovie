#!/usr/bin/env python3
"""SuperMovie Phase 3-D/H: VOICEVOX で transcript_fixed.json から narration を生成.

Phase 3-D 設計起点 (Codex CODEX_PHASE3D_VOICEVOX, 2026-05-04):
- engine 不在で skip (--require-engine 指定時のみ exit non-zero)
- 入力: transcript_fixed.json の segments[] / project-config.json の tone
- 入力 override: --script narration_script.txt / --script-json narration_script.json
- 出力: public/narration.wav (segments 個別 wav を結合、legacy fallback)
- API: POST /audio_query → POST /synthesis (公式 https://github.com/VOICEVOX/voicevox_engine)

Phase 3-H 拡張 (Codex CODEX_PHASE3H_NEXT, 2026-05-04):
- chunk_NNN.wav を public/narration/ に保持 (削除しない、render 時に必要)
- 各 chunk の wave header から実 duration を測定
- src/Narration/narrationData.ts を all-or-nothing で生成
  (NarrationSegment[] = [{id, startFrame, durationInFrames, file, text}])
- public/narration/chunk_meta.json も debug 用に出力
- stale chunk + 旧 narration.wav (前回実行の遺物) を synthesis 前に必ず cleanup
- partial failure 時は narrationData.ts を空に reset + 部分 chunk 削除、二重音声防止

Phase 3-H review fix (Codex CODEX_REVIEW_PHASE3H_20260504T213301, P1×2 + P2×4):
- 全 narration 出力 (narration.wav / narrationData.ts / chunk_meta.json) を
  tempfile + os.replace で atomic 書き換え (SIGINT/disk full 安全、P2 #3)
- FPS は src/videoConfig.ts の `export const FPS = N;` を一次 source に
  (P2 #4 + P2 #5、project-config.json の malformed AttributeError と Remotion 実値ズレを解消)
- wave.Error / EOFError を catch して exit 6 で all-or-nothing rollback (P2 #6)
- partial failure 時に旧 narration.wav も削除 (P1 #2、stale legacy 再生事故防止)

Usage:
    python3 scripts/voicevox_narration.py
    python3 scripts/voicevox_narration.py --speaker 3
    python3 scripts/voicevox_narration.py --script narration.txt
    python3 scripts/voicevox_narration.py --list-speakers
    python3 scripts/voicevox_narration.py --require-engine
    python3 scripts/voicevox_narration.py --fps 60      # FPS 明示 (default は src/videoConfig.ts FPS)

Engine 起動 (Roku ローカル):
    https://voicevox.hiroshiba.jp/ から VOICEVOX をインストール、起動すると
    127.0.0.1:50021 で REST API が立つ。Docker 版もあり (公式 README 参照)。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
# Phase 3 obs migration core: helper を経由して v1 schema + redaction を適用。
from _observability import (  # noqa: E402
    build_status,
    emit_json as _obs_emit_json,
    resolve_run_context,
    safe_artifact_path,
    user_content_meta,
)
from timeline import (  # noqa: E402
    DEFAULT_FPS,
    TranscriptSegmentError,
    VadSchemaError,
    build_cut_segments_from_vad,
    load_cut_segments,
    ms_to_playback_frame,
    read_video_config_fps,
    validate_transcript_segment,
)

ENGINE_BASE = "http://127.0.0.1:50021"
DEFAULT_SPEAKER = 3  # ずんだもん (ノーマル)、Roku が --speaker で上書き可
TIMEOUT = 30

NARRATION_DIR = PROJ / "public" / "narration"
NARRATION_LEGACY_WAV = PROJ / "public" / "narration.wav"
NARRATION_DATA_TS = PROJ / "src" / "Narration" / "narrationData.ts"
CHUNK_META_JSON = NARRATION_DIR / "chunk_meta.json"
# Phase 3-V post-freeze P5 (Codex CODEX_P5_VOICEVOX_SENTINEL_DESIGN_20260505T095934.md):
# publish 完了 signal sentinel。chunks → narrationData.ts → narration.wav 書き終わりの
# 最後に置く。Studio 側 useNarrationMode hook が watchStaticFile で監視して invalidate +
# setMode を発火する。root 配置 (subdirectory ではない) は Linux watchStaticFile 制約への
# 保守性で選択 (Remotion getStaticFiles docs: https://www.remotion.dev/docs/getstaticfiles)。
NARRATION_READY_JSON = PROJ / "public" / "narration.ready.json"
SENTINEL_SCHEMA_VERSION = 1
EMPTY_NARRATION_DATA = (
    "import type { NarrationSegment } from './types';\n"
    "\n"
    "export const narrationData: NarrationSegment[] = [];\n"
)


def _tmp_path(path: Path) -> Path:
    """`.{name}.{pid}.tmp` 形式の temp path を返す.

    PID 付与で同一 project の同時実行による tmp 衝突を回避
    (Codex Phase 3-H fix re-review 新規 P2 反映)。
    """
    return path.with_name(f".{path.name}.{os.getpid()}.tmp")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """tempfile + os.replace で atomic 書換 (Codex Phase 3-H review P2 #3 反映).

    write 中例外時は finally で tmp を unlink (Codex re-review 新規 P3 反映)。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(path)
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def atomic_write_text(path: Path, content: str) -> None:
    """tempfile + os.replace で atomic 書換 (Codex Phase 3-H review P2 #3 反映).

    write 中例外時は finally で tmp を unlink (Codex re-review 新規 P3 反映)。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(path)
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def http_request(method: str, path: str, params: dict | None = None,
                 body: dict | None = None) -> bytes:
    url = ENGINE_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def check_engine() -> tuple[bool, str | None]:
    try:
        body = http_request("GET", "/version")
        return True, body.decode("utf-8").strip().strip('"')
    except (urllib.error.URLError, OSError, urllib.error.HTTPError) as e:
        return False, str(e)


def list_speakers() -> list[dict]:
    body = http_request("GET", "/speakers")
    return json.loads(body.decode("utf-8"))


def synthesize(text: str, speaker: int) -> bytes:
    """audio_query → synthesis の二段階で WAV bytes を返す."""
    aq_body = http_request("POST", "/audio_query", params={"text": text, "speaker": speaker}, body={})
    aq = json.loads(aq_body.decode("utf-8"))
    wav_bytes = http_request("POST", "/synthesis", params={"speaker": speaker}, body=aq)
    return wav_bytes


def concat_wavs_atomic(wavs: list[Path], out_path: Path) -> None:
    """同一 sample rate / channel の wav 列を時系列で結合し atomic に書く.

    wave.Error は呼び出し側で catch して all-or-nothing rollback する
    (Codex Phase 3-H review P2 #6 反映). write 中例外時は finally で tmp を
    unlink して残骸を残さない (Codex re-review 新規 P3 反映)。
    """
    if not wavs:
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(out_path)
    try:
        with wave.open(str(wavs[0]), "rb") as w0:
            params = w0.getparams()
            frames = [w0.readframes(w0.getnframes())]
        for p in wavs[1:]:
            with wave.open(str(p), "rb") as w:
                if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (params.framerate, params.nchannels, params.sampwidth):
                    print(f"WARN: {p.name} format mismatch、skip", file=sys.stderr)
                    continue
                frames.append(w.readframes(w.getnframes()))
        with wave.open(str(tmp), "wb") as out:
            out.setparams(params)
            for f in frames:
                out.writeframes(f)
        os.replace(tmp, out_path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def measure_duration_seconds(wav_path: Path) -> float:
    """WAV header の (nframes / framerate) で正確な duration を返す.

    wave.Error / EOFError は呼び出し側で catch (Codex P2 #6 反映)。
    """
    with wave.open(str(wav_path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


class StaleCleanupError(RuntimeError):
    """cleanup_stale_all() で legacy narration.wav の削除に失敗した時に raise."""


def cleanup_stale_all() -> None:
    """旧 chunk_*.wav / chunk_meta.json / narration.wav / narrationData.ts を全 reset.

    Codex Phase 3-H review P1 #2 反映: 旧 narration.wav が残ると partial failure 後に
    legacy mode で stale narration が再生される事故を起こすため、必ず全削除する。

    Codex re-review P1 #2 partial 反映: legacy wav の削除失敗は WARN ではなく
    StaleCleanupError raise (削除失敗を黙過すると残置 → mode helper が legacy
    経路に入り、stale narration を再生してしまう)。chunks / chunk_meta は
    どうせ atomic で上書きされるため WARN 継続で OK。
    """
    if NARRATION_DIR.exists():
        for p in NARRATION_DIR.glob("chunk_*.wav"):
            try:
                p.unlink()
            except OSError as e:
                print(f"WARN: stale chunk {p.name} 削除失敗: {e}", file=sys.stderr)
        if CHUNK_META_JSON.exists():
            try:
                CHUNK_META_JSON.unlink()
            except OSError as e:
                print(f"WARN: stale chunk_meta.json 削除失敗: {e}", file=sys.stderr)
    if NARRATION_LEGACY_WAV.exists():
        try:
            NARRATION_LEGACY_WAV.unlink()
        except OSError as e:
            raise StaleCleanupError(
                f"stale narration.wav 削除失敗、stale legacy 再生事故防止のため処理中断: {e}"
            ) from e
    # Phase 3-V P5 sentinel: stale ready file は必ず削除 (Studio で旧 signal key を
    # 読んで「ready」と誤判定する事故防止)。削除失敗は WARN 継続 (sentinel 単独で
    # legacy mode に flip する経路はないため)。
    if NARRATION_READY_JSON.exists():
        try:
            NARRATION_READY_JSON.unlink()
        except OSError as e:
            print(f"WARN: stale narration.ready.json 削除失敗: {e}", file=sys.stderr)
    reset_narration_data_ts()


def reset_narration_data_ts() -> None:
    """narrationData.ts を空 array に戻す (Phase 3-H all-or-nothing)、atomic 書換。"""
    if NARRATION_DATA_TS.parent.exists():
        atomic_write_text(NARRATION_DATA_TS, EMPTY_NARRATION_DATA)


def write_narration_ready(chunk_count: int, total_frames: int) -> None:
    """Phase 3-V post-freeze P5: publish 完了 signal sentinel を atomic 書換.

    Codex CODEX_P5_VOICEVOX_SENTINEL_DESIGN_20260505T095934.md §1 設計準拠:
    最小 JSON {schemaVersion, status, chunkCount, totalFrames, generatedAtMs} で、
    chunks → narrationData.ts → narration.wav 全成功後の最後に書く。Studio 側は
    中身を必須 read しない (lastModified / sizeInBytes の signal key で dedup 駆動)。
    """
    payload = {
        "schemaVersion": SENTINEL_SCHEMA_VERSION,
        "status": "ready",
        "chunkCount": chunk_count,
        "totalFrames": total_frames,
        "generatedAtMs": int(time.time() * 1000),
    }
    atomic_write_text(NARRATION_READY_JSON, json.dumps(payload, ensure_ascii=False) + "\n")


def project_load_cut_segments(fps: int) -> list[dict]:
    """voicevox 側の load_cut_segments wrapper (PROJ + fail_fast 戦略を固定).

    Codex Phase 3-I review P1 #2 反映: narration script では legacy 経路へ
    流れて stale narration を出す危険があるので fail_fast=True (raise)。
    呼び出し側で VadSchemaError / OSError / json.JSONDecodeError を catch する。
    """
    return load_cut_segments(PROJ, fps, fail_fast=True)


def write_narration_data(
    chunks: list[tuple[Path, str, int | None, int | None]],
    fps: int,
    cut_segments: list[dict],
) -> tuple[list[dict], Path, Path]:
    """各 chunk の duration を測って narrationData.ts と chunk_meta.json を書く.

    両方とも atomic 書換 (Codex P2 #3 反映)。wave.Error は呼び出し側で catch。
    Phase 3-I: chunks tuple は (path, text, sourceStartMs, sourceEndMs)。
    sourceStartMs があれば transcript timing alignment、なければ累積 fallback。
    cut_segments があれば cut-aware mapping、cut で除外された ms は累積 fallback。
    """
    segments: list[dict] = []
    cumulative_frame = 0
    overlap_warns: list[str] = []
    for i, (path, text, source_start_ms, source_end_ms) in enumerate(chunks):
        duration_sec = measure_duration_seconds(path)
        duration_frames = max(1, round(duration_sec * fps))
        rel = path.relative_to(PROJ / "public").as_posix()

        # startFrame: transcript timing > 累積 fallback
        start_frame = cumulative_frame
        timing_source = "cumulative"
        if source_start_ms is not None:
            mapped = ms_to_playback_frame(source_start_ms, fps, cut_segments)
            if mapped is None:
                print(
                    f"WARN: chunk {i} sourceStartMs={source_start_ms}ms は cut 範囲外、"
                    f"累積 frame={cumulative_frame} で fallback",
                    file=sys.stderr,
                )
            else:
                start_frame = mapped
                timing_source = "transcript_aligned"

        # overlap 検出 (前 chunk の終端 > 現 startFrame)
        if segments:
            prev = segments[-1]
            prev_end = prev["startFrame"] + prev["durationInFrames"]
            if prev_end > start_frame:
                overlap_warns.append(
                    f"chunk {i - 1}->{i}: prev end frame={prev_end} > start={start_frame} "
                    f"({prev_end - start_frame} frames overlap)"
                )

        seg_dict: dict = {
            "id": i,
            "startFrame": start_frame,
            "durationInFrames": duration_frames,
            "file": rel,
            "text": text[:100],  # debug 用、長文は切り詰め
            "duration_sec": round(duration_sec, 3),
            "timing_source": timing_source,
        }
        if source_start_ms is not None:
            seg_dict["sourceStartMs"] = source_start_ms
        if source_end_ms is not None:
            seg_dict["sourceEndMs"] = source_end_ms
        segments.append(seg_dict)
        cumulative_frame = start_frame + duration_frames

    if overlap_warns:
        # Codex Phase 3-I review P2 #4 反映: 文言を「transcript bug ではなく
        # TTS が transcript より長い signal」として明確化、対処は transcript
        # 再分割 / chunk text 短縮 / TTS 早話速度 / 隣接 chunk の sourceStart 後ろ送り。
        print(
            f"WARN: {len(overlap_warns)} narration <Sequence> overlap(s) detected. "
            f"これは transcript の bug ではなく、TTS 出力 wav が元 transcript の "
            f"interval より長いか、隣接 chunk が transcript timing 上で接近しすぎ。"
            f"render では二重再生になるので、対処: "
            f"(1) transcript 再分割、(2) chunk text 短縮、(3) speaker 早話速度、"
            f"(4) sourceStartMs を後ろ送り。",
            file=sys.stderr,
        )
        for w in overlap_warns:
            print(f"  - {w}", file=sys.stderr)

    total_frames = max((s["startFrame"] + s["durationInFrames"] for s in segments), default=0)
    atomic_write_text(
        CHUNK_META_JSON,
        json.dumps(
            {
                "fps": fps,
                "total_frames": total_frames,
                "cut_aware": bool(cut_segments),
                "overlaps": overlap_warns,
                "segments": segments,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )

    ts_lines = [
        "/**",
        " * Phase 3-H: voicevox_narration.py が all-or-nothing + atomic で書換える narration timeline。",
        " * NarrationAudio.tsx が <Sequence from={startFrame} durationInFrames={...}> でループ再生する。",
        " * 手動編集禁止 (script 再実行で上書きされる)。",
        " */",
        "import type { NarrationSegment } from './types';",
        "",
        "export const narrationData: NarrationSegment[] = [",
    ]
    for s in segments:
        parts = [
            f"id: {s['id']}",
            f"startFrame: {s['startFrame']}",
            f"durationInFrames: {s['durationInFrames']}",
            f"file: {json.dumps(s['file'])}",
            f"text: {json.dumps(s['text'], ensure_ascii=False)}",
        ]
        if "sourceStartMs" in s:
            parts.append(f"sourceStartMs: {s['sourceStartMs']}")
        if "sourceEndMs" in s:
            parts.append(f"sourceEndMs: {s['sourceEndMs']}")
        ts_lines.append("  { " + ", ".join(parts) + " },")
    ts_lines.append("];")
    ts_lines.append("")
    atomic_write_text(NARRATION_DATA_TS, "\n".join(ts_lines))

    return segments, NARRATION_DATA_TS, CHUNK_META_JSON


def _resolve_path(path_str: str) -> Path:
    """非 absolute path は PROJ 相対に解決 (skill 実行時の cwd 揺れを吸収)."""
    p = Path(path_str)
    return p if p.is_absolute() else PROJ / p


def collect_chunks(args, transcript: dict) -> list[dict]:
    """各 chunk を {text, sourceStartMs, sourceEndMs} で返す.

    Phase 3-I: transcript_fixed.json の segments[].start/end を保持。
    Phase 3-J: validate_transcript_segment で start > end / 型不正を検出して
    TranscriptSegmentError raise (Codex review P2 #3 反映、mlx-whisper の
    稀なバグや手編集ミスによる壊れた transcript を早期検出)。
    --script は 1 行 1 chunk で timing なし、--script-json は startMs/endMs
    optional で受け付ける。
    """
    if args.script:
        text = _resolve_path(args.script).read_text(encoding="utf-8")
        return [
            {"text": line.strip(), "sourceStartMs": None, "sourceEndMs": None}
            for line in text.splitlines() if line.strip()
        ]
    # Codex Phase 3-J review P2 #1 反映: validate を `.get(...).strip()` より
    # 先に通す。segment が非 dict / text 非 str だと AttributeError で落ちて
    # TranscriptSegmentError として捕まらない経路があるため。
    if args.script_json:
        plan = load_json(_resolve_path(args.script_json))
        if not isinstance(plan, dict):
            raise TranscriptSegmentError(f"script-json must be dict, got {type(plan).__name__}")
        plan_segments = plan.get("segments", [])
        if not isinstance(plan_segments, list):
            raise TranscriptSegmentError(
                f"script-json segments must be list, got {type(plan_segments).__name__}"
            )
        out: list[dict] = []
        for i, s in enumerate(plan_segments):
            # validate を最初に通す (segment が非 dict なら raise)
            validate_transcript_segment(
                # script-json schema は startMs/endMs だが、validate は start/end を見るので map
                (
                    {
                        "text": s.get("text"),
                        "start": s.get("startMs"),
                        "end": s.get("endMs"),
                    }
                    if isinstance(s, dict)
                    else s
                ),
                idx=i,
            )
            text = (s.get("text") or "").strip()
            if not text:
                continue
            out.append(
                {
                    "text": text,
                    "sourceStartMs": s.get("startMs"),
                    "sourceEndMs": s.get("endMs"),
                }
            )
        return out
    transcript_segments = transcript.get("segments", []) if isinstance(transcript, dict) else []
    if not isinstance(transcript_segments, list):
        raise TranscriptSegmentError(
            f"transcript segments must be list, got {type(transcript_segments).__name__}"
        )
    out_t: list[dict] = []
    for i, s in enumerate(transcript_segments):
        # validate を最初に通す
        validate_transcript_segment(s, idx=i)
        text = (s.get("text") or "").strip()
        if not text:
            continue
        out_t.append(
            {
                "text": text,
                "sourceStartMs": s.get("start"),
                "sourceEndMs": s.get("end"),
            }
        )
    return out_t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--speaker", type=int, default=DEFAULT_SPEAKER,
                    help=f"VOICEVOX speaker id (default {DEFAULT_SPEAKER}=ずんだもんノーマル)")
    ap.add_argument("--script", help="custom plain-text narration (1 line = 1 chunk)")
    ap.add_argument("--script-json", help="custom JSON {segments:[{text}]} narration")
    ap.add_argument("--list-speakers", action="store_true")
    ap.add_argument("--require-engine", action="store_true",
                    help="engine 不在で exit 4 (default は skip exit 0)")
    ap.add_argument("--output", default=str(PROJ / "public" / "narration.wav"))
    ap.add_argument(
        "--fps",
        type=int,
        default=None,
        help=f"narrationData.ts に書き込む frame 換算 fps "
             f"(default: src/videoConfig.ts FPS、読めなければ {DEFAULT_FPS})",
    )
    ap.add_argument("--allow-partial", action="store_true",
                    help="一部 chunk synthesis 失敗でも narration.wav を出力 + narrationData.ts 部分書き出し "
                         "(default は全 chunk 成功必須)")
    # Phase 3-V post-freeze 第2弾 P3 (Codex CODEX_NEXT_PRIORITY:15-18):
    # 既存 stdout (human-readable print) を維持しつつ、--json-log で末尾に
    # 1 行 純 JSON summary を emit (downstream tool が tail 1 行 parse 可)。
    ap.add_argument("--json-log", action="store_true",
                    help="末尾に summary を 1 行純 JSON として emit "
                         "(downstream observability、既存 stdout は維持)")
    # Phase 3 obs migration core: redaction debug opt-in flags
    ap.add_argument("--unsafe-show-user-content", action="store_true",
                    help="chunk text / transcript を human stdout に raw で出す "
                         "(default: length / sha256、debug 専用)")
    ap.add_argument("--unsafe-keep-abs-path", action="store_true",
                    help="json tail / artifact path を絶対 path のまま emit "
                         "(default: project-root 相対 / <HOME> placeholder、debug 専用)")
    args = ap.parse_args()

    # PR-E (distributed tracing): main 冒頭で 1 回 resolve、全 emission に同 run_ctx を渡す。
    run_ctx = resolve_run_context()

    # Phase 3 obs migration core: helper 経由で v1 schema 経由 emit。
    # Codex 20:48 PR3 review P1 #2 fix: redact key 名を実 payload と一致させる。
    # 旧 (output / narration_data_path / chunk_meta_path) は誤、実 emit は
    # narration_wav / narration_data_ts / chunk_meta_json / narration_ready_json / out_path。
    PATH_KEYS = (
        "narration_wav",
        "narration_data_ts",
        "chunk_meta_json",
        "narration_ready_json",
        "out_path",
        "output",  # 防御的: 将来追加される可能性
    )

    def emit_json(status: str, exit_code: int, **extra) -> int:
        # Apply abs_path redaction to known path-bearing fields
        redaction_rules = []
        for key in PATH_KEYS:
            if key in extra and extra[key] is not None:
                extra[key] = safe_artifact_path(
                    extra[key],
                    project_root=PROJ,
                    unsafe_keep_abs_path=args.unsafe_keep_abs_path,
                )
                if not args.unsafe_keep_abs_path and "abs_path" not in redaction_rules:
                    redaction_rules.append("abs_path")
        payload = build_status(
            script="voicevox_narration",
            v0_status=status,
            exit_code=exit_code,
            redaction_rules=redaction_rules,
            run_id=run_ctx["run_id"],
            parent_run_id=run_ctx["parent_run_id"],
            step_id=run_ctx["step_id"],
            **extra,
        )
        return _obs_emit_json(args.json_log, payload)

    ok, info = check_engine()
    if not ok:
        msg = f"VOICEVOX engine unavailable at {ENGINE_BASE}: {info}"
        if args.require_engine:
            print(f"ERROR: {msg}", file=sys.stderr)
            return emit_json("engine_unavailable_strict", 4, info=info)
        print(f"INFO: {msg} -> narration generation skipped")
        print(
            "      Remotion 側の <NarrationAudio /> は public/narration.wav 不在 + "
            "narrationData.ts 空を asset gate で検出し null を返すため render は失敗しない "
            "(Phase 3-F asset gate + Phase 3-H per-segment Sequence)"
        )
        return emit_json("engine_skipped", 0, info=info)
    print(f"VOICEVOX engine OK (version: {info})")

    if args.list_speakers:
        speakers = list_speakers()
        for s in speakers:
            for style in s.get("styles", []):
                print(f"  [{style.get('id'):3}] {s.get('name')} / {style.get('name')}")
        return emit_json("list_speakers", 0)

    transcript_path = PROJ / "transcript_fixed.json"
    if not transcript_path.exists() and not (args.script or args.script_json):
        print(f"ERROR: transcript_fixed.json missing under {PROJ}", file=sys.stderr)
        return emit_json("transcript_missing", 3)
    transcript = load_json(transcript_path) if transcript_path.exists() else {}
    try:
        chunks = collect_chunks(args, transcript)
    except TranscriptSegmentError as e:
        # Codex Phase 3-I review P2 #3: 壊れた transcript で TTS を回さない
        print(f"ERROR: transcript validation failed: {e}", file=sys.stderr)
        return emit_json("transcript_invalid", 3, error=str(e))
    if not chunks:
        print("ERROR: no narration chunks", file=sys.stderr)
        return emit_json("no_chunks", 3)

    fps = args.fps if args.fps is not None else read_video_config_fps(PROJ)
    if fps <= 0:
        print(f"ERROR: invalid fps={fps} (--fps is positive integer required)", file=sys.stderr)
        return emit_json("invalid_fps", 4, fps=fps)
    print(f"target fps: {fps}")

    # Phase 3-H: stale narration を全 reset BEFORE synthesis
    # (chunk wav / chunk_meta.json / narration.wav / narrationData.ts 全部、Codex P1 #2)
    try:
        cleanup_stale_all()
    except StaleCleanupError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return emit_json("stale_cleanup_fail", 7, error=str(e))

    # Codex Phase 3-J review P1 反映: VAD validation を synthesis 前 (cleanup 直後)
    # に移動。VAD 破損時に synthesize / concat / narrationData 全 skip し、
    # stale narration.wav が legacy 経路に流れる余地を消す。
    # Codex Phase 3-J fix re-review P1 partial 反映: NARRATION_DIR.mkdir() は
    # VAD validation 成功後 (mkdir 順序契約)。
    # Codex Phase 3-L re-review P2 #1 反映: cleanup_stale_all() は narrationData.ts
    # を空 array に atomic 上書きする (all-or-nothing 契約の一部)。「何も書かない」
    # ではなく「成果物は cleanup 段階の clean state で固定、VAD 破損なら
    # narration.wav / chunk wav / 非空 narrationData.ts は一切作らず exit 8」が正しい契約。
    try:
        cut_segments = project_load_cut_segments(fps)
    except (VadSchemaError, OSError, json.JSONDecodeError) as e:
        print(
            f"ERROR: vad_result.json schema invalid or unreadable: {e}",
            file=sys.stderr,
        )
        # cleanup_stale_all() 直後で narrationData.ts は空 array、その他成果物
        # (chunk wav / narration.wav / chunk_meta.json) は未生成、mkdir も未実行。
        return emit_json("vad_invalid", 8, error=str(e))
    NARRATION_DIR.mkdir(parents=True, exist_ok=True)
    if cut_segments:
        print(f"cut-aware mapping: {len(cut_segments)} cut segments loaded from vad_result.json")

    chunk_paths: list[Path] = []
    chunk_meta: list[tuple[str, int | None, int | None]] = []  # (text, sourceStartMs, sourceEndMs)
    for i, ch in enumerate(chunks):
        text = ch["text"]
        try:
            wav_bytes = synthesize(text, args.speaker)
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            print(f"WARN: synth failed for chunk {i}: {e}", file=sys.stderr)
            continue
        p = NARRATION_DIR / f"chunk_{i:03d}.wav"
        atomic_write_bytes(p, wav_bytes)
        chunk_paths.append(p)
        chunk_meta.append((text, ch.get("sourceStartMs"), ch.get("sourceEndMs")))
        # Codex 20:48 PR3 review P1 #1 fix: chunk text raw partial を default redact、
        # --unsafe-show-user-content 指定時のみ raw 出力。
        if args.unsafe_show_user_content:
            print(f"  chunk[{i:3}] {len(wav_bytes)} bytes  text='{text[:30]}…'")
        else:
            meta = user_content_meta(text)
            print(
                f"  chunk[{i:3}] {len(wav_bytes)} bytes  "
                f"text=<redacted len={meta['length']} sha256={meta['sha256']}>"
            )

    if not chunk_paths:
        print("ERROR: no chunks succeeded", file=sys.stderr)
        return emit_json("no_chunks_succeeded", 5, total=len(chunks))
    if not args.allow_partial and len(chunk_paths) < len(chunks):
        print(
            f"ERROR: only {len(chunk_paths)}/{len(chunks)} chunks succeeded "
            f"(--allow-partial で部分成功でも narration.wav 出力可)",
            file=sys.stderr,
        )
        # Phase 3-H all-or-nothing (Codex P1 #2): 部分 chunk + narrationData 全削除
        # cleanup_stale_all() で narration.wav も含めて clean されているので追加削除のみ
        for p in chunk_paths:
            try:
                p.unlink()
            except OSError:
                pass
        return emit_json(
            "partial_chunks_disallowed", 6,
            succeeded=len(chunk_paths), total=len(chunks),
        )

    # Codex Phase 3-N review P2 #1 反映: write 順序を chunks → narrationData.ts →
    # narration.wav に変更。
    # 理由: Studio hot-reload 経路では narration.wav 出現 → useNarrationMode が
    # legacy 経路に flip → narrationData.ts 出現で HMR reload → chunks 経路に
    # flip という race が発生、その間 legacy fallback が一瞬鳴る。narrationData.ts
    # を先に書くことで、HMR が先に反映されて chunks 経路が確定してから legacy
    # narration.wav が現れる順序になる。

    # Codex Phase 3-I review P3 #6 反映: chunk_paths と chunk_meta の長さ ガード
    # python -O で assert は消えるため、runtime check + raise 化
    # (Codex Phase 3-J review checklist 指摘)。
    if len(chunk_paths) != len(chunk_meta):
        raise RuntimeError(
            f"chunk_paths ({len(chunk_paths)}) と chunk_meta ({len(chunk_meta)}) の長さズレ"
        )
    pairs = [
        (path, text, source_start, source_end)
        for path, (text, source_start, source_end) in zip(chunk_paths, chunk_meta)
    ]
    try:
        segments, ts_path, meta_path = write_narration_data(pairs, fps, cut_segments)
    except (wave.Error, EOFError) as e:
        print(f"ERROR: WAV duration probe failed (wave.Error / EOFError): {e}", file=sys.stderr)
        for p in chunk_paths:
            try:
                p.unlink()
            except OSError:
                pass
        return emit_json(
            "write_narration_data_wave_error", 6,
            error=f"{type(e).__name__}: {e}",
        )
    total_frames = max(
        (s["startFrame"] + s["durationInFrames"] for s in segments), default=0
    )
    print(f"wrote: {ts_path} ({len(segments)} segments, total_frames={total_frames})")
    print(f"wrote: {meta_path}")

    # Phase 3-N race fix: narrationData.ts を書いた後で narration.wav を書く
    # (これで Studio hot-reload で chunks 経路が先に成立、legacy fallback が
    # 一瞬鳴る race を解消)。
    # Codex Phase 3-O fix re-review P1 反映: rollback catch を Exception 全部に
    # 拡張 (旧 wave.Error / EOFError 限定だと os.replace / OSError / 権限 /
    # disk full で narrationData.ts populated + chunks 残置、all-or-nothing 破れ)。
    # KeyboardInterrupt は BaseException 系で捕まえない (ユーザ Ctrl+C 尊重)。
    out_path = _resolve_path(args.output)
    try:
        concat_wavs_atomic(chunk_paths, out_path)
    except Exception as e:
        print(f"ERROR: narration.wav concat failed: {type(e).__name__}: {e}", file=sys.stderr)
        # narrationData.ts と chunks を rollback (all-or-nothing 維持)
        for p in chunk_paths:
            try:
                p.unlink()
            except OSError:
                pass
        # narrationData.ts を空 array に戻す (cleanup_stale_all 同等の reset)
        try:
            reset_narration_data_ts()
        except OSError:
            pass
        if CHUNK_META_JSON.exists():
            try:
                CHUNK_META_JSON.unlink()
            except OSError:
                pass
        return emit_json(
            "concat_fail", 6,
            error=f"{type(e).__name__}: {e}",
            out_path=str(out_path),
        )
    print(f"\nwrote: {out_path} ({out_path.stat().st_size} bytes)")
    print(f"chunks succeeded: {len(chunk_paths)} / {len(chunks)} synthesized")

    # Phase 3-V post-freeze P5: publish 完了 signal sentinel を最後に書く
    # (Codex CODEX_P5_VOICEVOX_SENTINEL_DESIGN_20260505T095934.md §1 / §3)。
    # 失敗時は chunks / narration.wav / chunk_meta.json / narrationData.ts /
    # sentinel 全 rollback で all-or-nothing contract を維持 (concat fail と同型)。
    try:
        write_narration_ready(len(chunk_paths), total_frames)
    except Exception as e:
        print(
            f"ERROR: narration.ready.json write failed: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        for p in chunk_paths:
            try:
                p.unlink()
            except OSError:
                pass
        # Codex P5 review P1 反映: rollback 対象は --output 由来 out_path (custom
        # path 指定時の orphan 防止)。default は NARRATION_LEGACY_WAV と一致するため
        # 通常運用では同じ動作、custom path 時のみ差分が出る。
        if out_path.exists():
            try:
                out_path.unlink()
            except OSError:
                pass
        try:
            reset_narration_data_ts()
        except OSError:
            pass
        if CHUNK_META_JSON.exists():
            try:
                CHUNK_META_JSON.unlink()
            except OSError:
                pass
        if NARRATION_READY_JSON.exists():
            try:
                NARRATION_READY_JSON.unlink()
            except OSError:
                pass
        return emit_json(
            "sentinel_write_fail", 6,
            error=f"{type(e).__name__}: {e}",
            out_path=str(out_path),
        )
    print(f"wrote: {NARRATION_READY_JSON} (publish 完了 signal sentinel)")

    summary = {
        "speaker": args.speaker,
        "fps": fps,
        "chunks": len(chunk_paths),
        "total_chunks": len(chunks),
        "total_frames": total_frames,
        "cut_aware": bool(cut_segments),
        "transcript_aligned_count": sum(
            1 for s in segments if s.get("timing_source") == "transcript_aligned"
        ),
        "narration_wav": str(out_path),
        "narration_data_ts": str(ts_path),
        "chunk_meta_json": str(meta_path),
        "narration_ready_json": str(NARRATION_READY_JSON),
        "engine_version": info,
    }
    print(f"\nsummary: {json.dumps(summary, ensure_ascii=False)}")
    # Phase 3-V P3 review fix (Codex CODEX_2ND_BATCH_REVIEW P1): success path も
    # emit_json 経由で status / exit_code を含む形に統一 (全 return path で
    # 同一 schema、downstream tool が status field で分岐可能)。
    return emit_json("success", 0, **summary)


if __name__ == "__main__":
    sys.exit(main())
