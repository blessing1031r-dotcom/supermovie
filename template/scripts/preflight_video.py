#!/usr/bin/env python3
"""SuperMovie pre-flight video analyzer.

入力動画の forensics (rotation / SAR / DAR / HDR / DOVI / 10-bit / VFR / 字幕 /
複数 audio / 複数 video / interlace) を ffprobe + ffmpeg で完全に取り、
project-config.json source.* schema (nested) と risks リストを生成する。

supermovie-init Phase 2 で必ず最初に実行する。
side_data_list は必ず全走査して side_data_type で判定 (index 参照禁止)。

Usage:
    python3 scripts/preflight_video.py INPUT_VIDEO \\
        --write-config project-config.json \\
        [--force-format youtube|short|square] \\
        [--allow-risk hdr,dovi,10bit,vfr,multiple-audio,subtitle,sar]

Exit codes:
    0 = proceed (no risks 又は --allow-risk で許可済み)
    2 = requires confirmation (検出された risks が --allow-risk で許可されていない)
    3 = unsupported input (動画が読めない / video stream 不在 等)

設計起点: Codex prevention review (2026-05-04, CODEX_PREVENTION_ROTATION)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

FORMAT_TARGETS = {
    "youtube": 16 / 9,
    "short": 9 / 16,
    "square": 1.0,
}
FORMAT_TOLERANCE = 0.03  # relative

ALL_RISK_KEYS = [
    "rotation-non-canonical",
    "non-square-sar",
    "unknown-aspect",
    "vfr",
    "hdr-or-dovi",
    "10bit",
    "interlaced",
    "multiple-or-missing-video",
    "multiple-or-missing-audio",
    "embedded-subtitle",
]


class FFprobeError(Exception):
    """run_ffprobe() failure を main() 側で捕捉して emit する用 (Codex 21:34 PR5 P1 fix)。"""
    pass


def run_ffprobe(path: str) -> dict:
    """ffprobe を JSON で取得。失敗なら exit 3."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    # Codex 21:34 PR5 review P1 fix: bare sys.exit(3) を撤去し caller に raise する。
    # main() 側で try/except → _emit() で v1 status JSON tail を出してから exit。
    try:
        out = subprocess.run(cmd, capture_output=True, check=True, text=True)
        return json.loads(out.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError) as e:
        raise FFprobeError(f"ffprobe failed for {path}: {e}") from e


def normalize_rotation(raw: int | None) -> int | None:
    """rotation を [-180, 180] の canonical 値に正規化."""
    if raw is None:
        return None
    r = int(raw) % 360
    if r > 180:
        r -= 360
    if r in (-180, -90, 0, 90, 180):
        return r
    return r  # non-canonical だが値は返す (risk フラグ別途)


def parse_side_data(stream: dict) -> dict:
    """side_data_list を全走査 (index 参照禁止) して種類別に集約."""
    out = {
        "rotation_raw": None,
        "rotation_source": None,
        "dovi": None,
        "ambient_viewing_environment": None,
        "raw_entries": [],
    }
    for entry in stream.get("side_data_list", []) or []:
        sdt = entry.get("side_data_type")
        out["raw_entries"].append(sdt)
        if sdt == "Display Matrix":
            r = entry.get("rotation")
            if r is not None:
                out["rotation_raw"] = int(r)
                out["rotation_source"] = "Display Matrix"
        elif sdt == "DOVI configuration record":
            out["dovi"] = {
                "dv_profile": entry.get("dv_profile"),
                "dv_level": entry.get("dv_level"),
                "rpu_present_flag": entry.get("rpu_present_flag"),
                "el_present_flag": entry.get("el_present_flag"),
                "bl_present_flag": entry.get("bl_present_flag"),
            }
        elif sdt == "Ambient viewing environment":
            out["ambient_viewing_environment"] = True
    return out


def detect_rotation(video_stream: dict) -> tuple[int | None, str | None]:
    """rotation を Display Matrix → tags.rotate → root rotation の順で検出."""
    sd = parse_side_data(video_stream)
    if sd["rotation_raw"] is not None:
        return normalize_rotation(sd["rotation_raw"]), sd["rotation_source"]
    tags = video_stream.get("tags", {}) or {}
    legacy = tags.get("rotate")
    if legacy is not None:
        try:
            return normalize_rotation(int(legacy)), "tags.rotate"
        except (TypeError, ValueError):
            pass
    root = video_stream.get("rotation")
    if root is not None:
        try:
            return normalize_rotation(int(root)), "root.rotation"
        except (TypeError, ValueError):
            pass
    return 0, "default"


