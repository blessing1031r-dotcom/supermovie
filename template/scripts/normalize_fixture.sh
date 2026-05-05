#!/usr/bin/env bash
# normalize_fixture.sh — HEVC HDR DoVi / 10bit / rotated fixture を Remotion 互換の
# H.264 SDR / yuv420p / bt709 / rotation 0 portrait or landscape に変換する。
#
# 背景:
#   - Remotion legacy `<Video>` は HEVC Main 10 / HLG / DoVi / Display Matrix rotation
#     を decode 失敗 / sideways 描画する (Phase 3 b1 transcode incident、Codex 18:36 verdict)。
#   - preflight_video.py は risks を検出するが、修復はしない。本 script で正規化する。
#
# Usage:
#   normalize_fixture.sh <input_path> [output_path] [--format short|youtube|square]
#
#   default output = same dir / "main.mp4" (元 file は "main_orig_<codec>_<color>.mp4" に backup)。
#   --format = output 向きの target、未指定で source aspect から推定。
#
# Required: ffmpeg / ffprobe / python3。
# Idempotent: 既に H.264 SDR + Display Matrix なし + risks=[] なら no-op で skip。

set -euo pipefail

if [ "${1:-}" = "" ]; then
  echo "usage: normalize_fixture.sh <input_path> [output_path] [--format short|youtube|square]" >&2
  exit 2
fi

INPUT="$1"
shift

OUTPUT=""
TARGET_FORMAT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --format)
      shift
      TARGET_FORMAT="$1"
      ;;
    *)
      if [ -z "$OUTPUT" ]; then
        OUTPUT="$1"
      else
        echo "unknown arg: $1" >&2
        exit 2
      fi
      ;;
  esac
  shift
done

if [ ! -f "$INPUT" ]; then
  echo "input not found: $INPUT" >&2
  exit 2
fi

INPUT_DIR=$(cd "$(dirname "$INPUT")" && pwd)
INPUT_BASE=$(basename "$INPUT")
if [ -z "$OUTPUT" ]; then
  OUTPUT="$INPUT_DIR/main.mp4"
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
PREFLIGHT="$SCRIPT_DIR/preflight_video.py"
if [ ! -f "$PREFLIGHT" ]; then
  echo "preflight_video.py not found at $PREFLIGHT" >&2
  exit 2
fi

# === 1. preflight で source 解析、修復必要かを判定 ===
SOURCE_JSON=$(python3 "$PREFLIGHT" "$INPUT" 2>/dev/null)
RISKS=$(echo "$SOURCE_JSON" | python3 -c 'import json,sys;print(",".join(json.load(sys.stdin).get("risks",[])))')
SOURCE_CODEC=$(echo "$SOURCE_JSON" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("codec",{}).get("name",""))')
SOURCE_PIXFMT=$(echo "$SOURCE_JSON" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("codec",{}).get("pix_fmt",""))')
SOURCE_ROT=$(echo "$SOURCE_JSON" | python3 -c 'import json,sys;d=json.load(sys.stdin).get("rotation",{});r=d.get("normalized");print(r if r is not None else 0)')

# Display Matrix 残存チェック (ffprobe で side data 確認)
HAS_DISPLAY_MATRIX=$(ffprobe -v error -print_format json -show_streams -select_streams v:0 "$INPUT" 2>/dev/null \
  | python3 -c 'import json,sys;sd=json.load(sys.stdin)["streams"][0].get("side_data_list",[]);print("yes" if any(s.get("side_data_type")=="Display Matrix" for s in sd) else "no")')

NEED_TRANSCODE="no"
if [ "$SOURCE_CODEC" != "h264" ]; then
  NEED_TRANSCODE="yes"
elif [ "$SOURCE_PIXFMT" != "yuv420p" ]; then
  NEED_TRANSCODE="yes"
elif [ -n "$RISKS" ]; then
  case "$RISKS" in
    *hdr-or-dovi*|*10bit*|*vfr*|*rotation-non-canonical*) NEED_TRANSCODE="yes" ;;
  esac
fi

NEED_REMUX="no"
if [ "$HAS_DISPLAY_MATRIX" = "yes" ]; then
  NEED_REMUX="yes"
fi

if [ "$NEED_TRANSCODE" = "no" ] && [ "$NEED_REMUX" = "no" ] && [ "$INPUT" = "$OUTPUT" ]; then
  echo "[normalize] skip: $INPUT is already H.264 SDR + no Display Matrix + risks=[$RISKS]"
  exit 0
fi

# === 2. backup ===
if [ "$INPUT" = "$OUTPUT" ]; then
  TAG=$(echo "$SOURCE_CODEC" | tr '[:upper:]' '[:lower:]')
  if echo "$SOURCE_PIXFMT" | grep -qi "10le\|p10"; then TAG="${TAG}_10bit"; fi
  if echo "$SOURCE_JSON" | python3 -c 'import json,sys;sys.exit(0 if json.load(sys.stdin).get("color",{}).get("hdr_suspect") else 1)'; then
    TAG="${TAG}_hdr"
  fi
  BACKUP="$INPUT_DIR/main_orig_${TAG}.mp4"
  if [ ! -f "$BACKUP" ]; then
    cp -p "$INPUT" "$BACKUP"
    echo "[normalize] backup: $BACKUP"
  else
    echo "[normalize] backup already exists: $BACKUP (skip)"
  fi
  SRC="$BACKUP"
