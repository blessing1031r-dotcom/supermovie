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
      if [ $# -lt 2 ]; then
        echo "--format requires a value (short|youtube|square)" >&2
        exit 2
      fi
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

# Codex 20:04 3rd review P2: --format unknown validation を arg parse 直後 (skip/backup より前) に移動。
# 旧位置は section 3 (target dim 決定) で、idempotent skip path で invalid format を素通りさせる構造だった。
case "$TARGET_FORMAT" in
  short|youtube|square|"") ;;
  *)
    echo "unknown --format: $TARGET_FORMAT (expected short|youtube|square)" >&2
    exit 2 ;;
esac

if [ ! -f "$INPUT" ]; then
  echo "input not found: $INPUT" >&2
  exit 2
fi

# 相対 path で渡された場合の比較失敗を防ぐため、INPUT / OUTPUT を冒頭で絶対 path に正規化。
# Codex 19:39 PR re-review P1: relative `public/main.mp4` で INPUT != OUTPUT 比較が
# false になり、idempotent skip と backup が発火しない構造的バグを fix。
INPUT_DIR=$(cd "$(dirname "$INPUT")" && pwd)
INPUT_BASE=$(basename "$INPUT")
INPUT="$INPUT_DIR/$INPUT_BASE"
if [ -z "$OUTPUT" ]; then
  OUTPUT="$INPUT_DIR/main.mp4"
else
  OUTPUT_DIR=$(cd "$(dirname "$OUTPUT")" && pwd)
  OUTPUT="$OUTPUT_DIR/$(basename "$OUTPUT")"
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
PREFLIGHT="$SCRIPT_DIR/preflight_video.py"
if [ ! -f "$PREFLIGHT" ]; then
  echo "preflight_video.py not found at $PREFLIGHT" >&2
  exit 2
fi

# === 1. preflight で source 解析、修復必要かを判定 ===
# preflight_video.py は risks 不許容 / format 推定不能 で exit 2 を返すが
# JSON は exit 前に stdout に出力済み (preflight_video.py:328 print → :347 sys.exit(2))。
# 本 script は risk 付き入力の正規化が目的なので exit 0/2 を許容、それ以外は abort。
set +e
SOURCE_JSON=$(python3 "$PREFLIGHT" "$INPUT" 2>/dev/null)
PREFLIGHT_EXIT=$?
set -e
if [ "$PREFLIGHT_EXIT" -ne 0 ] && [ "$PREFLIGHT_EXIT" -ne 2 ]; then
  echo "[normalize][FAIL] preflight exit=$PREFLIGHT_EXIT (expected 0 or 2)" >&2
  exit "$PREFLIGHT_EXIT"
fi
if [ -z "$SOURCE_JSON" ]; then
  echo "[normalize][FAIL] preflight produced empty JSON" >&2
  exit 3
fi

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

# Codex 20:04 3rd review P2: idempotent skip 条件に RISKS empty 判定を追加。
# 旧条件は NEED_TRANSCODE=no + NEED_REMUX=no + INPUT=OUTPUT のみで、interlaced /
# multiple-or-missing-* / non-square-sar 等 (NEED_TRANSCODE 判定対象外の risks)
# を持つ source も skip exit 0 してしまう構造だった。RISKS empty を必須化。
if [ "$NEED_TRANSCODE" = "no" ] && [ "$NEED_REMUX" = "no" ] && [ "$INPUT" = "$OUTPUT" ] && [ -z "$RISKS" ]; then
  echo "[normalize] skip: $INPUT is already H.264 SDR + no Display Matrix + risks=[]"
  exit 0
fi