def apply_rotation_to_dimensions(w: int, h: int, rotation: int | None) -> tuple[int, int]:
    """rotation を適用した display 解像度を返す (90/-90/270 で w<->h 入れ替え)."""
    if rotation in (90, -90, 270, -270):
        return h, w
    return w, h


def infer_format(display_w: int, display_h: int, sar_value: str | None) -> str | None:
    if display_w <= 0 or display_h <= 0:
        return None
    if sar_value not in ("1:1", "0:1", None, ""):
        return None
    aspect = display_w / display_h
    matches = [
        name for name, target in FORMAT_TARGETS.items()
        if abs(aspect - target) / target <= FORMAT_TOLERANCE
    ]
    return matches[0] if len(matches) == 1 else None


def detect_vfr(video_stream: dict) -> dict:
    """r_frame_rate と avg_frame_rate を比較。差があれば VFR 疑い."""
    r = video_stream.get("r_frame_rate", "0/1")
    a = video_stream.get("avg_frame_rate", "0/1")
    def to_float(s: str) -> float:
        try:
            num, den = s.split("/")
            return float(num) / float(den) if float(den) != 0 else 0.0
        except (ValueError, ZeroDivisionError):
            return 0.0
    rf = to_float(r)
    af = to_float(a)
    suspect = rf > 0 and af > 0 and abs(rf - af) / max(rf, af) > 0.005
    return {"r_frame_rate": r, "avg_frame_rate": a, "vfr_metadata_suspect": suspect}


def detect_hdr(video_stream: dict, dovi: dict | None) -> bool:
    """HDR / DOVI を疑う."""
    if dovi:
        return True
    transfer = (video_stream.get("color_transfer") or "").lower()
    primaries = (video_stream.get("color_primaries") or "").lower()
    space = (video_stream.get("color_space") or "").lower()
    if transfer in ("smpte2084", "arib-std-b67"):
        return True
    if primaries == "bt2020" or space.startswith("bt2020"):
        return True
    return False


def is_10bit(video_stream: dict) -> bool:
    pix_fmt = (video_stream.get("pix_fmt") or "").lower()
    return "10" in pix_fmt or "p010" in pix_fmt or "12" in pix_fmt


def build_source(video: dict, audio_streams: list[dict], subtitle_streams: list[dict],
                 video_streams: list[dict], data_streams: list[dict],
                 fmt_meta: dict, input_path: str) -> dict:
    raw_w = int(video.get("width", 0))
    raw_h = int(video.get("height", 0))
    rotation_normalized, rotation_source = detect_rotation(video)
    raw_rotation = parse_side_data(video).get("rotation_raw")

    display_w, display_h = apply_rotation_to_dimensions(raw_w, raw_h, rotation_normalized)
    sar = video.get("sample_aspect_ratio") or "1:1"
    dar = video.get("display_aspect_ratio")
    aspect = display_w / display_h if display_h else 0.0
    inferred_format = infer_format(display_w, display_h, sar)

    fps = detect_vfr(video)
    sd = parse_side_data(video)
    dovi = sd.get("dovi")
    hdr_suspect = detect_hdr(video, dovi)

    duration_sec = float(fmt_meta.get("duration", 0)) if fmt_meta.get("duration") else 0.0

    return {
        "video": Path(input_path).name,
        "raw": {"width": raw_w, "height": raw_h},
        "display": {"width": display_w, "height": display_h},
        "rotation": {
            "raw": raw_rotation,
            "normalized": rotation_normalized,
            "source": rotation_source,
        },
        "aspect": round(aspect, 6),
        "sar": sar,
        "dar": dar,
        "inferred_format": inferred_format,
        "fps": {**fps, "render_fps": int(round(eval_fps(fps["r_frame_rate"])))},
        "duration_sec": duration_sec,
        "duration_frames": int(round(duration_sec * eval_fps(fps["r_frame_rate"]))),
        "codec": {
            "name": video.get("codec_name"),
            "profile": video.get("profile"),
            "pix_fmt": video.get("pix_fmt"),
            "field_order": video.get("field_order"),
        },
        "color": {
            "range": video.get("color_range"),
            "space": video.get("color_space"),
            "transfer": video.get("color_transfer"),
            "primaries": video.get("color_primaries"),
            "hdr_suspect": hdr_suspect,
            "dovi": dovi,
        },
        "streams": {
            "video": len(video_streams),
            "audio": len(audio_streams),
            "subtitle": len(subtitle_streams),
            "data": len(data_streams),
        },
    }