else
  SRC="$INPUT"
fi

# === 3. target dimension 決定 ===
case "$TARGET_FORMAT" in
  short)   W=1080; H=1920 ;;
  youtube) W=1920; H=1080 ;;
  square)  W=1080; H=1080 ;;
  "")
    INFERRED=$(echo "$SOURCE_JSON" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("inferred_format") or "short")')
    case "$INFERRED" in
      short)   W=1080; H=1920 ;;
      youtube) W=1920; H=1080 ;;
      square)  W=1080; H=1080 ;;
      *)       W=1080; H=1920 ;;
    esac
    ;;
  *)
    echo "unknown --format: $TARGET_FORMAT" >&2
    exit 2 ;;
esac

# === 4. transcode (HLG → SDR + tonemap + transpose if rotated) ===
TMP="$INPUT_DIR/.normalize_tmp_$$.mp4"
trap 'rm -f "$TMP"' EXIT

VF=""
if [ "$SOURCE_ROT" = "-90" ] || [ "$SOURCE_ROT" = "270" ]; then
  VF="transpose=clock,"
elif [ "$SOURCE_ROT" = "90" ] || [ "$SOURCE_ROT" = "-270" ]; then
  VF="transpose=cclock,"
elif [ "$SOURCE_ROT" = "180" ] || [ "$SOURCE_ROT" = "-180" ]; then
  VF="transpose=clock,transpose=clock,"
fi

# tonemap chain (HLG/PQ/HDR → bt709 SDR)、SDR source ならば bypass。
IS_HDR=$(echo "$SOURCE_JSON" | python3 -c 'import json,sys;print("yes" if json.load(sys.stdin).get("color",{}).get("hdr_suspect") else "no")')
if [ "$IS_HDR" = "yes" ]; then
  VF="${VF}scale=w=${W}:h=${H}:flags=lanczos:in_color_matrix=bt2020:out_color_matrix=bt2020:in_range=tv:out_range=tv:in_primaries=bt2020:out_primaries=bt2020:in_transfer=arib-std-b67:out_transfer=linear,format=gbrpf32le,tonemap=tonemap=mobius:desat=2:peak=1000,scale=in_color_matrix=bt2020:out_color_matrix=bt709:in_range=tv:out_range=tv:in_primaries=bt2020:out_primaries=bt709:in_transfer=linear:out_transfer=bt709,format=yuv420p,fps=fps=60:start_time=0:round=near"
else
  VF="${VF}scale=w=${W}:h=${H}:flags=lanczos,format=yuv420p,fps=fps=60:start_time=0:round=near"
fi

echo "[normalize] transcode: $SRC → $TMP (W=${W} H=${H} HDR=$IS_HDR ROT=$SOURCE_ROT)"
ffmpeg -hide_banner -y -noautorotate -i "$SRC" \
  -map 0:v:0 -map 0:a:0? -sn -dn -map_metadata -1 -map_chapters -1 \
  -vf "$VF" \
  -fps_mode cfr \
  -c:v libx264 -preset medium -crf 18 -profile:v high -level:v 4.2 \
  -pix_fmt yuv420p \
  -g 60 -keyint_min 60 -sc_threshold 0 \
  -color_range tv -colorspace bt709 -color_trc bt709 -color_primaries bt709 \
  -metadata:s:v:0 rotate=0 \
  -video_track_timescale 600 -movflags +faststart \
  -c:a aac -b:a 192k -ar 48000 -ac 2 \
  "$TMP"

# === 5. remux で Display Matrix を完全除去 ===
TMP2="$INPUT_DIR/.normalize_remux_$$.mp4"
trap 'rm -f "$TMP" "$TMP2"' EXIT

ffmpeg -hide_banner -y -display_rotation 0 -i "$TMP" \
  -c copy -map 0 -map_metadata:s:v:0 -1 \
  "$TMP2"

mv -f "$TMP2" "$OUTPUT"
rm -f "$TMP"

# === 6. ffprobe gate: Display Matrix 不在を検証 ===
POST_DM=$(ffprobe -v error -print_format json -show_streams -select_streams v:0 "$OUTPUT" 2>/dev/null \
  | python3 -c 'import json,sys;sd=json.load(sys.stdin)["streams"][0].get("side_data_list",[]);print("yes" if any(s.get("side_data_type")=="Display Matrix" for s in sd) else "no")')
if [ "$POST_DM" = "yes" ]; then
  echo "[normalize][FAIL] Display Matrix metadata still present in $OUTPUT" >&2
  exit 3
fi

# === 7. preflight 再走行で risks=[] を確認 ===
POST_RISKS=$(python3 "$PREFLIGHT" "$OUTPUT" 2>/dev/null \
  | python3 -c 'import json,sys;print(",".join(json.load(sys.stdin).get("risks",[])))')
if [ -n "$POST_RISKS" ]; then
  echo "[normalize][WARN] post-normalize risks: [$POST_RISKS]" >&2
fi

echo "[normalize][OK] $OUTPUT (Display Matrix removed, risks=[$POST_RISKS])"
