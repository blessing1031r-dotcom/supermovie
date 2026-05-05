#!/usr/bin/env python3
"""SuperMovie Phase 3-G visual smoke: format dimension regression detector.

3 format (youtube/short/square) × N frame で `npx remotion still` を生成し、
各 PNG の dimension が format 期待値と一致するか ffprobe で検証する。

| format | width × height | aspect |
|--------|---------------|--------|
| youtube | 1920 × 1080 | 16:9 |
| short   | 1080 × 1920 | 9:16 |
| square  | 1080 × 1080 | 1:1 |

不一致 = exit non-zero (regression として CI/Roku 視認の双方で検知)。
ffmpeg で 3×N grid PNG を out/visual_smoke/grid.png に合成し、目視レビューも可。

Codex Phase 3G design (2026-05-04, CODEX_PHASE3G_NEXT) 推奨:
- videoConfig.ts FORMAT を try/finally で in-place 切替 + restore
- per-format remotion still、frame 30/90 デフォルト
- 各 still を ffprobe で width/height 検証
- ffmpeg vstack/hstack で grid 合成 (`--no-grid` で skip 可)

前提:
- 対象 SuperMovie project (main.mp4 / node_modules / remotion installed)
- ffprobe / ffmpeg コマンドが PATH に存在 (Phase 3-A 以降 preflight で検証済み)

Usage:
    python3 scripts/visual_smoke.py                       # 3 format × 2 frame
    python3 scripts/visual_smoke.py --formats youtube,short
    python3 scripts/visual_smoke.py --frames 30,90,180
    python3 scripts/visual_smoke.py --no-grid             # grid 合成 skip

Exit code:
    0 = 全 still 出力 + dimension 一致
    2 = 1 件以上 dimension mismatch (regression、render は成功している)
    3 = 実行環境問題 (main.mp4 不在 / node_modules 不在 / remotion still failed /
         ffprobe failed / ffmpeg drawtext filter なし / grid 合成 failed)
    4 = 入力 / 設定不正 (videoConfig.ts 不在 or FORMAT 行 regex 不一致 /
         空 formats / 空 frames / 未知 format)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
VIDEO_CONFIG = PROJ / "src" / "videoConfig.ts"
SMOKE_OUT = PROJ / "out" / "visual_smoke"
COMPOSITION_ID = "MainVideo"
MAIN_VIDEO = PROJ / "public" / "main.mp4"
REMOTION_BIN = PROJ / "node_modules" / ".bin" / "remotion"

# Phase 3 obs migration step 3 (Codex 21:01 verdict S3-4): summary.json artifact
# は維持、--json-log で v1 status を stdout 末尾に追加 emit。
# category_override="dimension-regression" (Codex S3-4)、cost=null。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _observability import (  # noqa: E402
    build_status,
    emit_json as _obs_emit_json,
    resolve_run_context,
    safe_artifact_path,
)

FORMAT_DIMS = {
    "youtube": (1920, 1080),
    "short": (1080, 1920),
    "square": (1080, 1080),
}
FORMAT_LINE_RE = re.compile(
    r"^(export const FORMAT: VideoFormat = ')(youtube|short|square)(';)$",
    re.MULTILINE,
)


def patch_format(content: str, fmt: str) -> str:
    """videoConfig.ts の FORMAT 行を fmt に書き換える。

    一致 0 件で ValueError、複数一致でも先頭1件のみ書換 (Anchored multi-line)。
    """
    if not FORMAT_LINE_RE.search(content):
        raise ValueError(
            f"FORMAT 行が videoConfig.ts に見つからない: pattern={FORMAT_LINE_RE.pattern}"
        )
    return FORMAT_LINE_RE.sub(rf"\g<1>{fmt}\g<3>", content, count=1)


def probe_dim(png: Path) -> tuple[int, int]:
    """ffprobe で PNG の width × height を取得。"""
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(png),
        ],
        text=True,
    )
    info = json.loads(out)
    s = info["streams"][0]
    return int(s["width"]), int(s["height"])


def render_still(project: Path, frame: int, png_out: Path) -> None:
    """`npx remotion still` で 1 frame の PNG 出力。"""
    png_out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [
            "npx",
            "--no-install",
            "remotion",
            "still",
            COMPOSITION_ID,
            str(png_out),
            "--frame",
            str(frame),
        ],
        cwd=str(project),
    )


def has_drawtext_filter() -> bool:
    """ffmpeg build に drawtext filter (libfreetype) が含まれるか確認。

    Mac Homebrew 標準 build は drawtext を持つが、minimal build では落ちている
    ことがあるため事前検査する (Codex Phase 3-G review P1 #2 反映)。
    """
    try:
        out = subprocess.check_output(
            ["ffmpeg", "-hide_banner", "-filters"], text=True, stderr=subprocess.STDOUT
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    for line in out.splitlines():
        if " drawtext " in f" {line} ":
            return True
    return False


CELL_W = 480
CELL_H = 360


def make_grid(
    stills: list[Path],
    grid_out: Path,
    formats: list[str],
    frames: list[int],
    label: bool,
) -> None:
    """ffmpeg で N(format) × M(frame) の grid を 1 PNG に合成。

    呼び出し側で full matrix (len(stills) == n_fmt * n_frm) を保証すること
    (部分失敗時の cell 対応崩れを防ぐ、Codex Phase 3-G P2 #5 反映)。
    label=True の時は drawtext で format/frame を焼き込み、False の時は scale のみ
    (drawtext filter 不在環境向け、Codex P1 #2 反映)。

    各 cell を CELL_W × CELL_H の固定 box に letterbox (scale + pad) で統一する。
    youtube/short/square は aspect 比が異なるため、共通 height だけだと row ごとに
    width が変わり vstack が input width 不一致で reject する (Codex Phase 3-G fix
    再 review investigation で実証、新規 P1)。
    """
    if not stills:
        return
    grid_out.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    for s in stills:
        inputs.extend(["-i", str(s)])

    n_fmt = len(formats)
    n_frm = len(frames)
    filter_parts: list[str] = []
    # 各 cell を CELL_W×CELL_H box に letterbox (aspect 維持で fit、余白は黒)
    for i, s in enumerate(stills):
        fmt = formats[i // n_frm]
        frm = frames[i % n_frm]
        scale_pad = (
            f"scale={CELL_W}:{CELL_H}:force_original_aspect_ratio=decrease,"
            f"pad={CELL_W}:{CELL_H}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
        if label:
            txt = f"{fmt} f{frm}".replace("'", r"\'").replace(":", r"\:")
            filter_parts.append(
                f"[{i}:v]{scale_pad},"
                f"drawtext=text='{txt}':fontcolor=white:fontsize=24:"
                f"box=1:boxcolor=black@0.6:boxborderw=8:x=20:y=20[c{i}]"
            )
        else:
            filter_parts.append(f"[{i}:v]{scale_pad}[c{i}]")

    # 各 format 行の hstack
    row_labels: list[str] = []
    for r in range(n_fmt):
        row_in = "".join(f"[c{r * n_frm + c}]" for c in range(n_frm))
        row_label = f"row{r}"
        if n_frm == 1:
            filter_parts.append(f"{row_in}copy[{row_label}]")
        else:
            filter_parts.append(f"{row_in}hstack=inputs={n_frm}[{row_label}]")
        row_labels.append(f"[{row_label}]")

    # vstack
    if n_fmt == 1:
        filter_parts.append(f"{row_labels[0]}copy[grid]")
    else:
        filter_parts.append(f"{''.join(row_labels)}vstack=inputs={n_fmt}[grid]")

    cmd = (
        ["ffmpeg", "-y"]
        + inputs
        + [
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[grid]",
            "-frames:v",
            "1",
            str(grid_out),
        ]
    )
    subprocess.check_call(cmd)


def cli() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--formats",
        default="youtube,short,square",
        help="検証対象 format (カンマ区切り、default 全 3 種)",
    )
    ap.add_argument(
        "--frames",
        default="30,90",
        help="検証 frame 番号 (カンマ区切り、default 30,90)",
    )
    ap.add_argument("--out-dir", default=str(SMOKE_OUT), help="出力ディレクトリ")
    ap.add_argument("--no-grid", action="store_true", help="ffmpeg grid 合成 skip")
    ap.add_argument("--json-log", action="store_true",
                    help="末尾に summary を 1 行純 JSON として emit "
                         "(downstream observability、既存 stdout / summary.json は維持)")
    ap.add_argument("--unsafe-keep-abs-path", action="store_true",
                    help="json tail の artifact path を絶対 path のまま emit (debug 専用)")
    args = ap.parse_args()
    start_time = time.monotonic()

    # PR-E: trace context resolve (1 invocation 1 resolve、_emit_early / 本走 emit 両方で共有)
    run_ctx = resolve_run_context()

    # Phase 3 obs migration step 3 (Codex 21:14 PR4 review P1 #1):
    # contract は --json-log 時 1 invocation 1 emission。validation/env early return も
    # tail emit する helper closure を冒頭で定義し、全 return path で使う。
    def _emit_early(v0_status, exit_code, **extra):
        """Early-return path 用 v1 status emit。validation / env error を tail に記録。

        category_override は使わず STATUS_MAP の登録値を活かす:
        - usage_error_* → category="usage-error"
        - env_* → category="env-failure"
        smoke 本走の dimension-regression category とは区別される (smoke が走っていない段階の error)。
        """
        duration_ms = int((time.monotonic() - start_time) * 1000)
        payload = build_status(
            script="visual_smoke",
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

    formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    if not formats:
        print("ERROR: --formats が空です (例: --formats youtube,short)", file=sys.stderr)
        return _emit_early("usage_error_formats_empty", 4)
    for f in formats:
        if f not in FORMAT_DIMS:
            print(f"ERROR: 未知の format: {f} (許容: {','.join(FORMAT_DIMS)})", file=sys.stderr)
            return _emit_early("usage_error_unknown_format", 4, bad_format=f)
    # Codex 21:23 PR4 re-review P1: 非整数 frame を try-except で捕捉、JSON tail emit する。
    try:
        frames = [int(x) for x in args.frames.split(",") if x.strip()]
    except ValueError as e:
        print(f"ERROR: --frames に非整数値: {args.frames!r} ({e})", file=sys.stderr)
        return _emit_early("usage_error_frames_invalid", 4, raw_value=args.frames)
    if not frames:
        print("ERROR: --frames が空です (例: --frames 30,90)", file=sys.stderr)
        return _emit_early("usage_error_frames_empty", 4)
    if any(f < 0 for f in frames):
        print(f"ERROR: --frames に負数: {frames}", file=sys.stderr)
        return _emit_early("usage_error_frames_negative", 4)

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 環境チェック (Codex Phase 3-G review P1 #1 反映、render 失敗を環境問題として早期検知)
    for tool in ("npx", "ffprobe", "ffmpeg"):
        if shutil.which(tool) is None:
            print(f"ERROR: {tool} コマンドが PATH にない", file=sys.stderr)
            return _emit_early("env_tool_missing", 3, missing_tool=tool)
    if not MAIN_VIDEO.exists():
        print(f"ERROR: base 動画が無い: {MAIN_VIDEO} (npm run visual-smoke は実 project で実行)", file=sys.stderr)
        return _emit_early("env_main_video_missing", 3)
    if not REMOTION_BIN.exists():
        print(
            f"ERROR: remotion CLI が無い: {REMOTION_BIN} "
            f"(npm install を先に実行してください)",
            file=sys.stderr,
        )
        return _emit_early("env_remotion_cli_missing", 3)
    grid_label = has_drawtext_filter()
    if not grid_label and not args.no_grid:
        print(
            "INFO: ffmpeg drawtext filter なし、grid label を skip して画像のみ合成します",
            file=sys.stderr,
        )

    # videoConfig.ts 原本保持
    if not VIDEO_CONFIG.exists():
        print(f"ERROR: videoConfig.ts が無い: {VIDEO_CONFIG}", file=sys.stderr)
        return _emit_early("env_video_config_missing", 4)
    original = VIDEO_CONFIG.read_text(encoding="utf-8")

    results: list[dict] = []
    stills: list[Path] = []
    mismatched = 0  # dimension 不一致のみカウント (render/probe 失敗は exit 3 系で別扱い)
    env_error: str | None = None

    try:
        for fmt in formats:
            if env_error:
                break
            try:
                patched = patch_format(original, fmt)
            except ValueError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return _emit_early("usage_error_patch_format", 4, error=str(e))
            VIDEO_CONFIG.write_text(patched, encoding="utf-8")
            print(f"\n[smoke] format={fmt} に切替て still を出力します")
            for frame in frames:
                png = out_dir / f"smoke_{fmt}_f{frame:04d}.png"
                try:
                    render_still(PROJ, frame, png)
                except subprocess.CalledProcessError as e:
                    # P1 #1: render 失敗は環境問題 (exit 3) として即終了
                    print(
                        f"  ERROR: remotion still failed (fmt={fmt}, frame={frame}): {e}",
                        file=sys.stderr,
                    )
                    env_error = "still_failed"
                    results.append(
                        {"format": fmt, "frame": frame, "ok": False, "error": "still_failed"}
                    )
                    break
                try:
                    w, h = probe_dim(png)
                except subprocess.CalledProcessError as e:
                    print(f"  ERROR: ffprobe failed for {png}: {e}", file=sys.stderr)
                    env_error = "probe_failed"
                    results.append(
                        {"format": fmt, "frame": frame, "ok": False, "error": "probe_failed"}
                    )
                    break
                expected = FORMAT_DIMS[fmt]
                ok = (w, h) == expected
                if not ok:
                    mismatched += 1
                results.append(
                    {
                        "format": fmt,
                        "frame": frame,
                        "ok": ok,
                        "expected": list(expected),
                        "actual": [w, h],
                        "png": str(png),
                    }
                )
                stills.append(png)
                marker = "OK" if ok else "MISMATCH"
                print(
                    f"  [{marker}] {png.name}: expected={expected[0]}x{expected[1]}, "
                    f"actual={w}x{h}"
                )
    finally:
        VIDEO_CONFIG.write_text(original, encoding="utf-8")
        print(f"\n[smoke] videoConfig.ts を原本に restore しました")

    grid_status = "skipped"
    grid_error: str | None = None
    full_matrix = len(stills) == len(formats) * len(frames) and not env_error
    if not args.no_grid and full_matrix:
        # P2 #5: full matrix の時のみ grid (部分失敗時は cell 対応が崩れるため)
        grid_out = out_dir / "grid.png"
        try:
            make_grid(stills, grid_out, formats, frames, label=grid_label)
            print(f"\n[smoke] grid: {grid_out}")
            grid_status = "ok"
        except subprocess.CalledProcessError as e:
            # P1 #2: grid 失敗は環境問題として exit 3 (silent WARN にしない)
            print(f"ERROR: ffmpeg grid 合成失敗: {e}", file=sys.stderr)
            grid_status = "failed"
            grid_error = str(e)

    summary_path = out_dir / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "formats": formats,
                "frames": frames,
                "results": results,
                "mismatched": mismatched,
                "total": len(results),
                "env_error": env_error,
                "grid": {"status": grid_status, "error": grid_error},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nsummary: {summary_path}")
    print(f"  total={len(results)}, mismatched={mismatched}, env_error={env_error}, grid={grid_status}")

    # Phase 3 obs migration step 3: --json-log で v1 status を stdout 末尾に追加。
    # summary.json は artifact channel で別 (引き続き file 出力)。
    duration_ms = int((time.monotonic() - start_time) * 1000)
    artifacts = [
        {
            "path": safe_artifact_path(
                summary_path, project_root=PROJ,
                unsafe_keep_abs_path=args.unsafe_keep_abs_path,
            ),
            "kind": "json",
        },
    ]
    if grid_status == "ok":
        artifacts.append({
            "path": safe_artifact_path(
                out_dir / "grid.png", project_root=PROJ,
                unsafe_keep_abs_path=args.unsafe_keep_abs_path,
            ),
            "kind": "png",
        })

    redaction_rules = []
    if not args.unsafe_keep_abs_path:
        redaction_rules.append("abs_path")

    if env_error:
        v0 = "env_error"
        exit_code = 3
    elif grid_status == "failed":
        v0 = "grid_failed"
        exit_code = 3
    elif mismatched:
        v0 = "dimension_mismatch"
        exit_code = 2
    else:
        v0 = "smoke_ok"
        exit_code = 0

    payload = build_status(
        script="visual_smoke",
        v0_status=v0,
        exit_code=exit_code,
        counts={
            "total": len(results),
            "mismatched": mismatched,
            "formats_count": len(formats),
            "frames_count": len(frames),
        },
        artifacts=artifacts,
        cost=None,
        duration_ms=duration_ms,
        category_override="dimension-regression",
        redaction_rules=redaction_rules,
        run_id=run_ctx["run_id"],
        parent_run_id=run_ctx["parent_run_id"],
        step_id=run_ctx["step_id"],
        env_error=env_error,
        grid_status=grid_status,
    )
    _obs_emit_json(args.json_log, payload)
    return exit_code


if __name__ == "__main__":
    sys.exit(cli())