def eval_fps(rate_str: str) -> float:
    try:
        num, den = rate_str.split("/")
        return float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def build_risks(source: dict) -> list[str]:
    risks = []
    rotation_norm = (source.get("rotation") or {}).get("normalized")
    if rotation_norm not in (None, 0, 90, -90, 180, -180):
        risks.append("rotation-non-canonical")
    sar = source.get("sar")
    if sar not in ("1:1", "0:1", None, ""):
        risks.append("non-square-sar")
    if source.get("inferred_format") is None:
        risks.append("unknown-aspect")
    if (source.get("fps") or {}).get("vfr_metadata_suspect"):
        risks.append("vfr")
    if (source.get("color") or {}).get("hdr_suspect"):
        risks.append("hdr-or-dovi")
    if is_10bit_codec(source):
        risks.append("10bit")
    field = (source.get("codec") or {}).get("field_order")
    if field not in (None, "unknown", "progressive"):
        risks.append("interlaced")
    streams = source.get("streams") or {}
    if streams.get("video", 0) != 1:
        risks.append("multiple-or-missing-video")
    if streams.get("audio", 0) != 1:
        risks.append("multiple-or-missing-audio")
    if streams.get("subtitle", 0) > 0:
        risks.append("embedded-subtitle")
    return risks


def is_10bit_codec(source: dict) -> bool:
    pix_fmt = ((source.get("codec") or {}).get("pix_fmt") or "").lower()
    return "10" in pix_fmt or "p010" in pix_fmt or "12" in pix_fmt