# === 2. backup (Codex 20:04 P2: stale backup reuse 防止) ===
# 旧実装は BACKUP が既にあれば SRC=BACKUP 固定だった。INPUT が後から書き換えられた
# 場合 (例: ユーザが別 fixture を同 path に置いた) に古い BACKUP を source に使う bug。
# 本 fix: BACKUP が既存で INPUT と内容差分ありなら、timestamp suffix で別 BACKUP に
# 退避してから INPUT を改めて backup。SRC は常に INPUT (current state) を使う。
if [ "$INPUT" = "$OUTPUT" ]; then
  TAG=$(echo "$SOURCE_CODEC" | tr '[:upper:]' '[:lower:]')
  if echo "$SOURCE_PIXFMT" | grep -qi "10le\|p10"; then TAG="${TAG}_10bit"; fi
  if echo "$SOURCE_JSON" | python3 -c 'import json,sys;sys.exit(0 if json.load(sys.stdin).get("color",{}).get("hdr_suspect") else 1)'; then
    TAG="${TAG}_hdr"
  fi
  BACKUP="$INPUT_DIR/main_orig_${TAG}.mp4"
  if [ -f "$BACKUP" ]; then
    if cmp -s "$INPUT" "$BACKUP"; then
      echo "[normalize] backup already exists (matches INPUT): $BACKUP"
    else
      # Codex 20:14 4th review P2: 同秒連続実行で `date +%s` 単独だと collision するため、
      # mktemp -u の random suffix を使って衝突回避。事前 path 予約のみで file 作成は mv。
      OLD_BACKUP=$(mktemp -u "$INPUT_DIR/main_orig_${TAG}_archived_XXXXXX").mp4
      mv "$BACKUP" "$OLD_BACKUP"
      cp -p "$INPUT" "$BACKUP"
      echo "[normalize] backup (existing differs, archived to $OLD_BACKUP): $BACKUP"
    fi
  else
    cp -p "$INPUT" "$BACKUP"
    echo "[normalize] backup: $BACKUP"
  fi
  SRC="$INPUT"
else
  SRC="$INPUT"
fi

# === 3. target dimension 決定 ===
case "$TARGET_FORMAT" in
  short)   W=1080; H=1920 ;;
  youtube) W=1920; H=1080 ;;
  square)  W=1080; H=1080 ;;
  "")
    # Codex 20:14 4th review P2: inferred_format 空時の short fallback を strict 化。
    # preflight が aspect から format を推定できない場合 (unknown-aspect risk が立つ source)、
    # silent fallback では誤った target dim で transcode する risk あり。
    # --format 必須 fail に寄せ、user に明示判断を要求する。
    INFERRED=$(echo "$SOURCE_JSON" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("inferred_format") or "")')
    case "$INFERRED" in
      short)   W=1080; H=1920 ;;
      youtube) W=1920; H=1080 ;;
      square)  W=1080; H=1080 ;;
      "")
        echo "[normalize][FAIL] preflight が format を推定できません (aspect 異常 / unknown-aspect risk)。--format short|youtube|square を明示してください。" >&2
        exit 2 ;;
      *)
        echo "[normalize][FAIL] preflight returned unknown inferred_format: $INFERRED" >&2
        exit 2 ;;
    esac
    ;;
  *)
    echo "unknown --format: $TARGET_FORMAT" >&2
    exit 2 ;;
esac

# === 4. transcode (HLG → SDR + tonemap + transpose if rotated) ===
# Codex 19:39 PR re-review P2: mktemp -u は path 未予約のまま race window があるため、
# mktemp -d で directory を atomic に作成してから内部に temp file を置く形に変更。
# directory 自体は mktemp -d で race-free、ffmpeg が内部 file を作成。
TMP_DIR=""
trap 'if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then rm -rf "$TMP_DIR"; fi' EXIT
TMP_DIR=$(mktemp -d "$INPUT_DIR/.normalize.XXXXXX")
TMP="$TMP_DIR/transcode.mp4"
TMP2="$TMP_DIR/remux.mp4"

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
# 同様に exit 0/2 を許容 (post fixture は通常 exit 0 だが防御的に)
set +e
POST_JSON=$(python3 "$PREFLIGHT" "$OUTPUT" 2>/dev/null)
POST_EXIT=$?
set -e
if [ "$POST_EXIT" -ne 0 ] && [ "$POST_EXIT" -ne 2 ]; then
  echo "[normalize][FAIL] post-preflight exit=$POST_EXIT" >&2
  exit "$POST_EXIT"
fi
POST_RISKS=$(echo "$POST_JSON" | python3 -c 'import json,sys;print(",".join(json.load(sys.stdin).get("risks",[])))')
# Codex 19:39 PR re-review P2: post-condition (risks=[]) を弱い WARN ではなく
# strict-fail に変更。release note の「正規化完了 = risks=[]」契約と整合させる。
# rotation-non-canonical / interlaced / multiple-or-missing-* / non-square-sar 等の
# 残 risk は本 transcode chain で扱えない種類なので、strict-fail で reviewer に通知する。
if [ -n "$POST_RISKS" ]; then
  echo "[normalize][FAIL] post-normalize risks=[$POST_RISKS] (expected empty, transcode chain で扱えない種別の risk が残存)" >&2
  exit 4
fi

echo "[normalize][OK] $OUTPUT (Display Matrix removed, risks=[])"