def main():
    # Phase 3 obs migration step 3 PR-B (Codex 21:01 step 3 verdict S3-3):
    # 既存 stdout の source JSON は維持、--json-log 時のみ末尾 1 行に v1 status を追加。
    # 既存 schema を helper schema に置換しない (downstream parser 互換性維持)。
    import time as _time
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _observability import (
        build_status,
        emit_json as _obs_emit_json,
        redact_error_message,
        resolve_run_context,
        safe_artifact_path,
    )

    ap = argparse.ArgumentParser()
    ap.add_argument("input_video")
    ap.add_argument("--write-config", help="project-config.json path to write")
    ap.add_argument("--force-format", choices=list(FORMAT_TARGETS.keys()))
    ap.add_argument("--allow-risk", default="", help="comma-separated risk keys")
    ap.add_argument("--json-log", action="store_true",
                    help="末尾に summary を 1 行純 JSON として emit "
                         "(downstream observability、既存 stdout source JSON は維持)")
    ap.add_argument("--unsafe-keep-abs-path", action="store_true",
                    help="json tail の artifact path を絶対 path のまま emit (debug 専用)")
    args = ap.parse_args()
    start_time = _time.monotonic()
    PROJ_ROOT = Path(__file__).resolve().parent.parent

    # PR-E: trace context resolve、_emit closure に閉じ込め
    run_ctx = resolve_run_context()

    def _emit(v0_status, exit_code, *, category=None, **extra):
        """v1 status JSON tail emit (preflight)。Codex 21:34 PR5 review P2 fix:
        category は default で STATUS_MAP entry (input-not-found / no-video-stream 等)
        を活かす。success path のみ "preflight-source-meta" override で意味付け。
        既存 stdout (source JSON dump + write-config message) は維持される。
        """
        duration_ms = int((_time.monotonic() - start_time) * 1000)
        artifacts = []
        if args.write_config:
            artifacts.append({
                "path": safe_artifact_path(
                    args.write_config,
                    project_root=PROJ_ROOT,
                    unsafe_keep_abs_path=args.unsafe_keep_abs_path,
                ),
                "kind": "json",
            })
        redaction_rules = []
        if not args.unsafe_keep_abs_path:
            redaction_rules.append("abs_path")
        payload = build_status(
            script="preflight_video",
            v0_status=v0_status,
            exit_code=exit_code,
            counts={},
            artifacts=artifacts,
            cost=None,
            duration_ms=duration_ms,
            category_override=category,
            redaction_rules=redaction_rules,
            run_id=run_ctx["run_id"],
            parent_run_id=run_ctx["parent_run_id"],
            step_id=run_ctx["step_id"],
            **extra,
        )
        _obs_emit_json(args.json_log, payload)
        return exit_code

    if not Path(args.input_video).exists():
        # PR-J (stderr path leak audit): redact input path on stderr (default redact、unsafe で raw)。
        _safe_input = safe_artifact_path(args.input_video, project_root=PROJ_ROOT, unsafe_keep_abs_path=args.unsafe_keep_abs_path)
        print(f"ERROR: input not found: {_safe_input}", file=sys.stderr)
        sys.exit(_emit("input_not_found", 3))

    # Codex 21:34 PR5 review P1 fix: run_ffprobe failure (CalledProcess /
    # JSONDecode / FileNotFound) を try/except で捕捉し _emit() 経由で
    # v1 status JSON tail emit してから exit。
    try:
        probe = run_ffprobe(args.input_video)
    except FFprobeError as e:
        # PR-J fix iter (Codex 00:28 P1 #1): stderr 側も redact、`FFprobeError` は path を内包する。
        print(f"ERROR: {redact_error_message(str(e))}", file=sys.stderr)
        sys.exit(_emit("ffprobe_failed", 3, error=redact_error_message(str(e))))
    streams = probe.get("streams", []) or []
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    subtitle_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    data_streams = [s for s in streams if s.get("codec_type") == "data"]
    if not video_streams:
        print("ERROR: no video stream", file=sys.stderr)
        sys.exit(_emit("no_video_stream", 3))
    video = video_streams[0]

    source = build_source(
        video, audio_streams, subtitle_streams, video_streams, data_streams,
        probe.get("format", {}) or {}, args.input_video,
    )
    risks = build_risks(source)
    source["risks"] = risks

    chosen_format = args.force_format or source["inferred_format"]
    source["chosen_format"] = chosen_format
    source["requiresConfirmation"] = bool(risks) and not args.force_format

    allow = {k.strip() for k in args.allow_risk.split(",") if k.strip()}
    unhandled = [r for r in risks if r not in allow]

    # 既存 stdout: source JSON dump (downstream consumer はこの形式を読む、互換性維持)
    print(json.dumps(source, ensure_ascii=False, indent=2))

    if args.write_config:
        cfg_path = Path(args.write_config)
        # PR-G: 既存 config の parse / write 失敗を tail emit する。
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                print(f"ERROR: existing write-config parse failed: {redact_error_message(str(e))}", file=sys.stderr)
                sys.exit(_emit("write_config_parse_error", 3,
                               error=redact_error_message(str(e))))
        else:
            cfg = {}
        cfg.setdefault("source", {})
        cfg["source"] = source
        cfg["resolution"] = {
            "width": source["display"]["width"],
            "height": source["display"]["height"],
        }
        if chosen_format:
            cfg["format"] = chosen_format
        try:
            cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            print(f"ERROR: write-config write failed: {redact_error_message(str(e))}", file=sys.stderr)
            sys.exit(_emit("write_config_write_error", 3,
                           error=redact_error_message(str(e))))
        # PR-I: default redact、--unsafe-keep-abs-path で raw。stderr も同 contract。
        _safe_cfg = safe_artifact_path(cfg_path, project_root=PROJ_ROOT, unsafe_keep_abs_path=args.unsafe_keep_abs_path)
        print(f"\nwrote: {_safe_cfg}", file=sys.stderr)

    if unhandled:
        print(f"\nrisks not allowed: {unhandled}", file=sys.stderr)
        sys.exit(_emit(
            "risks_not_allowed", 2,
            unhandled_risks=unhandled, chosen_format=chosen_format,
        ))
    if chosen_format is None:
        print("\nERROR: format could not be inferred (--force-format required)", file=sys.stderr)
        sys.exit(_emit("format_inference_failed", 2))
    # Codex 21:34 PR5 review P2 fix: success path のみ preflight-source-meta category 上書き
    # (success に意味付け)。error path は STATUS_MAP の詳細 category (input-not-found 等) 維持。
    sys.exit(_emit(
        "preflight_ok", 0,
        category="preflight-source-meta",
        chosen_format=chosen_format, risks=risks,
    ))


if __name__ == "__main__":
    main()
