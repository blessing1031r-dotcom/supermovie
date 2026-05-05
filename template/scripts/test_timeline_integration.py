#!/usr/bin/env python3
"""SuperMovie Phase 3-K integration smoke test (pure python).

template/scripts/timeline.py + 3 script (voicevox_narration / build_slide_data /
build_telop_data) の連鎖が破損していないか、engine / node_modules 不要で
unit test する。Phase 3-J で導入した timeline.py の前提を壊す変更があれば
失敗する。

Usage:
    python3 scripts/test_timeline_integration.py

Exit code:
    0 = 全 assertion pass
    1 = 1 件以上 fail (assertion error)、stderr に詳細
"""
from __future__ import annotations

import json
import struct
import sys
import tempfile
import wave
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import timeline  # noqa: E402


def make_videoconfig_ts(fps: int) -> str:
    return (
        "export type VideoFormat = 'youtube' | 'short' | 'square';\n"
        "export const FORMAT: VideoFormat = 'youtube';\n"
        f"export const FPS = {fps};\n"
        "export const SOURCE_DURATION_FRAMES = 1500;\n"
        "export const VIDEO_FILE = 'main.mp4';\n"
    )


def write_synthetic_wav(path: Path, duration_sec: float, framerate: int = 22050) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(framerate)
        n_frames = int(framerate * duration_sec)
        w.writeframes(struct.pack("<%dh" % n_frames, *[0] * n_frames))


def assert_eq(actual, expected, msg: str) -> None:
    if actual != expected:
        raise AssertionError(f"{msg}: expected {expected!r}, got {actual!r}")


def assert_raises(callable_, exc_type, msg: str):
    try:
        callable_()
    except exc_type:
        return
    raise AssertionError(f"{msg}: expected {exc_type.__name__} but no exception raised")


def test_fps_consistency() -> None:
    """3 script が timeline.read_video_config_fps を経由して同じ FPS を返す."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        (proj / "src").mkdir()
        (proj / "src" / "videoConfig.ts").write_text(make_videoconfig_ts(60))

        # timeline 直読
        assert_eq(timeline.read_video_config_fps(proj), 60, "timeline FPS read")

        # malformed 検出 (FPS 行なし)
        (proj / "src" / "videoConfig.ts").write_text("// no fps line\n")
        assert_eq(
            timeline.read_video_config_fps(proj, default=42),
            42,
            "malformed FPS fallback",
        )

        # FPS=0 を default に倒す
        (proj / "src" / "videoConfig.ts").write_text(
            "export const FPS = 0;\n"
        )
        assert_eq(timeline.read_video_config_fps(proj), timeline.DEFAULT_FPS, "FPS=0 fallback")


def test_vad_schema_validation() -> None:
    """VadSchemaError が部分破損を全て検出する."""
    # 非 dict
    assert_raises(
        lambda: timeline.validate_vad_schema("not dict"),
        timeline.VadSchemaError,
        "non-dict",
    )
    # speech_segments 非 list
    assert_raises(
        lambda: timeline.validate_vad_schema({"speech_segments": "wrong"}),
        timeline.VadSchemaError,
        "speech_segments non-list",
    )
    # segment 非 dict
    assert_raises(
        lambda: timeline.validate_vad_schema({"speech_segments": ["str"]}),
        timeline.VadSchemaError,
        "segment non-dict",
    )
    # start 型不正
    assert_raises(
        lambda: timeline.validate_vad_schema(
            {"speech_segments": [{"start": "bad", "end": 100}]}
        ),
        timeline.VadSchemaError,
        "start non-numeric",
    )
    # end 欠落
    assert_raises(
        lambda: timeline.validate_vad_schema({"speech_segments": [{"start": 0}]}),
        timeline.VadSchemaError,
        "end missing",
    )
    # start > end
    assert_raises(
        lambda: timeline.validate_vad_schema(
            {"speech_segments": [{"start": 100, "end": 50}]}
        ),
        timeline.VadSchemaError,
        "start > end",
    )
    # OK
    timeline.validate_vad_schema(
        {"speech_segments": [{"start": 0, "end": 1000}]}
    )


def test_ms_to_playback_frame() -> None:
    # No cut: 直接 ms→frame
    assert_eq(timeline.ms_to_playback_frame(0, 30, []), 0, "no-cut start")
    assert_eq(timeline.ms_to_playback_frame(1000, 30, []), 30, "no-cut 1s")
    assert_eq(timeline.ms_to_playback_frame(500, 30, []), 15, "no-cut 0.5s")

    # With cut: gap removed
    cut_segs = [
        {"originalStartMs": 0, "originalEndMs": 500, "playbackStart": 0, "playbackEnd": 15},
        {"originalStartMs": 1500, "originalEndMs": 3000, "playbackStart": 15, "playbackEnd": 60},
    ]
    assert_eq(timeline.ms_to_playback_frame(200, 30, cut_segs), 6, "cut seg0 200ms")
    assert_eq(timeline.ms_to_playback_frame(1500, 30, cut_segs), 15, "cut seg1 start")
    assert_eq(timeline.ms_to_playback_frame(2000, 30, cut_segs), 30, "cut seg1 +500ms")
    # 800ms: gap (excluded)
    assert_eq(timeline.ms_to_playback_frame(800, 30, cut_segs), None, "cut gap excluded")


def test_load_cut_segments_fail_fast() -> None:
    """fail_fast=True で部分破損を raise する."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        (proj / "vad_result.json").write_text(
            json.dumps({"speech_segments": [{"start": 100}]})  # end missing
        )
        # default fail_fast=False で []
        assert_eq(timeline.load_cut_segments(proj, 30, fail_fast=False), [], "soft fail")
        # fail_fast=True で raise
        assert_raises(
            lambda: timeline.load_cut_segments(proj, 30, fail_fast=True),
            timeline.VadSchemaError,
            "fail_fast raise",
        )


def test_transcript_segment_validation() -> None:
    """validate_transcript_segment が壊れた transcript を検出する."""
    # OK: timing なし (--script の chunk)
    timeline.validate_transcript_segment({"text": "hi"}, 0)
    # OK: 通常 transcript
    timeline.validate_transcript_segment({"text": "hi", "start": 0, "end": 1000}, 0)
    # NG: start > end
    assert_raises(
        lambda: timeline.validate_transcript_segment(
            {"text": "hi", "start": 1000, "end": 500}, 0
        ),
        timeline.TranscriptSegmentError,
        "transcript start>end",
    )
    # NG: text 非 str
    assert_raises(
        lambda: timeline.validate_transcript_segment({"text": 123}, 0),
        timeline.TranscriptSegmentError,
        "text non-str",
    )
    # NG: start 型不正
    assert_raises(
        lambda: timeline.validate_transcript_segment(
            {"text": "hi", "start": "bad"}, 0
        ),
        timeline.TranscriptSegmentError,
        "start non-numeric",
    )

    # Phase 3-L (Codex Phase 3-J review Part B 推奨): require_timing=True で
    # start/end 必須化、欠落 / None で raise。
    assert_raises(
        lambda: timeline.validate_transcript_segment(
            {"text": "hi"}, 0, require_timing=True
        ),
        timeline.TranscriptSegmentError,
        "require_timing missing both",
    )
    assert_raises(
        lambda: timeline.validate_transcript_segment(
            {"text": "hi", "start": 0, "end": None}, 0, require_timing=True
        ),
        timeline.TranscriptSegmentError,
        "require_timing end None",
    )
    # OK: require_timing=True + 両方 numeric
    timeline.validate_transcript_segment(
        {"text": "hi", "start": 0, "end": 1000}, 0, require_timing=True
    )

    # validate_transcript_segments 一括 helper
    out = timeline.validate_transcript_segments(
        [{"text": "a", "start": 0, "end": 100}, {"text": "b", "start": 100, "end": 200}],
        require_timing=True,
    )
    assert_eq(len(out), 2, "validate_transcript_segments OK length")
    # 非 list で raise
    assert_raises(
        lambda: timeline.validate_transcript_segments("not a list"),
        timeline.TranscriptSegmentError,
        "validate_transcript_segments non-list",
    )


def test_voicevox_collect_chunks_validation() -> None:
    """voicevox_narration.collect_chunks が壊れた transcript で raise する."""
    import voicevox_narration as vn

    class Args:
        script = None
        script_json = None

    bad = {"segments": [{"text": "hi", "start": 100, "end": 50}]}
    assert_raises(
        lambda: vn.collect_chunks(Args(), bad),
        vn.TranscriptSegmentError,
        "voicevox start>end transcript",
    )

    good = {"segments": [{"text": "hi", "start": 0, "end": 1000}]}
    out = vn.collect_chunks(Args(), good)
    assert_eq(len(out), 1, "voicevox good transcript len")
    assert_eq(out[0]["sourceStartMs"], 0, "voicevox sourceStartMs")
    assert_eq(out[0]["sourceEndMs"], 1000, "voicevox sourceEndMs")

    # Codex Phase 3-J review P2 #1 反映: validate を `.get(...).strip()` 前に通す
    # 非 dict segment → TranscriptSegmentError
    assert_raises(
        lambda: vn.collect_chunks(Args(), {"segments": ["not a dict"]}),
        vn.TranscriptSegmentError,
        "voicevox non-dict segment",
    )
    # segments 非 list → TranscriptSegmentError
    assert_raises(
        lambda: vn.collect_chunks(Args(), {"segments": "wrong"}),
        vn.TranscriptSegmentError,
        "voicevox non-list segments",
    )
    # text 非 str (int) → TranscriptSegmentError
    assert_raises(
        lambda: vn.collect_chunks(Args(), {"segments": [{"text": 123}]}),
        vn.TranscriptSegmentError,
        "voicevox text non-str",
    )
    # text=None は filter (空文字列と同じ扱い、空 list 返す)
    assert_eq(
        vn.collect_chunks(Args(), {"segments": [{"text": None, "start": 0, "end": 100}]}),
        [],
        "voicevox text=None filtered",
    )


def test_voicevox_write_order_narrationdata_before_wav() -> None:
    """Phase 3-N race fix regression: write 順序 narrationData.ts → narration.wav.

    旧順序 (narration.wav 先) では Studio hot-reload で legacy 経路に一瞬流れる
    race があった (Codex CODEX_REVIEW_PHASE3N_AND_3O P2 #1)。新順序を保証する
    ため、本 test は call order を直接 verify する:

    1. main() を temp project + module-level state monkey-patch で実行
    2. concat_wavs_atomic を「narrationData.ts 存在を assert する mock」で
       置換、call 時点で narrationData.ts populated でないなら raise
    3. 旧順序に戻れば assert で必ず落ちる

    Codex Phase 3-O fix re-review P2 #2 反映 (旧 test は逆順でも通る)。
    """
    import voicevox_narration as vn

    state = {
        "PROJ": vn.PROJ,
        "NARRATION_DIR": vn.NARRATION_DIR,
        "NARRATION_DATA_TS": vn.NARRATION_DATA_TS,
        "CHUNK_META_JSON": vn.CHUNK_META_JSON,
        "NARRATION_LEGACY_WAV": vn.NARRATION_LEGACY_WAV,
    }
    original_concat = vn.concat_wavs_atomic
    original_check_engine = vn.check_engine
    original_synthesize = vn.synthesize

    try:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            vn.PROJ = proj
            vn.NARRATION_DIR = proj / "public" / "narration"
            vn.NARRATION_DATA_TS = proj / "src" / "Narration" / "narrationData.ts"
            vn.CHUNK_META_JSON = vn.NARRATION_DIR / "chunk_meta.json"
            vn.NARRATION_LEGACY_WAV = proj / "public" / "narration.wav"
            (proj / "src" / "Narration").mkdir(parents=True)
            (proj / "src" / "videoConfig.ts").write_text(
                make_videoconfig_ts(30), encoding="utf-8"
            )
            # transcript で 1 chunk 用意
            (proj / "transcript_fixed.json").write_text(
                json.dumps(
                    {"segments": [{"text": "hi", "start": 0, "end": 1000}]}
                ),
                encoding="utf-8",
            )

            # engine OK + synthesize stub (synthetic 22050Hz mono WAV bytes)
            import wave
            import io

            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                import struct
                w.writeframes(struct.pack("<22050h", *([0] * 22050)))
            wav_bytes = buf.getvalue()

            vn.check_engine = lambda: (True, "0.0.0-test")
            vn.synthesize = lambda text, speaker: wav_bytes

            # concat_wavs_atomic を「narrationData.ts populated を assert + raise」に
            order_check_log = []

            def assert_concat_after_narrationdata(wavs, out_path):
                # narrationData.ts 存在 + 空 array でないことを確認
                if not vn.NARRATION_DATA_TS.exists():
                    order_check_log.append("FAIL: narrationData.ts not created before concat")
                    raise RuntimeError("write order regression: narrationData.ts missing")
                content = vn.NARRATION_DATA_TS.read_text(encoding="utf-8")
                if "narration/chunk_000.wav" not in content:
                    order_check_log.append(
                        f"FAIL: narrationData.ts not populated before concat: {content[:100]}"
                    )
                    raise RuntimeError("write order regression: narrationData.ts empty")
                order_check_log.append("OK: narrationData.ts populated before concat")
                # 失敗を演じて rollback path を起動 (P1 fix の Exception catch)
                raise PermissionError("simulated permission error")

            vn.concat_wavs_atomic = assert_concat_after_narrationdata

            # main() を実行、concat で失敗 → exit 6 期待
            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = ["voicevox_narration.py"]
            try:
                ret = vn.main()
            finally:
                _sys.argv = old_argv

            # call order assertion
            if not order_check_log:
                raise AssertionError("concat mock not invoked (main() flow regression)")
            if "OK:" not in order_check_log[0]:
                raise AssertionError(
                    f"write order regression detected: {order_check_log}"
                )
            # exit code: rollback path の return 6 を期待 (Codex P1 fix が動作)
            assert_eq(ret, 6, "concat failure → exit 6 (P1 rollback)")
            # rollback 後: narrationData.ts は empty に戻り、chunks 削除済み
            content = vn.NARRATION_DATA_TS.read_text(encoding="utf-8")
            if "export const narrationData: NarrationSegment[] = []" not in content:
                raise AssertionError(
                    f"rollback failed: narrationData.ts not empty: {content[:100]}"
                )
            chunk_files = list(vn.NARRATION_DIR.glob("chunk_*.wav"))
            if chunk_files:
                raise AssertionError(f"rollback failed: chunks left: {chunk_files}")
    finally:
        for k, v in state.items():
            setattr(vn, k, v)
        vn.concat_wavs_atomic = original_concat
        vn.check_engine = original_check_engine
        vn.synthesize = original_synthesize


def test_voicevox_write_narration_data_alignment() -> None:
    """transcript timing alignment が cut-aware で正しく動く end-to-end."""
    import voicevox_narration as vn

    # Codex Phase 3-L re-review P3 #2 反映: module-level state を try/finally で
    # restore (test 間 leak 防止、後続 voicevox test 追加時の汚染回避)。
    original_proj = vn.PROJ
    original_narration_dir = vn.NARRATION_DIR
    original_narration_data_ts = vn.NARRATION_DATA_TS
    original_chunk_meta_json = vn.CHUNK_META_JSON
    original_narration_legacy_wav = vn.NARRATION_LEGACY_WAV

    try:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            vn.PROJ = proj
            vn.NARRATION_DIR = proj / "public" / "narration"
            vn.NARRATION_DATA_TS = proj / "src" / "Narration" / "narrationData.ts"
            vn.CHUNK_META_JSON = vn.NARRATION_DIR / "chunk_meta.json"
            vn.NARRATION_LEGACY_WAV = proj / "public" / "narration.wav"
            vn.NARRATION_DIR.mkdir(parents=True)
            vn.NARRATION_DATA_TS.parent.mkdir(parents=True)

            write_synthetic_wav(vn.NARRATION_DIR / "chunk_000.wav", 1.0)
            write_synthetic_wav(vn.NARRATION_DIR / "chunk_001.wav", 0.5)

            # No cut, transcript timing 0ms と 1000ms
            chunks_data = [
                (vn.NARRATION_DIR / "chunk_000.wav", "first", 0, 1000),
                (vn.NARRATION_DIR / "chunk_001.wav", "second", 1000, 1500),
            ]
            segments, ts_path, meta_path = vn.write_narration_data(chunks_data, 30, [])
            assert_eq(segments[0]["startFrame"], 0, "chunk0 startFrame")
            assert_eq(segments[0]["durationInFrames"], 30, "chunk0 dur")
            assert_eq(segments[0]["timing_source"], "transcript_aligned", "chunk0 source")
            assert_eq(segments[1]["startFrame"], 30, "chunk1 startFrame (1000ms*30fps)")

            # Verify TS file is valid
            ts = ts_path.read_text(encoding="utf-8")
            assert "narrationData" in ts
            assert "sourceStartMs: 0" in ts
            assert "sourceStartMs: 1000" in ts
    finally:
        vn.PROJ = original_proj
        vn.NARRATION_DIR = original_narration_dir
        vn.NARRATION_DATA_TS = original_narration_data_ts
        vn.CHUNK_META_JSON = original_chunk_meta_json
        vn.NARRATION_LEGACY_WAV = original_narration_legacy_wav


def _setup_temp_project(tmp: Path, fps: int = 30) -> Path:
    """build_slide / build_telop が読む最小ファイルを temp project に揃える."""
    (tmp / "src").mkdir(parents=True, exist_ok=True)
    (tmp / "src" / "videoConfig.ts").write_text(make_videoconfig_ts(fps))
    (tmp / "src" / "Slides").mkdir(parents=True, exist_ok=True)
    (tmp / "src" / "テロップテンプレート").mkdir(parents=True, exist_ok=True)
    return tmp


def test_build_slide_data_main_e2e() -> None:
    """build_slide_data.py を temp project で main() 実行、SlideData.ts 出力を検証.

    Codex Phase 3-L 推奨 vi 反映: integration_smoke を build_slide にも展開。
    monkey-patch (PROJ / FPS) で in-process 実行。
    """
    import importlib
    import build_slide_data as bsd

    with tempfile.TemporaryDirectory() as tmp:
        proj = _setup_temp_project(Path(tmp))
        # 通常 transcript: 2 segments
        (proj / "transcript_fixed.json").write_text(
            json.dumps(
                {
                    "duration_ms": 5000,
                    "text": "test",
                    "segments": [
                        {"text": "hello", "start": 0, "end": 2000},
                        {"text": "world", "start": 2000, "end": 4000},
                    ],
                    "words": [],
                }
            ),
            encoding="utf-8",
        )
        (proj / "project-config.json").write_text(
            json.dumps({"format": "short", "tone": "プロフェッショナル"}),
            encoding="utf-8",
        )

        # monkey-patch PROJ + FPS (import time に固定されるため re-binding 必要)
        original_proj = bsd.PROJ
        original_fps = bsd.FPS
        bsd.PROJ = proj
        bsd.FPS = 30
        try:
            # main() を直接呼出 (引数は空 → topic mode default)
            import sys as _sys

            old_argv = _sys.argv
            _sys.argv = ["build_slide_data.py"]
            try:
                bsd.main()
            finally:
                _sys.argv = old_argv

            # slideData.ts が生成されたか
            slide_ts = proj / "src" / "Slides" / "slideData.ts"
            if not slide_ts.exists():
                raise AssertionError(f"slideData.ts not generated at {slide_ts}")
            content = slide_ts.read_text(encoding="utf-8")
            if "slideData" not in content:
                raise AssertionError(f"slideData.ts does not export slideData: {content[:100]}")
        finally:
            bsd.PROJ = original_proj
            bsd.FPS = original_fps


def test_build_slide_data_validates_bad_transcript() -> None:
    """build_slide_data.py が壊れた transcript で SystemExit する.

    Codex 21:46 PR6 review P1 fix で SystemExit message → stderr + sys.exit(_emit_error(...))
    に変更したため、test も stderr 経由 assertion に更新。
    """
    import build_slide_data as bsd
    import contextlib as _contextlib
    import io as _io

    with tempfile.TemporaryDirectory() as tmp:
        proj = _setup_temp_project(Path(tmp))
        # 壊れた transcript: start > end
        (proj / "transcript_fixed.json").write_text(
            json.dumps(
                {
                    "segments": [{"text": "hi", "start": 1000, "end": 500}],
                    "words": [],
                }
            ),
            encoding="utf-8",
        )
        (proj / "project-config.json").write_text(
            json.dumps({"format": "short", "tone": "プロフェッショナル"}),
            encoding="utf-8",
        )

        original_proj = bsd.PROJ
        bsd.PROJ = proj
        try:
            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = ["build_slide_data.py"]
            try:
                err_buf = _io.StringIO()
                with _contextlib.redirect_stderr(err_buf):
                    try:
                        bsd.main()
                        raise AssertionError("build_slide_data should fail with bad transcript")
                    except SystemExit as e:
                        # 期待: validation error は stderr に出力 + exit code 3
                        if e.code != 3:
                            raise AssertionError(f"Expected exit code 3, got: {e.code}")
                err_text = err_buf.getvalue()
                if "transcript validation failed" not in err_text:
                    raise AssertionError(
                        f"Expected validation error in stderr, got: {err_text!r}"
                    )
            finally:
                _sys.argv = old_argv
        finally:
            bsd.PROJ = original_proj


def test_build_telop_data_main_e2e() -> None:
    """build_telop_data.py を temp project で main() 実行、call_budoux stub.

    Codex Phase 3-L 推奨 vi 拡張 (Phase 3-M 候補 i): build_telop も e2e。
    Node 依存の budoux_split.mjs は deterministic stub に差し替え。
    """
    import build_telop_data as btd

    with tempfile.TemporaryDirectory() as tmp:
        proj = _setup_temp_project(Path(tmp))
        (proj / "transcript_fixed.json").write_text(
            json.dumps(
                {
                    "duration_ms": 5000,
                    "text": "test",
                    "segments": [
                        {"text": "こんにちは世界", "start": 0, "end": 2000},
                        {"text": "さようなら空", "start": 2000, "end": 4000},
                    ],
                    "words": [],
                }
            ),
            encoding="utf-8",
        )
        (proj / "vad_result.json").write_text(
            json.dumps(
                {"speech_segments": [{"start": 0, "end": 4000}]}
            ),
            encoding="utf-8",
        )

        # call_budoux stub: text を 4文字毎に分割した phrases に変換
        def stub_call_budoux(seg_texts):
            return [
                [t[i : i + 4] for i in range(0, len(t), 4)] or [t]
                for t in seg_texts
            ]

        original_proj = btd.PROJ
        original_call = btd.call_budoux
        btd.PROJ = proj
        btd.call_budoux = stub_call_budoux
        try:
            import sys as _sys

            old_argv = _sys.argv
            _sys.argv = ["build_telop_data.py"]
            try:
                btd.main()
            finally:
                _sys.argv = old_argv
            # telopData.ts が生成されたか
            telop_ts = proj / "src" / "テロップテンプレート" / "telopData.ts"
            if not telop_ts.exists():
                raise AssertionError(f"telopData.ts not generated at {telop_ts}")
            content = telop_ts.read_text(encoding="utf-8")
            if "telopData" not in content:
                raise AssertionError(
                    f"telopData.ts does not export telopData: {content[:100]}"
                )
        finally:
            btd.PROJ = original_proj
            btd.call_budoux = original_call


def test_build_telop_data_validates_bad_transcript() -> None:
    """build_telop_data.py が壊れた transcript で SystemExit する.

    Codex 21:46 PR6 review P1 fix で stderr + sys.exit(_emit_error(...)) に変更。
    """
    import build_telop_data as btd
    import contextlib as _contextlib
    import io as _io

    with tempfile.TemporaryDirectory() as tmp:
        proj = _setup_temp_project(Path(tmp))
        (proj / "transcript_fixed.json").write_text(
            json.dumps(
                {
                    "segments": [{"text": "hi", "start": 1000, "end": 500}],
                    "words": [],
                }
            ),
            encoding="utf-8",
        )
        (proj / "vad_result.json").write_text(
            json.dumps({"speech_segments": [{"start": 0, "end": 1000}]}),
            encoding="utf-8",
        )

        original_proj = btd.PROJ
        original_call = btd.call_budoux
        btd.PROJ = proj
        # call_budoux stub (validation 前で raise されるので invoke されない想定)
        btd.call_budoux = lambda x: [["dummy"] for _ in x]
        try:
            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = ["build_telop_data.py"]
            try:
                err_buf = _io.StringIO()
                with _contextlib.redirect_stderr(err_buf):
                    try:
                        btd.main()
                        raise AssertionError(
                            "build_telop_data should fail with bad transcript"
                        )
                    except SystemExit as e:
                        if e.code != 3:
                            raise AssertionError(f"Expected exit code 3, got: {e.code}")
                err_text = err_buf.getvalue()
                if "transcript validation failed" not in err_text:
                    raise AssertionError(
                        f"Expected validation error in stderr, got: {err_text!r}"
                    )
            finally:
                _sys.argv = old_argv
        finally:
            btd.PROJ = original_proj
            btd.call_budoux = original_call


def test_generate_slide_plan_skip_no_api_key() -> None:
    """generate_slide_plan.py: ANTHROPIC_API_KEY 未設定で exit 0 (skip)."""
    import generate_slide_plan as gsp
    import os as _os

    original_proj = gsp.PROJ
    with tempfile.TemporaryDirectory() as tmp:
        gsp.PROJ = Path(tmp)
        original_key = _os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = ["generate_slide_plan.py"]
            try:
                ret = gsp.main()
                assert_eq(ret, 0, "no-api-key skip exit 0")
            finally:
                _sys.argv = old_argv
        finally:
            if original_key is not None:
                _os.environ["ANTHROPIC_API_KEY"] = original_key
            gsp.PROJ = original_proj


def test_generate_slide_plan_missing_inputs() -> None:
    """generate_slide_plan.py: transcript / config 不在で exit 3."""
    import generate_slide_plan as gsp
    import os as _os

    original_proj = gsp.PROJ
    # Codex Phase 3-M review P2 #2 反映: 既存 ANTHROPIC_API_KEY を save、
    # finally で復元 (test 間の env leak 防止)。
    original_api_key = _os.environ.get("ANTHROPIC_API_KEY")
    with tempfile.TemporaryDirectory() as tmp:
        gsp.PROJ = Path(tmp)  # transcript_fixed.json / project-config.json なし
        _os.environ["ANTHROPIC_API_KEY"] = "test-key-fake"
        try:
            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = ["generate_slide_plan.py"]
            try:
                ret = gsp.main()
                assert_eq(ret, 3, "missing inputs exit 3")
            finally:
                _sys.argv = old_argv
        finally:
            if original_api_key is None:
                _os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                _os.environ["ANTHROPIC_API_KEY"] = original_api_key
            gsp.PROJ = original_proj


def test_generate_slide_plan_api_mock_success() -> None:
    """generate_slide_plan API mock: valid response → slide_plan.json 生成.

    Codex Phase 3-M cand iii の残置部分 (urllib mock + valid response 検証)。
    """
    import generate_slide_plan as gsp
    import os as _os
    import urllib.request as _urlreq

    fake_plan = {
        "version": gsp.PLAN_VERSION,
        "slides": [
            {
                "id": 1,
                "startWordIndex": 0,
                "endWordIndex": 0,
                "title": "テスト",
                "bullets": [],
                "align": "left",
            }
        ],
    }
    fake_response_body = json.dumps(
        {"content": [{"type": "text", "text": json.dumps(fake_plan, ensure_ascii=False)}]}
    ).encode("utf-8")

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self):
            return self._body

    def mock_urlopen(req, timeout=60):
        return FakeResponse(fake_response_body)

    original_urlopen = _urlreq.urlopen
    original_proj = gsp.PROJ
    original_api_key = _os.environ.get("ANTHROPIC_API_KEY")  # Codex P2 #2 反映

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        gsp.PROJ = proj
        (proj / "transcript_fixed.json").write_text(
            json.dumps(
                {
                    "words": [{"text": "hi", "start": 0, "end": 100}],
                    "segments": [{"text": "hi", "start": 0, "end": 100}],
                }
            ),
            encoding="utf-8",
        )
        (proj / "project-config.json").write_text(
            json.dumps({"format": "short", "tone": "プロ"}),
            encoding="utf-8",
        )

        _os.environ["ANTHROPIC_API_KEY"] = "fake-key"
        _urlreq.urlopen = mock_urlopen
        try:
            import sys as _sys
            old_argv = _sys.argv
            output_path = proj / "slide_plan.json"
            _sys.argv = ["generate_slide_plan.py", "--output", str(output_path)]
            try:
                ret = gsp.main()
                assert_eq(ret, 0, "API mock success exit 0")
                if not output_path.exists():
                    raise AssertionError(f"slide_plan.json not generated at {output_path}")
                plan = json.loads(output_path.read_text(encoding="utf-8"))
                assert_eq(plan["version"], gsp.PLAN_VERSION, "plan version")
                assert_eq(len(plan["slides"]), 1, "plan slides count")
            finally:
                _sys.argv = old_argv
        finally:
            if original_api_key is None:
                _os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                _os.environ["ANTHROPIC_API_KEY"] = original_api_key
            _urlreq.urlopen = original_urlopen
            gsp.PROJ = original_proj


def test_generate_slide_plan_api_rate_limited_429() -> None:
    """Phase 3-V P2 cost guard (Codex CODEX_P2_COST_GUARD_DESIGN §1 / §2.5):
    HTTP 429 は exit 9 (rate_limited) で分離、retry-after 表示。"""
    import generate_slide_plan as gsp
    import os as _os
    import urllib.error as _urlerr
    import urllib.request as _urlreq
    from io import BytesIO

    def mock_urlopen_429(req, timeout=60):
        raise _urlerr.HTTPError(
            "https://api.anthropic.com/v1/messages",
            429,
            "Rate Limit",
            {"retry-after": "30"},
            BytesIO(b'{"error": {"type": "rate_limit_error"}}'),
        )

    original_urlopen = _urlreq.urlopen
    original_proj = gsp.PROJ
    original_api_key = _os.environ.get("ANTHROPIC_API_KEY")

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        gsp.PROJ = proj
        (proj / "transcript_fixed.json").write_text(
            json.dumps({"words": [], "segments": []}),
            encoding="utf-8",
        )
        (proj / "project-config.json").write_text(
            json.dumps({"format": "short", "tone": "プロ"}),
            encoding="utf-8",
        )
        _os.environ["ANTHROPIC_API_KEY"] = "fake-key"
        _urlreq.urlopen = mock_urlopen_429
        try:
            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = ["generate_slide_plan.py"]
            try:
                ret = gsp.main()
                assert_eq(ret, 9, "API 429 → exit 9 (rate_limited)")
            finally:
                _sys.argv = old_argv
        finally:
            if original_api_key is None:
                _os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                _os.environ["ANTHROPIC_API_KEY"] = original_api_key
            _urlreq.urlopen = original_urlopen
            gsp.PROJ = original_proj


def test_generate_slide_plan_api_http_error_non_429() -> None:
    """Phase 3-V P2 cost guard: 429 以外の HTTP error は exit 4 維持 (api_http_error)."""
    import generate_slide_plan as gsp
    import os as _os
    import urllib.error as _urlerr
    import urllib.request as _urlreq
    from io import BytesIO

    def mock_urlopen_500(req, timeout=60):
        raise _urlerr.HTTPError(
            "https://api.anthropic.com/v1/messages",
            500,
            "Internal Server Error",
            {},
            BytesIO(b'{"error": {"type": "internal_error"}}'),
        )

    original_urlopen = _urlreq.urlopen
    original_proj = gsp.PROJ
    original_api_key = _os.environ.get("ANTHROPIC_API_KEY")

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        gsp.PROJ = proj
        (proj / "transcript_fixed.json").write_text(
            json.dumps({"words": [], "segments": []}),
            encoding="utf-8",
        )
        (proj / "project-config.json").write_text(
            json.dumps({"format": "short", "tone": "プロ"}),
            encoding="utf-8",
        )
        _os.environ["ANTHROPIC_API_KEY"] = "fake-key"
        _urlreq.urlopen = mock_urlopen_500
        try:
            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = ["generate_slide_plan.py"]
            try:
                ret = gsp.main()
                assert_eq(ret, 4, "API 500 → exit 4 (non-429 HTTP error)")
            finally:
                _sys.argv = old_argv
        finally:
            if original_api_key is None:
                _os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                _os.environ["ANTHROPIC_API_KEY"] = original_api_key
            _urlreq.urlopen = original_urlopen
            gsp.PROJ = original_proj


def test_generate_slide_plan_dry_run_no_api_key() -> None:
    """Phase 3-V P2: --dry-run は API key 不要、prompt 生成 + estimate JSON で exit 0."""
    import generate_slide_plan as gsp
    import os as _os
    import urllib.request as _urlreq
    import io as _io
    import contextlib

    def mock_urlopen_should_not_be_called(req, timeout=60):
        raise AssertionError("--dry-run で urlopen が呼ばれた (期待: API skip)")

    original_urlopen = _urlreq.urlopen
    original_proj = gsp.PROJ
    original_api_key = _os.environ.get("ANTHROPIC_API_KEY")

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        gsp.PROJ = proj
        (proj / "transcript_fixed.json").write_text(
            json.dumps({
                "words": [{"text": "hi", "start": 0, "end": 100}],
                "segments": [{"text": "hi", "start": 0, "end": 100}],
            }),
            encoding="utf-8",
        )
        (proj / "project-config.json").write_text(
            json.dumps({"format": "short", "tone": "プロ"}),
            encoding="utf-8",
        )
        _os.environ.pop("ANTHROPIC_API_KEY", None)  # API key unset
        _urlreq.urlopen = mock_urlopen_should_not_be_called
        try:
            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = ["generate_slide_plan.py", "--dry-run"]
            captured = _io.StringIO()
            try:
                with contextlib.redirect_stdout(captured):
                    ret = gsp.main()
                assert_eq(ret, 0, "dry-run exit 0")
                stdout = captured.getvalue()
                lines = [ln for ln in stdout.splitlines() if ln.strip()]
                payload = json.loads(lines[-1])
                assert_eq(payload.get("status"), "dry_run", "dry-run status field")
                assert_eq(payload.get("api_called"), False, "api_called=false")
                if "estimated_input_tokens" not in payload:
                    raise AssertionError(f"missing estimated_input_tokens: {payload}")
                if payload.get("estimation_method") != "ceil(prompt_chars/4)":
                    raise AssertionError(
                        f"unexpected estimation_method: {payload.get('estimation_method')}"
                    )
                # rate 未設定なら cost null
                assert_eq(
                    payload.get("estimated_cost_usd_upper_bound"), None,
                    "no rate → cost null",
                )
            finally:
                _sys.argv = old_argv
        finally:
            if original_api_key is None:
                _os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                _os.environ["ANTHROPIC_API_KEY"] = original_api_key
            _urlreq.urlopen = original_urlopen
            gsp.PROJ = original_proj


def test_generate_slide_plan_max_tokens_override_cli_env_precedence() -> None:
    """Phase 3-V P2: --max-tokens CLI > env > default、body に反映."""
    import generate_slide_plan as gsp
    import os as _os
    import urllib.request as _urlreq
    from io import BytesIO

    captured_body = {}

    def mock_urlopen_capture(req, timeout=60):
        captured_body["data"] = json.loads(req.data.decode("utf-8"))
        # success response
        resp_text = json.dumps({
            "content": [{"type": "text", "text": json.dumps({
                "version": "supermovie.slide_plan.v1",
                "slides": [{"id": 1, "startWordIndex": 0, "endWordIndex": 0, "title": "t"}],
            })}]
        })
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return resp_text.encode("utf-8")
        return FakeResp()

    original_urlopen = _urlreq.urlopen
    original_proj = gsp.PROJ
    original_api_key = _os.environ.get("ANTHROPIC_API_KEY")
    original_env_max = _os.environ.get("SUPERMOVIE_MAX_TOKENS")

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        gsp.PROJ = proj
        (proj / "transcript_fixed.json").write_text(
            json.dumps({"words": [{"text": "x"}], "segments": []}),
            encoding="utf-8",
        )
        (proj / "project-config.json").write_text(
            json.dumps({"format": "short"}), encoding="utf-8",
        )
        _os.environ["ANTHROPIC_API_KEY"] = "fake"
        _urlreq.urlopen = mock_urlopen_capture
        try:
            # ケース 1: CLI override (--max-tokens=1000、env 設定あっても CLI 勝ち)
            _os.environ["SUPERMOVIE_MAX_TOKENS"] = "2000"
            import sys as _sys
            old_argv = _sys.argv
            output_path = proj / "slide_plan.json"
            _sys.argv = [
                "generate_slide_plan.py",
                "--max-tokens", "1000",
                "--output", str(output_path),
            ]
            try:
                gsp.main()
                assert_eq(captured_body["data"].get("max_tokens"), 1000, "CLI override")
            finally:
                _sys.argv = old_argv
            # ケース 2: env のみ (CLI なし)
            captured_body.clear()
            _sys.argv = [
                "generate_slide_plan.py",
                "--output", str(output_path),
            ]
            try:
                gsp.main()
                assert_eq(captured_body["data"].get("max_tokens"), 2000, "env value applied")
            finally:
                _sys.argv = old_argv
            # ケース 3: env も CLI もなし → default 4096
            _os.environ.pop("SUPERMOVIE_MAX_TOKENS", None)
            captured_body.clear()
            _sys.argv = [
                "generate_slide_plan.py",
                "--output", str(output_path),
            ]
            try:
                gsp.main()
                assert_eq(captured_body["data"].get("max_tokens"), 4096, "default 4096")
            finally:
                _sys.argv = old_argv
        finally:
            if original_api_key is None:
                _os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                _os.environ["ANTHROPIC_API_KEY"] = original_api_key
            if original_env_max is None:
                _os.environ.pop("SUPERMOVIE_MAX_TOKENS", None)
            else:
                _os.environ["SUPERMOVIE_MAX_TOKENS"] = original_env_max
            _urlreq.urlopen = original_urlopen
            gsp.PROJ = original_proj


def test_generate_slide_plan_max_tokens_cap_rejects() -> None:
    """Phase 3-V P2: --max-tokens=16385 → cap 超過で exit 4."""
    import generate_slide_plan as gsp
    import os as _os

    original_proj = gsp.PROJ
    original_api_key = _os.environ.get("ANTHROPIC_API_KEY")

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        gsp.PROJ = proj
        # 入力ファイル不要 (cost guard arg validation で先に exit 4)
        _os.environ["ANTHROPIC_API_KEY"] = "fake"
        try:
            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = ["generate_slide_plan.py", "--max-tokens", "16385"]
            try:
                ret = gsp.main()
                assert_eq(ret, 4, "max-tokens 16385 (cap+1) → exit 4")
            finally:
                _sys.argv = old_argv
        finally:
            if original_api_key is None:
                _os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                _os.environ["ANTHROPIC_API_KEY"] = original_api_key
            gsp.PROJ = original_proj


def test_generate_slide_plan_json_log_status_path() -> None:
    """Phase 3-V P3 logging 拡張 (Codex P2 design §4): --json-log で全 return path に
    status / exit_code 付き JSON emit を確認 (success / api_key_skipped 2 path)."""
    import generate_slide_plan as gsp
    import os as _os
    import urllib.request as _urlreq
    import io as _io
    import contextlib

    def mock_urlopen_success(req, timeout=60):
        resp_text = json.dumps({
            "content": [{"type": "text", "text": json.dumps({
                "version": "supermovie.slide_plan.v1",
                "slides": [{"id": 1, "startWordIndex": 0, "endWordIndex": 0, "title": "t"}],
            })}]
        })
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return resp_text.encode("utf-8")
        return FakeResp()

    original_urlopen = _urlreq.urlopen
    original_proj = gsp.PROJ
    original_api_key = _os.environ.get("ANTHROPIC_API_KEY")

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        gsp.PROJ = proj
        (proj / "transcript_fixed.json").write_text(
            json.dumps({"words": [{"text": "x"}], "segments": []}),
            encoding="utf-8",
        )
        (proj / "project-config.json").write_text(
            json.dumps({"format": "short"}), encoding="utf-8",
        )
        # ケース 1: success path with --json-log
        _os.environ["ANTHROPIC_API_KEY"] = "fake"
        _urlreq.urlopen = mock_urlopen_success
        try:
            captured = _io.StringIO()
            import sys as _sys
            old_argv = _sys.argv
            output_path = proj / "slide_plan.json"
            _sys.argv = [
                "generate_slide_plan.py",
                "--json-log",
                "--output", str(output_path),
            ]
            try:
                with contextlib.redirect_stdout(captured):
                    ret = gsp.main()
                assert_eq(ret, 0, "success exit 0")
                lines = [ln for ln in captured.getvalue().splitlines() if ln.strip()]
                payload = json.loads(lines[-1])
                # Phase 3 obs migration core: v0 `success` → v1 `ok` (category=None)
                assert_eq(payload.get("status"), "ok", "success status field (v1)")
                assert payload.get("category") is None, f"success category should be None: {payload.get('category')}"
                assert_eq(payload.get("exit_code"), 0, "success exit_code")
                assert_eq(payload.get("schema_version"), 1, "v1 schema_version")
                if "slides" not in payload:
                    raise AssertionError(f"missing slides in success JSON: {payload}")
                # Phase 3-V P3 review fix (CODEX_P3_SLIDE_PLAN_REVIEW P3): 既存 human
                # stdout (wrote: / slides:) も維持されていることを assert
                if not any(ln.startswith("wrote:") for ln in lines):
                    raise AssertionError(
                        f"existing 'wrote:' stdout missing under --json-log: {lines}"
                    )
                if not any(ln.startswith("slides:") for ln in lines):
                    raise AssertionError(
                        f"existing 'slides:' stdout missing under --json-log: {lines}"
                    )
            finally:
                _sys.argv = old_argv

            # ケース 1b: --json-log なし success → JSON 行が増えない (既存 stdout 完全互換)
            captured.seek(0)
            captured.truncate()
            _sys.argv = [
                "generate_slide_plan.py",
                "--output", str(output_path),
            ]
            try:
                with contextlib.redirect_stdout(captured):
                    ret = gsp.main()
                assert_eq(ret, 0, "no-flag success exit 0")
                lines = [ln for ln in captured.getvalue().splitlines() if ln.strip()]
                # 末尾に status JSON が出ていない (既存 print のみ)
                last = lines[-1] if lines else ""
                # last は "slides: 1" などの human-readable line のはず、JSON ではない
                if last.startswith("{") and last.endswith("}"):
                    try:
                        parsed = json.loads(last)
                        if "status" in parsed and "exit_code" in parsed:
                            raise AssertionError(
                                f"--json-log なしで status JSON が emit された: {last!r}"
                            )
                    except json.JSONDecodeError:
                        pass  # 偶然 {} で囲まれているだけならパス
            finally:
                _sys.argv = old_argv

            # ケース 2: api_key skip path with --json-log
            captured.seek(0)
            captured.truncate()
            _os.environ.pop("ANTHROPIC_API_KEY", None)
            _sys.argv = ["generate_slide_plan.py", "--json-log"]
            try:
                with contextlib.redirect_stdout(captured):
                    ret = gsp.main()
                assert_eq(ret, 0, "api_key_skipped exit 0")
                lines = [ln for ln in captured.getvalue().splitlines() if ln.strip()]
                payload = json.loads(lines[-1])
                # Phase 3 obs migration core: v0 `api_key_skipped` → v1 `skipped` + category=`api_key_missing`
                assert_eq(payload.get("status"), "skipped", "api_key skip status field (v1)")
                assert_eq(payload.get("category"), "api_key_missing", "api_key skip category")
                assert_eq(payload.get("exit_code"), 0, "skip exit_code")
                assert_eq(payload.get("schema_version"), 1, "v1 schema_version")
            finally:
                _sys.argv = old_argv
        finally:
            if original_api_key is None:
                _os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                _os.environ["ANTHROPIC_API_KEY"] = original_api_key
            _urlreq.urlopen = original_urlopen
            gsp.PROJ = original_proj


def test_generate_slide_plan_skip_preserves_with_bad_env() -> None:
    """Phase 3-V P2 review P1 fix (CODEX_P2_COST_GUARD_REVIEW:3-5):
    API key 未設定 skip は cost guard env 解決より前に実行される (既存挙動維持)。

    SUPERMOVIE_MAX_TOKENS=bad のような壊れた env でも、API key unset なら
    cost guard 解決を skip して exit 0 で抜ける。
    """
    import generate_slide_plan as gsp
    import os as _os

    original_proj = gsp.PROJ
    original_api_key = _os.environ.get("ANTHROPIC_API_KEY")
    original_env_max = _os.environ.get("SUPERMOVIE_MAX_TOKENS")

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        gsp.PROJ = proj
        _os.environ.pop("ANTHROPIC_API_KEY", None)
        _os.environ["SUPERMOVIE_MAX_TOKENS"] = "not-an-int"
        try:
            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = ["generate_slide_plan.py"]
            try:
                ret = gsp.main()
                assert_eq(ret, 0, "API key unset + bad env → skip 0 (P1 fix)")
            finally:
                _sys.argv = old_argv
        finally:
            if original_api_key is None:
                _os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                _os.environ["ANTHROPIC_API_KEY"] = original_api_key
            if original_env_max is None:
                _os.environ.pop("SUPERMOVIE_MAX_TOKENS", None)
            else:
                _os.environ["SUPERMOVIE_MAX_TOKENS"] = original_env_max
            gsp.PROJ = original_proj


def test_generate_slide_plan_rate_rejects_nan_inf() -> None:
    """Phase 3-V P2 review P2 fix (CODEX_P2_COST_GUARD_REVIEW:7-9):
    rate-input/rate-output の nan/inf を拒否 (math.isfinite check)、exit 4."""
    import generate_slide_plan as gsp
    import os as _os

    # Codex 22:05 PR7 review P2 fix: v1 env も clear/restore 対象に追加 (alias 動作で
    # v1 valid + v0 nan の場合 exit 3 になるため、test 環境を clean にする必要)。
    RATE_ENVS = (
        "SUPERMOVIE_RATE_INPUT_PER_MTOK",
        "SUPERMOVIE_RATE_OUTPUT_PER_MTOK",
        "SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK",
        "SUPERMOVIE_RATE_ANTHROPIC_OUTPUT_USD_PER_MTOK",
    )

    original_proj = gsp.PROJ
    original_api_key = _os.environ.get("ANTHROPIC_API_KEY")
    saved_rate_envs = {k: _os.environ.get(k) for k in RATE_ENVS}

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        gsp.PROJ = proj
        # 入力ファイル不要 (cost guard arg validation で先に exit 4)
        _os.environ["ANTHROPIC_API_KEY"] = "fake"
        # v1 / v0 env を全 clear (alias 動作の干渉を避ける)
        for k in RATE_ENVS:
            _os.environ.pop(k, None)
        try:
            # CLI nan
            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = ["generate_slide_plan.py", "--rate-input", "nan"]
            try:
                ret = gsp.main()
                assert_eq(ret, 4, "--rate-input=nan → exit 4")
            finally:
                _sys.argv = old_argv
            # CLI inf
            _sys.argv = ["generate_slide_plan.py", "--rate-input", "inf"]
            try:
                ret = gsp.main()
                assert_eq(ret, 4, "--rate-input=inf → exit 4")
            finally:
                _sys.argv = old_argv
            # env nan via v0 alias SUPERMOVIE_RATE_OUTPUT_PER_MTOK
            _os.environ["SUPERMOVIE_RATE_OUTPUT_PER_MTOK"] = "nan"
            _sys.argv = ["generate_slide_plan.py"]
            try:
                ret = gsp.main()
                assert_eq(ret, 4, "v0 alias rate_output=nan → exit 4")
            finally:
                _sys.argv = old_argv
            _os.environ.pop("SUPERMOVIE_RATE_OUTPUT_PER_MTOK", None)
            # env nan via v1 canonical (Codex P2 fix: v1 path も nan reject)
            _os.environ["SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK"] = "nan"
            _sys.argv = ["generate_slide_plan.py"]
            try:
                ret = gsp.main()
                assert_eq(ret, 4, "v1 canonical rate_input=nan → exit 4")
            finally:
                _sys.argv = old_argv
        finally:
            if original_api_key is None:
                _os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                _os.environ["ANTHROPIC_API_KEY"] = original_api_key
            for k, v in saved_rate_envs.items():
                if v is None:
                    _os.environ.pop(k, None)
                else:
                    _os.environ[k] = v
            gsp.PROJ = original_proj


def test_generate_slide_plan_rate_v0_v1_alias_precedence() -> None:
    """Codex 21:54 PR-D verdict: v1 canonical (SUPERMOVIE_RATE_ANTHROPIC_*_USD_PER_MTOK)
    + v0 alias (SUPERMOVIE_RATE_*_PER_MTOK) の precedence + alias 動作を検証。

    docs/OBSERVABILITY.md §Rate Env Var Convention の v1 / v0 alias 仕様 regression。
    Test cases:
    - v0 alias only → v0 から読む (後方互換維持)
    - v1 only → v1 から読む
    - v1 + v0 both set → v1 が勝つ (v0 alias は ignore)
    - CLI --rate-input → env 全て上書き
    """
    import generate_slide_plan as gsp
    import os as _os
    import urllib.request as _urlreq
    import io as _io
    import contextlib

    V1_IN = "SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK"
    V1_OUT = "SUPERMOVIE_RATE_ANTHROPIC_OUTPUT_USD_PER_MTOK"
    V0_IN = "SUPERMOVIE_RATE_INPUT_PER_MTOK"
    V0_OUT = "SUPERMOVIE_RATE_OUTPUT_PER_MTOK"

    saved_env = {k: _os.environ.get(k) for k in (V1_IN, V1_OUT, V0_IN, V0_OUT, "ANTHROPIC_API_KEY")}
    original_proj = gsp.PROJ
    original_urlopen = _urlreq.urlopen

    def mock_urlopen_unused(req, timeout=60):
        raise AssertionError("API not expected in dry-run path")

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        gsp.PROJ = proj
        (proj / "transcript_fixed.json").write_text(
            json.dumps({"words": [{"text": "x"}], "segments": []}),
            encoding="utf-8",
        )
        (proj / "project-config.json").write_text(
            json.dumps({"format": "short"}), encoding="utf-8",
        )
        for k in (V1_IN, V1_OUT, V0_IN, V0_OUT):
            _os.environ.pop(k, None)
        _os.environ["ANTHROPIC_API_KEY"] = "fake"
        _urlreq.urlopen = mock_urlopen_unused

        import sys as _sys
        old_argv = _sys.argv
        try:
            # Case A: v0 alias only → 後方互換 (旧 env が機能し続ける)
            _os.environ[V0_IN] = "1.5"
            _os.environ[V0_OUT] = "5.0"
            captured = _io.StringIO()
            _sys.argv = ["generate_slide_plan.py", "--dry-run"]
            with contextlib.redirect_stdout(captured):
                ret = gsp.main()
            assert_eq(ret, 0, "v0 alias only → exit 0")
            payload = json.loads(captured.getvalue().strip().splitlines()[-1])
            assert_eq(payload.get("rate_input_per_mtok"), 1.5, "v0 alias rate_input read")
            assert_eq(payload.get("rate_output_per_mtok"), 5.0, "v0 alias rate_output read")

            # Case B: v1 only → v1 が読まれる
            _os.environ.pop(V0_IN, None)
            _os.environ.pop(V0_OUT, None)
            _os.environ[V1_IN] = "2.0"
            _os.environ[V1_OUT] = "8.0"
            captured = _io.StringIO()
            _sys.argv = ["generate_slide_plan.py", "--dry-run"]
            with contextlib.redirect_stdout(captured):
                ret = gsp.main()
            assert_eq(ret, 0, "v1 only → exit 0")
            payload = json.loads(captured.getvalue().strip().splitlines()[-1])
            assert_eq(payload.get("rate_input_per_mtok"), 2.0, "v1 rate_input read")
            assert_eq(payload.get("rate_output_per_mtok"), 8.0, "v1 rate_output read")

            # Case C: v1 + v0 both → v1 が勝つ
            _os.environ[V0_IN] = "9.9"  # v1 と異なる値、v1 が勝つことを確認
            _os.environ[V0_OUT] = "9.9"
            _os.environ[V1_IN] = "2.5"
            _os.environ[V1_OUT] = "10.0"
            captured = _io.StringIO()
            _sys.argv = ["generate_slide_plan.py", "--dry-run"]
            with contextlib.redirect_stdout(captured):
                ret = gsp.main()
            assert_eq(ret, 0, "v1 + v0 both → exit 0")
            payload = json.loads(captured.getvalue().strip().splitlines()[-1])
            assert_eq(payload.get("rate_input_per_mtok"), 2.5, "v1 wins over v0 alias (input)")
            assert_eq(payload.get("rate_output_per_mtok"), 10.0, "v1 wins over v0 alias (output)")

            # Case D: CLI > env all → CLI が勝つ
            captured = _io.StringIO()
            _sys.argv = [
                "generate_slide_plan.py", "--dry-run",
                "--rate-input", "0.5", "--rate-output", "1.0",
            ]
            with contextlib.redirect_stdout(captured):
                ret = gsp.main()
            assert_eq(ret, 0, "CLI > env all → exit 0")
            payload = json.loads(captured.getvalue().strip().splitlines()[-1])
            assert_eq(payload.get("rate_input_per_mtok"), 0.5, "CLI wins over v1+v0 (input)")
            assert_eq(payload.get("rate_output_per_mtok"), 1.0, "CLI wins over v1+v0 (output)")
        finally:
            _sys.argv = old_argv
            for k, v in saved_env.items():
                if v is None:
                    _os.environ.pop(k, None)
                else:
                    _os.environ[k] = v
            gsp.PROJ = original_proj
            _urlreq.urlopen = original_urlopen


def test_generate_slide_plan_max_input_caps_prompt() -> None:
    """Phase 3-V P2: --max-input-words / --max-input-segments で prompt 入力 cap."""
    import generate_slide_plan as gsp
    import os as _os
    import urllib.request as _urlreq

    captured_body = {}

    def mock_urlopen_capture(req, timeout=60):
        captured_body["data"] = json.loads(req.data.decode("utf-8"))
        resp_text = json.dumps({
            "content": [{"type": "text", "text": json.dumps({
                "version": "supermovie.slide_plan.v1",
                "slides": [{"id": 1, "startWordIndex": 0, "endWordIndex": 0, "title": "t"}],
            })}]
        })
        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return resp_text.encode("utf-8")
        return FakeResp()

    original_urlopen = _urlreq.urlopen
    original_proj = gsp.PROJ
    original_api_key = _os.environ.get("ANTHROPIC_API_KEY")

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        gsp.PROJ = proj
        # 3 words + 3 segments
        (proj / "transcript_fixed.json").write_text(
            json.dumps({
                "words": [
                    {"text": "alpha", "start": 0, "end": 100},
                    {"text": "bravo", "start": 100, "end": 200},
                    {"text": "charlie", "start": 200, "end": 300},
                ],
                "segments": [
                    {"text": "first segment text", "start": 0, "end": 100},
                    {"text": "second segment text", "start": 100, "end": 200},
                    {"text": "third segment text", "start": 200, "end": 300},
                ],
            }),
            encoding="utf-8",
        )
        (proj / "project-config.json").write_text(
            json.dumps({"format": "short"}), encoding="utf-8",
        )
        _os.environ["ANTHROPIC_API_KEY"] = "fake"
        _urlreq.urlopen = mock_urlopen_capture
        try:
            import sys as _sys
            old_argv = _sys.argv
            output_path = proj / "slide_plan.json"
            _sys.argv = [
                "generate_slide_plan.py",
                "--max-input-words", "2",
                "--max-input-segments", "1",
                "--output", str(output_path),
            ]
            try:
                gsp.main()
                prompt_text = captured_body["data"]["messages"][0]["content"]
                # words[2] = "charlie" は cap で除外、words[0/1] は含まれる
                if "charlie" in prompt_text:
                    raise AssertionError(
                        "--max-input-words=2 で 3rd word 'charlie' が prompt に残った"
                    )
                if "alpha" not in prompt_text or "bravo" not in prompt_text:
                    raise AssertionError("first 2 words missing from prompt")
                # segments[1+] = "second/third segment" は cap で除外
                if "second segment" in prompt_text or "third segment" in prompt_text:
                    raise AssertionError(
                        "--max-input-segments=1 で 2nd/3rd segment が prompt に残った"
                    )
                if "first segment" not in prompt_text:
                    raise AssertionError("first segment missing from prompt")
                # max_input_words が prompt header にも反映 (200 → 2)
                if "最大 2 word" not in prompt_text:
                    raise AssertionError(
                        f"prompt header に max_input_words={'2'} が反映されていない"
                    )
            finally:
                _sys.argv = old_argv
        finally:
            if original_api_key is None:
                _os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                _os.environ["ANTHROPIC_API_KEY"] = original_api_key
            _urlreq.urlopen = original_urlopen
            gsp.PROJ = original_proj


def test_generate_slide_plan_api_invalid_json() -> None:
    """generate_slide_plan API mock: response が JSON parse 失敗 → exit 5."""
    import generate_slide_plan as gsp
    import os as _os
    import urllib.request as _urlreq

    invalid_response = json.dumps(
        {"content": [{"type": "text", "text": "this is not json {{{"}]}
    ).encode("utf-8")

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self):
            return self._body

    def mock_urlopen(req, timeout=60):
        return FakeResponse(invalid_response)

    original_urlopen = _urlreq.urlopen
    original_proj = gsp.PROJ
    original_api_key = _os.environ.get("ANTHROPIC_API_KEY")  # Codex P2 #2 反映

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        gsp.PROJ = proj
        (proj / "transcript_fixed.json").write_text(
            json.dumps({"words": [], "segments": []}),
            encoding="utf-8",
        )
        (proj / "project-config.json").write_text(
            json.dumps({"format": "short", "tone": "プロ"}),
            encoding="utf-8",
        )
        _os.environ["ANTHROPIC_API_KEY"] = "fake-key"
        _urlreq.urlopen = mock_urlopen
        try:
            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = ["generate_slide_plan.py"]
            try:
                ret = gsp.main()
                assert_eq(ret, 5, "API invalid JSON → exit 5")
            finally:
                _sys.argv = old_argv
        finally:
            if original_api_key is None:
                _os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                _os.environ["ANTHROPIC_API_KEY"] = original_api_key
            _urlreq.urlopen = original_urlopen
            gsp.PROJ = original_proj


def test_build_slide_data_plan_validation_fallback() -> None:
    """build_slide_data --plan で validate 失敗 → deterministic fallback (default).

    Codex Phase 3-M review P2 #3 反映: API mock の出口に build_slide_data
    を繋いで schema validation 経路まで踏む integration test。
    """
    import build_slide_data as bsd

    with tempfile.TemporaryDirectory() as tmp:
        proj = _setup_temp_project(Path(tmp))
        # 通常 transcript
        (proj / "transcript_fixed.json").write_text(
            json.dumps(
                {
                    "duration_ms": 4000,
                    "text": "test",
                    "segments": [
                        {"text": "hello", "start": 0, "end": 2000},
                        {"text": "world", "start": 2000, "end": 4000},
                    ],
                    "words": [
                        {"text": "hello", "start": 0, "end": 1000},
                        {"text": "world", "start": 2000, "end": 3000},
                    ],
                }
            ),
            encoding="utf-8",
        )
        (proj / "project-config.json").write_text(
            json.dumps({"format": "short", "tone": "プロフェッショナル"}),
            encoding="utf-8",
        )
        # 壊れた slide_plan: 必須 version 欠落
        bad_plan = {
            "slides": [
                {
                    "id": 1,
                    "startWordIndex": 0,
                    "endWordIndex": 1,
                    "title": "test",
                    "bullets": [],
                    "align": "left",
                }
            ]
        }
        plan_path = proj / "bad_plan.json"
        plan_path.write_text(json.dumps(bad_plan), encoding="utf-8")

        original_proj = bsd.PROJ
        bsd.PROJ = proj
        try:
            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = [
                "build_slide_data.py",
                "--plan",
                str(plan_path),
                # default: validation 失敗で WARN + deterministic fallback
            ]
            try:
                bsd.main()
                # fallback 経路: deterministic で slideData.ts 生成
                slide_ts = proj / "src" / "Slides" / "slideData.ts"
                if not slide_ts.exists():
                    raise AssertionError(
                        f"slideData.ts not generated (fallback expected): {slide_ts}"
                    )
            finally:
                _sys.argv = old_argv
        finally:
            bsd.PROJ = original_proj


def test_build_slide_data_plan_strict_failure() -> None:
    """build_slide_data --plan + --strict-plan で validate 失敗 → SystemExit 2."""
    import build_slide_data as bsd

    with tempfile.TemporaryDirectory() as tmp:
        proj = _setup_temp_project(Path(tmp))
        (proj / "transcript_fixed.json").write_text(
            json.dumps(
                {
                    "duration_ms": 2000,
                    "text": "test",
                    "segments": [{"text": "hello", "start": 0, "end": 2000}],
                    "words": [{"text": "hello", "start": 0, "end": 1000}],
                }
            ),
            encoding="utf-8",
        )
        (proj / "project-config.json").write_text(
            json.dumps({"format": "short", "tone": "プロ"}),
            encoding="utf-8",
        )
        # 壊れた plan: version 欠落
        bad_plan = {"slides": []}
        plan_path = proj / "bad_plan.json"
        plan_path.write_text(json.dumps(bad_plan), encoding="utf-8")

        original_proj = bsd.PROJ
        bsd.PROJ = proj
        try:
            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = [
                "build_slide_data.py",
                "--plan",
                str(plan_path),
                "--strict-plan",
            ]
            try:
                bsd.main()
                raise AssertionError(
                    "build_slide_data --strict-plan should fail with bad plan"
                )
            except SystemExit as e:
                # exit code 2 期待 (strict-plan + validation error)
                code = e.code if e.code is not None else 0
                assert_eq(code, 2, "strict-plan validation failure → exit 2")
            finally:
                _sys.argv = old_argv
        finally:
            bsd.PROJ = original_proj


def test_build_scripts_wiring() -> None:
    """build_slide_data / build_telop_data が timeline 経由で正しく wire されている."""
    import importlib
    bsd = importlib.import_module("build_slide_data")
    btd = importlib.import_module("build_telop_data")

    # FPS が videoConfig.ts から読まれている (Phase 3-J 統合確認)
    if bsd.FPS <= 0:
        raise AssertionError(f"build_slide_data FPS should be positive, got {bsd.FPS}")
    if btd.FPS <= 0:
        raise AssertionError(f"build_telop_data FPS should be positive, got {btd.FPS}")

    # validate_transcript_segment が timeline から wire されている
    if bsd.validate_transcript_segment is None:
        raise AssertionError("build_slide_data should import validate_transcript_segment")
    if btd.validate_transcript_segment is None:
        raise AssertionError("build_telop_data should import validate_transcript_segment")

    # build_slide_data の cut helper wrapper が timeline 経由で動く
    cuts = bsd.build_cut_segments_from_vad(
        {"speech_segments": [{"start": 0, "end": 1000}]}
    )
    assert_eq(len(cuts), 1, "bsd.build_cut_segments_from_vad len")
    assert_eq(cuts[0]["originalStartMs"], 0, "bsd cuts[0] originalStartMs")

    # build_telop_data の cut helper も validate_vad_schema 経由
    cuts_t = btd.build_cut_segments_from_vad(
        {"speech_segments": [{"start": 0, "end": 1000}]}
    )
    assert_eq(len(cuts_t), 1, "btd.build_cut_segments_from_vad len")

    # 壊れた VAD で raise (3 script で挙動統一の確認)
    bad_vad = {"speech_segments": [{"start": 100, "end": 50}]}
    assert_raises(
        lambda: bsd.build_cut_segments_from_vad(bad_vad),
        timeline.VadSchemaError,
        "bsd raises VadSchemaError",
    )
    assert_raises(
        lambda: btd.build_cut_segments_from_vad(bad_vad),
        timeline.VadSchemaError,
        "btd raises VadSchemaError",
    )


def test_ms_to_playback_frame_edge_cases() -> None:
    """ms_to_playback_frame の境界・degenerate ケース (Phase 3 post-freeze 補強)."""
    cut_segs = [
        {"originalStartMs": 0, "originalEndMs": 500, "playbackStart": 0, "playbackEnd": 15},
        {"originalStartMs": 1500, "originalEndMs": 3000, "playbackStart": 15, "playbackEnd": 60},
    ]
    # 境界 inclusive: <= / >=
    assert_eq(timeline.ms_to_playback_frame(0, 30, cut_segs), 0, "boundary seg0 originalStartMs")
    assert_eq(timeline.ms_to_playback_frame(500, 30, cut_segs), 15, "boundary seg0 originalEndMs")
    assert_eq(timeline.ms_to_playback_frame(3000, 30, cut_segs), 60, "boundary seg1 originalEndMs")

    # cut 範囲外 (隙間 / 全体外)
    assert_eq(timeline.ms_to_playback_frame(501, 30, cut_segs), None, "1ms after seg0 end (gap)")
    assert_eq(timeline.ms_to_playback_frame(1499, 30, cut_segs), None, "1ms before seg1 start (gap)")
    assert_eq(timeline.ms_to_playback_frame(3500, 30, cut_segs), None, "after all cuts")

    # Single cut
    single = [{"originalStartMs": 100, "originalEndMs": 600, "playbackStart": 0, "playbackEnd": 15}]
    assert_eq(timeline.ms_to_playback_frame(100, 30, single), 0, "single cut at originalStart")
    assert_eq(timeline.ms_to_playback_frame(50, 30, single), None, "single cut before range")
    assert_eq(timeline.ms_to_playback_frame(700, 30, single), None, "single cut after range")

    # fps boundary
    assert_eq(timeline.ms_to_playback_frame(1000, 1, []), 1, "no-cut fps=1 1s")
    assert_eq(timeline.ms_to_playback_frame(1000, 120, []), 120, "no-cut fps=120 1s")

    # Adjacent cuts (gap=0): seg0 ends at 500, seg1 starts at 500.
    # 仕様: for-loop が最初に matching 要素を返す。境界値で両方 match だが
    # 累積 playback の連続性により同じ frame に着地する。
    adjacent = [
        {"originalStartMs": 0, "originalEndMs": 500, "playbackStart": 0, "playbackEnd": 15},
        {"originalStartMs": 500, "originalEndMs": 1000, "playbackStart": 15, "playbackEnd": 30},
    ]
    assert_eq(
        timeline.ms_to_playback_frame(500, 30, adjacent), 15,
        "adjacent cuts boundary 500: seg0 first match, both yield 15",
    )
    assert_eq(
        timeline.ms_to_playback_frame(600, 30, adjacent), 18,
        "adjacent cuts: 600ms in seg1, offset=100ms, 15+round(3.0)=18",
    )


def test_load_cut_segments_edge_cases() -> None:
    """load_cut_segments の corner case (空 / 単一 / 不在)."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        # vad_result.json 不在 → []
        assert_eq(timeline.load_cut_segments(proj, 30), [], "no vad file returns empty")

        # 空 speech_segments → []
        (proj / "vad_result.json").write_text(
            json.dumps({"speech_segments": [], "duration_ms": 5000}),
            encoding="utf-8",
        )
        result = timeline.load_cut_segments(proj, 30)
        assert_eq(len(result), 0, "empty speech_segments → []")

        # 単一 speech_segment → 1 要素 with playback frames
        (proj / "vad_result.json").write_text(
            json.dumps({
                "speech_segments": [{"start": 200, "end": 1200}],
                "duration_ms": 5000,
            }),
            encoding="utf-8",
        )
        result = timeline.load_cut_segments(proj, 30)
        assert_eq(len(result), 1, "single speech_segment → 1 cut")
        assert_eq(result[0]["originalStartMs"], 200, "single seg originalStartMs")
        assert_eq(result[0]["originalEndMs"], 1200, "single seg originalEndMs")
        assert_eq(result[0]["playbackStart"], 0, "single seg playbackStart=0")
        assert_eq(result[0]["playbackEnd"], 30, "single seg playbackEnd=30 (1s @ 30fps)")


def test_build_cut_segments_multi_with_gaps() -> None:
    """build_cut_segments_from_vad で複数 speech segment + gap removal を検証."""
    vad = {
        "speech_segments": [
            {"start": 0, "end": 1000},      # 1.0s
            {"start": 2000, "end": 3500},   # 1.5s (gap 1000ms 除去)
            {"start": 5000, "end": 6000},   # 1.0s
        ],
        "duration_ms": 7000,
    }
    cuts = timeline.build_cut_segments_from_vad(vad, 30)
    assert_eq(len(cuts), 3, "3 segments expected")

    # seg 0: original 0→1000、playback 0→30 (1s @ 30fps)
    assert_eq(cuts[0]["playbackStart"], 0, "seg0 playbackStart")
    assert_eq(cuts[0]["playbackEnd"], 30, "seg0 playbackEnd")

    # seg 1: original 2000→3500、cumulative 1000→2500ms → playback 30→75
    assert_eq(cuts[1]["playbackStart"], 30, "seg1 playbackStart (cumulative 1000ms = 30 frames)")
    assert_eq(cuts[1]["playbackEnd"], 75, "seg1 playbackEnd (cumulative 2500ms = 75 frames)")

    # seg 2: original 5000→6000、cumulative 2500→3500ms → playback 75→105
    assert_eq(cuts[2]["playbackStart"], 75, "seg2 playbackStart (cumulative 2500ms = 75 frames)")
    assert_eq(cuts[2]["playbackEnd"], 105, "seg2 playbackEnd (cumulative 3500ms = 105 frames)")


def test_voicevox_cleanup_stale_unlinks_sentinel() -> None:
    """Phase 3-V P5: cleanup_stale_all() が stale narration.ready.json を削除."""
    import voicevox_narration as vn

    state = {
        "PROJ": vn.PROJ,
        "NARRATION_DIR": vn.NARRATION_DIR,
        "NARRATION_DATA_TS": vn.NARRATION_DATA_TS,
        "CHUNK_META_JSON": vn.CHUNK_META_JSON,
        "NARRATION_LEGACY_WAV": vn.NARRATION_LEGACY_WAV,
        "NARRATION_READY_JSON": vn.NARRATION_READY_JSON,
    }
    try:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            vn.PROJ = proj
            vn.NARRATION_DIR = proj / "public" / "narration"
            vn.NARRATION_DATA_TS = proj / "src" / "Narration" / "narrationData.ts"
            vn.CHUNK_META_JSON = vn.NARRATION_DIR / "chunk_meta.json"
            vn.NARRATION_LEGACY_WAV = proj / "public" / "narration.wav"
            vn.NARRATION_READY_JSON = proj / "public" / "narration.ready.json"
            (proj / "src" / "Narration").mkdir(parents=True)
            (proj / "public").mkdir(parents=True)

            # Pre-create stale sentinel + stale narrationData.ts
            vn.NARRATION_READY_JSON.write_text(
                '{"schemaVersion":1,"status":"ready","chunkCount":3,"totalFrames":99,"generatedAtMs":1000}',
                encoding="utf-8",
            )
            vn.NARRATION_DATA_TS.write_text(
                "// stale stub\nexport const narrationData = [];\n", encoding="utf-8"
            )
            if not vn.NARRATION_READY_JSON.exists():
                raise AssertionError("pre-cond: stale sentinel must exist")

            vn.cleanup_stale_all()

            if vn.NARRATION_READY_JSON.exists():
                raise AssertionError(
                    "cleanup_stale_all failed to remove stale narration.ready.json"
                )
    finally:
        for k, v in state.items():
            setattr(vn, k, v)


def test_voicevox_sentinel_written_after_wav() -> None:
    """Phase 3-V P5: sentinel write 順序 narration.wav → narration.ready.json verify.

    write_narration_ready の mock が「narration.wav existence を assert + 元実装に
    delegate」する形で、call 時点で narration.wav が出ていなければ raise。旧順序
    (sentinel 先) に regress すると必ず fail。
    """
    import voicevox_narration as vn

    state = {
        "PROJ": vn.PROJ,
        "NARRATION_DIR": vn.NARRATION_DIR,
        "NARRATION_DATA_TS": vn.NARRATION_DATA_TS,
        "CHUNK_META_JSON": vn.CHUNK_META_JSON,
        "NARRATION_LEGACY_WAV": vn.NARRATION_LEGACY_WAV,
        "NARRATION_READY_JSON": vn.NARRATION_READY_JSON,
    }
    original_concat = vn.concat_wavs_atomic
    original_check_engine = vn.check_engine
    original_synthesize = vn.synthesize
    original_write_ready = vn.write_narration_ready

    try:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            vn.PROJ = proj
            vn.NARRATION_DIR = proj / "public" / "narration"
            vn.NARRATION_DATA_TS = proj / "src" / "Narration" / "narrationData.ts"
            vn.CHUNK_META_JSON = vn.NARRATION_DIR / "chunk_meta.json"
            vn.NARRATION_LEGACY_WAV = proj / "public" / "narration.wav"
            vn.NARRATION_READY_JSON = proj / "public" / "narration.ready.json"
            (proj / "src" / "Narration").mkdir(parents=True)
            (proj / "src" / "videoConfig.ts").write_text(
                make_videoconfig_ts(30), encoding="utf-8"
            )
            (proj / "transcript_fixed.json").write_text(
                json.dumps({"segments": [{"text": "hi", "start": 0, "end": 1000}]}),
                encoding="utf-8",
            )

            import wave as _wave
            import io as _io
            import struct as _struct

            buf = _io.BytesIO()
            with _wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes(_struct.pack("<22050h", *([0] * 22050)))
            wav_bytes = buf.getvalue()

            vn.check_engine = lambda: (True, "0.0.0-test")
            vn.synthesize = lambda text, speaker: wav_bytes

            # concat: 実際に narration.wav を書く (next step で sentinel check が exists を見る)
            def real_concat(wavs, out_path):
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(wav_bytes)

            vn.concat_wavs_atomic = real_concat

            # sentinel write 順序 check + delegate
            order_log = []

            def assert_sentinel_after_wav(chunk_count, total_frames):
                if not vn.NARRATION_LEGACY_WAV.exists():
                    order_log.append("FAIL: narration.wav missing when sentinel write called")
                    raise RuntimeError("write order regression: narration.wav missing")
                order_log.append("OK: narration.wav exists before sentinel write")
                original_write_ready(chunk_count, total_frames)

            vn.write_narration_ready = assert_sentinel_after_wav

            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = ["voicevox_narration.py"]
            try:
                ret = vn.main()
            finally:
                _sys.argv = old_argv

            assert_eq(ret, 0, "successful main() exit 0")
            if not order_log or "OK:" not in order_log[0]:
                raise AssertionError(
                    f"sentinel write order regression: {order_log}"
                )
            if not vn.NARRATION_READY_JSON.exists():
                raise AssertionError("sentinel file not created after success")
            payload = json.loads(vn.NARRATION_READY_JSON.read_text(encoding="utf-8"))
            assert_eq(payload.get("schemaVersion"), 1, "sentinel schemaVersion=1")
            assert_eq(payload.get("status"), "ready", "sentinel status=ready")
            assert_eq(payload.get("chunkCount"), 1, "sentinel chunkCount=1")
            if not isinstance(payload.get("totalFrames"), int):
                raise AssertionError(
                    f"sentinel totalFrames must be int, got {type(payload.get('totalFrames'))}"
                )
            if not isinstance(payload.get("generatedAtMs"), int):
                raise AssertionError(
                    f"sentinel generatedAtMs must be int, got {type(payload.get('generatedAtMs'))}"
                )
    finally:
        for k, v in state.items():
            setattr(vn, k, v)
        vn.concat_wavs_atomic = original_concat
        vn.check_engine = original_check_engine
        vn.synthesize = original_synthesize
        vn.write_narration_ready = original_write_ready


def test_voicevox_sentinel_write_fail_rollback() -> None:
    """Phase 3-V P5 review P2 #3 + P1 反映: write_narration_ready 失敗時の rollback.

    sentinel write を強制 OSError で fail させ、rollback path で chunks /
    out_path / narrationData.ts / sentinel が全削除されること、custom --output
    指定時も out_path が unlink されることを verify (Codex P5 review P1)。
    """
    import voicevox_narration as vn

    state = {
        "PROJ": vn.PROJ,
        "NARRATION_DIR": vn.NARRATION_DIR,
        "NARRATION_DATA_TS": vn.NARRATION_DATA_TS,
        "CHUNK_META_JSON": vn.CHUNK_META_JSON,
        "NARRATION_LEGACY_WAV": vn.NARRATION_LEGACY_WAV,
        "NARRATION_READY_JSON": vn.NARRATION_READY_JSON,
    }
    original_concat = vn.concat_wavs_atomic
    original_check_engine = vn.check_engine
    original_synthesize = vn.synthesize
    original_write_ready = vn.write_narration_ready

    try:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            vn.PROJ = proj
            vn.NARRATION_DIR = proj / "public" / "narration"
            vn.NARRATION_DATA_TS = proj / "src" / "Narration" / "narrationData.ts"
            vn.CHUNK_META_JSON = vn.NARRATION_DIR / "chunk_meta.json"
            vn.NARRATION_LEGACY_WAV = proj / "public" / "narration.wav"
            vn.NARRATION_READY_JSON = proj / "public" / "narration.ready.json"
            (proj / "src" / "Narration").mkdir(parents=True)
            (proj / "src" / "videoConfig.ts").write_text(
                make_videoconfig_ts(30), encoding="utf-8"
            )
            (proj / "transcript_fixed.json").write_text(
                json.dumps({"segments": [{"text": "hi", "start": 0, "end": 1000}]}),
                encoding="utf-8",
            )

            import wave as _wave
            import io as _io
            import struct as _struct

            buf = _io.BytesIO()
            with _wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes(_struct.pack("<22050h", *([0] * 22050)))
            wav_bytes = buf.getvalue()

            vn.check_engine = lambda: (True, "0.0.0-test")
            vn.synthesize = lambda text, speaker: wav_bytes

            # custom out_path で concat 成功 (P5 review P1: rollback が
            # NARRATION_LEGACY_WAV ではなく out_path を unlink するか verify)
            custom_out = proj / "public" / "custom_narration.wav"

            def real_concat(wavs, out_path):
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(wav_bytes)

            vn.concat_wavs_atomic = real_concat
            vn.write_narration_ready = lambda c, t: (_ for _ in ()).throw(
                OSError("simulated sentinel write failure")
            )

            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = ["voicevox_narration.py", "--output", str(custom_out)]
            try:
                ret = vn.main()
            finally:
                _sys.argv = old_argv

            assert_eq(ret, 6, "sentinel write fail → exit 6 (rollback path)")
            # all-or-nothing rollback verify
            chunk_files = list(vn.NARRATION_DIR.glob("chunk_*.wav"))
            if chunk_files:
                raise AssertionError(
                    f"rollback failed: chunks left after sentinel fail: {chunk_files}"
                )
            # P5 review P1 fix: custom out_path も unlink される
            if custom_out.exists():
                raise AssertionError(
                    f"P5 review P1 fix regression: custom out_path 残置: {custom_out}"
                )
            # default NARRATION_LEGACY_WAV は元々書かれていない (custom 指定なので)
            # narrationData.ts は empty に reset
            content = vn.NARRATION_DATA_TS.read_text(encoding="utf-8")
            if "export const narrationData: NarrationSegment[] = []" not in content:
                raise AssertionError(
                    f"rollback failed: narrationData.ts not reset: {content[:100]}"
                )
            # sentinel 不在 (write 中に失敗したので)
            if vn.NARRATION_READY_JSON.exists():
                raise AssertionError(
                    "rollback failed: sentinel left after write failure"
                )
            # chunk_meta.json も削除
            if vn.CHUNK_META_JSON.exists():
                raise AssertionError(
                    "rollback failed: chunk_meta.json left after sentinel fail"
                )
    finally:
        for k, v in state.items():
            setattr(vn, k, v)
        vn.concat_wavs_atomic = original_concat
        vn.check_engine = original_check_engine
        vn.synthesize = original_synthesize
        vn.write_narration_ready = original_write_ready


def test_voicevox_sentinel_rollback_on_concat_fail() -> None:
    """Phase 3-V P5: concat 失敗時に sentinel が残らない (all-or-nothing 維持).

    concat_wavs_atomic を強制 PermissionError で fail させ、rollback 経路後に
    NARRATION_READY_JSON が存在しないことを確認。sentinel は concat 後に書く設計
    なので、concat fail 経路では当然書かれないが、defensive verification として明示。
    """
    import voicevox_narration as vn

    state = {
        "PROJ": vn.PROJ,
        "NARRATION_DIR": vn.NARRATION_DIR,
        "NARRATION_DATA_TS": vn.NARRATION_DATA_TS,
        "CHUNK_META_JSON": vn.CHUNK_META_JSON,
        "NARRATION_LEGACY_WAV": vn.NARRATION_LEGACY_WAV,
        "NARRATION_READY_JSON": vn.NARRATION_READY_JSON,
    }
    original_concat = vn.concat_wavs_atomic
    original_check_engine = vn.check_engine
    original_synthesize = vn.synthesize

    try:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            vn.PROJ = proj
            vn.NARRATION_DIR = proj / "public" / "narration"
            vn.NARRATION_DATA_TS = proj / "src" / "Narration" / "narrationData.ts"
            vn.CHUNK_META_JSON = vn.NARRATION_DIR / "chunk_meta.json"
            vn.NARRATION_LEGACY_WAV = proj / "public" / "narration.wav"
            vn.NARRATION_READY_JSON = proj / "public" / "narration.ready.json"
            (proj / "src" / "Narration").mkdir(parents=True)
            (proj / "src" / "videoConfig.ts").write_text(
                make_videoconfig_ts(30), encoding="utf-8"
            )
            (proj / "transcript_fixed.json").write_text(
                json.dumps({"segments": [{"text": "hi", "start": 0, "end": 1000}]}),
                encoding="utf-8",
            )

            import wave as _wave
            import io as _io
            import struct as _struct

            buf = _io.BytesIO()
            with _wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes(_struct.pack("<22050h", *([0] * 22050)))
            wav_bytes = buf.getvalue()

            vn.check_engine = lambda: (True, "0.0.0-test")
            vn.synthesize = lambda text, speaker: wav_bytes
            vn.concat_wavs_atomic = lambda wavs, out_path: (_ for _ in ()).throw(
                PermissionError("simulated concat failure")
            )

            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = ["voicevox_narration.py"]
            try:
                ret = vn.main()
            finally:
                _sys.argv = old_argv

            assert_eq(ret, 6, "concat failure → exit 6 (rollback path)")
            if vn.NARRATION_READY_JSON.exists():
                raise AssertionError(
                    "sentinel must not exist after concat failure (all-or-nothing 破れ)"
                )
            # rollback: chunks も 削除されている
            chunk_files = list(vn.NARRATION_DIR.glob("chunk_*.wav"))
            if chunk_files:
                raise AssertionError(
                    f"rollback failed: chunks left after concat fail: {chunk_files}"
                )
    finally:
        for k, v in state.items():
            setattr(vn, k, v)
        vn.concat_wavs_atomic = original_concat
        vn.check_engine = original_check_engine
        vn.synthesize = original_synthesize


def test_voicevox_json_log_emits_pure_json() -> None:
    """Phase 3-V 第2弾 P3 (Codex CODEX_NEXT_PRIORITY:15-18):
    --json-log flag で末尾 1 行に純 JSON summary が emit される."""
    import voicevox_narration as vn
    import io as _io
    import contextlib

    state = {
        "PROJ": vn.PROJ,
        "NARRATION_DIR": vn.NARRATION_DIR,
        "NARRATION_DATA_TS": vn.NARRATION_DATA_TS,
        "CHUNK_META_JSON": vn.CHUNK_META_JSON,
        "NARRATION_LEGACY_WAV": vn.NARRATION_LEGACY_WAV,
        "NARRATION_READY_JSON": vn.NARRATION_READY_JSON,
    }
    original_concat = vn.concat_wavs_atomic
    original_check_engine = vn.check_engine
    original_synthesize = vn.synthesize

    try:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            vn.PROJ = proj
            vn.NARRATION_DIR = proj / "public" / "narration"
            vn.NARRATION_DATA_TS = proj / "src" / "Narration" / "narrationData.ts"
            vn.CHUNK_META_JSON = vn.NARRATION_DIR / "chunk_meta.json"
            vn.NARRATION_LEGACY_WAV = proj / "public" / "narration.wav"
            vn.NARRATION_READY_JSON = proj / "public" / "narration.ready.json"
            (proj / "src" / "Narration").mkdir(parents=True)
            (proj / "src" / "videoConfig.ts").write_text(
                make_videoconfig_ts(30), encoding="utf-8"
            )
            (proj / "transcript_fixed.json").write_text(
                json.dumps({"segments": [{"text": "hi", "start": 0, "end": 1000}]}),
                encoding="utf-8",
            )

            import wave as _wave
            import struct as _struct

            buf = _io.BytesIO()
            with _wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes(_struct.pack("<22050h", *([0] * 22050)))
            wav_bytes = buf.getvalue()

            vn.check_engine = lambda: (True, "0.0.0-test")
            vn.synthesize = lambda text, speaker: wav_bytes
            vn.concat_wavs_atomic = lambda wavs, out: out.write_bytes(wav_bytes)

            captured = _io.StringIO()
            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = ["voicevox_narration.py", "--json-log"]
            try:
                with contextlib.redirect_stdout(captured):
                    ret = vn.main()
            finally:
                _sys.argv = old_argv

            assert_eq(ret, 0, "main() exit 0 with --json-log")
            stdout = captured.getvalue()
            lines = [ln for ln in stdout.splitlines() if ln.strip()]
            if not lines:
                raise AssertionError("--json-log produced no stdout")
            # 末尾行は純 JSON (prefix なし)
            last = lines[-1]
            try:
                payload = json.loads(last)
            except json.JSONDecodeError as e:
                raise AssertionError(
                    f"--json-log last line not pure JSON: {last!r} ({e})"
                ) from e
            # expected key 集
            for key in ("speaker", "fps", "chunks", "total_chunks", "narration_ready_json"):
                if key not in payload:
                    raise AssertionError(
                        f"--json-log missing expected key {key}: {payload}"
                    )
            # 既存 stdout (`summary: {...}` 行) も維持されている
            if not any("summary:" in ln for ln in lines):
                raise AssertionError(
                    "existing 'summary:' line missing (should be preserved when --json-log set)"
                )
    finally:
        for k, v in state.items():
            setattr(vn, k, v)
        vn.concat_wavs_atomic = original_concat
        vn.check_engine = original_check_engine
        vn.synthesize = original_synthesize


def test_voicevox_json_log_engine_skip_path() -> None:
    """Phase 3-V P3 review P1 fix (CODEX_2ND_BATCH_REVIEW:5):
    engine unavailable の skip path も --json-log emit する."""
    import voicevox_narration as vn
    import io as _io
    import contextlib

    state = {"PROJ": vn.PROJ}
    original_check_engine = vn.check_engine

    try:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            vn.PROJ = proj
            # Force engine unavailable, --require-engine 無し → skip 経路
            vn.check_engine = lambda: (False, "connection refused")

            captured = _io.StringIO()
            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = ["voicevox_narration.py", "--json-log"]
            try:
                with contextlib.redirect_stdout(captured):
                    ret = vn.main()
            finally:
                _sys.argv = old_argv

            assert_eq(ret, 0, "engine skip exit 0")
            stdout = captured.getvalue()
            lines = [ln for ln in stdout.splitlines() if ln.strip()]
            if not lines:
                raise AssertionError("--json-log produced no stdout for skip path")
            last = lines[-1]
            try:
                payload = json.loads(last)
            except json.JSONDecodeError as e:
                raise AssertionError(
                    f"--json-log skip path last line not pure JSON: {last!r} ({e})"
                ) from e
            # Phase 3 obs migration core: v0 status `engine_skipped` → v1 `skipped`,
            # category=`engine_unavailable` (docs/OBSERVABILITY.md §Migration Policy)
            assert_eq(payload.get("status"), "skipped", "skip status field (v1)")
            assert_eq(payload.get("category"), "engine_unavailable", "skip category field")
            assert_eq(payload.get("exit_code"), 0, "skip exit_code field")
            assert_eq(payload.get("schema_version"), 1, "v1 schema_version")
            if "info" not in payload:
                raise AssertionError(
                    f"engine skip JSON missing 'info' field: {payload}"
                )
    finally:
        for k, v in state.items():
            setattr(vn, k, v)
        vn.check_engine = original_check_engine


def test_voicevox_json_log_engine_strict_path() -> None:
    """P3 review P1 fix: --require-engine + engine 不在 → exit 4 + status json."""
    import voicevox_narration as vn
    import io as _io
    import contextlib

    state = {"PROJ": vn.PROJ}
    original_check_engine = vn.check_engine

    try:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            vn.PROJ = proj
            vn.check_engine = lambda: (False, "connection refused")

            captured = _io.StringIO()
            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = ["voicevox_narration.py", "--json-log", "--require-engine"]
            try:
                with contextlib.redirect_stdout(captured):
                    ret = vn.main()
            finally:
                _sys.argv = old_argv

            assert_eq(ret, 4, "engine strict fail exit 4")
            lines = [ln for ln in captured.getvalue().splitlines() if ln.strip()]
            payload = json.loads(lines[-1])
            # Phase 3 obs migration core: v0 status `engine_unavailable_strict` → v1 `error`,
            # category=`engine_unavailable` (docs/OBSERVABILITY.md §Migration Policy)
            assert_eq(payload.get("status"), "error", "strict status field (v1)")
            assert_eq(
                payload.get("category"), "engine_unavailable",
                "strict category field",
            )
            assert_eq(payload.get("exit_code"), 4, "strict exit_code field")
            assert_eq(payload.get("schema_version"), 1, "v1 schema_version")
    finally:
        for k, v in state.items():
            setattr(vn, k, v)
        vn.check_engine = original_check_engine


def test_visual_smoke_cli_mismatch_and_restore() -> None:
    """Phase 3-V P4 review P2 fix (CODEX_2ND_BATCH_REVIEW:9):
    cli() の finally restore + mismatched/exit 2 を mock-only で verify."""
    import visual_smoke as vs
    import shutil as _shutil

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        (proj / "src").mkdir()
        original_content = (
            "export const FORMAT: VideoFormat = 'youtube';\n"
            "export const FPS = 30;\n"
        )
        config_path = proj / "src" / "videoConfig.ts"
        config_path.write_text(original_content, encoding="utf-8")
        main_video = proj / "public" / "main.mp4"
        main_video.parent.mkdir()
        main_video.touch()
        remotion_bin = proj / "node_modules" / ".bin" / "remotion"
        remotion_bin.parent.mkdir(parents=True)
        remotion_bin.touch()
        out_dir = proj / "out" / "visual_smoke"

        def fake_render(project, frame, png_out):
            png_out.parent.mkdir(parents=True, exist_ok=True)
            png_out.write_bytes(b"fake-png")

        # backup originals
        bak = {
            "PROJ": vs.PROJ,
            "VIDEO_CONFIG": vs.VIDEO_CONFIG,
            "MAIN_VIDEO": vs.MAIN_VIDEO,
            "REMOTION_BIN": vs.REMOTION_BIN,
            "render_still": vs.render_still,
            "probe_dim": vs.probe_dim,
            "has_drawtext_filter": vs.has_drawtext_filter,
        }
        original_which = _shutil.which
        try:
            vs.PROJ = proj
            vs.VIDEO_CONFIG = config_path
            vs.MAIN_VIDEO = main_video
            vs.REMOTION_BIN = remotion_bin
            vs.render_still = fake_render
            # mismatch を強制 (期待 1920x1080、actual 1000x1000)
            vs.probe_dim = lambda png: (1000, 1000)
            vs.has_drawtext_filter = lambda: False
            _shutil.which = lambda cmd: "/usr/bin/" + cmd

            import sys as _sys
            old_argv = _sys.argv
            _sys.argv = [
                "visual_smoke.py",
                "--formats", "youtube",
                "--frames", "30",
                "--no-grid",
                "--out-dir", str(out_dir),
            ]
            try:
                ret = vs.cli()
            finally:
                _sys.argv = old_argv

            assert_eq(ret, 2, "mismatch detected → cli exit 2")
            restored = config_path.read_text(encoding="utf-8")
            if restored != original_content:
                raise AssertionError(
                    f"finally restore failed: expected {original_content!r}, got {restored!r}"
                )
            # summary.json も書かれている
            summary_path = out_dir / "summary.json"
            if not summary_path.exists():
                raise AssertionError("summary.json not written")
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            assert_eq(summary.get("mismatched"), 1, "summary mismatched=1")
            assert_eq(summary.get("total"), 1, "summary total=1")
        finally:
            for k, v in bak.items():
                setattr(vs, k, v)
            _shutil.which = original_which


def test_visual_smoke_patch_format_youtube_to_short() -> None:
    """Phase 3-V post-freeze 第2弾 P4 (Codex CODEX_NEXT_PRIORITY:21-23):
    visual_smoke.patch_format が videoConfig.ts の FORMAT 行を正しく書き換える."""
    import visual_smoke as vs

    src = "export const FORMAT: VideoFormat = 'youtube';\nexport const FPS = 30;\n"
    out = vs.patch_format(src, "short")
    if "export const FORMAT: VideoFormat = 'short';" not in out:
        raise AssertionError(f"patch_format failed: {out!r}")
    if "export const FORMAT: VideoFormat = 'youtube';" in out:
        raise AssertionError(
            f"patch_format left old FORMAT in content: {out!r}"
        )
    # 他行は破壊しない
    if "export const FPS = 30;" not in out:
        raise AssertionError(
            f"patch_format broke unrelated lines: {out!r}"
        )


def test_visual_smoke_patch_format_no_match_raises() -> None:
    """Phase 3-V P4: FORMAT 行不在で ValueError."""
    import visual_smoke as vs

    bad = "export const FPS = 30;\n// no FORMAT line\n"
    raised = False
    try:
        vs.patch_format(bad, "short")
    except ValueError:
        raised = True
    if not raised:
        raise AssertionError("patch_format should raise ValueError on missing FORMAT")


def test_visual_smoke_patch_format_round_trip() -> None:
    """Phase 3-V P4: youtube → short → youtube round trip で原型に戻る."""
    import visual_smoke as vs

    src = "export const FORMAT: VideoFormat = 'youtube';\n"
    step1 = vs.patch_format(src, "short")
    step2 = vs.patch_format(step1, "youtube")
    if step2 != src:
        raise AssertionError(
            f"round trip mismatch: src={src!r} step2={step2!r}"
        )


def test_visual_smoke_format_dims_completeness() -> None:
    """Phase 3-V P4: FORMAT_DIMS が 3 format 全部を期待 dimension で持つ."""
    import visual_smoke as vs

    expected = {
        "youtube": (1920, 1080),
        "short": (1080, 1920),
        "square": (1080, 1080),
    }
    for fmt, dims in expected.items():
        if fmt not in vs.FORMAT_DIMS:
            raise AssertionError(f"FORMAT_DIMS missing {fmt}")
        if vs.FORMAT_DIMS[fmt] != dims:
            raise AssertionError(
                f"FORMAT_DIMS[{fmt}] = {vs.FORMAT_DIMS[fmt]}, expected {dims}"
            )
    # 余計な entry がない (3 format のみ)
    if set(vs.FORMAT_DIMS.keys()) != set(expected.keys()):
        raise AssertionError(
            f"FORMAT_DIMS unexpected extras: {set(vs.FORMAT_DIMS.keys()) - set(expected.keys())}"
        )


def test_observability_helper_status_map() -> None:
    """v0 → v1 status mapping が doc table と整合しているか検証。

    docs/OBSERVABILITY.md §Migration Policy で defined v0 → v1 mapping を
    helper の STATUS_MAP がカバーすること。失敗時 = mapping completeness 不足。
    """
    from _observability import STATUS_MAP, map_status

    # 既存 v0 emit 経路 (slide-plan + voicevox 全 status) が STATUS_MAP に登録済み確認。
    # Codex 20:48 PR3 review P2 #2 で must_have の文言ズレ指摘を解消、voicevox error statuses 列挙。
    must_have = {
        # success / skip / dry-run
        "success", "api_key_skipped", "engine_skipped", "engine_unavailable_strict",
        "list_speakers", "dry_run",
        # slide-plan error variants (PR-F で cost_guard_aborted 追加)
        "cost_guard_arg_invalid", "cost_guard_aborted", "inputs_missing", "rate_limited",
        "api_http_error", "llm_json_invalid",
        # PR-G error path tail audit additions
        "typo_dict_invalid", "telop_ts_missing", "telop_ts_invalid", "kpi_calc_error",
        "write_config_parse_error", "write_config_write_error",
        "out_dir_mkdir_error", "video_config_read_error",
        "video_config_write_error", "video_config_restore_error",
        "summary_write_error",
        # voicevox error variants (voicevox_narration.py 全 emit_json("error_status",...) 経路)
        "transcript_missing", "transcript_invalid", "no_chunks", "invalid_fps",
        "stale_cleanup_fail", "vad_invalid", "no_chunks_succeeded",
        "partial_chunks_disallowed", "concat_fail",
        "write_narration_data_wave_error", "sentinel_write_fail",
        # compare_telop_split (Codex 21:01 verdict S3-6 KPI comparison)
        "all_pass", "some_fail",
        # visual_smoke (Codex 21:01 verdict S3-4 dimension regression)
        "smoke_ok", "dimension_mismatch", "env_error", "grid_failed",
        # visual_smoke early return (Codex 21:14 PR4 review P1 #1 で 1 invocation 1 emission contract 化)
        "usage_error_formats_empty", "usage_error_unknown_format",
        "usage_error_frames_empty", "usage_error_frames_negative", "usage_error_patch_format",
        "env_tool_missing", "env_main_video_missing",
        "env_remotion_cli_missing", "env_video_config_missing",
        "usage_error_frames_invalid",
        # preflight_video (PR-B、Codex 21:01 step 3 S3-3 既存 stdout 維持 + tail v1)
        "preflight_ok", "input_not_found", "no_video_stream", "ffprobe_failed",
        "risks_not_allowed", "format_inference_failed",
        # build_slide_data / build_telop_data (PR-C、Codex 21:01 step 3 S3-5 user_content redaction)
        "build_slide_ok", "build_telop_ok",
        # build_slide / build_telop error variants (Codex 21:46 PR6 review P1 fix)
        "build_slide_inputs_missing", "build_slide_transcript_invalid",
        "build_slide_plan_missing", "build_slide_plan_invalid",
        "build_telop_transcript_invalid",
    }
    missing = must_have - set(STATUS_MAP.keys())
    assert not missing, f"STATUS_MAP missing v0 statuses: {missing}"

    # mapping verdict
    assert map_status("success") == ("ok", None)
    assert map_status("api_key_skipped") == ("skipped", "api_key_missing")
    assert map_status("engine_skipped") == ("skipped", "engine_unavailable")
    assert map_status("list_speakers") == ("ok", "list_speakers")
    assert map_status("dry_run") == ("dry_run", None)
    assert map_status("rate_limited") == ("error", "rate_limited")
    # Unknown status → ("error", v0 status as category)
    assert map_status("nonexistent_v0") == ("error", "nonexistent_v0")


def test_observability_status_map_lint() -> None:
    """STATUS_MAP の static 整合性を lint する (Codex 01:57 verdict AB)。

    `test_observability_helper_status_map` は emit site 追加漏れ
    (must_have set diff) と 6 件の代表 mapping spot assert を行うが、
    STATUS_MAP 内部の構造的不整合は検出しない:
      - dict literal silent overwrite (重複 key)
      - 不正 v1_status (typo: "okay" / "skip" / "errror")
      - 空文字列 / 不正型 category
      - tuple shape mismatch (要素数 != 2)

    map_status() の defensive fallback `("error", v0_status)` が
    unknown key を silent fallback するため、上記 drift は runtime
    では即座に表面化せず category lint や consumer 集計で contract
    drift を起こす。本 test は AST parse + value scan で early fail
    させる lint 層。
    """
    import ast

    from _observability import STATUS_MAP

    # (1) value shape: 全 entry は 2-tuple、key は非空 str
    for k, v in STATUS_MAP.items():
        assert isinstance(k, str) and k, f"STATUS_MAP key invalid: {k!r}"
        assert isinstance(v, tuple) and len(v) == 2, (
            f"STATUS_MAP[{k!r}] must be 2-tuple, got {v!r}"
        )

    # (2) v1_status は schema 定義の 4 値のみ
    valid_v1_statuses = {"ok", "skipped", "error", "dry_run"}
    for k, (v1_status, _v1_category) in STATUS_MAP.items():
        assert v1_status in valid_v1_statuses, (
            f"STATUS_MAP[{k!r}] has invalid v1_status {v1_status!r}, "
            f"must be one of {sorted(valid_v1_statuses)}"
        )

    # (3) v1_category は str か None、空文字列・空白のみ禁止
    for k, (_v1_status, v1_category) in STATUS_MAP.items():
        if v1_category is None:
            continue
        assert isinstance(v1_category, str), (
            f"STATUS_MAP[{k!r}] category must be str-or-None, "
            f"got {type(v1_category).__name__}"
        )
        assert v1_category.strip(), (
            f"STATUS_MAP[{k!r}] category is empty/whitespace-only: {v1_category!r}"
        )

    # (4) duplicate key 検出: dict literal は silent overwrite なので
    # _observability.py を AST parse して dict literal の key node を集計。
    # Codex 02:05 PR-T review P2 fix: first-match break だと後続の同名
    # assign を見落とすので、module 全体から STATUS_MAP assignment を
    # 全列挙し「ちょうど 1 件」を assert してから lint 実行 (Python
    # runtime は最後の binding を使うので、複数 assign は contract
    # ambiguity として lint レイヤーで reject する)。
    obs_path = SCRIPTS / "_observability.py"
    tree = ast.parse(obs_path.read_text(encoding="utf-8"))
    status_map_assigns = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "STATUS_MAP"
    ]
    assert len(status_map_assigns) == 1, (
        f"STATUS_MAP must have exactly one module-level assignment, "
        f"found {len(status_map_assigns)} at lines "
        f"{[n.lineno for n in status_map_assigns]}"
    )
    assign_node = status_map_assigns[0]
    assert isinstance(assign_node.value, ast.Dict), (
        "STATUS_MAP must be a dict literal (lint 前提)"
    )
    literal_keys: list[str] = []
    for key_node in assign_node.value.keys:
        assert isinstance(key_node, ast.Constant) and isinstance(
            key_node.value, str
        ), (
            f"STATUS_MAP key must be a literal str, "
            f"got {ast.dump(key_node) if key_node else 'None (dict-unpack)'}"
        )
        literal_keys.append(key_node.value)
    duplicates = sorted({k for k in literal_keys if literal_keys.count(k) > 1})
    assert not duplicates, (
        f"STATUS_MAP has duplicate keys (dict literal silent overwrite): "
        f"{duplicates}"
    )
    # AST literal key 数 = runtime dict key 数 でもあるべき
    # (overwrite が起きていれば dict は短くなる)
    assert len(literal_keys) == len(STATUS_MAP), (
        f"STATUS_MAP literal has {len(literal_keys)} keys but dict has "
        f"{len(STATUS_MAP)} (silent overwrite suspected)"
    )


def test_observability_status_map_category_format_invariant() -> None:
    """`STATUS_MAP` の category string format invariant lock-in
    (Codex 04:11 PR-AN verdict AW、observability v1 contract drift 防止)。

    `category` は v1 status JSON の bucket field で、downstream consumer /
    log analyzer / dashboard が同 category 値で payload を集約する。STATUS_MAP
    の (status, category) tuple の category 値が以下の drift パターンに
    silent regression するのを早期検出:

      - UPPERCASE letter / mixed case (downstream grep が大文字小文字違いで
        bucket miss)
      - whitespace / 制御文字 / non-ASCII 特殊文字 (1-line JSON / log line
        format 破壊)
      - leading / trailing dash / underscore (typo 由来 silent drift)
      - 空文字列 (PR-T `test_observability_status_map_lint` で既に reject、
        本 test は format 側を独立 lock)

    permissive regex `^[a-z](?:[a-z0-9_-]*[a-z0-9])?$` で lock-in:
      - lowercase letter で開始
      - 本体は lowercase + digit + underscore + dash 許容
      - 末尾は必ず lowercase letter or digit (trailing _ / - を reject)
      - 1 char (単独 letter) も accept

    snake_case と kebab-case の両方を accept (現 STATUS_MAP は両方混在、
    segment 単位の case style 統一は別 lint 候補で別 PR)。

    `None` (success → ("ok", None) 経路) は除外、str-only 値のみ regex 検査。
    """
    import re as _re

    from _observability import STATUS_MAP

    pat = _re.compile(r"^[a-z](?:[a-z0-9_-]*[a-z0-9])?$")

    fails = []
    for v0_status, (v1_status, v1_category) in STATUS_MAP.items():
        if v1_category is None:
            continue
        # 必須: str
        assert isinstance(v1_category, str), (
            f"STATUS_MAP[{v0_status!r}] category must be str-or-None, "
            f"got {type(v1_category).__name__}"
        )
        # 必須: pattern match
        if not pat.fullmatch(v1_category):
            fails.append((v0_status, v1_category))

    assert not fails, (
        f"STATUS_MAP contains {len(fails)} category value(s) violating "
        f"format `^[a-z][a-z0-9_-]*$`: {fails[:10]}"
    )

    # negative control: regex 自体が drift パターンを正しく reject する確認
    drift_patterns = [
        "UPPERCASE",                # uppercase
        "Mixed-Case",               # mixed
        "1leading-digit",           # leading digit
        "-leading-dash",            # leading dash
        "_leading_underscore",      # leading underscore
        "trailing-",                # trailing dash
        "trailing_",                # trailing underscore
        "with space",               # whitespace
        "with\ttab",                # tab
        "with\nnewline",            # newline
        "with.dot",                 # dot
        "with/slash",               # slash
        "with$dollar",              # special char
        "",                         # empty (PR-T と相互 lock)
    ]
    for bad in drift_patterns:
        assert not pat.fullmatch(bad), (
            f"regex must reject drift pattern {bad!r}, but it passed"
        )


def test_observability_safe_artifact_path_redacts() -> None:
    """abs_path が project_root 相対 / <HOME> placeholder に正規化されること。

    `unsafe_keep_abs_path=True` で raw 維持を確認。
    """
    from _observability import safe_artifact_path

    proj = "/Users/rokumasuda/tmp/proj"
    # in project: relative
    assert safe_artifact_path(f"{proj}/public/main.mp4", project_root=proj) == "public/main.mp4"
    # outside project: HOME placeholder
    sp = safe_artifact_path("/Users/rokumasuda/elsewhere/foo.json", project_root=proj)
    assert sp.startswith("<HOME>"), f"expected <HOME> placeholder, got {sp!r}"
    # unsafe_keep_abs_path: raw
    assert safe_artifact_path(f"{proj}/x.json", project_root=proj,
                              unsafe_keep_abs_path=True) == f"{proj}/x.json"
    # None passthrough
    assert safe_artifact_path(None, project_root=proj) is None
    # Codex 21:14 PR4 review P1 #2 fix: /tmp / /var/folders 等 system tmpdir は <TMP> placeholder
    sp_tmp = safe_artifact_path("/tmp/telop_baseline.ts", project_root=proj)
    assert sp_tmp.startswith("<TMP>"), f"expected <TMP> placeholder for /tmp/, got {sp_tmp!r}"
    assert "/tmp/" not in sp_tmp, f"raw /tmp/ leaked: {sp_tmp!r}"
    # macOS tmpfs (/var/folders) も <TMP>
    sp_macos = safe_artifact_path("/var/folders/kn/abc/foo.ts", project_root=proj)
    assert sp_macos.startswith("<TMP>"), f"expected <TMP> for /var/folders, got {sp_macos!r}"
    # 任意の絶対 path (project / HOME / TMP 外) は <ABS>/basename に隠す
    sp_abs = safe_artifact_path("/etc/some_secret/foo.ts", project_root=proj)
    assert sp_abs.startswith("<ABS>"), f"expected <ABS> placeholder, got {sp_abs!r}"
    assert "/etc/" not in sp_abs and "some_secret" not in sp_abs, \
        f"raw absolute path structure leaked: {sp_abs!r}"


def test_observability_safe_artifact_path_collision_corners() -> None:
    """`safe_artifact_path()` の path 衝突 corner case を lock-in (Codex 02:09 verdict AE)。

    既存 `test_observability_safe_artifact_path_redacts` は基本 4 経路
    (in-project / outside / unsafe_keep / None / TMP / ABS) を網羅するが、
    以下の collision-prone な corner は未被覆:

      (1) project_root + repo_root の同時指定で path が両方の配下にある
          場合、最初の root (project_root) 側が優先されること
          (relative_to の最短表現に勝手に切り替わらない、output 安定性)
      (2) path == project_root の完全一致 → relative_to は "." を返す
          (None / 空文字列 fallback でない、artifact 表示の正規形)
      (3) `..` を含む traversal で project_root の外に escape する path
          → relative_to に成功させず lexical redaction の placeholder に
          落として、root 直下に偽装した artifact を漏らさない
          (出力先一意性 / 監査 trail 健全性の保証)
      (4) project_root に trailing slash を付けても結果が変わらない
          (caller 流儀差で output 揺れが出ない契約)
      (5) `proj` と `proj_extra` の似た prefix が誤って collision 扱いに
          ならない (substring match で root 判定していない、Path.relative_to
          が segment 単位で判定する性質に依存)
      (6) repo_root のみ指定で path が repo 配下のとき repo 相対化される
          (project_root 未指定経路のフォールバック contract)
      (7) 相対 path 入力 (絶対化しない) が意図せず HOME / TMP / ABS
          placeholder に巻き込まれず as-is で返ること
    """
    from _observability import safe_artifact_path

    proj = "/Users/rokumasuda/tmp/proj"
    repo = "/Users/rokumasuda/tmp"

    # (1) project_root と repo_root の両方が match: project_root 優先
    p1 = safe_artifact_path(f"{proj}/sub/x.json", project_root=proj, repo_root=repo)
    assert p1 == "sub/x.json", (
        f"expected project_root precedence ('sub/x.json'), got {p1!r}"
    )

    # (2) path が project_root と完全一致 → "."
    p2 = safe_artifact_path(proj, project_root=proj)
    assert p2 == ".", f"expected '.' for self-relative, got {p2!r}"

    # (3) `..` traversal escape: relative_to で root 配下に偽装しない
    p3 = safe_artifact_path(f"{proj}/../secret/foo.txt", project_root=proj)
    # project_root 直下のように見える形 ('secret/foo.txt' or 'foo.txt') では
    # 絶対 NG (relative_to に成功してはいけない)。<HOME> / <TMP> / <ABS> / 元 path 由来の
    # placeholder に落ちる必要がある。
    assert p3.startswith(("<HOME>", "<TMP>", "<ABS>")), (
        f"escape via .. must yield placeholder, got {p3!r}"
    )
    assert not p3.startswith("secret/"), (
        f"escape via .. must not be exposed as project-relative, got {p3!r}"
    )

    # (4) trailing slash on project_root → 結果が同じ
    p4_no = safe_artifact_path(f"{proj}/x.json", project_root=proj)
    p4_yes = safe_artifact_path(f"{proj}/x.json", project_root=proj + "/")
    assert p4_no == p4_yes == "x.json", (
        f"trailing slash must not change output: no_slash={p4_no!r}, "
        f"with_slash={p4_yes!r}"
    )

    # (5) similar prefix (`proj_extra` not under `proj`) は collision にしない
    p5 = safe_artifact_path(f"{repo}/proj_extra/file.txt", project_root=proj)
    # `proj_extra` は project_root 配下ではないので relative_to fail、
    # lexical redaction で placeholder。`proj_extra/file.txt` 形式を
    # project-relative として返してはいけない。
    assert p5.startswith(("<HOME>", "<TMP>", "<ABS>")), (
        f"similar-prefix dir must yield placeholder, got {p5!r}"
    )
    assert not p5.startswith("proj_extra/") and not p5.startswith("../"), (
        f"similar-prefix substring leak: {p5!r}"
    )

    # (6) repo_root only fallback
    p6 = safe_artifact_path(f"{repo}/proj/sub/x.json", repo_root=repo)
    assert p6 == "proj/sub/x.json", (
        f"expected repo-relative ('proj/sub/x.json') with only repo_root, got {p6!r}"
    )

    # (7) 相対 path 入力は as-is (placeholder 巻き込み防止)
    p7 = safe_artifact_path("public/main.mp4", project_root=proj)
    assert p7 == "public/main.mp4", (
        f"relative input must pass through unchanged, got {p7!r}"
    )


def test_observability_safe_artifact_path_tilde_expansion() -> None:
    """`~/...` 入力が `<HOME>` placeholder / project-relative に正規化されること
    (Codex 02:14 PR-V verdict AF、helper-level redaction contract gap fix)。

    `docs/OBSERVABILITY.md §Path Policy` は abs path を `<HOME>` /
    `<TMP>` / `<ABS>` placeholder に正規化する契約だが、旧実装は
    `Path(s).is_absolute()` ガードで `~` を `expanduser` せず、
    `Path("~/x").is_absolute() == False` の python 仕様により
    後段 `_lexical_redact(s, home)` も `s.startswith(home)` 判定を
    通せず literal `~/...` をそのまま漏らしていた。

    fix: `s.startswith("~")` 時のみ `os.path.expanduser` を早期適用、
    後段 resolve / relative_to / lexical_redact が一貫して absolute
    path として処理。相対 path 入力 (`public/main.mp4` 等) は
    PR-U test 7 の as-is passthrough invariant を維持する。
    """
    import os as _os

    from _observability import safe_artifact_path
    home = _os.path.expanduser("~")

    # (1) `~/outside/...` で root 未指定 → <HOME>/... placeholder
    p1 = safe_artifact_path("~/outside/foo.json")
    assert p1 == "<HOME>/outside/foo.json", (
        f"~ must expand and redact to <HOME>, got {p1!r}"
    )
    assert "~" not in p1, f"raw ~ leaked: {p1!r}"

    # (2) `~/outside/...` w/ 異なる project_root → relative_to fail で <HOME>
    p2 = safe_artifact_path(
        "~/outside/foo.json", project_root="/Users/rokumasuda/tmp/proj"
    )
    assert p2 == "<HOME>/outside/foo.json", (
        f"~ outside project must yield <HOME>, got {p2!r}"
    )

    # (3) `~/...` 入力 + `~/...` project_root → 両方 expanduser されて
    # project-relative になる (caller 流儀差で output 揺れない)
    p3 = safe_artifact_path("~/tmp/proj/sub/x.json", project_root="~/tmp/proj")
    assert p3 == "sub/x.json", (
        f"both ~ args must produce project-relative, got {p3!r}"
    )

    # (4) `~/...` 入力 + abs project_root も match (mixed style 吸収)
    p4 = safe_artifact_path(
        "~/tmp/proj/sub/x.json", project_root=f"{home}/tmp/proj"
    )
    assert p4 == "sub/x.json", (
        f"~ input + abs root must produce project-relative, got {p4!r}"
    )

    # (5) `unsafe_keep_abs_path=True` は `~/...` も bypass で raw 維持
    p5 = safe_artifact_path("~/x", unsafe_keep_abs_path=True)
    assert p5 == "~/x", f"unsafe_keep must preserve raw ~/, got {p5!r}"

    # (6) regression guard: 相対 path 入力 (`~` 接頭辞なし) は as-is、
    # cwd-relative 解決で巻き込み redaction しない (PR-U test 7 invariant)
    p6 = safe_artifact_path(
        "public/main.mp4", project_root="/Users/rokumasuda/tmp/proj"
    )
    assert p6 == "public/main.mp4", (
        f"non-tilde relative input must passthrough, got {p6!r}"
    )

    # (7) regression guard: 既存 abs-HOME 経路が壊れていない
    p7 = safe_artifact_path(
        f"{home}/elsewhere/foo.json", project_root="/Users/rokumasuda/tmp/proj"
    )
    assert p7.startswith("<HOME>"), (
        f"abs HOME path regression, got {p7!r}"
    )

    # (8) Codex 02:18 PR-V re-review P2: 存在しない user 名 (`~unknown/x`)
    # は `os.path.expanduser` で展開されず literal のまま残るため、
    # `<ABS>/<basename>` placeholder に落として user 名 + path 構造の
    # 漏れを防ぐ (expanduser-fail 経路の最終ガード)。
    p8 = safe_artifact_path("~definitely_not_a_real_user_zz/secret/x.json")
    assert p8 == "<ABS>/x.json", (
        f"~unknownuser must be redacted to <ABS>/<basename>, got {p8!r}"
    )
    assert "definitely_not_a_real_user_zz" not in p8, (
        f"unknown user name leaked: {p8!r}"
    )
    assert "secret" not in p8, f"middle path segment leaked: {p8!r}"


def test_observability_user_content_meta_no_raw() -> None:
    """user_content_meta が length / sha256 のみ返し、raw text を含まないこと。

    Codex 20:14 review P1 #2 で raw partial preview 禁止と doc に固定。
    """
    from _observability import user_content_meta

    secret_text = "API_KEY=sk-secret123 transcript content"
    meta = user_content_meta(secret_text)
    assert "raw" not in meta, "user_content_meta should not return raw"
    assert "first_chars" not in meta, "user_content_meta should not include preview"
    assert "preview" not in meta
    assert meta["length"] == len(secret_text)
    assert "sha256" in meta and len(meta["sha256"]) == 16
    # raw text が meta dict に紛れていない
    serialized = json.dumps(meta, ensure_ascii=False)
    assert "API_KEY" not in serialized
    assert "sk-secret123" not in serialized
    assert "transcript content" not in serialized


def test_observability_sha256_hash_format_invariant() -> None:
    """`user_content_meta()` / `redact_provider_body()` が出す `sha256` field の
    format invariant lock-in (Codex 03:45 PR-AJ verdict AX、observability v1
    contract drift 防止)。

    `sha256` は v1 schema の機械的指紋で、downstream consumer / log diff /
    regression test が「16 char lower hex prefix」前提で扱う。`_hash16()`
    内部実装が SHA-256 full hex (64 char) や upper case や別 encoding に
    silent drift した場合、format-dependent caller が壊れる contract drift
    を early fail させる。

    本 test は 6 層で format invariant を固定:
      (1) `user_content_meta()` の `sha256` field: str / 16 char / `[0-9a-f]`
      (2) `redact_provider_body()` 出力の `sha256` field: 同 format
      (3) deterministic: 同 input → 同 hash (純関数性)
      (4) 既知入力に対する固定値 snapshot (algorithm drift detection)
      (5) 異 input → 異 hash (algorithm 変更による全 collision 化を検出)
      (6) 空文字 / 非 ASCII / control char / emoji 入力でも format 維持

    `_hash16()` 直接 import せず、public API (`user_content_meta` /
    `redact_provider_body`) 経由で format を verify する (downstream consumer
    と同じ抽象 layer で test)。
    """
    import re as _re

    from _observability import redact_provider_body, user_content_meta

    hex16_re = _re.compile(r"\A[0-9a-f]{16}\Z")

    # (1) user_content_meta の sha256 format
    meta = user_content_meta("hello world")
    assert isinstance(meta, dict)
    sha = meta["sha256"]
    assert isinstance(sha, str), (
        f"sha256 must be str, got {type(sha).__name__}"
    )
    assert len(sha) == 16, f"sha256 must be 16 char, got {len(sha)}: {sha!r}"
    assert hex16_re.fullmatch(sha), (
        f"sha256 must match [0-9a-f]{{16}}, got {sha!r}"
    )

    # (2) redact_provider_body の sha256 format
    summary = redact_provider_body("API response body content")
    sha2 = summary["sha256"]
    assert isinstance(sha2, str)
    assert len(sha2) == 16
    assert hex16_re.fullmatch(sha2), (
        f"provider body sha256 format drift: {sha2!r}"
    )

    # (3) deterministic: 同 input → 同 hash (純関数性、salt 混入 / 時刻依存
    # にリファクタされた場合 regression detect)
    meta_a = user_content_meta("hello world")
    meta_b = user_content_meta("hello world")
    assert meta_a["sha256"] == meta_b["sha256"], (
        f"sha256 must be deterministic for same input: "
        f"{meta_a['sha256']!r} vs {meta_b['sha256']!r}"
    )
    summary_a = redact_provider_body("API response body content")
    summary_b = redact_provider_body("API response body content")
    assert summary_a["sha256"] == summary_b["sha256"]

    # (4) 既知 input snapshot: SHA-256("hello world", utf-8) の最初 16 hex
    # = "b94d27b9934d3e08" (Python 標準 hashlib 実測)、helper の
    # `_hash16` が hashlib.sha256 + utf-8 encode + hexdigest()[:16] 仕様を
    # 維持していることを bytes-level で snapshot 化
    expected_hash_hello = "b94d27b9934d3e08"
    assert meta["sha256"] == expected_hash_hello, (
        f"sha256 algorithm drift for 'hello world': "
        f"expected {expected_hash_hello}, got {meta['sha256']!r}"
    )

    # (5) 異 input → 異 hash (collision 偶発以外で必ず差分、algorithm
    # 変更で全部同じ hash を返すような regression を detect)
    meta_diff = user_content_meta("different content")
    assert meta_diff["sha256"] != meta["sha256"]
    assert hex16_re.fullmatch(meta_diff["sha256"])

    # (6) 空文字 / 非 ASCII / control char / emoji 入力でも format invariant 維持
    for input_text in ("", "日本語テスト", "\n\t\r control",
                       "long " * 100, "🎬 emoji 🚀"):
        m = user_content_meta(input_text)
        sha_x = m["sha256"]
        assert isinstance(sha_x, str)
        assert len(sha_x) == 16
        assert hex16_re.fullmatch(sha_x), (
            f"sha256 format drift for input {input_text!r}: {sha_x!r}"
        )


def test_observability_redact_provider_body_default_strict() -> None:
    """provider_response_body default が raw 禁止、unsafe_dump=True で raw を返すこと。

    docs/OBSERVABILITY.md §Redaction Rules provider_response_body strict 化。
    """
    from _observability import redact_provider_body

    raw_body = '{"error": "rate_limited", "message": "internal token leaked: sk-LEAK"}'
    # default: structured summary、raw body 不含
    redacted = redact_provider_body(raw_body)
    assert redacted["kind"] == "summary"
    assert "body" not in redacted
    assert "sk-LEAK" not in json.dumps(redacted, ensure_ascii=False)
    assert redacted["length"] == len(raw_body)
    # unsafe_dump: raw が出る (debug-only flag が機能していること)
    raw_dump = redact_provider_body(raw_body, unsafe_dump=True)
    assert raw_dump["kind"] == "raw"
    assert raw_dump["body"] == raw_body


def test_observability_build_status_v1_schema() -> None:
    """build_status が v1 schema (schema_version / category / redaction 等) を出力すること。

    docs/OBSERVABILITY.md §Common Fields の必須 fields 全部含んでいるか。
    """
    from _observability import SCHEMA_VERSION, REDACTION_VERSION, build_status

    p = build_status(
        script="generate_slide_plan",
        v0_status="success",
        exit_code=0,
        counts={"slides": 10},
        artifacts=[{"path": "out/foo.json", "kind": "json"}],
        cost={"currency": "USD", "estimate": 0.0023},
        redaction_rules=["abs_path"],
        run_id="test-run-001",
        # v0 extra (model / max_tokens / output) — top-level merge
        model="claude-haiku-4-5",
        max_tokens=2048,
        output="out/foo.json",
    )
    # v1 fields
    assert p["schema_version"] == SCHEMA_VERSION
    assert p["script"] == "generate_slide_plan"
    assert p["status"] == "ok"
    assert p["category"] is None
    assert p["ok"] is True
    assert p["exit_code"] == 0
    assert p["counts"] == {"slides": 10}
    assert p["artifacts"] == [{"path": "out/foo.json", "kind": "json"}]
    assert p["cost"] == {"currency": "USD", "estimate": 0.0023}
    assert p["redaction"] == {"applied_rules": ["abs_path"], "version": REDACTION_VERSION}
    assert p["run_id"] == "test-run-001"
    # v0 compat: extras at top level
    assert p["model"] == "claude-haiku-4-5"
    assert p["max_tokens"] == 2048
    assert p["output"] == "out/foo.json"


def test_observability_build_status_duration_ms_and_category_override() -> None:
    """Codex 21:01 step 3 S3-7: helper hardening — `duration_ms` を common field
    として明示 + `category_override` で STATUS_MAP の v1_category を上書き可能。
    """
    from _observability import build_status

    # duration_ms None で payload に含まれない (default 動作)
    p_default = build_status(script="x", v0_status="success", exit_code=0)
    assert "duration_ms" not in p_default, f"duration_ms should be omitted when None: {p_default}"
    # duration_ms 明示で含まれる
    p_dur = build_status(script="x", v0_status="success", exit_code=0, duration_ms=1234)
    assert p_dur["duration_ms"] == 1234

    # category_override で STATUS_MAP の category を上書き
    # success → ("ok", None) by default
    p_no_override = build_status(script="x", v0_status="success", exit_code=0)
    assert p_no_override["category"] is None
    # category_override="kpi-comparison" で上書き
    p_override = build_status(
        script="x", v0_status="success", exit_code=0,
        category_override="kpi-comparison",
    )
    assert p_override["category"] == "kpi-comparison"
    assert p_override["status"] == "ok"  # v1_status は STATUS_MAP のまま (success → ok)
    # category_override="dimension-regression" を error 系の v0 status に適用
    p_err = build_status(
        script="visual_smoke", v0_status="dimension_mismatch", exit_code=2,
        category_override="dimension-regression",
    )
    assert p_err["status"] == "error"
    assert p_err["category"] == "dimension-regression"


def test_observability_build_status_top_level_field_order() -> None:
    """`build_status()` 出力の top-level field 順序を deterministic に lock-in
    (Codex 02:24 PR-W verdict W、observability v1 contract drift 防止)。

    Python 3.7+ では dict insertion order が保たれ、`json.dumps` も
    その順で書き出すため、status JSON tail の field 順は contract の
    一部 (consumer 側 diff / log grep / regression test 安定性)。
    本 test は次の不変条件を lock-in:

    1. reserved core 10 field の順序固定:
       schema_version → script → status → ok → exit_code → category
       → counts → artifacts → cost → redaction
    2. optional field (duration_ms / run_id / parent_run_id / step_id)
       は reserved の後、source 宣言順 (duration_ms → run_id →
       parent_run_id → step_id) で並ぶ
    3. extras (model / slides / output 等の v0 compat top-level merge)
       は reserved + optional の後ろに来る
    4. extras に reserved key と同名を渡しても reserved 値が勝つ
       (build_status reserved guard 経路)
    """
    from _observability import build_status

    reserved_core = [
        "schema_version", "script", "status", "ok", "exit_code",
        "category", "counts", "artifacts", "cost", "redaction",
    ]

    # (1) bare 呼び出しで reserved core 10 field のみ、順序固定
    p1 = build_status(script="x", v0_status="success", exit_code=0)
    keys1 = list(p1.keys())
    assert keys1 == reserved_core, (
        f"bare build_status field order drift: expected {reserved_core}, got {keys1}"
    )

    # (2) duration_ms + run_id 追加で reserved の後に source 宣言順で並ぶ
    p2 = build_status(
        script="x", v0_status="success", exit_code=0,
        duration_ms=42, run_id="r1",
    )
    keys2 = list(p2.keys())
    assert keys2 == reserved_core + ["duration_ms", "run_id"], (
        f"optional field order drift (duration+run): got {keys2}"
    )

    # (3) full trace (run_id + parent + step) で source 宣言順
    p3 = build_status(
        script="x", v0_status="success", exit_code=0,
        run_id="r1", parent_run_id="p1", step_id="s1",
    )
    keys3 = list(p3.keys())
    assert keys3 == reserved_core + ["run_id", "parent_run_id", "step_id"], (
        f"trace field order drift: got {keys3}"
    )

    # (4) extras (v0 compat) は reserved + optional の後ろに caller 順で並ぶ
    p4 = build_status(
        script="x", v0_status="success", exit_code=0,
        cost={"currency": "USD"},
        model="claude-3", slides=10, output="out/x.json",
    )
    keys4 = list(p4.keys())
    assert keys4[: len(reserved_core)] == reserved_core, (
        f"extras case reserved prefix drift: got {keys4}"
    )
    assert keys4[len(reserved_core):] == ["model", "slides", "output"], (
        f"extras suffix order drift: got {keys4[len(reserved_core):]!r}"
    )

    # (5) extras に reserved key 同名を入れても reserved 値が勝ち、extras は
    # 落ちる (foo は通る、status は reserved 側の "ok" のまま)
    extras = {"status": "SHOULD_NOT_OVERRIDE", "foo": "bar"}
    p5 = build_status(script="x", v0_status="success", exit_code=0, **extras)
    keys5 = list(p5.keys())
    assert keys5 == reserved_core + ["foo"], (
        f"reserved-key extras must be filtered, got {keys5}"
    )
    assert p5["status"] == "ok", (
        f"reserved status overridden by extras: {p5['status']!r}"
    )

    # (6) reserved + optional + extras の合成順序 lock-in
    p6 = build_status(
        script="x", v0_status="success", exit_code=0,
        duration_ms=99, run_id="r2",
        model="claude-3", slides=5,
    )
    keys6 = list(p6.keys())
    assert keys6 == reserved_core + ["duration_ms", "run_id", "model", "slides"], (
        f"combined order drift: got {keys6}"
    )


def test_observability_warn_legacy_cost_extras_env_gated() -> None:
    """`warn_legacy_cost_extras()` が env-gated deprecation warning を
    stderr のみに出し、stdout JSON contract を汚さないこと
    (Codex 02:31 PR-X verdict AH、nested `cost` migration roadmap)。

    PR-S で nested `cost` object を canonical 化したが PR-N 由来の
    top-level extras (estimated_input_tokens / estimated_output_tokens_upper_bound
    / estimated_cost_usd_upper_bound / cost_abort_at / rate_missing) を
    backward compat で dual emit 中。本 helper は
    `SUPERMOVIE_OBSERVABILITY_WARN_LEGACY_COST_EXTRAS=1` 時にのみ
    deprecation warning を stderr に書き、downstream consumer に
    nested 形式への migration を促す。
    """
    import io
    import os as _os

    from _observability import (
        WARN_LEGACY_COST_EXTRAS_ENV,
        LEGACY_COST_EXTRAS_KEYS,
        warn_legacy_cost_extras,
    )

    # 5 legacy keys が helper の認識対象に揃っている前提を lock-in
    assert set(LEGACY_COST_EXTRAS_KEYS) == {
        "estimated_input_tokens",
        "estimated_output_tokens_upper_bound",
        "estimated_cost_usd_upper_bound",
        "cost_abort_at",
        "rate_missing",
    }, f"LEGACY_COST_EXTRAS_KEYS drift: {LEGACY_COST_EXTRAS_KEYS}"

    payload_with_dual = {
        "cost": {"currency": "USD", "estimate": 0.001},
        "estimated_input_tokens": 100,
        "estimated_cost_usd_upper_bound": 0.001,
        "rate_missing": False,
    }
    payload_without_legacy = {"cost": {"currency": "USD", "estimate": 0.001}}
    payload_no_cost = {
        "cost": None,
        "estimated_input_tokens": 100,
        "rate_missing": False,
    }

    saved_env = _os.environ.get(WARN_LEGACY_COST_EXTRAS_ENV)
    try:
        # (1) env 未設定 → no-op、戻り値 False、stream 無音
        _os.environ.pop(WARN_LEGACY_COST_EXTRAS_ENV, None)
        buf = io.StringIO()
        emitted = warn_legacy_cost_extras(payload_with_dual, stream=buf)
        assert emitted is False, f"env unset must no-op, got emitted={emitted}"
        assert buf.getvalue() == "", (
            f"env unset must produce no output, got {buf.getvalue()!r}"
        )

        # (2) env="0" → no-op
        _os.environ[WARN_LEGACY_COST_EXTRAS_ENV] = "0"
        buf = io.StringIO()
        emitted = warn_legacy_cost_extras(payload_with_dual, stream=buf)
        assert emitted is False, f"env=0 must no-op, got emitted={emitted}"
        assert buf.getvalue() == "", (
            f"env=0 must produce no output, got {buf.getvalue()!r}"
        )

        # (3) env="1" + dual emission → warning emit、戻り値 True、warning に
        # legacy key と env var 名が含まれる
        _os.environ[WARN_LEGACY_COST_EXTRAS_ENV] = "1"
        buf = io.StringIO()
        emitted = warn_legacy_cost_extras(payload_with_dual, stream=buf)
        assert emitted is True, f"env=1 + dual must emit, got emitted={emitted}"
        out = buf.getvalue()
        assert "WARNING" in out, f"warning prefix missing: {out!r}"
        assert "deprecated" in out, f"deprecated label missing: {out!r}"
        for k in ("estimated_input_tokens", "estimated_cost_usd_upper_bound",
                  "rate_missing"):
            assert k in out, f"legacy key {k!r} not listed in warning: {out!r}"
        assert WARN_LEGACY_COST_EXTRAS_ENV in out, (
            f"env var name missing from warning: {out!r}"
        )

        # (4) env="1" + nested cost only (legacy 不在) → no-op
        buf = io.StringIO()
        emitted = warn_legacy_cost_extras(payload_without_legacy, stream=buf)
        assert emitted is False, (
            f"env=1 + no legacy keys must no-op, got emitted={emitted}"
        )
        assert buf.getvalue() == "", (
            f"no-legacy case must produce no output, got {buf.getvalue()!r}"
        )

        # (5) env="1" + cost None (legacy keys あり) → no-op
        # (nested 化していない legacy-only 経路は migration 対象外、
        # dual emission だけが warning 対象 contract)
        buf = io.StringIO()
        emitted = warn_legacy_cost_extras(payload_no_cost, stream=buf)
        assert emitted is False, (
            f"cost=None must no-op even with legacy keys, got emitted={emitted}"
        )
        assert buf.getvalue() == "", (
            f"cost=None case must produce no output, got {buf.getvalue()!r}"
        )
    finally:
        if saved_env is None:
            _os.environ.pop(WARN_LEGACY_COST_EXTRAS_ENV, None)
        else:
            _os.environ[WARN_LEGACY_COST_EXTRAS_ENV] = saved_env


def test_observability_warn_legacy_cost_extras_env_strict_opt_in() -> None:
    """`warn_legacy_cost_extras()` env gate が strict opt-in (`!= "1"` 全 ignore)
    であることを lock-in (Codex 02:55 PR-AB verdict AI)。

    PR-X 既存 test (`test_observability_warn_legacy_cost_extras_env_gated`) は
    unset / "0" / "1" の 3 値しかカバーしない。env が "2" / "true" / "TRUE" /
    "yes" / "on" / "" / "1\\n" / "  1" / 大文字小文字混在等の「truthy 風だが
    "1" ではない」 値を渡した時に、誤って warning が emit されない (strict
    opt-in 契約) ことを explicit に固定する。

    一般的に Python `os.environ.get(...) != "1"` は厳密 string 比較なので
    truthy 風 string は ignore されるが、リファクタで `bool()` 経由 / 空文字
    弾き / `.lower() in ("1","true","yes")` 等に変えると warning が広範に
    emit されて downstream noise が増える regression を起こす。本 test は
    その drift を early fail させる。
    """
    import io
    import os as _os

    from _observability import (
        WARN_LEGACY_COST_EXTRAS_ENV,
        warn_legacy_cost_extras,
    )

    payload_with_dual = {
        "cost": {"currency": "USD", "estimate": 0.001},
        "estimated_input_tokens": 100,
        "estimated_cost_usd_upper_bound": 0.001,
        "rate_missing": False,
    }

    # truthy 風の非"1" env 値群: 全て no-op (False + stream 空) になる契約
    invalid_truthy_values = [
        "2", "10", "-1",                      # 数値で 1 でない
        "true", "TRUE", "True",               # 文字列 truthy
        "yes", "YES", "y", "Y",
        "on", "ON",
        "enabled",
        "",                                    # 空 string (empty != unset)
        " ",                                   # whitespace のみ
        "1 ", " 1", "1\n",                     # padded "1"
        "11", "01",                            # 部分一致 / leading zero
        "True\n",
    ]

    saved_env = _os.environ.get(WARN_LEGACY_COST_EXTRAS_ENV)
    try:
        for value in invalid_truthy_values:
            _os.environ[WARN_LEGACY_COST_EXTRAS_ENV] = value
            buf = io.StringIO()
            emitted = warn_legacy_cost_extras(payload_with_dual, stream=buf)
            assert emitted is False, (
                f"env={value!r} must be strict-rejected (no-op), "
                f"got emitted={emitted}"
            )
            assert buf.getvalue() == "", (
                f"env={value!r} must produce no output, got {buf.getvalue()!r}"
            )

        # positive control: "1" exactly では emit する (gate 機能自体は活きている)
        _os.environ[WARN_LEGACY_COST_EXTRAS_ENV] = "1"
        buf = io.StringIO()
        emitted = warn_legacy_cost_extras(payload_with_dual, stream=buf)
        assert emitted is True, (
            f"env='1' positive control must emit, got emitted={emitted}"
        )
        assert buf.getvalue() != "", "env='1' positive control must write to stream"
    finally:
        if saved_env is None:
            _os.environ.pop(WARN_LEGACY_COST_EXTRAS_ENV, None)
        else:
            _os.environ[WARN_LEGACY_COST_EXTRAS_ENV] = saved_env


def test_observability_redact_provider_body_preview_length_boundaries() -> None:
    """`redact_provider_body()` の `preview_length` boundary 範囲 lock-in
    + negative defense (Codex 03:38 PR-AI verdict AK)。

    `preview_length` は consumer 側 progress bar / log summary 表示用の
    長さ hint で、`[0, len(body)]` range invariant が前提:

      - max_preview > len(body)        → preview_length = len(body)  (上限 clamp)
      - max_preview == len(body)       → preview_length = len(body)  (boundary)
      - 0 < max_preview < len(body)    → preview_length = max_preview (通常)
      - max_preview == 1               → preview_length = 1
      - max_preview == 0               → preview_length = 0 (preview 抑止 contract)
      - max_preview < 0 (negative)     → preview_length = 0 (PR-AI fix で下限 clamp)

    旧実装 `min(len(body), max_preview)` は `max_preview=-1` で
    `preview_length=-1` semantic violation を起こしていた gap を
    `max(0, min(...))` で defensive 化、本 test で 8 boundary 全部 lock-in。

    raw body / sensitive token は出力に含まれない invariant (PR-J / 既存
    `test_observability_redact_provider_body_default_strict` と相互強化) も
    併せて確認。
    """
    from _observability import redact_provider_body

    body = "API response body content example with secret token sk-abc123def"
    body_len = len(body)

    cases = [
        # (max_preview input, expected preview_length)
        (body_len + 100, body_len),     # 上限 clamp 大幅超過
        (body_len + 1, body_len),       # 上限 clamp +1 boundary
        (body_len, body_len),           # ぴったり境界
        (body_len - 1, body_len - 1),   # 通常範囲 (-1 boundary)
        (1, 1),                         # 最小 positive
        (0, 0),                         # preview 抑止 contract
        (-1, 0),                        # PR-AI fix: negative → 0 clamp
        (-100, 0),                      # PR-AI fix: 大きな negative も 0 clamp
    ]

    for max_preview, expected in cases:
        r = redact_provider_body(body, max_preview=max_preview)
        assert r["preview_length"] == expected, (
            f"max_preview={max_preview}: expected preview_length={expected}, "
            f"got {r['preview_length']}"
        )
        # raw body / token がそもそも summary に含まれない invariant
        assert "body" not in r, (
            f"summary must not contain raw body, got {r}"
        )
        assert "sk-abc123def" not in str(r), (
            f"sensitive token leaked in summary: {r}"
        )
        # length は常に body 全長 (preview_length とは独立)
        assert r["length"] == body_len
        assert r["kind"] == "summary"

    # default max_preview=80 の挙動も維持確認 (caller が省略した場合の慣習)
    r_default = redact_provider_body(body)
    assert r_default["preview_length"] == min(body_len, 80)


def test_observability_provider_body_stderr_default_redact() -> None:
    """generate_slide_plan の HTTP error response body と LLM raw text が
    default で stderr に raw 出力されないこと (Codex 20:48 PR3 review P2 #1)。

    docs/OBSERVABILITY.md §Redaction Rules provider_response_body strict 化
    の regression test。
    """
    import generate_slide_plan as gsp
    import os as _os
    import urllib.request as _urlreq
    import urllib.error as _urlerr
    import io as _io
    import contextlib

    secret_body = '{"error": "Anthropic internal: token sk-LEAK-secret-12345"}'

    class FakeHTTPError(_urlerr.HTTPError):
        def __init__(self, body):
            self._body = body.encode("utf-8")
            self.code = 500
            self.headers = None

        def read(self):
            return self._body

    def mock_urlopen_500(req, timeout=60):
        raise FakeHTTPError(secret_body)

    original_urlopen = _urlreq.urlopen
    original_proj = gsp.PROJ
    original_api_key = _os.environ.get("ANTHROPIC_API_KEY")

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp)
        gsp.PROJ = proj
        (proj / "transcript_fixed.json").write_text(
            json.dumps({"words": [{"text": "x"}], "segments": []}),
            encoding="utf-8",
        )
        (proj / "project-config.json").write_text(
            json.dumps({"format": "short"}), encoding="utf-8",
        )
        _os.environ["ANTHROPIC_API_KEY"] = "fake"
        _urlreq.urlopen = mock_urlopen_500
        try:
            import sys as _sys
            old_argv = _sys.argv
            captured_err = _io.StringIO()
            _sys.argv = ["generate_slide_plan.py", "--output", str(proj / "x.json")]
            try:
                with contextlib.redirect_stderr(captured_err):
                    ret = gsp.main()
                assert_eq(ret, 4, "HTTP 500 → exit 4")
                err_text = captured_err.getvalue()
                # default: raw secret string が stderr に出ていない
                if "sk-LEAK-secret-12345" in err_text:
                    raise AssertionError(
                        f"raw secret body leaked to stderr in default mode: {err_text!r}"
                    )
                # redacted summary が含まれている
                if "redacted" not in err_text or "sha256=" not in err_text:
                    raise AssertionError(
                        f"expected redacted summary in stderr, got: {err_text!r}"
                    )
            finally:
                _sys.argv = old_argv

            # --unsafe-dump-response: raw が出ること (debug flag が機能している)
            captured_err2 = _io.StringIO()
            _sys.argv = [
                "generate_slide_plan.py",
                "--output", str(proj / "x.json"),
                "--unsafe-dump-response",
            ]
            try:
                with contextlib.redirect_stderr(captured_err2):
                    ret2 = gsp.main()
                assert_eq(ret2, 4, "unsafe-dump-response also exits 4")
                err_text2 = captured_err2.getvalue()
                if "sk-LEAK-secret-12345" not in err_text2:
                    raise AssertionError(
                        f"--unsafe-dump-response did not output raw body: {err_text2!r}"
                    )
            finally:
                _sys.argv = old_argv
        finally:
            if original_api_key is None:
                _os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                _os.environ["ANTHROPIC_API_KEY"] = original_api_key
            _urlreq.urlopen = original_urlopen
            gsp.PROJ = original_proj


def test_observability_emit_json_disabled_no_print(capsys=None) -> None:
    """emit_json は --json-log なし (enabled=False) で stdout に出さないこと。

    Existing v0 emit pattern (--json-log opt-in) との互換性維持。
    """
    import io
    from contextlib import redirect_stdout
    from _observability import emit_json, build_status

    payload = build_status(script="x", v0_status="success", exit_code=0)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = emit_json(False, payload)
    assert buf.getvalue() == "", f"emit_json(False) printed: {buf.getvalue()!r}"
    assert rc == 0
    # enabled=True: prints valid JSON line
    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        rc2 = emit_json(True, payload)
    out = buf2.getvalue().strip()
    parsed = json.loads(out)
    assert parsed["status"] == "ok"
    assert rc2 == 0


def test_observability_emit_json_format_lint() -> None:
    """`emit_json()` の stdout output が「最終行 1 line / pure JSON /
    final newline」契約を保つこと (Codex 02:34 PR-Y verdict AJ)。

    `--json-log` の downstream parser (log analyzer / regression test)
    は stdout 末尾を `splitlines()[-1]` で取って `json.loads()` する
    pattern を前提にしている。format contract の要素:

      (1) enabled=True で stdout 出力は exactly 1 line + 末尾 `\\n`
      (2) その行は `json.loads` 可能で payload と semantically 等価
      (3) embedded control char (`\\n` / `\\t` / `\\"`) を含む value も
          escape されて pure single line を維持
      (4) non-ASCII (ja 文字列) も `ensure_ascii=False` で literal 維持、
          ただし行構造は崩れない
      (5) enabled=False は stdout に何も書かない (existing test 補強)

    既存 `test_observability_emit_json_disabled_no_print` は出力 1 行を
    `strip()` した後の json parse のみ assert、行数 / 末尾改行 / 制御
    文字 escape の format invariant までは固定していない。
    """
    import io
    from contextlib import redirect_stdout

    from _observability import build_status, emit_json

    # (1) plain payload で exactly 1 newline (= 1 line + final \n)、blank
    # 行混入なし。Codex 02:38 PR-Y review P2 fix: `out.count("\n") == 1`
    # で blank line 検出を tighten (downstream `splitlines()[-1]` が空行を
    # 拾って壊れる risk を closure)。
    payload = build_status(script="x", v0_status="success", exit_code=0)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = emit_json(True, payload)
    out = buf.getvalue()
    assert out.endswith("\n"), f"emit_json output must end with newline, got {out!r}"
    assert out.count("\n") == 1, (
        f"emit_json output must contain exactly 1 newline (no blank lines), "
        f"got {out.count('\n')}: {out!r}"
    )
    parsed = json.loads(out[:-1])
    assert parsed["status"] == "ok"
    assert parsed["script"] == "x"
    assert rc == 0

    # (2) embedded control char (\n / \t / \" / \\) in extras を escape
    extras_with_ctrl = build_status(
        script="x", v0_status="success", exit_code=0,
        weird_field="line1\nline2\twith \"quote\" and \\backslash",
    )
    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        emit_json(True, extras_with_ctrl)
    out2 = buf2.getvalue()
    assert out2.endswith("\n"), f"output must end with newline: {out2!r}"
    # exactly 1 newline (control char escape の double check、blank line nor
    # body 内 raw \n がない)
    assert out2.count("\n") == 1, (
        f"control-char case must produce exactly 1 newline, got "
        f"{out2.count('\n')}: {out2!r}"
    )
    body2 = out2[:-1]
    assert "\n" not in body2, (
        f"emit_json body must not contain raw newline (must be escaped): {body2!r}"
    )
    assert "\t" not in body2, (
        f"emit_json body must not contain raw tab (must be escaped): {body2!r}"
    )
    parsed2 = json.loads(body2)
    assert parsed2["weird_field"] == "line1\nline2\twith \"quote\" and \\backslash"

    # (3) non-ASCII (日本語) は ensure_ascii=False で literal 維持、ただし
    # 行構造は崩れない
    extras_jp = build_status(
        script="x", v0_status="success", exit_code=0,
        title="日本語テスト",
    )
    buf3 = io.StringIO()
    with redirect_stdout(buf3):
        emit_json(True, extras_jp)
    out3 = buf3.getvalue()
    assert out3.endswith("\n")
    assert out3.count("\n") == 1, (
        f"non-ASCII case must produce exactly 1 newline, got "
        f"{out3.count('\n')}: {out3!r}"
    )
    body3 = out3[:-1]
    assert "\n" not in body3, "single-line invariant broken with non-ASCII"
    assert "日本語テスト" in body3, (
        f"ensure_ascii=False expected to keep literal JP, got {body3!r}"
    )
    parsed3 = json.loads(body3)
    assert parsed3["title"] == "日本語テスト"

    # (4) enabled=False で stdout に何も書かない (existing test 補強)
    buf4 = io.StringIO()
    with redirect_stdout(buf4):
        rc4 = emit_json(False, payload)
    assert buf4.getvalue() == "", (
        f"emit_json(False) must produce no stdout, got {buf4.getvalue()!r}"
    )
    assert rc4 == 0

    # (5) exit_code propagation: payload["exit_code"] が rc に出る
    err_payload = build_status(script="x", v0_status="rate_limited", exit_code=2)
    buf5 = io.StringIO()
    with redirect_stdout(buf5):
        rc5 = emit_json(True, err_payload)
    out5 = buf5.getvalue()
    assert out5.endswith("\n")
    assert out5.count("\n") == 1, (
        f"error case must produce exactly 1 newline, got "
        f"{out5.count('\n')}: {out5!r}"
    )
    parsed5 = json.loads(out5[:-1])
    assert parsed5["status"] == "error"
    assert parsed5["exit_code"] == 2
    assert rc5 == 2, f"emit_json must return payload exit_code, got {rc5}"


def test_observability_emit_json_stderr_clean() -> None:
    """`emit_json()` は stdout 専用、stderr に何も書かない invariant
    (Codex 02:42 PR-Z verdict AL、observability transport contract drift 防止)。

    `--json-log` の stdout/stderr 分離契約:
      - stdout: emit_json() の JSON tail 1 行のみ + 既存 v0 human stdout
      - stderr: error / warning / debug 用 (`provider_response_body` redact /
        `warn_legacy_cost_extras` deprecation / human error message)

    emit_json 内部実装が `print()` で stdout に書く前提が崩れて
    `print(..., file=sys.stderr)` 等にリファクタされると、log collector /
    parser / CI assertion が dual stream で混乱する。本 test は
    enabled / disabled / error-status / non-ASCII / control-char の 5 case で
    `redirect_stderr(StringIO())` 下に呼んで stderr が空のまま維持される
    invariant を lock-in。

    既存 `test_observability_emit_json_format_lint` (PR-Y) は stdout の
    1-line / pure JSON / final newline / non-ASCII / exit_code を見るが、
    stderr 非混入は asserted されていない。
    """
    import io
    from contextlib import redirect_stderr, redirect_stdout

    from _observability import build_status, emit_json

    payload_ok = build_status(script="x", v0_status="success", exit_code=0)
    payload_err = build_status(script="x", v0_status="rate_limited", exit_code=2)
    payload_jp = build_status(
        script="x", v0_status="success", exit_code=0,
        title="日本語テスト",
    )
    payload_ctrl = build_status(
        script="x", v0_status="success", exit_code=0,
        weird_field="line1\nline2\twith \"quote\"",
    )

    cases = [
        ("enabled=True ok", True, payload_ok),
        ("enabled=True error", True, payload_err),
        ("enabled=True non-ASCII", True, payload_jp),
        ("enabled=True control-char", True, payload_ctrl),
        ("enabled=False (no print)", False, payload_ok),
    ]

    for label, enabled, payload in cases:
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            emit_json(enabled, payload)
        assert err_buf.getvalue() == "", (
            f"emit_json({label}) leaked to stderr: {err_buf.getvalue()!r}"
        )
        if enabled:
            # sanity guard: stdout には書いている (test 経路自体が機能している
            # ことを false-positive 防止で確認)
            assert out_buf.getvalue() != "", (
                f"emit_json({label}) produced no stdout while expected to: "
                f"err={err_buf.getvalue()!r}"
            )
        else:
            assert out_buf.getvalue() == "", (
                f"emit_json({label}) wrote stdout while disabled: "
                f"{out_buf.getvalue()!r}"
            )


def test_observability_build_cost_payload_rate_source_contract() -> None:
    """`build_cost_payload(rate_source=...)` の str + `env:` prefix + non-empty
    env name contract lock-in (Codex 04:00 PR-AL verdict AV、observability v1
    cost telemetry contract drift 防止)。

    `rate_source` は §Cost JSON Shape の env var convention placeholder で、
    canonical format は `env:<ENV_VAR_NAME>` (非空 env name 必須)。旧実装は
    型 / format validation なしで以下の drift を silent payload 通過:

      - `""` (空文字) / `None` / 数値 / bool / list 等 type 違反
      - `"SUPERMOVIE_RATE_X"` (env: prefix 欠如) → caller が機械的に env
        name を抽出できない
      - `"env:"` (prefix 直後 env name 空) → 同上

    新 contract: TypeError (非 str) + ValueError (prefix 欠如 / env name 空)
    で fail-loud。default 値 `env:SUPERMOVIE_RATE_<PROVIDER>_<DIR>_USD_PER_MTOK`
    と典型 caller 経路 (env:SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK 等)
    は backward compatible。

    既存 strict 系 (PR-AC exit_code int / PR-AD redaction_rules / PR-AF cost /
    PR-AK counts/artifacts) と同 level の defensive lint。
    """
    from _observability import build_cost_payload

    # ===== accept 経路 =====
    # (1a) default (省略) → default placeholder が出る
    p_def = build_cost_payload(0.001, 1.5, 3.0)
    assert p_def["rate_source"] == "env:SUPERMOVIE_RATE_<PROVIDER>_<DIR>_USD_PER_MTOK"

    # (1b) 通常 caller の env: prefix + ENV_NAME
    p_anthropic = build_cost_payload(
        0.001, 1.5, 3.0,
        rate_source="env:SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK",
    )
    assert p_anthropic["rate_source"] == \
        "env:SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK"

    # (1c) env: + 1 char minimal env name
    p_min = build_cost_payload(0.001, 1.5, 3.0, rate_source="env:X")
    assert p_min["rate_source"] == "env:X"

    # ===== reject 経路 =====
    # (2) 型違反: None / int / float / bool / list / dict
    for bad_type in (None, 5, 1.5, True, False, ["env:X"], {"env": "X"}):
        try:
            build_cost_payload(0.001, 1.5, 3.0, rate_source=bad_type)
        except TypeError as e:
            assert "rate_source" in str(e) and "str" in str(e), (
                f"TypeError msg should mention rate_source + str, got {e!r}"
            )
        else:
            raise AssertionError(
                f"non-str rate_source={bad_type!r} must raise TypeError"
            )

    # (3) prefix 欠如: env: なし
    for no_prefix in ("SUPERMOVIE_RATE_X", "ENV:X", "env_X", "env-X",
                      "anthropic_input"):
        try:
            build_cost_payload(0.001, 1.5, 3.0, rate_source=no_prefix)
        except ValueError as e:
            assert "rate_source" in str(e) and "env:" in str(e), (
                f"ValueError msg should mention rate_source + env:, got {e!r}"
            )
        else:
            raise AssertionError(
                f"no-prefix rate_source={no_prefix!r} must raise ValueError"
            )

    # (4) prefix 直後 env name 空: "env:" or "" (空文字)
    for empty_or_prefix in ("", "env:"):
        try:
            build_cost_payload(0.001, 1.5, 3.0, rate_source=empty_or_prefix)
        except (ValueError, TypeError):
            pass
        else:
            raise AssertionError(
                f"empty-name rate_source={empty_or_prefix!r} must raise"
            )


def test_observability_build_cost_payload_nan_inf_defense() -> None:
    """`build_cost_payload()` が NaN / Inf / -Inf rate を None 正規化し、
    `rate_missing=True` 維持 + JSON pollution (`NaN` / `Infinity` token)
    を防ぐ contract lock-in (Codex 02:46 PR-AA verdict AM)。

    CLI / env 経路は `math.isfinite` + ValueError reject で early guard
    済み (`generate_slide_plan.py` argparse type)、ただし helper を独立
    caller から呼ぶ場合や CLI guard をすり抜ける将来 path への
    defense-in-depth layer。

    `compute_rate_missing(estimate)` は PR-O で `estimate is None` 判定の
    single source of truth、PR-AA で NaN / Inf も rate_missing=True に
    拡張。`build_cost_payload` 内で estimate / rate_input / rate_output を
    `_coerce_finite_or_none` 経由で正規化、payload に non-finite を
    入れないことで `json.dumps(allow_nan=False)` でも fail しない契約。
    """
    import json as _json

    from _observability import build_cost_payload, compute_rate_missing

    nan = float("nan")
    inf = float("inf")
    ninf = float("-inf")

    # (1) estimate=NaN / Inf / -Inf → estimate normalized to None +
    # rate_missing=True、JSON strict (allow_nan=False) でも fail しない
    for bad_estimate in (nan, inf, ninf):
        p = build_cost_payload(bad_estimate, 1.5, 3.0)
        assert p["estimate"] is None, (
            f"non-finite estimate {bad_estimate!r} must be normalized to None, "
            f"got {p['estimate']!r}"
        )
        assert p["rate_missing"] is True, (
            f"non-finite estimate must mark rate_missing=True, got {p}"
        )
        _json.dumps(p, allow_nan=False)  # raises ValueError if non-finite leaked

    # (2) rate_input=NaN → rate_input normalized to None
    p_ri = build_cost_payload(0.001, nan, 3.0)
    assert p_ri["rate_input_usd_per_mtok"] is None, (
        f"non-finite rate_input must be normalized to None, got "
        f"{p_ri['rate_input_usd_per_mtok']!r}"
    )
    # estimate は finite なので rate_missing は False のまま
    assert p_ri["rate_missing"] is False, (
        f"finite estimate + non-finite rate_input: rate_missing should track "
        f"estimate (False), got {p_ri['rate_missing']}"
    )
    _json.dumps(p_ri, allow_nan=False)

    # (3) rate_output=Inf → rate_output normalized to None
    p_ro = build_cost_payload(0.001, 1.5, inf)
    assert p_ro["rate_output_usd_per_mtok"] is None, (
        f"non-finite rate_output must be normalized to None, got "
        f"{p_ro['rate_output_usd_per_mtok']!r}"
    )
    _json.dumps(p_ro, allow_nan=False)

    # (4) 全 finite で通常経路は壊れていない (regression guard)
    p_ok = build_cost_payload(0.001, 1.5, 3.0)
    assert p_ok["estimate"] == 0.001
    assert p_ok["rate_input_usd_per_mtok"] == 1.5
    assert p_ok["rate_output_usd_per_mtok"] == 3.0
    assert p_ok["rate_missing"] is False
    _json.dumps(p_ok, allow_nan=False)

    # (5) 全 None で通常経路 (rate 未設定 path) は壊れていない
    p_none = build_cost_payload(None, None, None)
    assert p_none["estimate"] is None
    assert p_none["rate_input_usd_per_mtok"] is None
    assert p_none["rate_output_usd_per_mtok"] is None
    assert p_none["rate_missing"] is True

    # (6) compute_rate_missing 直接呼び出しでも NaN / Inf を rate_missing=True
    assert compute_rate_missing(nan) is True
    assert compute_rate_missing(inf) is True
    assert compute_rate_missing(ninf) is True
    assert compute_rate_missing(None) is True
    assert compute_rate_missing(0.001) is False
    # boundary: 0 は finite なので rate_missing=False (estimate=0 は技術的に有効)
    assert compute_rate_missing(0) is False, (
        "estimate=0 (zero cost) is finite; rate_missing must be False"
    )

    # (7) 非数値型 (str) も None 正規化されて rate_missing=True
    # (caller 側で type 違いを通した場合の defense)
    p_bad_type = build_cost_payload("not_a_number", 1.5, 3.0)
    assert p_bad_type["estimate"] is None
    assert p_bad_type["rate_missing"] is True


def test_observability_emit_json_exit_code_int_contract() -> None:
    """`emit_json()` の `payload['exit_code']` int 限定 contract を lock-in
    (Codex 03:01 PR-AC verdict AO、observability v1 schema drift 防止)。

    `exit_code` は v1 schema の core 字段で、shell rc + downstream consumer
    が int を前提にする。旧実装 `int(payload.get("exit_code", 0))` は:

      - str "2" → 2 (silent coerce)
      - float 1.5 → 1 (silent truncate)
      - bool True → 1 (silent、bool は int subclass)
      - str "abc" → uncaught ValueError
      - None → uncaught TypeError

    の weak coercion で schema drift を silent に通していた。新 contract は
    int (bool 除く) のみ受理、それ以外は explicit TypeError で fail-loud、
    payload 構築側の責務として固定する。missing key の場合は default 0 を
    維持 (既存 helper 慣習)。
    """
    import io
    from contextlib import redirect_stdout

    from _observability import emit_json

    # (1) 正常 int は通る (shell rc に伝搬)
    for ec in (0, 2, 10, -1):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = emit_json(False, {"status": "ok", "exit_code": ec})
        assert rc == ec, f"int exit_code={ec} must round-trip, got {rc}"

    # (2) missing key は default 0 (既存 helper 慣習を保持)
    rc_default = emit_json(False, {"status": "ok"})
    assert rc_default == 0, f"missing exit_code must default to 0, got {rc_default}"

    # (3) bool (int subclass) は明示 reject
    for bad_bool in (True, False):
        try:
            emit_json(False, {"status": "ok", "exit_code": bad_bool})
        except TypeError as e:
            assert "exit_code" in str(e) and "int" in str(e), (
                f"TypeError msg should mention exit_code + int, got {e!r}"
            )
        else:
            raise AssertionError(f"bool exit_code={bad_bool} must raise TypeError")

    # (4) str (numeric or non-numeric) は reject (silent coerce 防止)
    for bad_str in ("2", "abc", "", "1.5", "10"):
        try:
            emit_json(False, {"status": "ok", "exit_code": bad_str})
        except TypeError:
            pass
        else:
            raise AssertionError(
                f"str exit_code={bad_str!r} must raise TypeError"
            )

    # (5) float は reject (silent truncate 防止)
    for bad_float in (1.5, 0.0, 2.0, -3.5):
        try:
            emit_json(False, {"status": "ok", "exit_code": bad_float})
        except TypeError:
            pass
        else:
            raise AssertionError(
                f"float exit_code={bad_float} must raise TypeError"
            )

    # (6) None は reject (旧実装の uncaught TypeError → 明示 TypeError に統一)
    try:
        emit_json(False, {"status": "ok", "exit_code": None})
    except TypeError:
        pass
    else:
        raise AssertionError("None exit_code must raise TypeError")

    # (7) その他 (list / dict / object) も reject
    for bad_other in ([], [1], {}, object()):
        try:
            emit_json(False, {"status": "ok", "exit_code": bad_other})
        except TypeError:
            pass
        else:
            raise AssertionError(
                f"non-int exit_code={bad_other!r} must raise TypeError"
            )

    # (8) reject 経路で stdout に何も書かれていない (type check は print より前)
    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            emit_json(True, {"status": "error", "exit_code": "2"})
        except TypeError:
            pass
    assert buf.getvalue() == "", (
        f"emit_json must reject before printing, got stdout={buf.getvalue()!r}"
    )


def test_observability_build_status_redaction_rules_strict() -> None:
    """`build_status(redaction_rules=...)` の str 限定 contract lock-in
    (Codex 03:09 PR-AD verdict AQ-改、observability v1 schema drift 防止)。

    旧実装 `sorted(set(redaction_rules)) if redaction_rules else []` は
    None / empty 時 [] を返すが、`[None]` / `[1]` を silent pass、
    `["a", None]` で「'<' not supported」の意味不明 TypeError、
    bare str (`"abs_path"`) を渡すと iter で char 分解されて
    `["_", "a", "b", "h", "p", "s", "t"]` という schema drift を起こす。

    新 `_normalize_redaction_rules()` は:
      - None → []
      - list/tuple of str → sorted unique
      - bare str → TypeError fail-loud (caller の wrap 漏れ早期検出)
      - 非 str entry を含む → TypeError with helpful message
      - その他 type → TypeError

    test contract: 既存 caller (`["abs_path"]` / `[]` / None / 重複 dedup /
    tuple) を維持しつつ、上記 schema drift を early fail させる。
    """
    from _observability import build_status

    # (1) None → []
    p_none = build_status(script="x", v0_status="success", exit_code=0,
                          redaction_rules=None)
    assert p_none["redaction"]["applied_rules"] == []

    # (2) empty list / tuple → []
    p_empty = build_status(script="x", v0_status="success", exit_code=0,
                           redaction_rules=[])
    assert p_empty["redaction"]["applied_rules"] == []
    p_empty_tuple = build_status(script="x", v0_status="success", exit_code=0,
                                 redaction_rules=())
    assert p_empty_tuple["redaction"]["applied_rules"] == []

    # (3) list of str → sorted unique
    p_dup = build_status(script="x", v0_status="success", exit_code=0,
                         redaction_rules=["b", "a", "a", "b"])
    assert p_dup["redaction"]["applied_rules"] == ["a", "b"]

    # (4) tuple of str も accept
    p_tuple = build_status(script="x", v0_status="success", exit_code=0,
                           redaction_rules=("abs_path", "secret"))
    assert p_tuple["redaction"]["applied_rules"] == ["abs_path", "secret"]

    # (5) bare str (not list) → TypeError (char 分解 silent drift 防止)
    try:
        build_status(script="x", v0_status="success", exit_code=0,
                     redaction_rules="abs_path")
    except TypeError as e:
        assert "bare str" in str(e) or "list" in str(e), (
            f"TypeError msg should mention bare str or list, got {e!r}"
        )
    else:
        raise AssertionError("bare str redaction_rules must raise TypeError")

    # (6) [None] → TypeError (silent pass 防止)
    try:
        build_status(script="x", v0_status="success", exit_code=0,
                     redaction_rules=[None])
    except TypeError:
        pass
    else:
        raise AssertionError("[None] redaction_rules must raise TypeError")

    # (7) [int] → TypeError (silent pass 防止)
    try:
        build_status(script="x", v0_status="success", exit_code=0,
                     redaction_rules=[1])
    except TypeError:
        pass
    else:
        raise AssertionError("[int] redaction_rules must raise TypeError")

    # (8) ["a", None] mixed → TypeError (旧「'<' not supported」意味不明 msg を
    # 「entries must be str」明示メッセージに置換)
    try:
        build_status(script="x", v0_status="success", exit_code=0,
                     redaction_rules=["abs_path", None])
    except TypeError as e:
        assert "str" in str(e), (
            f"mixed-type TypeError msg should mention str expectation, got {e!r}"
        )
    else:
        raise AssertionError("mixed-type redaction_rules must raise TypeError")

    # (9) dict / set / int (not list/tuple) → TypeError
    for bad in ({"abs_path": True}, {"abs_path"}, 42):
        try:
            build_status(script="x", v0_status="success", exit_code=0,
                         redaction_rules=bad)
        except TypeError:
            pass
        else:
            raise AssertionError(
                f"non list/tuple redaction_rules ({type(bad).__name__}) "
                f"must raise TypeError"
            )


def test_observability_build_status_reserved_key_collision() -> None:
    """`build_status()` が **extras 経由の reserved key 同名 override を全て
    filter する invariant を全 reserved key に対して lock-in
    (Codex 03:14 PR-AE verdict AS、observability v1 contract drift 防止)。

    PR-W (`test_observability_build_status_top_level_field_order` case 5) で
    `status` 1 件のみ被覆していたが、build_status の `reserved =
    set(payload.keys())` ガードの効果は他 reserved key にも及ぶべき contract。

    signature kwargs (script / v0_status / exit_code / counts / artifacts /
    cost / redaction_rules / duration_ms / category_override / run_id /
    parent_run_id / step_id) は Python の routing で **extras に流れない
    ため、本 test の対象は **extras に流せる non-signature reserved key:
      - schema_version
      - status
      - ok
      - category
      - redaction

    の 5 件全て。各 key を単独で extras に injection した場合 + 5 件まとめて
    injection した場合に reserved 値が勝ち、injected 値が payload に出ないこと
    を assert。injection されなかった unrelated extras key (foo / model 等) は
    通常通り passthrough されることも併せて確認。
    """
    from _observability import build_status

    non_sig_reserved = ["schema_version", "status", "ok", "category", "redaction"]

    # (1) 各 reserved key を単独 extras injection、reserved 値が勝つ
    for key in non_sig_reserved:
        injected = {key: f"INJECTED_{key}"}
        p = build_status(script="x", v0_status="success", exit_code=0,
                         **injected)
        assert p[key] != f"INJECTED_{key}", (
            f"extras key {key!r} leaked into payload: {p[key]!r}"
        )
        # reserved 値が helper の正規 value で残る
        if key == "schema_version":
            assert p[key] == 1
        elif key == "status":
            assert p[key] == "ok"
        elif key == "ok":
            assert p[key] is True
        elif key == "category":
            assert p[key] is None  # success → ("ok", None)
        elif key == "redaction":
            assert isinstance(p[key], dict)
            assert p[key]["version"] == 1
            assert p[key]["applied_rules"] == []

    # (2) 5 件まとめて injection、全 reserved 値が勝つ
    all_inject = {k: f"INJECTED_{k}" for k in non_sig_reserved}
    p_all = build_status(script="x", v0_status="success", exit_code=0,
                         **all_inject)
    for key in non_sig_reserved:
        assert p_all[key] != f"INJECTED_{key}", (
            f"all-inject case: extras key {key!r} leaked: {p_all[key]!r}"
        )
    # injected key は payload に出ない (extras filter 効いている)
    for key in non_sig_reserved:
        assert key in p_all, f"reserved key {key!r} missing from payload"

    # (3) injection されなかった unrelated extras (foo / model 等) は passthrough
    p_extra = build_status(script="x", v0_status="success", exit_code=0,
                           **{**all_inject, "foo": "bar", "model": "claude-3"})
    assert p_extra.get("foo") == "bar", (
        f"unrelated extras key 'foo' must pass through, got {p_extra.get('foo')!r}"
    )
    assert p_extra.get("model") == "claude-3", (
        f"unrelated extras key 'model' must pass through, "
        f"got {p_extra.get('model')!r}"
    )
    # reserved key は injected 値ではない
    for key in non_sig_reserved:
        assert p_extra[key] != f"INJECTED_{key}"

    # (4) 既存 PR-W test 5 を逆方向から validate: payload key の最終順序で
    # injected reserved 値が登場しない
    keys_with_inject = list(p_all.keys())
    for key in non_sig_reserved:
        idx = keys_with_inject.index(key)
        # reserved key 位置は injected 値ではなく helper 構築の原 value
        assert p_all[key] != f"INJECTED_{key}", (
            f"position {idx} key {key!r} leaked"
        )


def test_observability_build_status_cost_dict_strict() -> None:
    """`build_status(cost=...)` の dict-or-None 限定 contract lock-in
    (Codex 03:21 PR-AF verdict AU、observability v1 schema drift 防止)。

    `cost` は docs/OBSERVABILITY.md §Cost JSON Shape の canonical nested
    object で、`build_cost_payload()` で構築する dict 形式が前提。旧実装は
    型 validation がなく、`list` / `str` / `int` / `bool` / `tuple` / `set`
    全て payload に素通りで以下の drift を起こしていた:

      - schema drift: `cost: [1,2,3]` 等の意図不明 payload が emit
      - `warn_legacy_cost_extras(payload)` の truthiness 判定 drift:
        `payload.get("cost")` truthy で nested cost 存在判定、list / str
        も truthy なので legacy extras warning が誤発火する経路
      - downstream parser が `cost.estimate` / `cost.rate_*` を AttributeError
        / TypeError で読み込み失敗

    新 contract: cost は None または dict のみ、それ以外 explicit TypeError
    で fail-loud。caller の build_cost_payload() 経由で dict 渡される正規
    経路は backward compatible。

    既存 strict 系 test (`exit_code int` PR-AC, `redaction_rules str-only`
    PR-AD, `reserved key collision` PR-AE) と同 level の defensive lint。
    """
    from _observability import build_cost_payload, build_status

    # (1) None は通る (rate 未設定 path、helper 慣習)
    p_none = build_status(script="x", v0_status="success", exit_code=0,
                          cost=None)
    assert p_none["cost"] is None

    # (2) 通常 dict (build_cost_payload 経由) は通る
    cost_dict = build_cost_payload(0.001, 1.5, 3.0)
    p_dict = build_status(script="x", v0_status="success", exit_code=0,
                          cost=cost_dict)
    assert p_dict["cost"] == cost_dict
    assert p_dict["cost"]["estimate"] == 0.001

    # (3) empty dict も accept (caller が partial cost dict を渡す経路)
    p_empty = build_status(script="x", v0_status="success", exit_code=0,
                           cost={})
    assert p_empty["cost"] == {}

    # (4) list reject (silent schema drift 防止)
    for bad_list in ([1, 2, 3], [], ["currency", "USD"]):
        try:
            build_status(script="x", v0_status="success", exit_code=0,
                         cost=bad_list)
        except TypeError as e:
            assert "cost" in str(e) and "dict" in str(e), (
                f"TypeError msg should mention cost + dict, got {e!r}"
            )
        else:
            raise AssertionError(
                f"list cost={bad_list!r} must raise TypeError"
            )

    # (5) str reject (silent schema drift 防止)
    for bad_str in ("cost", "USD", ""):
        try:
            build_status(script="x", v0_status="success", exit_code=0,
                         cost=bad_str)
        except TypeError:
            pass
        else:
            raise AssertionError(
                f"str cost={bad_str!r} must raise TypeError"
            )

    # (6) int / float reject
    for bad_num in (0, 5, 0.001, -1):
        try:
            build_status(script="x", v0_status="success", exit_code=0,
                         cost=bad_num)
        except TypeError:
            pass
        else:
            raise AssertionError(
                f"numeric cost={bad_num!r} must raise TypeError"
            )

    # (7) bool reject (dict subclass ではないが念のため strict 対象)
    for bad_bool in (True, False):
        try:
            build_status(script="x", v0_status="success", exit_code=0,
                         cost=bad_bool)
        except TypeError:
            pass
        else:
            raise AssertionError(
                f"bool cost={bad_bool!r} must raise TypeError"
            )

    # (8) tuple / set / その他 type reject
    for bad_other in ((1, 2), {1, 2}, object()):
        try:
            build_status(script="x", v0_status="success", exit_code=0,
                         cost=bad_other)
        except TypeError:
            pass
        else:
            raise AssertionError(
                f"non-dict cost={bad_other!r} must raise TypeError"
            )


def test_observability_build_status_counts_artifacts_strict() -> None:
    """`build_status(counts=...)` / `build_status(artifacts=...)` の defensive
    contract lock-in (Codex 03:51 PR-AK verdict AP、observability v1 schema
    drift 防止)。

    旧実装 `counts or {}` / `artifacts or []` は型 validation なしで以下の
    drift を silent に通していた:

      - counts=[1,2] (truthy list) → payload に list が schema 違反で乗る
      - counts="abc" (truthy str)  → payload に str が乗る
      - counts=5 / True (truthy)   → 同上
      - artifacts=[{"a":1}, "str", 5] (mixed) → list 内 non-dict 通過
      - artifacts={"a":1} (dict 単体) → list ではなく dict 通過
      - artifacts="x" (truthy str) → 同上

    新 contract:
      - counts: None or dict のみ受理、それ以外 explicit TypeError
      - artifacts: None or list のみ受理 + list 内全 entry が dict、
        それ以外 explicit TypeError (entry index 含む詳細 msg)

    既存 strict 系 (PR-AC exit_code int / PR-AD redaction_rules str-only /
    PR-AF cost dict-or-None) と同 level の defensive lint。
    """
    from _observability import build_status

    # ===== counts =====
    # (1a) None → {} (helper 慣習維持)
    p1 = build_status(script="x", v0_status="success", exit_code=0,
                      counts=None)
    assert p1["counts"] == {}

    # (1b) 通常 dict → そのまま
    p2 = build_status(script="x", v0_status="success", exit_code=0,
                      counts={"slides": 10})
    assert p2["counts"] == {"slides": 10}

    # (1c) empty dict → そのまま
    p3 = build_status(script="x", v0_status="success", exit_code=0,
                      counts={})
    assert p3["counts"] == {}

    # (1d) list / str / int / bool / tuple / set 全部 reject
    for bad_counts in ([1, 2], [], "abc", "", 5, 0, True, False,
                       (1, 2), {1, 2}, object()):
        try:
            build_status(script="x", v0_status="success", exit_code=0,
                         counts=bad_counts)
        except TypeError as e:
            assert "counts" in str(e) and "dict" in str(e), (
                f"TypeError msg should mention counts + dict, got {e!r}"
            )
        else:
            raise AssertionError(
                f"counts={bad_counts!r} ({type(bad_counts).__name__}) "
                f"must raise TypeError"
            )

    # ===== artifacts =====
    # (2a) None → [] (helper 慣習維持)
    pa1 = build_status(script="x", v0_status="success", exit_code=0,
                       artifacts=None)
    assert pa1["artifacts"] == []

    # (2b) 通常 list-of-dict → そのまま
    arts = [{"path": "x.json", "kind": "output"},
            {"path": "y.json", "kind": "report"}]
    pa2 = build_status(script="x", v0_status="success", exit_code=0,
                       artifacts=arts)
    assert pa2["artifacts"] == arts

    # (2c) empty list → そのまま
    pa3 = build_status(script="x", v0_status="success", exit_code=0,
                       artifacts=[])
    assert pa3["artifacts"] == []

    # (2d) dict 単体 (not list) reject
    try:
        build_status(script="x", v0_status="success", exit_code=0,
                     artifacts={"path": "x.json"})
    except TypeError as e:
        assert "artifacts" in str(e) and "list" in str(e)
    else:
        raise AssertionError("dict 単体 artifacts must raise TypeError")

    # (2e) str / int / tuple reject (not list)
    for bad_arts in ("artifact", "", 5, (1, 2)):
        try:
            build_status(script="x", v0_status="success", exit_code=0,
                         artifacts=bad_arts)
        except TypeError:
            pass
        else:
            raise AssertionError(
                f"non-list artifacts={bad_arts!r} must raise TypeError"
            )

    # (2f) list 内 non-dict entry reject (混在も含む)
    bad_artifact_lists = [
        ["str_only"],
        [{"path": "ok.json"}, "str_in_middle", {"path": "ok2.json"}],
        [{"a": 1}, 5],
        [None],
        [[]],
    ]
    for bad_list in bad_artifact_lists:
        try:
            build_status(script="x", v0_status="success", exit_code=0,
                         artifacts=bad_list)
        except TypeError as e:
            assert "artifacts" in str(e) and "dict" in str(e), (
                f"TypeError msg should mention artifacts + dict, got {e!r}"
            )
        else:
            raise AssertionError(
                f"artifacts={bad_list!r} (mixed/non-dict) must raise TypeError"
            )


def test_observability_build_status_script_identifier_contract() -> None:
    """`build_status(script=...)` の str + non-empty + no-control-char contract
    lock-in (Codex 04:04 PR-AM verdict AZ、observability v1 core identifier
    drift 防止 + emit_json 1-line format protection)。

    `script` は v1 status JSON の core identifier で、downstream consumer /
    log filter / dashboard が script 名で payload を bucket する根拠 field。
    旧実装は型 / 空文字 / 制御文字 validation なしで以下の drift を silent
    payload 通過させていた:

      - `""` / `"   "` (whitespace-only) → bucket 不能 / dashboard で
        unidentified group に集約
      - `"a\\nb"` / `"a\\tb"` → emit_json `print(json.dumps(payload))` で
        json.dumps が escape はするが、caller の grep / log line parse が
        broken
      - `"a\\x00b"` → null byte が JSON output / log file で表示崩れ
      - None / int / bool / list → downstream str-method 呼び出しで
        AttributeError / TypeError

    新 contract:
      - script: 非 None str (TypeError on type mismatch)
      - 非空 (whitespace-only も含む) (ValueError)
      - 制御文字 \\x00-\\x1F + \\x7F 不含 (ValueError)

    既存 strict 系 (PR-AC exit_code int / PR-AD redaction_rules / PR-AF cost /
    PR-AK counts/artifacts / PR-AL rate_source) と同 level の defensive lint。
    """
    from _observability import build_status

    # ===== accept =====
    # (1a) 通常 script 名 (snake_case identifier)
    p_normal = build_status(script="generate_slide_plan",
                            v0_status="success", exit_code=0)
    assert p_normal["script"] == "generate_slide_plan"

    # (1b) 拡張子付き
    p_ext = build_status(script="visual_smoke.py",
                         v0_status="success", exit_code=0)
    assert p_ext["script"] == "visual_smoke.py"

    # (1c) 1 char
    p_min = build_status(script="x", v0_status="success", exit_code=0)
    assert p_min["script"] == "x"

    # (1d) 日本語 / unicode (制御文字以外は accept)
    p_jp = build_status(script="日本語スクリプト",
                        v0_status="success", exit_code=0)
    assert p_jp["script"] == "日本語スクリプト"

    # ===== reject =====
    # (2) 型違反: None / int / float / bool / list / dict
    for bad_type in (None, 5, 1.5, True, False, ["script"], {"name": "x"}):
        try:
            build_status(script=bad_type, v0_status="success", exit_code=0)
        except TypeError as e:
            assert "script" in str(e) and "str" in str(e), (
                f"TypeError msg should mention script + str, got {e!r}"
            )
        else:
            raise AssertionError(
                f"non-str script={bad_type!r} must raise TypeError"
            )

    # (3) 空文字 / whitespace-only (bucket 不能) → ValueError 限定で contract 明示
    # Codex 04:08 PR-AM review P2 fix: tab / newline 単独も whitespace なので
    # strip() ガードが先に trip して "non-empty" ValueError 経路を通る (PR-AM
    # 検査順 1=isinstance str → 2=non-empty strip → 3=control char に依存)。
    for empty_or_ws in ("", " ", "   ", "\t", "\n", "  \t  "):
        try:
            build_status(script=empty_or_ws, v0_status="success", exit_code=0)
        except ValueError as e:
            assert "script" in str(e) and "non-empty" in str(e), (
                f"ValueError msg should mention script + non-empty, got {e!r}"
            )
        else:
            raise AssertionError(
                f"empty/whitespace-only script={empty_or_ws!r} must raise ValueError"
            )

    # (4) 制御文字含む (emit_json format / log line parser 破壊)
    for ctrl_script in ("a\nb", "a\rb", "a\x00b", "abc\x01",
                        "abc\x1f", "a\x7fb", "\nabc"):
        try:
            build_status(script=ctrl_script, v0_status="success", exit_code=0)
        except ValueError as e:
            assert "control" in str(e) and "script" in str(e), (
                f"ValueError msg should mention control + script, got {e!r}"
            )
        else:
            raise AssertionError(
                f"control-char script={ctrl_script!r} must raise ValueError"
            )

    # (5) regression guard: 既存 callers が使う 7 script 名は全 accept
    for existing in ("generate_slide_plan", "voicevox_narration",
                     "build_slide_data", "build_telop_data",
                     "preflight_video", "visual_smoke",
                     "compare_telop_split"):
        p = build_status(script=existing, v0_status="success", exit_code=0)
        assert p["script"] == existing


def test_observability_build_status_v0_status_defensive_lint() -> None:
    """`build_status(v0_status=...)` の str + non-empty + no-control-char
    contract lock-in (Codex 04:24 PR-AP verdict BA、observability v1 STATUS_MAP
    fallback path drift 防止)。

    `v0_status` は `map_status()` の lookup key で、未知 status は
    `("error", v0_status)` defensive fallback で category=v0_status となる
    仕様 (PR-T 設計)。fallback 仕様は維持しつつ、以下の drift を fail-loud:

      - `""` / `"   "` (whitespace-only) → category="" / "   " で PR-AN
        format invariant 違反 + bucket 不能
      - `"a\\nb"` / `"a\\x00b"` → category に control char が漏れて
        emit_json 1-line format invariant (PR-Y) 破壊 + log line parser break
      - None / int / bool → 非 str fallback で category 型違反 (downstream
        str-method AttributeError)
      - list → dict lookup の unhashable で uncaught TypeError (旧実装)
        → 明示 TypeError msg に統一

    新 contract:
      - v0_status: 非 None str (TypeError on type mismatch)
      - 非空 (whitespace-only も含む) (ValueError)
      - 制御文字 \\x00-\\x1F + \\x7F 不含 (ValueError)

    PR-AM script identifier contract と同型 + STATUS_MAP fallback 経路の
    defensive lint。既存 strict 系 (PR-AC/AD/AF/AK/AL/AM/AO) と同 level。
    """
    from _observability import build_status

    # ===== accept =====
    # (1a) STATUS_MAP に存在する v0_status (success / rate_limited 等)
    p_success = build_status(script="x", v0_status="success", exit_code=0)
    assert p_success["status"] == "ok"
    assert p_success["category"] is None

    # (1b) PR-T 設計の defensive fallback: 未知 v0_status → ("error", v0_status)
    p_unknown = build_status(script="x", v0_status="mystery_status",
                             exit_code=2)
    assert p_unknown["status"] == "error"
    assert p_unknown["category"] == "mystery_status"

    # (1c) 1 char (snake_case identifier 形式)
    p_min = build_status(script="x", v0_status="a", exit_code=0)
    assert p_min["category"] == "a"

    # ===== reject 型違反 =====
    for bad_type in (None, 5, 1.5, True, False, [], ["status"], {"k": "v"}):
        try:
            build_status(script="x", v0_status=bad_type, exit_code=0)
        except TypeError as e:
            assert "v0_status" in str(e) and "str" in str(e), (
                f"TypeError msg should mention v0_status + str, got {e!r}"
            )
        else:
            raise AssertionError(
                f"non-str v0_status={bad_type!r} must raise TypeError"
            )

    # ===== reject 空文字 / whitespace-only =====
    for empty_or_ws in ("", " ", "   ", "\t", "\n", "  \t  "):
        try:
            build_status(script="x", v0_status=empty_or_ws, exit_code=0)
        except ValueError as e:
            assert "v0_status" in str(e) and "non-empty" in str(e), (
                f"ValueError msg should mention v0_status + non-empty, "
                f"got {e!r}"
            )
        else:
            raise AssertionError(
                f"empty/whitespace v0_status={empty_or_ws!r} must raise "
                f"ValueError"
            )

    # ===== reject 制御文字 =====
    for ctrl_v0 in ("a\nb", "a\rb", "a\x00b", "abc\x01",
                    "abc\x1f", "a\x7fb", "\nabc"):
        try:
            build_status(script="x", v0_status=ctrl_v0, exit_code=0)
        except ValueError as e:
            assert "control" in str(e) and "v0_status" in str(e), (
                f"ValueError msg should mention control + v0_status, got {e!r}"
            )
        else:
            raise AssertionError(
                f"control-char v0_status={ctrl_v0!r} must raise ValueError"
            )

    # ===== regression guard: 既存 v0 emission status 群は全 accept =====
    # PR-T must_have set 由来、representative 7 件
    for existing in ("success", "rate_limited", "api_key_skipped",
                     "dry_run", "cost_guard_aborted", "ffprobe_failed",
                     "smoke_ok"):
        p = build_status(script="x", v0_status=existing, exit_code=0)
        # status / category は STATUS_MAP に従って解決 (詳細は PR-T で固定)
        assert p["status"] in ("ok", "skipped", "error", "dry_run")


def test_observability_emit_json_payload_must_be_dict() -> None:
    """`emit_json(enabled, payload)` の payload dict 必須 contract lock-in
    (Codex 04:30 PR-AQ verdict BB、observability v1 transport contract drift 防止)。

    `--json-log` の末尾行は v1 status JSON object 前提に downstream parser
    が組まれるため、payload は dict 必須。旧実装は entry で直接
    `payload.get("exit_code", 0)` を呼ぶため、non-dict 入力 (None / list /
    str / int / tuple / set) で uncaught AttributeError "X object has no
    attribute 'get'" が出るだけで、caller の責務違反が分かりにくい drift。

    新 contract: emit_json 入口で `isinstance(payload, dict)` 違反を explicit
    TypeError で fail-loud、`payload` 名 + 期待 + 実値 repr 含めて debug 可能。
    既存 callers (emit_json は build_status() 経由 dict のみ受け取る) は
    backward compatible。

    既存 strict 系 (PR-AC exit_code int / PR-AD redaction_rules / PR-AF cost /
    PR-AK counts/artifacts / PR-AL rate_source / PR-AM script / PR-AO secret /
    PR-AP v0_status) と同 level の defensive lint。
    """
    from _observability import emit_json

    # ===== accept =====
    # (1a) 通常 dict (build_status 由来想定)
    rc = emit_json(False, {"status": "ok", "exit_code": 0})
    assert rc == 0
    # (1b) empty dict (default exit_code=0)
    rc_empty = emit_json(False, {})
    assert rc_empty == 0
    # (1c) exit_code 含む dict
    rc_err = emit_json(False, {"status": "error", "exit_code": 2})
    assert rc_err == 2

    # ===== reject 非 dict 全種 =====
    for bad_payload in (
        None, [], [1, 2], "payload", "", 5, 0, 1.5,
        True, False, (), (1, 2), {1, 2}, object(),
    ):
        try:
            emit_json(False, bad_payload)
        except TypeError as e:
            assert "payload" in str(e) and "dict" in str(e), (
                f"TypeError msg should mention payload + dict, got {e!r}"
            )
        else:
            raise AssertionError(
                f"non-dict payload={bad_payload!r} ({type(bad_payload).__name__}) "
                f"must raise TypeError"
            )

    # ===== reject 経路で stdout に何も書かれない (PR-AC stdout 空 invariant 維持) =====
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            emit_json(True, None)  # enabled=True でも payload 違反で raise
        except TypeError:
            pass
    assert buf.getvalue() == "", (
        f"emit_json must reject before printing, got stdout={buf.getvalue()!r}"
    )


def test_observability_resolve_run_context_uses_env() -> None:
    """env (SUPERMOVIE_RUN_ID / PARENT_RUN_ID / STEP_ID) 設定値をそのまま採用する。"""
    import os as _os
    from _observability import (
        resolve_run_context,
        TRACE_RUN_ID_ENV,
        TRACE_PARENT_RUN_ID_ENV,
        TRACE_STEP_ID_ENV,
    )

    keys = (TRACE_RUN_ID_ENV, TRACE_PARENT_RUN_ID_ENV, TRACE_STEP_ID_ENV)
    saved = {k: _os.environ.get(k) for k in keys}
    try:
        _os.environ[TRACE_RUN_ID_ENV] = "run-abc"
        _os.environ[TRACE_PARENT_RUN_ID_ENV] = "parent-xyz"
        _os.environ[TRACE_STEP_ID_ENV] = "step-1"
        ctx = resolve_run_context()
        assert ctx == {
            "run_id": "run-abc",
            "parent_run_id": "parent-xyz",
            "step_id": "step-1",
        }, f"env override should be passed through, got {ctx}"
    finally:
        for k, v in saved.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v


def test_observability_resolve_run_context_generates_when_missing() -> None:
    """env 未設定時 + generate_if_missing=True → uuid4 hex (32 char) を生成。

    parent / step は env のみ、未設定なら None (auto-generate しない)。
    """
    import os as _os
    import re
    from _observability import (
        resolve_run_context,
        TRACE_RUN_ID_ENV,
        TRACE_PARENT_RUN_ID_ENV,
        TRACE_STEP_ID_ENV,
    )

    keys = (TRACE_RUN_ID_ENV, TRACE_PARENT_RUN_ID_ENV, TRACE_STEP_ID_ENV)
    saved = {k: _os.environ.get(k) for k in keys}
    try:
        for k in keys:
            _os.environ.pop(k, None)
        ctx = resolve_run_context()
        assert ctx["run_id"] is not None, "missing env should auto-generate run_id"
        assert re.fullmatch(r"[0-9a-f]{32}", ctx["run_id"]), \
            f"generated run_id should be 32-char hex, got {ctx['run_id']!r}"
        assert ctx["parent_run_id"] is None, "parent should remain None when env unset"
        assert ctx["step_id"] is None, "step should remain None when env unset"
        # 連続 call で違う run_id が出る (uuid4 由来)
        ctx2 = resolve_run_context()
        assert ctx2["run_id"] != ctx["run_id"], "uuid4 collision suspected"
    finally:
        for k, v in saved.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v


def test_observability_resolve_run_context_no_generate() -> None:
    """generate_if_missing=False で env 未設定 → run_id=None (生成しない)。"""
    import os as _os
    from _observability import resolve_run_context, TRACE_RUN_ID_ENV

    saved = _os.environ.get(TRACE_RUN_ID_ENV)
    try:
        _os.environ.pop(TRACE_RUN_ID_ENV, None)
        ctx = resolve_run_context(generate_if_missing=False)
        assert ctx["run_id"] is None, f"generate_if_missing=False should return None, got {ctx['run_id']!r}"
    finally:
        if saved is None:
            _os.environ.pop(TRACE_RUN_ID_ENV, None)
        else:
            _os.environ[TRACE_RUN_ID_ENV] = saved


def test_observability_resolve_run_context_empty_env_fallback() -> None:
    """env が空文字列 → 未設定扱い、generate_if_missing=True で uuid4 生成。"""
    import os as _os
    from _observability import resolve_run_context, TRACE_RUN_ID_ENV

    saved = _os.environ.get(TRACE_RUN_ID_ENV)
    try:
        _os.environ[TRACE_RUN_ID_ENV] = ""
        ctx = resolve_run_context()
        assert ctx["run_id"] is not None, "empty env should be treated as missing"
        assert len(ctx["run_id"]) == 32, "empty env should fall through to uuid4"
    finally:
        if saved is None:
            _os.environ.pop(TRACE_RUN_ID_ENV, None)
        else:
            _os.environ[TRACE_RUN_ID_ENV] = saved


def test_observability_resolve_run_context_cap_exceeded() -> None:
    """env 値が MAX_TRACE_CONTEXT_VALUE_LEN 超過 → TraceContextError raise (truncation せず error)。"""
    import os as _os
    from _observability import (
        resolve_run_context,
        TRACE_RUN_ID_ENV,
        MAX_TRACE_CONTEXT_VALUE_LEN,
        TraceContextError,
    )

    saved = _os.environ.get(TRACE_RUN_ID_ENV)
    try:
        _os.environ[TRACE_RUN_ID_ENV] = "x" * (MAX_TRACE_CONTEXT_VALUE_LEN + 1)
        try:
            resolve_run_context()
        except TraceContextError as e:
            assert "exceeds" in str(e) or "MAX_TRACE_CONTEXT_VALUE_LEN" in str(e), \
                f"error should mention cap, got {e}"
        else:
            assert False, "should raise TraceContextError on cap exceed"
    finally:
        if saved is None:
            _os.environ.pop(TRACE_RUN_ID_ENV, None)
        else:
            _os.environ[TRACE_RUN_ID_ENV] = saved


def test_observability_resolve_run_context_cap_boundary() -> None:
    """env value cap (`MAX_TRACE_CONTEXT_VALUE_LEN = 128`) の境界値 lock-in
    (Codex 03:28 PR-AG verdict AR、distributed tracing contract drift 防止)。

    既存 `test_observability_resolve_run_context_cap_exceeded` は `129 char`
    の reject のみ被覆、`127/128 char` accept 側 (boundary inclusive contract)
    は固定されていなかった。

    `_validate_trace_value` の cap 比較が `> MAX_TRACE_CONTEXT_VALUE_LEN`
    から `>= ...` 等にリファクタされた場合、`128 char` が突然 reject される
    silent contract regression を early fail させるため、127 / 128 / 129
    の 3 boundary 全てを explicit assert する。

    対象 env: TRACE_RUN_ID_ENV / TRACE_PARENT_RUN_ID_ENV / TRACE_STEP_ID_ENV
    全て同 helper validation 経路 (`_validate_trace_value`) なので、
    1 env で boundary を固定すれば 3 env 共通の invariant lock-in。本 test
    は 3 env それぞれで独立 boundary 確認を行う。
    """
    import os as _os

    from _observability import (
        MAX_TRACE_CONTEXT_VALUE_LEN,
        TRACE_PARENT_RUN_ID_ENV,
        TRACE_RUN_ID_ENV,
        TRACE_STEP_ID_ENV,
        TraceContextError,
        resolve_run_context,
    )

    assert MAX_TRACE_CONTEXT_VALUE_LEN == 128, (
        f"contract: cap is 128, got {MAX_TRACE_CONTEXT_VALUE_LEN}"
    )

    saved_run = _os.environ.get(TRACE_RUN_ID_ENV)
    saved_parent = _os.environ.get(TRACE_PARENT_RUN_ID_ENV)
    saved_step = _os.environ.get(TRACE_STEP_ID_ENV)
    try:
        for env_name, ctx_key in [
            (TRACE_RUN_ID_ENV, "run_id"),
            (TRACE_PARENT_RUN_ID_ENV, "parent_run_id"),
            (TRACE_STEP_ID_ENV, "step_id"),
        ]:
            # 他 env を空 (unset) にして対象 env 単独で検査
            for other in (TRACE_RUN_ID_ENV, TRACE_PARENT_RUN_ID_ENV,
                          TRACE_STEP_ID_ENV):
                _os.environ.pop(other, None)

            # (1) 127 char accept (boundary 内、`<= cap` 経路)
            _os.environ[env_name] = "x" * (MAX_TRACE_CONTEXT_VALUE_LEN - 1)
            ctx = resolve_run_context()
            assert ctx[ctx_key] == "x" * (MAX_TRACE_CONTEXT_VALUE_LEN - 1), (
                f"127 char must be accepted as-is for {env_name}, "
                f"got {ctx[ctx_key]!r}"
            )

            # (2) 128 char accept (boundary inclusive、`<= cap` 経路)
            _os.environ[env_name] = "y" * MAX_TRACE_CONTEXT_VALUE_LEN
            ctx = resolve_run_context()
            assert ctx[ctx_key] == "y" * MAX_TRACE_CONTEXT_VALUE_LEN, (
                f"128 char (cap inclusive) must be accepted for {env_name}, "
                f"got {ctx[ctx_key]!r} (len={len(ctx[ctx_key])})"
            )

            # (3) 129 char reject (boundary 外、`> cap` 経路)
            _os.environ[env_name] = "z" * (MAX_TRACE_CONTEXT_VALUE_LEN + 1)
            try:
                resolve_run_context()
            except TraceContextError as e:
                assert env_name in str(e) or "exceeds" in str(e) or \
                    "MAX_TRACE_CONTEXT_VALUE_LEN" in str(e), (
                        f"error msg should reference cap, got {e}"
                    )
            else:
                raise AssertionError(
                    f"129 char must raise TraceContextError for {env_name}"
                )
    finally:
        for env_name, saved in [
            (TRACE_RUN_ID_ENV, saved_run),
            (TRACE_PARENT_RUN_ID_ENV, saved_parent),
            (TRACE_STEP_ID_ENV, saved_step),
        ]:
            if saved is None:
                _os.environ.pop(env_name, None)
            else:
                _os.environ[env_name] = saved


def test_observability_build_status_schema_version_invariant() -> None:
    """`build_status()` の `schema_version: 1` invariant + header hash lock-in
    (Codex 03:33 PR-AH verdict AT、observability v1 contract drift 防止)。

    `schema_version` は v1 schema の root identifier で、downstream consumer /
    log analyzer / regression test が「v1 payload を読んでいる」前提を立てる
    最上流契約。本 test は 4 層で invariant を固定:

      (1) `SCHEMA_VERSION` module constant が int 1 (型 + 値 lock-in、定数差替
          regression を early fail)
      (2) build_status() の出力で `schema_version=1` を全 v0 status (success /
          error / skipped / dry_run 各経路) で確認、`map_status` 経由でも
          schema header が壊れないことを保証
      (3) `**extras` で `schema_version=999` 等を inject しても reserved-wins
          で 1 維持 (PR-AE 5 keys の `schema_version` 単独ピン留め)
      (4) header (`schema_version` / `script` / `status` / `ok` / `exit_code` /
          `category`) の deterministic JSON hash snapshot を固定。リファクタで
          header 部分が変質した場合に hash mismatch で early fail

    既存 PR-W (field order) / PR-AE (reserved key collision) と役割が分離:
    本 test は schema_version 単独の root identifier 不変性 + header bytes
    snapshot を独立 lock-in。
    """
    import hashlib as _hashlib
    import json as _json

    from _observability import SCHEMA_VERSION, build_status

    # (1) 定数値 + 型 lock-in (drift detection at module level)
    assert SCHEMA_VERSION == 1, (
        f"SCHEMA_VERSION constant drift: expected 1, got {SCHEMA_VERSION!r}"
    )
    assert isinstance(SCHEMA_VERSION, int), (
        f"SCHEMA_VERSION must be int, got {type(SCHEMA_VERSION).__name__}"
    )
    # bool は int subclass なので明示 reject
    assert not isinstance(SCHEMA_VERSION, bool), (
        f"SCHEMA_VERSION must not be bool, got {SCHEMA_VERSION!r}"
    )

    # (2) build_status 出力で `schema_version=1` を全 v0 status 経路で確認
    for v0, exit_code in [
        ("success", 0),
        ("rate_limited", 2),
        ("api_key_skipped", 0),
        ("dry_run", 0),
        ("cost_guard_aborted", 10),
        ("ffprobe_failed", 2),
        ("smoke_ok", 0),
    ]:
        p = build_status(script="x", v0_status=v0, exit_code=exit_code)
        assert p["schema_version"] == 1, (
            f"schema_version drift for v0={v0!r}: {p['schema_version']!r}"
        )
        assert isinstance(p["schema_version"], int), (
            f"schema_version must remain int for v0={v0!r}, "
            f"got {type(p['schema_version']).__name__}"
        )
        assert not isinstance(p["schema_version"], bool), (
            f"schema_version must not be bool for v0={v0!r}"
        )

    # (3) extras で schema_version override 試行 → reserved-wins で 1 維持
    p_inj = build_status(script="x", v0_status="success", exit_code=0,
                         **{"schema_version": 999})
    assert p_inj["schema_version"] == 1, (
        f"extras schema_version=999 leaked: {p_inj['schema_version']!r}"
    )

    # (4) header 6 field の deterministic hash snapshot lock-in。
    # 入力: script="test_script" / v0="success" / exit_code=0 で
    # header == {"schema_version":1,"script":"test_script","status":"ok",
    #            "ok":true,"exit_code":0,"category":null}
    # JSON string (sort_keys=False で payload の insertion order 保持) を
    # SHA-256 16 char prefix で snapshot、PR-W 順序契約と相互強化。
    p_baseline = build_status(script="test_script", v0_status="success",
                              exit_code=0)
    header = {
        k: p_baseline[k]
        for k in ("schema_version", "script", "status", "ok",
                  "exit_code", "category")
    }
    expected_header = {
        "schema_version": 1,
        "script": "test_script",
        "status": "ok",
        "ok": True,
        "exit_code": 0,
        "category": None,
    }
    assert header == expected_header, (
        f"header content drift: {header}"
    )
    header_json = _json.dumps(header, ensure_ascii=False, sort_keys=False)
    header_hash = _hashlib.sha256(header_json.encode("utf-8")).hexdigest()[:16]
    expected_hash = "4e1cd359d000dd2b"
    assert header_hash == expected_hash, (
        f"header snapshot hash drift: expected {expected_hash}, "
        f"got {header_hash} (header_json={header_json!r}); "
        f"if header schema 意図的に changed、update expected_hash"
    )


def test_observability_run_id_in_payload() -> None:
    """build_status は run_id / parent_run_id / step_id が non-None で payload に乗せる。"""
    from _observability import build_status

    p1 = build_status(
        script="x", v0_status="success", exit_code=0,
        run_id="run-1", parent_run_id="parent-1", step_id="step-1",
    )
    assert p1["run_id"] == "run-1"
    assert p1["parent_run_id"] == "parent-1"
    assert p1["step_id"] == "step-1"

    # parent / step が None の時は payload に含めない (legacy 互換)
    p2 = build_status(
        script="x", v0_status="success", exit_code=0,
        run_id="run-2", parent_run_id=None, step_id=None,
    )
    assert p2["run_id"] == "run-2"
    assert "parent_run_id" not in p2
    assert "step_id" not in p2


def test_generate_slide_plan_run_id_propagation() -> None:
    """generate_slide_plan dry-run --json-log 経由で v1 tail に run_id が乗る (env 設定時)。

    既存 dry-run legacy JSON は維持、--json-log 時のみ v1 tail を追加 emit (2-emission pattern)。
    """
    import os as _os
    import io
    import sys as _sys
    import importlib
    from contextlib import redirect_stdout

    saved_env = {k: _os.environ.get(k) for k in (
        "ANTHROPIC_API_KEY",
        "SUPERMOVIE_RUN_ID",
        "SUPERMOVIE_PARENT_RUN_ID",
        "SUPERMOVIE_STEP_ID",
        "SUPERMOVIE_RATE_INPUT_PER_MTOK",
        "SUPERMOVIE_RATE_OUTPUT_PER_MTOK",
        "SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK",
        "SUPERMOVIE_RATE_ANTHROPIC_OUTPUT_USD_PER_MTOK",
    )}
    saved_argv = list(_sys.argv)
    saved_cwd = _os.getcwd()

    import shutil as _shutil
    proj = Path(tempfile.mkdtemp(prefix="run_id_prop_"))
    (proj / "transcript_fixed.json").write_text(
        json.dumps({
            "duration_ms": 5000,
            "words": [{"text": "hi", "start": 0, "end": 500, "confidence": 0.9}],
            "segments": [{"text": "hi", "start": 0, "end": 500}],
        }),
        encoding="utf-8",
    )
    (proj / "project-config.json").write_text(
        json.dumps({"format": "youtube", "videoType": "test", "tone": "test"}),
        encoding="utf-8",
    )
    try:
        _os.environ["SUPERMOVIE_RUN_ID"] = "run-prop-test"
        _os.environ["SUPERMOVIE_PARENT_RUN_ID"] = "parent-prop"
        _os.environ["SUPERMOVIE_STEP_ID"] = "step-prop"
        _os.environ.pop("ANTHROPIC_API_KEY", None)
        for k in (
            "SUPERMOVIE_RATE_INPUT_PER_MTOK",
            "SUPERMOVIE_RATE_OUTPUT_PER_MTOK",
            "SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK",
            "SUPERMOVIE_RATE_ANTHROPIC_OUTPUT_USD_PER_MTOK",
        ):
            _os.environ.pop(k, None)
        _os.chdir(str(proj))

        import generate_slide_plan as gsp
        importlib.reload(gsp)
        gsp.PROJ = proj

        _sys.argv = ["generate_slide_plan.py", "--dry-run", "--json-log"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = gsp.main()
        assert rc == 0, f"dry-run --json-log should return 0, got {rc}"
        lines = [l for l in buf.getvalue().splitlines() if l.strip()]
        # 2 emission: dry-run legacy JSON + v1 tail
        assert len(lines) >= 2, f"expected >=2 lines (legacy + v1 tail), got {len(lines)}: {lines}"
        v1_tail = json.loads(lines[-1])
        assert v1_tail.get("schema_version") == 1, f"v1 tail missing schema_version: {v1_tail}"
        assert v1_tail.get("run_id") == "run-prop-test", \
            f"run_id propagation failed: {v1_tail.get('run_id')!r}"
        assert v1_tail.get("parent_run_id") == "parent-prop"
        assert v1_tail.get("step_id") == "step-prop"
        assert v1_tail.get("status") == "dry_run"
    finally:
        _os.chdir(saved_cwd)
        _sys.argv = saved_argv
        for k, v in saved_env.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v
        _shutil.rmtree(proj, ignore_errors=True)


def test_generate_slide_plan_cost_abort_blocks_api_when_estimate_exceeds() -> None:
    """estimate cost > cost-abort-at で API 呼ばず exit 10 + status=cost_guard_aborted。

    rate 設定 + ANTHROPIC_API_KEY 設定で本来 API call に進む path を、cost-abort 閾値で
    pre-flight abort する。urllib.urlopen を fail_if_called で監視、call 0 確認。
    """
    import os as _os
    import io
    import sys as _sys
    import importlib
    import urllib.request as _urlreq
    from contextlib import redirect_stdout, redirect_stderr

    saved_env = {k: _os.environ.get(k) for k in (
        "ANTHROPIC_API_KEY",
        "SUPERMOVIE_RATE_INPUT_PER_MTOK",
        "SUPERMOVIE_RATE_OUTPUT_PER_MTOK",
        "SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK",
        "SUPERMOVIE_RATE_ANTHROPIC_OUTPUT_USD_PER_MTOK",
        "SUPERMOVIE_COST_USD_ABORT_AT",
    )}
    saved_argv = list(_sys.argv)
    saved_cwd = _os.getcwd()
    saved_urlopen = _urlreq.urlopen

    proj = Path(tempfile.mkdtemp(prefix="cost_abort_"))
    (proj / "transcript_fixed.json").write_text(
        json.dumps({
            "duration_ms": 5000,
            "words": [{"text": "ab", "start": 0, "end": 500, "confidence": 0.9}],
            "segments": [{"text": "ab", "start": 0, "end": 500}],
        }),
        encoding="utf-8",
    )
    (proj / "project-config.json").write_text(
        json.dumps({"format": "youtube", "videoType": "test", "tone": "test"}),
        encoding="utf-8",
    )

    api_called = {"count": 0}

    def _fail_if_called(*args, **kwargs):
        api_called["count"] += 1
        raise AssertionError("urllib.urlopen should NOT be called when cost-abort blocks API")

    try:
        # rate 設定 + ANTHROPIC_API_KEY 設定 (本来 API path)
        _os.environ["ANTHROPIC_API_KEY"] = "test-key"
        # rate を高く設定して estimate が確実に閾値超え
        _os.environ["SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK"] = "100.0"
        _os.environ["SUPERMOVIE_RATE_ANTHROPIC_OUTPUT_USD_PER_MTOK"] = "100.0"
        _os.environ.pop("SUPERMOVIE_RATE_INPUT_PER_MTOK", None)
        _os.environ.pop("SUPERMOVIE_RATE_OUTPUT_PER_MTOK", None)
        # 閾値 0.001 USD (microscopic) → estimate >> threshold で必ず abort
        _os.environ["SUPERMOVIE_COST_USD_ABORT_AT"] = "0.001"
        _os.chdir(str(proj))

        import generate_slide_plan as gsp
        importlib.reload(gsp)
        gsp.PROJ = proj
        _urlreq.urlopen = _fail_if_called

        _sys.argv = ["generate_slide_plan.py", "--json-log"]
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = gsp.main()

        assert rc == 10, f"cost-abort should return exit 10, got {rc}"
        assert api_called["count"] == 0, "urllib.urlopen called despite cost-abort"
        assert "cost-abort" in err_buf.getvalue() or "cost_abort" in err_buf.getvalue() or "abort" in err_buf.getvalue(), \
            f"stderr should mention abort, got: {err_buf.getvalue()!r}"
        # v1 tail
        lines = [l for l in out_buf.getvalue().splitlines() if l.strip()]
        v1_tail = json.loads(lines[-1])
        assert v1_tail["status"] == "error"
        assert v1_tail["category"] == "cost_guard_aborted"
        assert v1_tail["exit_code"] == 10
        assert v1_tail.get("estimated_cost_usd_upper_bound") is not None
        assert v1_tail.get("cost_abort_at") == 0.001
    finally:
        _os.chdir(saved_cwd)
        _sys.argv = saved_argv
        _urlreq.urlopen = saved_urlopen
        for k, v in saved_env.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v
        import shutil as _shutil
        _shutil.rmtree(proj, ignore_errors=True)


def test_generate_slide_plan_cost_abort_skipped_when_rate_unset() -> None:
    """rate 未設定 (estimate=None) 時は閾値設定があっても abort せず通常進行。

    cost 不明状態で勝手に abort すると後方互換が壊れるため、rate 未設定時は閾値スキップ。
    本テストは API 呼び出しを mock して通常 path に到達することを確認。
    """
    import os as _os
    import io
    import sys as _sys
    import importlib
    import urllib.request as _urlreq
    from contextlib import redirect_stdout, redirect_stderr

    saved_env = {k: _os.environ.get(k) for k in (
        "ANTHROPIC_API_KEY",
        "SUPERMOVIE_RATE_INPUT_PER_MTOK",
        "SUPERMOVIE_RATE_OUTPUT_PER_MTOK",
        "SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK",
        "SUPERMOVIE_RATE_ANTHROPIC_OUTPUT_USD_PER_MTOK",
        "SUPERMOVIE_COST_USD_ABORT_AT",
    )}
    saved_argv = list(_sys.argv)
    saved_cwd = _os.getcwd()
    saved_urlopen = _urlreq.urlopen

    proj = Path(tempfile.mkdtemp(prefix="cost_abort_skip_"))
    (proj / "transcript_fixed.json").write_text(
        json.dumps({
            "duration_ms": 5000,
            "words": [{"text": "ab", "start": 0, "end": 500, "confidence": 0.9}],
            "segments": [{"text": "ab", "start": 0, "end": 500}],
        }),
        encoding="utf-8",
    )
    (proj / "project-config.json").write_text(
        json.dumps({"format": "youtube", "videoType": "test", "tone": "test"}),
        encoding="utf-8",
    )

    api_called = {"count": 0}

    class _MockResp:
        def __init__(self, payload):
            self._payload = payload
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return json.dumps(self._payload).encode("utf-8")

    def _mock_urlopen(*args, **kwargs):
        api_called["count"] += 1
        return _MockResp({
            "content": [{"type": "text", "text": json.dumps({
                "version": "supermovie.slide_plan.v1",
                "slides": [],
                "model": "test", "max_tokens": 100,
            })}],
        })

    try:
        _os.environ["ANTHROPIC_API_KEY"] = "test-key"
        # rate 未設定 (全 unset) → estimate=None
        for k in ("SUPERMOVIE_RATE_INPUT_PER_MTOK", "SUPERMOVIE_RATE_OUTPUT_PER_MTOK",
                  "SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK",
                  "SUPERMOVIE_RATE_ANTHROPIC_OUTPUT_USD_PER_MTOK"):
            _os.environ.pop(k, None)
        # 閾値設定するが、rate 未設定で skip 想定
        _os.environ["SUPERMOVIE_COST_USD_ABORT_AT"] = "0.001"
        _os.chdir(str(proj))

        import generate_slide_plan as gsp
        importlib.reload(gsp)
        gsp.PROJ = proj
        _urlreq.urlopen = _mock_urlopen

        _sys.argv = ["generate_slide_plan.py", "--json-log"]
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = gsp.main()

        # rate 未設定だが abort せず通常 path 進行 (API call 1 回)
        assert api_called["count"] == 1, \
            f"rate-unset + threshold-set should NOT abort (estimate=None skip), api_called={api_called['count']}"
        assert rc == 0, f"normal path should return 0, got {rc}"
    finally:
        _os.chdir(saved_cwd)
        _sys.argv = saved_argv
        _urlreq.urlopen = saved_urlopen
        for k, v in saved_env.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v
        import shutil as _shutil
        _shutil.rmtree(proj, ignore_errors=True)


def test_generate_slide_plan_cost_abort_cli_overrides_env() -> None:
    """CLI --cost-abort-at が env SUPERMOVIE_COST_USD_ABORT_AT より優先される (precedence: CLI > env)。"""
    import os as _os
    import io
    import sys as _sys
    import importlib
    import urllib.request as _urlreq
    from contextlib import redirect_stdout, redirect_stderr

    saved_env = {k: _os.environ.get(k) for k in (
        "ANTHROPIC_API_KEY",
        "SUPERMOVIE_RATE_INPUT_PER_MTOK",
        "SUPERMOVIE_RATE_OUTPUT_PER_MTOK",
        "SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK",
        "SUPERMOVIE_RATE_ANTHROPIC_OUTPUT_USD_PER_MTOK",
        "SUPERMOVIE_COST_USD_ABORT_AT",
    )}
    saved_argv = list(_sys.argv)
    saved_cwd = _os.getcwd()
    saved_urlopen = _urlreq.urlopen

    proj = Path(tempfile.mkdtemp(prefix="cost_abort_cli_"))
    (proj / "transcript_fixed.json").write_text(
        json.dumps({
            "duration_ms": 5000,
            "words": [{"text": "ab", "start": 0, "end": 500, "confidence": 0.9}],
            "segments": [{"text": "ab", "start": 0, "end": 500}],
        }),
        encoding="utf-8",
    )
    (proj / "project-config.json").write_text(
        json.dumps({"format": "youtube", "videoType": "test", "tone": "test"}),
        encoding="utf-8",
    )

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("API should not be called when CLI threshold blocks")

    try:
        _os.environ["ANTHROPIC_API_KEY"] = "test-key"
        _os.environ["SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK"] = "100.0"
        _os.environ["SUPERMOVIE_RATE_ANTHROPIC_OUTPUT_USD_PER_MTOK"] = "100.0"
        _os.environ.pop("SUPERMOVIE_RATE_INPUT_PER_MTOK", None)
        _os.environ.pop("SUPERMOVIE_RATE_OUTPUT_PER_MTOK", None)
        # env=高い (allow)、CLI=低い (block) → CLI 優先で block されること検証
        _os.environ["SUPERMOVIE_COST_USD_ABORT_AT"] = "1000.0"
        _os.chdir(str(proj))

        import generate_slide_plan as gsp
        importlib.reload(gsp)
        gsp.PROJ = proj
        _urlreq.urlopen = _fail_if_called

        _sys.argv = ["generate_slide_plan.py", "--json-log", "--cost-abort-at", "0.001"]
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = gsp.main()

        assert rc == 10, f"CLI override should block, got rc={rc}"
        lines = [l for l in out_buf.getvalue().splitlines() if l.strip()]
        v1_tail = json.loads(lines[-1])
        assert v1_tail["category"] == "cost_guard_aborted"
        assert v1_tail.get("cost_abort_at") == 0.001  # CLI 値が反映
    finally:
        _os.chdir(saved_cwd)
        _sys.argv = saved_argv
        _urlreq.urlopen = saved_urlopen
        for k, v in saved_env.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v
        import shutil as _shutil
        _shutil.rmtree(proj, ignore_errors=True)


def test_compare_telop_split_transcript_missing_emits_tail() -> None:
    """compare_telop_split で transcript_fixed.json 欠落時に v1 tail 出力 + exit 3。

    PR-G error path tail audit: emit_obs 定義前の file read failure でも
    --json-log で tail を返すこと。
    """
    import os as _os
    import io
    import sys as _sys
    import importlib
    from contextlib import redirect_stdout, redirect_stderr

    saved_argv = list(_sys.argv)
    saved_cwd = _os.getcwd()

    proj = Path(tempfile.mkdtemp(prefix="cts_no_transcript_"))
    # transcript_fixed.json を意図的に置かない
    try:
        _os.chdir(str(proj))
        import compare_telop_split as cts
        importlib.reload(cts)
        cts.PROJ = proj

        _sys.argv = ["compare_telop_split.py", "/dev/null", "/dev/null", "--json-log"]
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = cts.main()
        assert rc == 3, f"transcript missing should exit 3, got {rc}"
        lines = [l for l in out_buf.getvalue().splitlines() if l.strip()]
        v1_tail = json.loads(lines[-1])
        assert v1_tail["status"] == "error"
        assert v1_tail["category"] == "transcript_missing"
        assert v1_tail["exit_code"] == 3
    finally:
        _os.chdir(saved_cwd)
        _sys.argv = saved_argv
        import shutil as _shutil
        _shutil.rmtree(proj, ignore_errors=True)


def test_compare_telop_split_typo_dict_invalid_emits_tail() -> None:
    """compare_telop_split で typo_dict.json malformed JSON 時に v1 tail + exit 3。"""
    import os as _os
    import io
    import sys as _sys
    import importlib
    from contextlib import redirect_stdout, redirect_stderr

    saved_argv = list(_sys.argv)
    saved_cwd = _os.getcwd()

    proj = Path(tempfile.mkdtemp(prefix="cts_bad_typo_"))
    (proj / "transcript_fixed.json").write_text(
        json.dumps({"duration_ms": 1000, "words": [], "segments": []}),
        encoding="utf-8",
    )
    # typo_dict.json を invalid JSON で書く
    (proj / "typo_dict.json").write_text("{ this is not json }", encoding="utf-8")
    try:
        _os.chdir(str(proj))
        import compare_telop_split as cts
        importlib.reload(cts)
        cts.PROJ = proj

        _sys.argv = ["compare_telop_split.py", "/dev/null", "/dev/null", "--json-log"]
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = cts.main()
        assert rc == 3, f"typo_dict invalid should exit 3, got {rc}"
        lines = [l for l in out_buf.getvalue().splitlines() if l.strip()]
        v1_tail = json.loads(lines[-1])
        assert v1_tail["status"] == "error"
        assert v1_tail["category"] == "typo_dict_invalid"
    finally:
        _os.chdir(saved_cwd)
        _sys.argv = saved_argv
        import shutil as _shutil
        _shutil.rmtree(proj, ignore_errors=True)


def test_preflight_video_write_config_parse_error_emits_tail() -> None:
    """preflight_video で既存 write-config が malformed JSON の時に tail + exit 3。"""
    import os as _os
    import io
    import sys as _sys
    import subprocess
    import shutil as _shutil_mod
    from contextlib import redirect_stdout, redirect_stderr

    if _shutil_mod.which("ffprobe") is None:
        # ffprobe 不在環境では実行不可、skip 扱い (test pass、condition unmet)
        return

    saved_argv = list(_sys.argv)
    saved_cwd = _os.getcwd()

    tmp_dir = Path(tempfile.mkdtemp(prefix="preflight_bad_cfg_"))
    bad_cfg = tmp_dir / "project-config.json"
    bad_cfg.write_text("{ invalid json", encoding="utf-8")
    # 簡易 mp4: ffmpeg で生成 (ない環境では skip)
    src_mp4 = tmp_dir / "in.mp4"
    if _shutil_mod.which("ffmpeg") is None:
        _shutil_mod.rmtree(tmp_dir, ignore_errors=True)
        return
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
             "color=c=black:s=320x240:d=0.1", "-pix_fmt", "yuv420p", str(src_mp4)],
            check=True, capture_output=True, timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        _shutil_mod.rmtree(tmp_dir, ignore_errors=True)
        return

    try:
        result = subprocess.run(
            [_sys.executable, str(Path(__file__).parent / "preflight_video.py"),
             str(src_mp4),
             "--write-config", str(bad_cfg),
             "--json-log"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 3, \
            f"bad write-config should exit 3, got {result.returncode}\nstderr: {result.stderr}"
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        v1_tail = json.loads(lines[-1])
        assert v1_tail["status"] == "error"
        assert v1_tail["category"] == "write-config-parse-error"
    finally:
        _sys.argv = saved_argv
        _os.chdir(saved_cwd)
        _shutil_mod.rmtree(tmp_dir, ignore_errors=True)


def test_observability_redact_error_message_strips_abs_path() -> None:
    """PR-G review P1 #2: redact_error_message が error 文字列内の abs path を placeholder 化する。

    `error=str(e)` を tail JSON に出す経路で abs_path / `<HOME>` 配下 / `<TMP>` を leak しないこと。
    """
    import os as _os
    from _observability import redact_error_message

    # Ensure /tmp/ paths get redacted
    msg = "[Errno 2] No such file or directory: '/tmp/test/foo.json'"
    redacted = redact_error_message(msg)
    assert "/tmp/test/foo.json" not in redacted, f"raw /tmp path leaked: {redacted!r}"
    assert "<TMP>" in redacted or "<ABS>" in redacted, \
        f"placeholder missing: {redacted!r}"

    # Ensure HOME-prefixed abs path becomes <HOME>
    home = str(Path.home())
    msg2 = f"file not found: {home}/secret/foo.json"
    redacted2 = redact_error_message(msg2)
    assert home not in redacted2, f"raw HOME leaked: {redacted2!r}"
    assert "<HOME>" in redacted2, f"<HOME> placeholder missing: {redacted2!r}"

    # Ensure non-path content untouched
    msg3 = "ValueError: invalid input 'foo'"
    assert redact_error_message(msg3) == msg3

    # PR-G fix iter 2 (Codex 23:33 P2 #1): URL 破壊しないこと
    url_msg = "fetch failed: https://example.com/api/v1/foo"
    redacted_url = redact_error_message(url_msg)
    assert "https://example.com/api/v1/foo" in redacted_url, \
        f"URL should be preserved, got {redacted_url!r}"

    # file:// scheme もOK
    file_url = "could not open file://localhost/tmp/x.json"
    redacted_file_url = redact_error_message(file_url)
    assert "file://localhost" in redacted_file_url, \
        f"file:// URL should be preserved (scheme intact), got {redacted_file_url!r}"


def test_observability_redact_error_message_windows_path() -> None:
    """PR-K (Codex 00:36): Windows abs path (`C:\\...` / `D:/...`) も redact 対象。

    cross-platform error string leak (CI Windows runner / Windows tool 経由) への defense-in-depth。
    """
    from _observability import redact_error_message

    # Backslash separator (Windows native)
    msg = "Error: cannot open C:\\Users\\sensitive\\secret.json"
    redacted = redact_error_message(msg)
    assert "C:\\Users\\sensitive" not in redacted, \
        f"Windows path leaked: {redacted!r}"
    assert "secret.json" in redacted, f"basename should be preserved: {redacted!r}"
    assert "<ABS>/" in redacted

    # Forward slash separator (Windows-on-cygwin / cross-platform)
    msg2 = "fail D:/Projects/private/foo.txt missing"
    redacted2 = redact_error_message(msg2)
    assert "D:/Projects/private" not in redacted2, \
        f"Windows /-separator path leaked: {redacted2!r}"
    assert "foo.txt" in redacted2

    # 同じ msg 内に POSIX + Windows 両方混在
    msg3 = "POSIX /tmp/x and Windows C:\\Users\\y both leak"
    redacted3 = redact_error_message(msg3)
    assert "/tmp/x" not in redacted3, f"POSIX path should be redacted: {redacted3!r}"
    assert "C:\\Users\\y" not in redacted3, f"Windows path should be redacted: {redacted3!r}"


def test_observability_redact_error_message_ipv6_and_data_uri_safe() -> None:
    """PR-K: IPv6 アドレスや data: URI を破壊しない。

    `::1/64` の `/64` は path 風だが直前 `1` (alnum) で lookbehind 弾く。
    `data:image/png` も alnum lookbehind で弾く。回帰防止。
    """
    from _observability import redact_error_message

    msg_ipv6 = "bind ::1/64 failed"
    redacted_ipv6 = redact_error_message(msg_ipv6)
    assert "::1/64" in redacted_ipv6, f"IPv6 should be preserved: {redacted_ipv6!r}"

    msg_data = "decoded data:image/png;base64,iVBOR... successfully"
    redacted_data = redact_error_message(msg_data)
    assert "data:image/png" in redacted_data, f"data: URI should be preserved: {redacted_data!r}"

    # mailto:user@host.com もそのまま
    msg_mail = "send to mailto:foo@example.com/bar"
    redacted_mail = redact_error_message(msg_mail)
    assert "mailto:foo@example.com" in redacted_mail, \
        f"mailto: should be preserved: {redacted_mail!r}"


def test_observability_redact_error_message_url_with_port_query_fragment() -> None:
    """PR-R (Codex 01:37 T approve): URL に port / query / fragment が含まれていても
    破壊しない regression。`_ABS_PATH_RE` が `://` 直前の `:` lookbehind で URL scheme を
    弾く実装の lock-in test (T scope: redaction safety の未被覆 edge を閉じる)。
    """
    from _observability import redact_error_message

    # port: scheme://host:port/path
    msg_port = "fail http://example.com:8080/api/v1/users"
    redacted_port = redact_error_message(msg_port)
    assert "http://example.com:8080/api/v1/users" in redacted_port, \
        f"URL with port should be preserved: {redacted_port!r}"

    # query string
    msg_query = "fetch http://example.com/api?key=value&other=2 failed"
    redacted_query = redact_error_message(msg_query)
    assert "http://example.com/api?key=value&other=2" in redacted_query, \
        f"URL with query should be preserved: {redacted_query!r}"

    # fragment / anchor
    msg_frag = "open http://example.com/docs#section-2 in browser"
    redacted_frag = redact_error_message(msg_frag)
    assert "http://example.com/docs#section-2" in redacted_frag, \
        f"URL with fragment should be preserved: {redacted_frag!r}"

    # 全部入り (port + path + query + fragment)
    msg_all = "GET https://api.example.com:443/v1/users?id=42#top returned 404"
    redacted_all = redact_error_message(msg_all)
    assert "https://api.example.com:443/v1/users?id=42#top" in redacted_all, \
        f"full URL should be preserved: {redacted_all!r}"

    # localhost + port (dev environment)
    msg_local = "connect refused: http://localhost:3000/path/to/endpoint"
    redacted_local = redact_error_message(msg_local)
    assert "http://localhost:3000/path/to/endpoint" in redacted_local, \
        f"localhost URL should be preserved: {redacted_local!r}"

    # git+ssh:// scheme (uncommon but valid)
    msg_git = "clone failed: git+ssh://user@host:22/repo.git timeout"
    redacted_git = redact_error_message(msg_git)
    assert "git+ssh://user@host:22/repo.git" in redacted_git, \
        f"git+ssh URL should be preserved: {redacted_git!r}"

    # path part 含む URL と純粋 abs path 混在 → URL は維持、abs path は redact
    msg_path_in_url = "url=http://example.com/api/v1/foo and file=/tmp/x.json"
    redacted_mixed = redact_error_message(msg_path_in_url)
    assert "http://example.com/api/v1/foo" in redacted_mixed, \
        f"URL path within URL should not be redacted: {redacted_mixed!r}"
    assert "/tmp/x.json" not in redacted_mixed, \
        f"raw abs path should be redacted: {redacted_mixed!r}"


def test_observability_redact_error_message_multiple_paths_in_one_msg() -> None:
    """PR-K: 1 メッセージに POSIX abs path 複数 → すべて redact。"""
    from _observability import redact_error_message

    msg = "ERROR: copy /tmp/a/b.json to /var/log/c.log failed"
    redacted = redact_error_message(msg)
    assert "/tmp/a/b.json" not in redacted, f"first path leaked: {redacted!r}"
    assert "/var/log/c.log" not in redacted, f"second path leaked: {redacted!r}"
    # 両方 redact されている
    assert redacted.count("<TMP>") + redacted.count("<ABS>") >= 2, \
        f"both paths should be redacted: {redacted!r}"


def test_observability_redact_error_message_tilde_path_token() -> None:
    """`redact_error_message()` の `~/...` / `~user/...` tilde path token redact
    contract lock-in (Codex 04:46 PR-AS verdict BD、observability redaction
    contract gap 防止)。

    error message に `~/secret/file.json` 等の tilde-prefixed path が含まれた
    場合、PR-V `safe_artifact_path` の `~` expansion fix と同型の redaction が
    必要。旧実装は `_ABS_PATH_RE` のみで `/...` 部分しか拾わず、
    `~/secret/file.json` を `~<ABS>/file.json` という `~` 残留 leak として
    出力していた (`~` literal は path token 由来であることを示唆して partial
    leak)。

    fix:
      - `_TILDE_PATH_RE` を新設 (`~/...` / `~user/...` / `~` / `~user` greedy match)
      - `_sub_tilde` で `os.path.expanduser` → `_lexical_redact` 経由で
        `<HOME>/...` placeholder 化 (PR-V 同型)
      - 未知 user (`~unknownuser/...`) は `<ABS>/<basename>` に落とす
      - `_ABS_PATH_RE` lookbehind に `>` 追加で `<HOME>/...` placeholder の
        `/...` 部分を再 match させない (二重 redact 防止)

    8 case で contract lock-in: `~/path` / `~/dir-only` / `~/.dotfile` /
    `~unknownuser/data` / mixed (`~/x` + `/etc/y`) / abs HOME (regression
    guard) / URL+tilde (URL preserve) / `~` 単独。
    """
    from _observability import redact_error_message

    # (1) `~/secret/file.json` → `<HOME>/secret/file.json`
    out1 = redact_error_message("failed to read ~/secret/file.json")
    assert "<HOME>" in out1, f"~/path must be redacted to <HOME>, got {out1!r}"
    assert "~/secret" not in out1, f"raw ~/path leaked: {out1!r}"
    assert "<ABS>" not in out1, f"unwanted <ABS> in tilde path: {out1!r}"

    # (2) `~/secret` (no file) → `<HOME>/secret`
    out2 = redact_error_message("cannot access ~/secret")
    assert "<HOME>" in out2
    assert "~/secret" not in out2

    # (3) `~/.config/credentials` (dotfile path)
    out3 = redact_error_message("see at ~/.config/credentials")
    assert "<HOME>" in out3
    assert "~/.config" not in out3

    # (4) `~unknownuser/data` (expanduser fails) → `<ABS>/data`
    out4 = redact_error_message("access ~unknownuser/data error")
    assert "~unknownuser" not in out4, (
        f"unknown user tilde must be redacted: {out4!r}"
    )
    assert "<ABS>" in out4

    # (5) mixed: `/etc/conf` + `~/private/x.json` → 両方 redact
    out5 = redact_error_message(
        "config at /etc/conf and ~/private/x.json"
    )
    assert "<HOME>" in out5
    assert "<ABS>" in out5
    assert "~/private" not in out5
    assert "/etc/conf" not in out5

    # (6) regression guard: 旧来の abs HOME path も従来通り redact
    out6 = redact_error_message("failed at /Users/rokumasuda/secret/file.json")
    assert "<HOME>" in out6
    assert "/Users/rokumasuda" not in out6

    # (7) URL + tilde 混在: URL preserve + tilde redact
    out7 = redact_error_message(
        "POST https://api.example.com/v1/foo failed at ~/x.json"
    )
    assert "https://api.example.com" in out7, "URL must be preserved"
    assert "<HOME>" in out7
    assert "~/x.json" not in out7

    # (8) `~` 単独 → `<HOME>` placeholder
    out8 = redact_error_message("go to ~ now")
    assert "<HOME>" in out8
    # `~ ` (tilde + space) が raw のままでないこと (token として redact 済)
    assert "go to ~ " not in out8

    # ===== Codex 04:51 PR-AS review P1 fix: URL preserve regression =====
    # (9) URL path segment 内 `~` は redact しない (URL preserve invariant
    # 違反だった、`_TILDE_PATH_RE` lookbehind に `/` 追加で skip)
    out9 = redact_error_message("failed at https://example.com/~/x.json")
    assert "https://example.com/~/x.json" in out9, (
        f"URL-internal `~/` path segment must be preserved, got {out9!r}"
    )

    # (10) URL query parameter 内 `~` も同様 (lookbehind `=` 追加)
    out10 = redact_error_message("redirect to ?next=~/x.json failed")
    assert "~/x.json" in out10, (
        f"URL query param `=~/` must be preserved, got {out10!r}"
    )

    # (11) URL `~user` も path segment 内では preserve
    out11 = redact_error_message(
        "GET https://example.com/~user/repo failed"
    )
    assert "~user/repo" in out11, (
        f"URL-internal `~user/` path must be preserved, got {out11!r}"
    )

    # (12) plain abs path regression: URL 系 char に挟まれていない `/etc/conf`
    # は引き続き redact (PR-AS fix iter で `~` を `_ABS_PATH_RE` lookbehind に
    # 追加したが、word/`:`/`/`/`>`/`~` 以外の前置 (空白等) では match 維持)
    out12 = redact_error_message("failed at /etc/conf")
    assert "<ABS>" in out12 or "<HOME>" in out12, (
        f"plain abs path must be redacted, got {out12!r}"
    )
    assert "/etc/conf" not in out12


def test_observability_redact_error_message_url_path_order_independence() -> None:
    """`redact_error_message()` の URL+path 混在 input で order-independent
    redaction lock-in (Codex 04:35 PR-AR verdict AY、observability redaction
    contract drift 防止)。

    error message には URL と HOME 配下 abs path が混在することが多い (例:
    `failed to fetch <URL>: see <PATH>`)。caller の文章組み立て順で URL/path
    の出現順が異なっても、redaction 結果は順序非依存である必要がある:

      - HOME 配下 path → `<HOME>/...` placeholder で常に redact
      - URL は破壊しない (preserve as-is、URL 内 path-like segment も切らない)

    PR-K / PR-R で URL 破壊回避と複数 path の同時 redact を個別 lock-in 済だ
    が、両者の混在 + 入力順序違いの組み合わせは未 lock。リファクタで regex
    順序や iter 方向が変わって preserve / redact が drift する regression を
    early fail させる。

    入力順 4 pattern (URL→path / path→URL / URL containing path-segment +
    standalone path / path → URL+segment) で同 input を組み替えて、
    output が semantically equivalent (path redacted + URL preserved) で
    あることを確認。
    """
    import os as _os

    from _observability import redact_error_message

    home = _os.path.expanduser("~")
    url = "https://api.example.com/v1/foo"
    path = f"{home}/secret/file.json"

    cases = [
        ("URL→path",
         f"failed to fetch {url}: see {path}"),
        ("path→URL",
         f"see {path}: failed to fetch {url}"),
        ("URL with segment + path after",
         f"POST {url}/users/123 failed at {path}"),
        ("path → URL with segment",
         f"at {path} POST {url}/users/123 failed"),
    ]

    for label, msg in cases:
        out = redact_error_message(msg)
        # Codex 04:38 PR-AR review P2 fix: <HOME> placeholder が実際に出る
        # ことを直接 assert (count >= 1)、count==count だけだと「両方とも出ない」
        # でも一致する loophole を closure
        assert out.count("<HOME>") >= 1, (
            f"{label}: HOME path must be redacted to <HOME> placeholder, "
            f"got {out!r}"
        )
        assert f"{home}/secret" not in out, (
            f"{label}: raw HOME path leaked: {out!r}"
        )
        # URL は preserve (api.example.com host が残る)
        assert "api.example.com" in out, (
            f"{label}: URL host must be preserved, got {out!r}"
        )
        # URL scheme + host が破壊されていない (https:// が残る)
        assert "https://api.example.com" in out, (
            f"{label}: URL scheme+host must remain intact, got {out!r}"
        )

    # ===== 順序独立性: A と B は path/URL の順序だけ違う、出力の構成要素も
    # 同じ集合になることを確認 (token 単位で path placeholder + URL preserve
    # 数が一致) =====
    out_url_first = redact_error_message(cases[0][1])
    out_path_first = redact_error_message(cases[1][1])
    assert out_url_first.count("<HOME>") == out_path_first.count("<HOME>"), (
        f"<HOME> placeholder count must be order-independent: "
        f"URL-first={out_url_first.count('<HOME>')}, "
        f"path-first={out_path_first.count('<HOME>')}"
    )
    assert out_url_first.count("api.example.com") == \
        out_path_first.count("api.example.com"), (
            f"URL host count must be order-independent"
        )

    # C と D 同様
    out_c = redact_error_message(cases[2][1])
    out_d = redact_error_message(cases[3][1])
    assert out_c.count("<HOME>") == out_d.count("<HOME>")
    assert out_c.count("api.example.com") == out_d.count("api.example.com")


def test_observability_build_status_category_override_defensive_lint() -> None:
    """`build_status(category_override=...)` の str + non-empty + no-control-char
    contract lock-in (Codex 04:56 PR-AT verdict BE、observability v1 category
    bucket field drift 防止)。

    `category_override` は STATUS_MAP lookup の category を bypass して
    `if category_override is not None: v1_category = category_override` で
    payload core field に直接代入される経路。`STATUS_MAP` 側の category format
    invariant lint (PR-AL) は通常 lookup ペアのみ対象で、override 経路は
    検査されない gap が残っていた:

      - `""` / `"   "` (whitespace-only) → category="" / "   " で PR-AL
        format invariant 違反 + downstream bucket 不能
      - `"a\\nb"` / `"a\\x00b"` → category に control char が漏れて
        emit_json 1-line format invariant (PR-Y) 破壊 + log line parser break
      - 5 / True / [...] / {...} → 非 str fallback で category 型違反
        (downstream str-method AttributeError、JSON serialize 結果も乱れる)

    新 contract:
      - category_override: 非 None 渡し時は str 必須 (TypeError on type
        mismatch)
      - 非空 (whitespace-only も含む) (ValueError)
      - 制御文字 \\x00-\\x1F + \\x7F 不含 (ValueError)
      - None は引き続き許容 (STATUS_MAP の category を活かす経路、
        既存 visual_smoke / 通常 build_slide_data 経路と互換)

    PR-AM script identifier / PR-AP v0_status_strict と同型の build_status
    入口 defensive lint。既存 strict 系 (PR-AC/AD/AF/AK/AL/AM/AO/AP/AQ) と
    同 level。
    """
    from _observability import build_status

    # ===== accept =====
    # (1a) 既存 caller で実際に使われている category_override 群 (regression guard)
    for ok_cat in ("kpi-comparison", "dimension-regression",
                   "preflight-source-meta", "slide-build", "telop-build"):
        p = build_status(
            script="x", v0_status="success", exit_code=0,
            category_override=ok_cat,
        )
        assert p["category"] == ok_cat, (
            f"category_override={ok_cat!r} must propagate to category, "
            f"got {p['category']!r}"
        )

    # (1b) 1 char (snake_case identifier 形式の最小)
    p_min = build_status(
        script="x", v0_status="success", exit_code=0,
        category_override="a",
    )
    assert p_min["category"] == "a"

    # (1c) None は STATUS_MAP の category を活かす (既存挙動維持)
    # success → STATUS_MAP で category=None
    p_none = build_status(
        script="x", v0_status="success", exit_code=0,
        category_override=None,
    )
    assert p_none["category"] is None
    # 未知 v0_status は PR-T defensive fallback で category=v0_status
    p_fb = build_status(
        script="x", v0_status="mystery_status", exit_code=2,
        category_override=None,
    )
    assert p_fb["category"] == "mystery_status"

    # (1d) default kwarg 省略時も None と同じ挙動
    p_def = build_status(script="x", v0_status="success", exit_code=0)
    assert p_def["category"] is None

    # ===== reject 型違反 =====
    for bad_type in (5, 0, 1.5, True, False, [], ["x"],
                     {"k": "v"}, ("a",), object()):
        try:
            build_status(
                script="x", v0_status="success", exit_code=0,
                category_override=bad_type,
            )
        except TypeError as e:
            assert "category_override" in str(e) and "str" in str(e), (
                f"TypeError msg should mention category_override + str, "
                f"got {e!r}"
            )
        else:
            raise AssertionError(
                f"non-str category_override={bad_type!r} must raise TypeError"
            )

    # ===== reject 空文字 / whitespace-only =====
    for empty_or_ws in ("", " ", "   ", "\t", "  \t  "):
        try:
            build_status(
                script="x", v0_status="success", exit_code=0,
                category_override=empty_or_ws,
            )
        except ValueError as e:
            assert "category_override" in str(e) and "non-empty" in str(e), (
                f"ValueError msg should mention category_override + "
                f"non-empty, got {e!r}"
            )
        else:
            raise AssertionError(
                f"empty/whitespace category_override={empty_or_ws!r} "
                f"must raise ValueError"
            )

    # ===== reject 制御文字 =====
    for ctrl_cat in ("a\nb", "a\rb", "a\x00b", "abc\x01",
                     "abc\x1f", "a\x7fb", "\nabc"):
        try:
            build_status(
                script="x", v0_status="success", exit_code=0,
                category_override=ctrl_cat,
            )
        except ValueError as e:
            assert "control" in str(e) and "category_override" in str(e), (
                f"ValueError msg should mention control + category_override, "
                f"got {e!r}"
            )
        else:
            raise AssertionError(
                f"control-char category_override={ctrl_cat!r} must raise "
                f"ValueError"
            )


def test_observability_warn_legacy_cost_extras_payload_must_be_dict() -> None:
    """`warn_legacy_cost_extras(payload)` の payload dict 必須 contract lock-in
    (Codex 05:05 PR-AU verdict BF、observability deprecation warning contract
    drift 防止)。

    `warn_legacy_cost_extras()` は build_status() 出力 dict 前提に
    `payload.get("cost")` / `for k in LEGACY_COST_EXTRAS_KEYS if k in payload`
    を直接呼ぶ実装。旧実装は entry に型 guard がなく、non-dict (None / list /
    str / int / tuple / set / object) を渡されると uncaught AttributeError
    "X object has no attribute 'get'" / TypeError "argument of type 'X' is
    not iterable" を投げて caller 責務違反が見えにくい drift。env=0/unset
    の no-op 経路でも env check より前に payload 型違反は弾けるため、env 状態
    に依らず entry で fail-loud 化する (env=1 path の前置検査で warning emit
    の有無に関わらず caller bug 即時検知)。

    新 contract: warn_legacy_cost_extras 入口で `isinstance(payload, dict)`
    違反を explicit TypeError で fail-loud、emit_json (PR-AQ) と同型。
    既存 callers (build_status() 経由 dict のみ) は backward compatible。

    既存 strict 系 (PR-AC exit_code int / PR-AD redaction_rules / PR-AF cost /
    PR-AK counts/artifacts / PR-AL rate_source / PR-AM script / PR-AO secret /
    PR-AP v0_status / PR-AQ emit_json payload / PR-AT category_override) と
    同 level の defensive lint。
    """
    import io
    import os as _os
    from contextlib import redirect_stderr, redirect_stdout
    from _observability import (
        WARN_LEGACY_COST_EXTRAS_ENV,
        warn_legacy_cost_extras,
    )

    # ===== accept (env=0 / unset、no-op 経路) =====
    saved = _os.environ.get(WARN_LEGACY_COST_EXTRAS_ENV)
    _os.environ.pop(WARN_LEGACY_COST_EXTRAS_ENV, None)
    try:
        # (1a) 通常 dict (no nested cost) → False
        buf0 = io.StringIO()
        emitted0 = warn_legacy_cost_extras({"status": "ok"}, stream=buf0)
        assert emitted0 is False
        assert buf0.getvalue() == ""
        # (1b) empty dict → False
        buf1 = io.StringIO()
        emitted1 = warn_legacy_cost_extras({}, stream=buf1)
        assert emitted1 is False
        assert buf1.getvalue() == ""
        # (1c) build_status 出力風 dict (cost dict + legacy keys) でも env off
        # は no-op
        buf2 = io.StringIO()
        emitted2 = warn_legacy_cost_extras(
            {"cost": {"x": 1}, "rate_input_usd_per_mtok": 0.5}, stream=buf2
        )
        assert emitted2 is False
        assert buf2.getvalue() == ""

        # ===== reject 非 dict 全種 (env=0 でも payload 型は前置で fail-loud) =====
        for bad_payload in (
            None, [], [1, 2], "payload", "", 5, 0, 1.5,
            True, False, (), (1, 2), {1, 2}, object(),
        ):
            try:
                warn_legacy_cost_extras(bad_payload)
            except TypeError as e:
                assert "payload" in str(e) and "dict" in str(e), (
                    f"TypeError msg should mention payload + dict, got {e!r}"
                )
            else:
                raise AssertionError(
                    f"non-dict payload={bad_payload!r} "
                    f"({type(bad_payload).__name__}) must raise TypeError"
                )

        # ===== reject 経路で stderr に warning が漏れない (env off + 型違反で
        # warning print より前に raise) =====
        buf_err = io.StringIO()
        with redirect_stderr(buf_err):
            try:
                warn_legacy_cost_extras(None)
            except TypeError:
                pass
        assert buf_err.getvalue() == "", (
            f"warn_legacy_cost_extras must reject before printing warning, "
            f"got stderr={buf_err.getvalue()!r}"
        )
    finally:
        if saved is None:
            _os.environ.pop(WARN_LEGACY_COST_EXTRAS_ENV, None)
        else:
            _os.environ[WARN_LEGACY_COST_EXTRAS_ENV] = saved

    # ===== reject 経路は env=1 でも維持 (caller bug は env 状態に依らず fail-loud) =====
    saved2 = _os.environ.get(WARN_LEGACY_COST_EXTRAS_ENV)
    _os.environ[WARN_LEGACY_COST_EXTRAS_ENV] = "1"
    try:
        for bad_payload in (None, [], "payload", 5, ()):
            try:
                warn_legacy_cost_extras(bad_payload)
            except TypeError as e:
                assert "payload" in str(e) and "dict" in str(e), (
                    f"TypeError msg should mention payload + dict (env=1), "
                    f"got {e!r}"
                )
            else:
                raise AssertionError(
                    f"non-dict payload={bad_payload!r} (env=1) must raise "
                    f"TypeError"
                )
        # env=1 + dict は backward-compat (warning 出る or 出ない pathway)
        buf_ok = io.StringIO()
        emitted_ok = warn_legacy_cost_extras({"status": "ok"}, stream=buf_ok)
        # nested cost なしなので no-op
        assert emitted_ok is False
        assert buf_ok.getvalue() == ""
    finally:
        if saved2 is None:
            _os.environ.pop(WARN_LEGACY_COST_EXTRAS_ENV, None)
        else:
            _os.environ[WARN_LEGACY_COST_EXTRAS_ENV] = saved2


def test_compare_telop_split_error_message_redacted() -> None:
    """compare_telop_split で error=str(e) → tail に raw abs path が漏れないこと。

    PR-G review P1 #2 fix の actual emission を検証。
    """
    import os as _os
    import io
    import sys as _sys
    import importlib
    from contextlib import redirect_stdout, redirect_stderr

    saved_argv = list(_sys.argv)
    saved_cwd = _os.getcwd()

    proj = Path(tempfile.mkdtemp(prefix="cts_redact_"))
    try:
        _os.chdir(str(proj))
        import compare_telop_split as cts
        importlib.reload(cts)
        cts.PROJ = proj

        _sys.argv = ["compare_telop_split.py", "/dev/null", "/dev/null", "--json-log"]
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = cts.main()
        assert rc == 3
        lines = [l for l in out_buf.getvalue().splitlines() if l.strip()]
        v1_tail = json.loads(lines[-1])
        # error field は redact 済 (raw proj path が出ない)
        err_field = v1_tail.get("error", "")
        assert str(proj) not in err_field, \
            f"raw proj path leaked in error field: {err_field!r}"
    finally:
        _os.chdir(saved_cwd)
        _sys.argv = saved_argv
        import shutil as _shutil
        _shutil.rmtree(proj, ignore_errors=True)


def test_compare_telop_split_exit_code_propagates() -> None:
    """PR-G review P1 #1: __main__ entry が sys.exit(main()) で early-error exit code を propagate。

    transcript missing 時に subprocess 終了コード 3 が返ることを検証 (in-process では shell exit を取れないため subprocess 経由)。
    """
    import os as _os
    import sys as _sys
    import subprocess

    proj = Path(tempfile.mkdtemp(prefix="cts_exit_"))
    try:
        result = subprocess.run(
            [_sys.executable, str(Path(__file__).parent / "compare_telop_split.py"),
             "/dev/null", "/dev/null", "--json-log"],
            capture_output=True, text=True, timeout=15,
            cwd=str(proj),
        )
        assert result.returncode == 3, \
            f"early-error exit code should be 3, got {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
        # tail も exit_code=3 であること (P1 #1 の対称検証)
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        v1_tail = json.loads(lines[-1])
        assert v1_tail["exit_code"] == 3
    finally:
        import shutil as _shutil
        _shutil.rmtree(proj, ignore_errors=True)


def test_visual_smoke_out_dir_mkdir_error_emits_tail() -> None:
    """visual_smoke で out_dir が file (not dir) の時に mkdir failure + tail + exit 3。"""
    import os as _os
    import io
    import sys as _sys
    import importlib
    from contextlib import redirect_stdout, redirect_stderr

    saved_argv = list(_sys.argv)
    saved_cwd = _os.getcwd()

    tmp_dir = Path(tempfile.mkdtemp(prefix="vs_bad_outdir_"))
    # file を out_dir として渡す (mkdir は FileExistsError)
    bad_out = tmp_dir / "not_a_dir"
    bad_out.write_text("blocking file", encoding="utf-8")
    try:
        import visual_smoke as vs
        importlib.reload(vs)

        _sys.argv = [
            "visual_smoke.py",
            "--out-dir", str(bad_out),
            "--formats", "youtube",
            "--frames", "30",
            "--json-log",
        ]
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            rc = vs.cli()
        assert rc == 3, f"out_dir mkdir error should exit 3, got {rc}\nstderr: {err_buf.getvalue()}"
        lines = [l for l in out_buf.getvalue().splitlines() if l.strip()]
        v1_tail = json.loads(lines[-1])
        assert v1_tail["status"] == "error"
        assert v1_tail["category"] == "out-dir-mkdir-error"
    finally:
        _sys.argv = saved_argv
        _os.chdir(saved_cwd)
        import shutil as _shutil
        _shutil.rmtree(tmp_dir, ignore_errors=True)


def test_observability_redact_secret_long_value_keeps_last_4() -> None:
    """PR-H: 長い secret value (e.g. API key) は last-4 char 残し以外を mask する contract 検証。

    docs/OBSERVABILITY.md §Redaction Rules secret class 「最後 4 文字以外を mask」rule の実装。
    """
    from _observability import redact_secret

    # 通常 case (`sk-ant-` prefix + 30+ char body)
    api_key = "sk-ant-api03-AbCdEf1234567890XyZwVuTsRq"
    redacted = redact_secret(api_key)
    assert redacted.endswith(api_key[-4:]), \
        f"redact_secret should keep last 4 chars, got {redacted!r}"
    assert redacted[:-4] == "*" * (len(api_key) - 4), \
        f"redact_secret should mask all but last 4, got {redacted!r}"
    assert api_key[:5] not in redacted, "raw prefix should not leak"
    # 長さ保持 (length leak 自体は contract 上 OK、redaction.applied_rules で意図表明)
    assert len(redacted) == len(api_key)


def test_observability_redact_secret_short_value_full_mask() -> None:
    """PR-H: 短い value (last_n+1 以下) は全 mask、value partial leak 防止。

    secret が偶然 4 char しかない場合、last-4 ルールだと raw 全文が出てしまう。
    contract として short value は全 mask に倒す。
    """
    from _observability import redact_secret

    assert redact_secret("abcd") == "****"  # ちょうど 4 char → 全 mask
    assert redact_secret("abc") == "***"
    assert redact_secret("abcde") == "*****"  # 5 char (last_n+1) → 全 mask
    assert redact_secret("abcdef") == "**cdef"  # 6 char → mask 2 + last 4


def test_observability_redact_secret_non_string_passthrough() -> None:
    """PR-H: non-string (None / int / dict 等) はそのまま return、TypeError を吐かない。"""
    from _observability import redact_secret

    assert redact_secret(None) is None
    assert redact_secret("") == ""
    assert redact_secret(12345) == 12345  # int は passthrough
    assert redact_secret([]) == []


def test_observability_redact_secret_custom_last_n_and_mask_char() -> None:
    """PR-H: last_n / mask_char カスタマイズが効くこと (caller 側で表現変更可能)。"""
    from _observability import redact_secret

    # last_n=8 で長め保持
    assert redact_secret("sk-ant-api03-AbCdEf1234567890", last_n=8) == \
        "*" * (len("sk-ant-api03-AbCdEf1234567890") - 8) + "34567890"
    # mask_char で異 char (downstream parser で `[REDACTED]` 使う等)
    assert redact_secret("abcdefgh", last_n=4, mask_char="x") == "xxxxefgh"


def test_observability_redact_secret_last_n_zero_or_negative_full_mask() -> None:
    """PR-H fix iter (Codex 00:02 P1): last_n=0 / 負値で raw leak しない。

    Python slice の value[-0:] は value[0:] = 全文を返すため、custom param で 0 や
    負値が渡されると raw value が尾に連結されて leak。fail-closed で全 mask に倒す。
    """
    from _observability import redact_secret

    # last_n=0: 全 mask
    assert redact_secret("abcdefgh", last_n=0) == "********"
    # last_n=-1: 全 mask (defensive)
    assert redact_secret("abcdefgh", last_n=-1) == "********"
    # raw value は1文字も末尾に出ないこと
    redacted_zero = redact_secret("sk-1234567890", last_n=0)
    assert "sk" not in redacted_zero
    assert "1234" not in redacted_zero
    assert "7890" not in redacted_zero
    assert redacted_zero == "*" * len("sk-1234567890")


def test_observability_redact_secret_boundary_and_mask_char_strict() -> None:
    """`redact_secret()` の 0-length / extreme last_n boundary + mask_char
    contract lock-in (Codex 04:18 PR-AO verdict AN、observability v1 secret
    leak 防止 + caller bug fail-loud)。

    既存 PR-H test (long / short / non-string / custom / last_n <= 0) は
    主要経路を網羅していたが、以下の boundary / mask_char 経路は未 lock:

      - len(value) == last_n (exactly 5 char value with last_n=5) → 全 mask
      - len(value) == last_n + 1 (boundary, 5 char value with last_n=4) → 全 mask
      - len(value) == last_n + 2 (just over, 6 char with last_n=4) → 部分 mask
      - last_n >> len(value) (last_n=100 vs 5 char) → 全 mask
      - mask_char="" (空文字、`mask_char * N == ""` で last_n char raw 露出) →
        ValueError fail-loud (PR-AO fix で追加)
      - mask_char non-str (int / None) → TypeError
      - 1 char value (len=1) で last_n=0 / 4 / 100 全部 全 mask
      - multi-char mask_char (`"##"`) → 既存挙動維持 (no leak)

    `mask_char=""` は `"abcdefgh"` + last_n=4 で `"efgh"` を返す semantic
    leak を起こしていた gap を fix。caller の bug を fail-loud で表面化。
    """
    from _observability import redact_secret

    # ===== boundary len cases =====
    # (1) len == last_n (5 char, last_n=5) → 全 mask
    assert redact_secret("abcde", last_n=5) == "*****"
    # (2) len == last_n + 1 (5 char, last_n=4) → 全 mask (PR-H boundary)
    assert redact_secret("abcde", last_n=4) == "*****"
    # (3) len == last_n + 2 (6 char, last_n=4) → 部分 mask
    assert redact_secret("abcdef", last_n=4) == "**cdef"
    # (4) len == last_n + 3 (7 char, last_n=4) → 部分 mask
    assert redact_secret("abcdefg", last_n=4) == "***defg"

    # ===== extreme last_n =====
    # (5) last_n >> len(value) → 全 mask (boundary inside `<=` branch)
    assert redact_secret("abcde", last_n=100) == "*****"
    # (6) last_n == len(value) + 1 → 全 mask
    assert redact_secret("abcde", last_n=6) == "*****"

    # ===== 1 char value (extreme short) =====
    # (7) len 1 + last_n=0 → 全 mask
    assert redact_secret("x", last_n=0) == "*"
    # (8) len 1 + last_n=4 (default) → 全 mask
    assert redact_secret("x", last_n=4) == "*"
    # (9) len 1 + last_n=100 → 全 mask
    assert redact_secret("x", last_n=100) == "*"

    # ===== mask_char strict =====
    # (10) PR-AO fix: mask_char="" は ValueError fail-loud
    try:
        redact_secret("abcdefgh", last_n=4, mask_char="")
    except ValueError as e:
        assert "mask_char" in str(e) and "non-empty" in str(e), (
            f"ValueError msg should mention mask_char + non-empty, got {e!r}"
        )
    else:
        raise AssertionError("mask_char='' must raise ValueError")

    # (11) 旧挙動 regression detection: 空 mask_char で leak していた値が
    # 今は ValueError、`"efgh"` の raw leak が起きないことを確認
    try:
        result = redact_secret("abcdefgh", last_n=4, mask_char="")
    except ValueError:
        pass
    else:
        assert "efgh" not in result, (
            f"empty mask_char must not return raw tail, got {result!r}"
        )

    # (12) mask_char non-str (None / int) → TypeError
    for bad_mc in (None, 5, 1.5, ["*"], True):
        try:
            redact_secret("abcdefgh", last_n=4, mask_char=bad_mc)
        except TypeError as e:
            assert "mask_char" in str(e) and "str" in str(e), (
                f"TypeError msg should mention mask_char + str, got {e!r}"
            )
        else:
            raise AssertionError(
                f"non-str mask_char={bad_mc!r} must raise TypeError"
            )

    # (13) multi-char mask_char は accept (既存挙動維持、leak なし)
    result_multi = redact_secret("abcdefgh", last_n=4, mask_char="##")
    assert result_multi == "########efgh", (
        f"multi-char mask_char preserved: got {result_multi!r}"
    )
    # raw secret prefix が出ていない確認
    assert "abcd" not in result_multi

    # ===== 既存 passthrough 経路の backward compat 確認 =====
    # (14) None / 空文字 / non-str は mask_char 検査前に passthrough
    # (mask_char="" が指定されても None / "" / 12345 は早期 return で safe)
    assert redact_secret(None, mask_char="") is None
    assert redact_secret("", mask_char="") == ""
    assert redact_secret(12345, mask_char="") == 12345


def test_observability_compute_rate_missing_helper() -> None:
    """PR-O (Codex 01:12): compute_rate_missing(estimate) helper の discriminator 算出規約。

    `estimated_cost_usd_upper_bound is None ⇔ rate_missing=true` を helper で集約、
    caller の重複削減 (PR-N の 3 site で同式) の single source of truth。
    """
    from _observability import compute_rate_missing

    # estimate=None (rate unset case) → rate_missing=True
    assert compute_rate_missing(None) is True
    # estimate=finite float (rate set case) → rate_missing=False
    assert compute_rate_missing(0.0) is False
    assert compute_rate_missing(0.001) is False
    assert compute_rate_missing(123.456) is False
    # 0.0 を「false-y」と扱わないこと (Python `if estimate:` バグ回避の意図確認)
    assert compute_rate_missing(0.0) is False


def test_observability_build_cost_payload_helper() -> None:
    """PR-S (Codex 01:46 X approve): build_cost_payload() が docs §Cost JSON Shape の
    nested cost object schema を返すこと。
    """
    from _observability import build_cost_payload

    # rate 設定済 case: estimate / rate / tokens 全埋め
    p = build_cost_payload(
        estimate=0.000123,
        rate_input=1.0,
        rate_output=5.0,
        tokens_input=100,
        tokens_output=50,
    )
    assert p["currency"] == "USD"
    assert p["estimate"] == 0.000123
    assert p["rate_input_usd_per_mtok"] == 1.0
    assert p["rate_output_usd_per_mtok"] == 5.0
    assert p["tokens_input"] == 100
    assert p["tokens_output"] == 50
    assert p["rate_missing"] is False
    assert "rate_source" in p

    # rate 未設定 case: estimate=None / rate=None / rate_missing=True
    p_unset = build_cost_payload(estimate=None, rate_input=None, rate_output=None)
    assert p_unset["estimate"] is None
    assert p_unset["rate_input_usd_per_mtok"] is None
    assert p_unset["rate_output_usd_per_mtok"] is None
    assert p_unset["rate_missing"] is True
    assert p_unset["tokens_input"] is None
    assert p_unset["tokens_output"] is None


def test_generate_slide_plan_dry_run_emits_nested_cost() -> None:
    """PR-S: generate_slide_plan dry-run --json-log の v1 tail に nested cost object が
    含まれ、rate 設定時 estimate=finite / rate_missing=false、未設定時 estimate=null /
    rate_missing=true が一致すること (top-level extras と nested cost で discriminator 整合)。
    """
    import os as _os
    import io
    import sys as _sys
    import importlib
    from contextlib import redirect_stdout, redirect_stderr

    saved_argv = list(_sys.argv)
    saved_cwd = _os.getcwd()
    saved_env = {k: _os.environ.get(k) for k in (
        "ANTHROPIC_API_KEY",
        "SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK",
        "SUPERMOVIE_RATE_ANTHROPIC_OUTPUT_USD_PER_MTOK",
        "SUPERMOVIE_RATE_INPUT_PER_MTOK",
        "SUPERMOVIE_RATE_OUTPUT_PER_MTOK",
    )}

    proj = Path(tempfile.mkdtemp(prefix="cost_nested_set_"))
    (proj / "transcript_fixed.json").write_text(
        json.dumps({"duration_ms": 5000, "words": [], "segments": [{"text": "x", "start": 0, "end": 100}]}),
        encoding="utf-8",
    )
    (proj / "project-config.json").write_text(
        json.dumps({"format": "youtube", "videoType": "test", "tone": "test"}),
        encoding="utf-8",
    )
    try:
        _os.environ["SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK"] = "1.0"
        _os.environ["SUPERMOVIE_RATE_ANTHROPIC_OUTPUT_USD_PER_MTOK"] = "5.0"
        for k in ("SUPERMOVIE_RATE_INPUT_PER_MTOK", "SUPERMOVIE_RATE_OUTPUT_PER_MTOK"):
            _os.environ.pop(k, None)
        _os.environ.pop("ANTHROPIC_API_KEY", None)
        _os.chdir(str(proj))

        import generate_slide_plan as gsp
        importlib.reload(gsp)
        gsp.PROJ = proj

        _sys.argv = ["generate_slide_plan.py", "--dry-run", "--json-log"]
        out_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(io.StringIO()):
            gsp.main()
        lines = [l for l in out_buf.getvalue().splitlines() if l.strip()]
        v1_tail = json.loads(lines[-1])

        # nested cost object 存在確認
        cost = v1_tail.get("cost")
        assert isinstance(cost, dict), f"cost should be nested dict, got {cost!r}"
        assert cost.get("currency") == "USD"
        assert isinstance(cost.get("estimate"), (int, float))
        assert cost.get("rate_input_usd_per_mtok") == 1.0
        assert cost.get("rate_output_usd_per_mtok") == 5.0
        assert cost.get("rate_missing") is False
        assert "rate_source" in cost
        # tokens は dry-run の時点では estimated_input_tokens / output_upper_bound が入る
        assert cost.get("tokens_input") is not None
        assert cost.get("tokens_output") is not None

        # backward compat: top-level extras も維持
        assert v1_tail.get("estimated_cost_usd_upper_bound") is not None
        assert v1_tail.get("rate_missing") is False
        # discriminator 整合性 (nested と top-level)
        assert cost["rate_missing"] == v1_tail["rate_missing"]
        assert cost["estimate"] == v1_tail["estimated_cost_usd_upper_bound"]
    finally:
        _os.chdir(saved_cwd)
        _sys.argv = saved_argv
        for k, v in saved_env.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v
        import shutil as _shutil
        _shutil.rmtree(proj, ignore_errors=True)


def test_observability_redaction_applied_rules_canonicalized() -> None:
    """PR-Q (Codex 01:30 AC approve): build_status() が redaction.applied_rules を
    sorted(set(...)) で正規化すること。caller 側 dedup 漏れ / 順序差を helper で吸収、
    downstream diff の安定性を確保。
    """
    from _observability import build_status

    # 重複あり、順序逆 → sorted unique
    p1 = build_status(
        script="x", v0_status="success", exit_code=0,
        redaction_rules=["user_content", "abs_path", "abs_path", "user_content"],
    )
    assert p1["redaction"]["applied_rules"] == ["abs_path", "user_content"], \
        f"applied_rules should be sorted set, got {p1['redaction']['applied_rules']!r}"

    # 単一値、変更なし
    p2 = build_status(
        script="x", v0_status="success", exit_code=0,
        redaction_rules=["secret"],
    )
    assert p2["redaction"]["applied_rules"] == ["secret"]

    # None / 空 → 空 list
    p3 = build_status(script="x", v0_status="success", exit_code=0)
    assert p3["redaction"]["applied_rules"] == []
    p4 = build_status(script="x", v0_status="success", exit_code=0, redaction_rules=[])
    assert p4["redaction"]["applied_rules"] == []

    # 4 class 全部 sorted alphabetical
    p5 = build_status(
        script="x", v0_status="success", exit_code=0,
        redaction_rules=["user_content", "secret", "provider_response_body", "abs_path"],
    )
    assert p5["redaction"]["applied_rules"] == [
        "abs_path", "provider_response_body", "secret", "user_content"
    ]


def test_all_seven_scripts_use_sys_exit_in_main() -> None:
    """PR-P (Codex 01:21 Z approve): 7 script すべてが `if __name__ == "__main__":` で
    `sys.exit(...)` 経由で exit code を propagate していることを static check。

    `main()` 直呼びだと `_emit_error` 経由 return int が shell rc=0 に潰れる。PR-G fix
    iter で compare_telop_split.py で発見・修正された pattern を全 script に展開。
    """
    scripts_dir = Path(__file__).resolve().parent
    target_scripts = [
        "build_slide_data.py",
        "build_telop_data.py",
        "voicevox_narration.py",
        "visual_smoke.py",
        "compare_telop_split.py",
        "preflight_video.py",
        "generate_slide_plan.py",
    ]
    missing_sys_exit = []
    for name in target_scripts:
        src = (scripts_dir / name).read_text(encoding="utf-8")
        # __main__ block 内に sys.exit(...) があること検査
        # `if __name__ == "__main__":` 以降の最後の non-empty 行を見る
        lines = src.splitlines()
        in_main_block = False
        body_lines = []
        for line in lines:
            if 'if __name__ == "__main__":' in line:
                in_main_block = True
                continue
            if in_main_block:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    body_lines.append(stripped)
        if not body_lines:
            missing_sys_exit.append(f"{name}: __main__ block empty")
            continue
        # body 内に sys.exit( 呼び出しがあること
        if not any("sys.exit(" in bl for bl in body_lines):
            missing_sys_exit.append(f"{name}: __main__ block missing sys.exit() ({body_lines[-1]!r})")
    assert not missing_sys_exit, \
        f"sys.exit() in __main__ block 漏れ (exit code propagation 不可、PR-G fix iter pattern):\n" + \
        "\n".join(f"  - {m}" for m in missing_sys_exit)


def test_generate_slide_plan_rate_missing_true_when_rate_unset() -> None:
    """PR-N (Codex 01:02): rate 未設定時の dry-run payload に rate_missing=true、
    estimated_cost_usd_upper_bound=null 両方が出ること (downstream discriminator)。

    docs/OBSERVABILITY.md §Cost JSON Shape の rate_missing field を contract enforcement。
    """
    import os as _os
    import io
    import sys as _sys
    import importlib
    from contextlib import redirect_stdout, redirect_stderr

    saved_argv = list(_sys.argv)
    saved_cwd = _os.getcwd()
    saved_env = {k: _os.environ.get(k) for k in (
        "ANTHROPIC_API_KEY",
        "SUPERMOVIE_RATE_INPUT_PER_MTOK", "SUPERMOVIE_RATE_OUTPUT_PER_MTOK",
        "SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK",
        "SUPERMOVIE_RATE_ANTHROPIC_OUTPUT_USD_PER_MTOK",
    )}

    proj = Path(tempfile.mkdtemp(prefix="rate_missing_unset_"))
    (proj / "transcript_fixed.json").write_text(
        json.dumps({"duration_ms": 5000, "words": [], "segments": [{"text": "x", "start": 0, "end": 100}]}),
        encoding="utf-8",
    )
    (proj / "project-config.json").write_text(
        json.dumps({"format": "youtube", "videoType": "test", "tone": "test"}),
        encoding="utf-8",
    )
    try:
        # rate 未設定
        for k in ("SUPERMOVIE_RATE_INPUT_PER_MTOK", "SUPERMOVIE_RATE_OUTPUT_PER_MTOK",
                  "SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK",
                  "SUPERMOVIE_RATE_ANTHROPIC_OUTPUT_USD_PER_MTOK"):
            _os.environ.pop(k, None)
        _os.environ.pop("ANTHROPIC_API_KEY", None)
        _os.chdir(str(proj))

        import generate_slide_plan as gsp
        importlib.reload(gsp)
        gsp.PROJ = proj

        _sys.argv = ["generate_slide_plan.py", "--dry-run", "--json-log"]
        out_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(io.StringIO()):
            gsp.main()
        lines = [l for l in out_buf.getvalue().splitlines() if l.strip()]
        # legacy dry-run payload (line 1)
        legacy = json.loads(lines[0])
        assert legacy.get("rate_missing") is True, \
            f"rate_missing should be True with rate unset, got {legacy.get('rate_missing')!r}"
        assert legacy.get("estimated_cost_usd_upper_bound") is None
        # v1 tail (last line)
        v1_tail = json.loads(lines[-1])
        assert v1_tail.get("rate_missing") is True, \
            f"v1 tail rate_missing should be True, got {v1_tail.get('rate_missing')!r}"
    finally:
        _os.chdir(saved_cwd)
        _sys.argv = saved_argv
        for k, v in saved_env.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v
        import shutil as _shutil
        _shutil.rmtree(proj, ignore_errors=True)


def test_generate_slide_plan_rate_missing_false_when_rate_set() -> None:
    """PR-N: rate 両方設定時の dry-run payload に rate_missing=false、
    estimated_cost_usd_upper_bound=finite float 両方が出ること。
    """
    import os as _os
    import io
    import sys as _sys
    import importlib
    from contextlib import redirect_stdout, redirect_stderr

    saved_argv = list(_sys.argv)
    saved_cwd = _os.getcwd()
    saved_env = {k: _os.environ.get(k) for k in (
        "ANTHROPIC_API_KEY",
        "SUPERMOVIE_RATE_INPUT_PER_MTOK", "SUPERMOVIE_RATE_OUTPUT_PER_MTOK",
        "SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK",
        "SUPERMOVIE_RATE_ANTHROPIC_OUTPUT_USD_PER_MTOK",
    )}

    proj = Path(tempfile.mkdtemp(prefix="rate_missing_set_"))
    (proj / "transcript_fixed.json").write_text(
        json.dumps({"duration_ms": 5000, "words": [], "segments": [{"text": "x", "start": 0, "end": 100}]}),
        encoding="utf-8",
    )
    (proj / "project-config.json").write_text(
        json.dumps({"format": "youtube", "videoType": "test", "tone": "test"}),
        encoding="utf-8",
    )
    try:
        _os.environ["SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK"] = "1.0"
        _os.environ["SUPERMOVIE_RATE_ANTHROPIC_OUTPUT_USD_PER_MTOK"] = "5.0"
        for k in ("SUPERMOVIE_RATE_INPUT_PER_MTOK", "SUPERMOVIE_RATE_OUTPUT_PER_MTOK"):
            _os.environ.pop(k, None)
        _os.environ.pop("ANTHROPIC_API_KEY", None)
        _os.chdir(str(proj))

        import generate_slide_plan as gsp
        importlib.reload(gsp)
        gsp.PROJ = proj

        _sys.argv = ["generate_slide_plan.py", "--dry-run", "--json-log"]
        out_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(io.StringIO()):
            gsp.main()
        lines = [l for l in out_buf.getvalue().splitlines() if l.strip()]
        legacy = json.loads(lines[0])
        assert legacy.get("rate_missing") is False, \
            f"rate_missing should be False with rate set, got {legacy.get('rate_missing')!r}"
        assert isinstance(legacy.get("estimated_cost_usd_upper_bound"), (int, float)), \
            f"estimate should be finite number, got {legacy.get('estimated_cost_usd_upper_bound')!r}"
        v1_tail = json.loads(lines[-1])
        assert v1_tail.get("rate_missing") is False
    finally:
        _os.chdir(saved_cwd)
        _sys.argv = saved_argv
        for k, v in saved_env.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v
        import shutil as _shutil
        _shutil.rmtree(proj, ignore_errors=True)


def test_unsafe_keep_abs_path_flag_present_in_all_seven_scripts() -> None:
    """PR-M (Codex 00:54 approve): 7 script すべてが `--unsafe-keep-abs-path` argparse flag を
    受け取り、`safe_artifact_path()` 等で `args.unsafe_keep_abs_path` を使っていることを
    static check で verify。将来 script 追加時に flag 漏れた場合の regression 防止。

    PR-I/J/K で abs_path contract が unified knob 化された前提を維持する lint。
    """
    scripts_dir = Path(__file__).resolve().parent
    target_scripts = [
        "build_slide_data.py",
        "build_telop_data.py",
        "voicevox_narration.py",
        "visual_smoke.py",
        "compare_telop_split.py",
        "preflight_video.py",
        "generate_slide_plan.py",
    ]
    missing_argparse = []
    missing_usage = []
    for name in target_scripts:
        src = (scripts_dir / name).read_text(encoding="utf-8")
        if "--unsafe-keep-abs-path" not in src:
            missing_argparse.append(name)
            continue
        # add_argument 呼び出しで flag 定義していること (action="store_true" パターン)
        if 'add_argument("--unsafe-keep-abs-path"' not in src:
            missing_argparse.append(name)
        # 受け取った args を実際に safe_artifact_path に渡していること
        # (args.unsafe_keep_abs_path 参照あり)
        if "args.unsafe_keep_abs_path" not in src:
            missing_usage.append(name)
    assert not missing_argparse, \
        f"--unsafe-keep-abs-path argparse 定義漏れ: {missing_argparse}"
    assert not missing_usage, \
        f"args.unsafe_keep_abs_path 使用漏れ: {missing_usage}"


def test_voicevox_narration_summary_path_redacted_by_default() -> None:
    """PR-I fix iter (Codex 00:13 P1 #1): voicevox_narration の human stdout summary JSON で
    raw path leak しない (engine 不在で skip 経由しないため、内部 helper 関数を直接 audit)。

    summary dict 構築箇所の path 4 fields が safe_artifact_path() 経由で redact されること。
    """
    from _observability import safe_artifact_path

    proj = Path(tempfile.mkdtemp(prefix="vv_summary_redact_"))
    try:
        ts_path = proj / "src/Narration/narrationData.ts"
        meta_path = proj / "narration_chunks_meta.json"
        out_path = proj / "public/narration.wav"
        ready_path = proj / "narration.ready.json"

        # default (redact) — proj path が tmpdir 配下の絶対 path で、placeholder/相対化される
        summary = {
            "narration_wav": safe_artifact_path(out_path, project_root=proj, unsafe_keep_abs_path=False),
            "narration_data_ts": safe_artifact_path(ts_path, project_root=proj, unsafe_keep_abs_path=False),
            "chunk_meta_json": safe_artifact_path(meta_path, project_root=proj, unsafe_keep_abs_path=False),
            "narration_ready_json": safe_artifact_path(ready_path, project_root=proj, unsafe_keep_abs_path=False),
        }
        rendered = json.dumps(summary, ensure_ascii=False)
        # default redact: raw proj 絶対 path は出ない
        assert str(proj) not in rendered, \
            f"raw proj path leaked in default summary: {rendered!r}"

        # unsafe-keep-abs-path: raw 出力
        summary_unsafe = {
            "narration_wav": safe_artifact_path(out_path, project_root=proj, unsafe_keep_abs_path=True),
        }
        rendered_unsafe = json.dumps(summary_unsafe, ensure_ascii=False)
        assert str(out_path) in rendered_unsafe, \
            f"unsafe-keep-abs-path should preserve raw path, got {rendered_unsafe!r}"
    finally:
        import shutil as _shutil
        _shutil.rmtree(proj, ignore_errors=True)


def test_generate_slide_plan_stderr_proj_path_redacted() -> None:
    """PR-J (Codex 00:22): generate_slide_plan で transcript_fixed.json missing 時の stderr に
    raw PROJ abs path が出ないこと、--unsafe-keep-abs-path で raw 切替。
    """
    import os as _os
    import io
    import sys as _sys
    import importlib
    import shutil as _shutil
    from contextlib import redirect_stdout, redirect_stderr

    saved_argv = list(_sys.argv)
    saved_cwd = _os.getcwd()
    saved_env = {"ANTHROPIC_API_KEY": _os.environ.get("ANTHROPIC_API_KEY")}

    proj = Path(tempfile.mkdtemp(prefix="gsp_stderr_redact_"))
    try:
        _os.environ["ANTHROPIC_API_KEY"] = "test-key"  # api_key skip path 回避、inputs_missing 経路へ
        _os.chdir(str(proj))
        import generate_slide_plan as gsp
        importlib.reload(gsp)
        gsp.PROJ = proj

        # default (redact)
        _sys.argv = ["generate_slide_plan.py"]
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            try:
                gsp.main()
            except SystemExit:
                pass
        err = err_buf.getvalue()
        assert "missing under" in err, f"expected missing-under message, got {err!r}"
        assert str(proj) not in err, \
            f"raw proj path leaked in default stderr: {err!r}"

        # --unsafe-keep-abs-path で raw
        _sys.argv = ["generate_slide_plan.py", "--unsafe-keep-abs-path"]
        err_buf2 = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err_buf2):
            try:
                gsp.main()
            except SystemExit:
                pass
        err2 = err_buf2.getvalue()
        assert str(proj) in err2, \
            f"unsafe-keep-abs-path should preserve raw proj, got {err2!r}"
    finally:
        _os.chdir(saved_cwd)
        _sys.argv = saved_argv
        for k, v in saved_env.items():
            if v is None:
                _os.environ.pop(k, None)
            else:
                _os.environ[k] = v
        _shutil.rmtree(proj, ignore_errors=True)


def test_build_slide_data_human_stdout_path_redacted_by_default() -> None:
    """PR-I (Codex 00:08): build_slide_data の human stdout `path: ...` 行は default redact、
    --unsafe-keep-abs-path で raw 切替。
    """
    import os as _os
    import io
    import sys as _sys
    import importlib
    import shutil as _shutil_mod
    from contextlib import redirect_stdout, redirect_stderr

    saved_argv = list(_sys.argv)
    saved_cwd = _os.getcwd()

    proj = Path(tempfile.mkdtemp(prefix="stdout_redact_"))
    (proj / "transcript_fixed.json").write_text(
        json.dumps({
            "duration_ms": 5000,
            "words": [{"text": "hi", "start": 0, "end": 500, "confidence": 0.9}],
            "segments": [{"text": "hi", "start": 0, "end": 500}],
        }),
        encoding="utf-8",
    )
    (proj / "project-config.json").write_text(
        json.dumps({"format": "youtube", "videoType": "test", "tone": "test"}),
        encoding="utf-8",
    )
    (proj / "src").mkdir(exist_ok=True)
    (proj / "src" / "Slides").mkdir(exist_ok=True)
    try:
        _os.chdir(str(proj))
        import build_slide_data as bsd
        importlib.reload(bsd)
        bsd.PROJ = proj

        # default (redact)
        _sys.argv = ["build_slide_data.py", "--mode", "topic"]
        out_buf = io.StringIO()
        with redirect_stdout(out_buf), redirect_stderr(io.StringIO()):
            try:
                bsd.main()
            except SystemExit:
                pass
        out = out_buf.getvalue()
        assert "path:" in out, f"path line missing in stdout: {out!r}"
        # raw proj path が default で出ない (redact 経由で 相対 / <TMP> / <HOME> placeholder)
        path_line = next(l for l in out.splitlines() if l.startswith("path:"))
        assert str(proj) not in path_line, \
            f"raw proj path leaked in default human stdout: {path_line!r}"

        # --unsafe-keep-abs-path で raw
        _sys.argv = ["build_slide_data.py", "--mode", "topic", "--unsafe-keep-abs-path"]
        out_buf2 = io.StringIO()
        with redirect_stdout(out_buf2), redirect_stderr(io.StringIO()):
            try:
                bsd.main()
            except SystemExit:
                pass
        out2 = out_buf2.getvalue()
        # raw mode では full path component が含まれる (debug 用途)
        assert "src/Slides/slideData.ts" in out2, \
            f"unsafe-keep-abs-path should preserve full path, got {out2!r}"
    finally:
        _os.chdir(saved_cwd)
        _sys.argv = saved_argv
        _shutil_mod.rmtree(proj, ignore_errors=True)


def main() -> int:
    tests = [
        test_fps_consistency,
        test_vad_schema_validation,
        test_ms_to_playback_frame,
        test_ms_to_playback_frame_edge_cases,
        test_load_cut_segments_fail_fast,
        test_load_cut_segments_edge_cases,
        test_build_cut_segments_multi_with_gaps,
        test_transcript_segment_validation,
        test_voicevox_collect_chunks_validation,
        test_voicevox_write_narration_data_alignment,
        test_voicevox_write_order_narrationdata_before_wav,
        test_voicevox_cleanup_stale_unlinks_sentinel,
        test_voicevox_sentinel_written_after_wav,
        test_voicevox_sentinel_write_fail_rollback,
        test_voicevox_sentinel_rollback_on_concat_fail,
        test_voicevox_json_log_emits_pure_json,
        test_voicevox_json_log_engine_skip_path,
        test_voicevox_json_log_engine_strict_path,
        test_visual_smoke_cli_mismatch_and_restore,
        test_visual_smoke_patch_format_youtube_to_short,
        test_visual_smoke_patch_format_no_match_raises,
        test_visual_smoke_patch_format_round_trip,
        test_visual_smoke_format_dims_completeness,
        test_build_scripts_wiring,
        test_build_slide_data_main_e2e,
        test_build_slide_data_validates_bad_transcript,
        test_build_telop_data_main_e2e,
        test_build_telop_data_validates_bad_transcript,
        test_generate_slide_plan_skip_no_api_key,
        test_generate_slide_plan_missing_inputs,
        test_generate_slide_plan_api_mock_success,
        test_generate_slide_plan_api_rate_limited_429,
        test_generate_slide_plan_api_http_error_non_429,
        test_generate_slide_plan_dry_run_no_api_key,
        test_generate_slide_plan_max_tokens_override_cli_env_precedence,
        test_generate_slide_plan_max_tokens_cap_rejects,
        test_generate_slide_plan_json_log_status_path,
        test_generate_slide_plan_skip_preserves_with_bad_env,
        test_generate_slide_plan_rate_rejects_nan_inf,
        test_generate_slide_plan_rate_v0_v1_alias_precedence,
        test_generate_slide_plan_max_input_caps_prompt,
        test_generate_slide_plan_api_invalid_json,
        test_build_slide_data_plan_validation_fallback,
        test_build_slide_data_plan_strict_failure,
        # Phase 3 obs migration core: helper module regression test (6 件)
        test_observability_helper_status_map,
        test_observability_status_map_lint,
        test_observability_status_map_category_format_invariant,
        test_observability_safe_artifact_path_redacts,
        test_observability_safe_artifact_path_collision_corners,
        test_observability_safe_artifact_path_tilde_expansion,
        test_observability_user_content_meta_no_raw,
        test_observability_sha256_hash_format_invariant,
        test_observability_redact_provider_body_default_strict,
        test_observability_redact_provider_body_preview_length_boundaries,
        test_observability_build_status_v1_schema,
        test_observability_build_status_duration_ms_and_category_override,
        test_observability_build_status_top_level_field_order,
        test_observability_warn_legacy_cost_extras_env_gated,
        test_observability_warn_legacy_cost_extras_env_strict_opt_in,
        test_observability_provider_body_stderr_default_redact,
        test_observability_emit_json_disabled_no_print,
        test_observability_emit_json_format_lint,
        test_observability_emit_json_stderr_clean,
        test_observability_emit_json_exit_code_int_contract,
        test_observability_emit_json_payload_must_be_dict,
        test_observability_build_status_redaction_rules_strict,
        test_observability_build_status_reserved_key_collision,
        test_observability_build_status_cost_dict_strict,
        test_observability_build_status_counts_artifacts_strict,
        test_observability_build_status_script_identifier_contract,
        test_observability_build_status_v0_status_defensive_lint,
        test_observability_build_cost_payload_nan_inf_defense,
        test_observability_build_cost_payload_rate_source_contract,
        # PR-E (distributed tracing run_id active emission): 7 件
        test_observability_resolve_run_context_uses_env,
        test_observability_resolve_run_context_generates_when_missing,
        test_observability_resolve_run_context_no_generate,
        test_observability_resolve_run_context_empty_env_fallback,
        test_observability_resolve_run_context_cap_exceeded,
        test_observability_resolve_run_context_cap_boundary,
        test_observability_build_status_schema_version_invariant,
        test_observability_run_id_in_payload,
        test_generate_slide_plan_run_id_propagation,
        # PR-F (cost abort threshold): 3 件
        test_generate_slide_plan_cost_abort_blocks_api_when_estimate_exceeds,
        test_generate_slide_plan_cost_abort_skipped_when_rate_unset,
        test_generate_slide_plan_cost_abort_cli_overrides_env,
        # PR-G (error path tail emit audit): 7 件 (4 早期 path emit + 3 fix iter: redact unit/integ + exit code propagation)
        test_compare_telop_split_transcript_missing_emits_tail,
        test_compare_telop_split_typo_dict_invalid_emits_tail,
        test_preflight_video_write_config_parse_error_emits_tail,
        test_observability_redact_error_message_strips_abs_path,
        test_observability_redact_error_message_windows_path,
        test_observability_redact_error_message_ipv6_and_data_uri_safe,
        test_observability_redact_error_message_url_with_port_query_fragment,
        test_observability_redact_error_message_multiple_paths_in_one_msg,
        test_observability_redact_error_message_url_path_order_independence,
        test_observability_redact_error_message_tilde_path_token,
        test_observability_build_status_category_override_defensive_lint,
        test_observability_warn_legacy_cost_extras_payload_must_be_dict,
        test_compare_telop_split_error_message_redacted,
        test_compare_telop_split_exit_code_propagates,
        test_visual_smoke_out_dir_mkdir_error_emits_tail,
        # PR-H (helper-level secret redaction、Codex 23:58 approve): 5 件 (4 feat + 1 fix iter boundary)
        test_observability_redact_secret_long_value_keeps_last_4,
        test_observability_redact_secret_short_value_full_mask,
        test_observability_redact_secret_non_string_passthrough,
        test_observability_redact_secret_custom_last_n_and_mask_char,
        test_observability_redact_secret_last_n_zero_or_negative_full_mask,
        test_observability_redact_secret_boundary_and_mask_char_strict,
        # PR-M (--unsafe-keep-abs-path flag audit、Codex 00:54 approve): 1 件 (lint-style)
        test_unsafe_keep_abs_path_flag_present_in_all_seven_scripts,
        # PR-N (cost.estimate / rate_missing schema verification、Codex 01:02 approve): 2 件
        test_generate_slide_plan_rate_missing_true_when_rate_unset,
        test_generate_slide_plan_rate_missing_false_when_rate_set,
        # PR-O (compute_rate_missing helper sink、Codex 01:12 approve): 1 件
        test_observability_compute_rate_missing_helper,
        # PR-S (nested cost={...} schema migration、Codex 01:46 X approve): 2 件
        test_observability_build_cost_payload_helper,
        test_generate_slide_plan_dry_run_emits_nested_cost,
        # PR-P (entry exit code propagation audit、Codex 01:21 approve): 1 件 (lint-style)
        test_all_seven_scripts_use_sys_exit_in_main,
        # PR-Q (redaction.applied_rules canonicalize、Codex 01:30 approve): 1 件
        test_observability_redaction_applied_rules_canonicalized,
        # PR-I (human stdout path leak audit、Codex 00:08 approve): 2 件 (1 feat + 1 fix iter voicevox summary redact)
        test_build_slide_data_human_stdout_path_redacted_by_default,
        test_voicevox_narration_summary_path_redacted_by_default,
        # PR-J (stderr path leak audit、Codex 00:22 approve): 1 件
        test_generate_slide_plan_stderr_proj_path_redacted,
    ]
    failed = []
    for t in tests:
        name = t.__name__
        try:
            t()
            print(f"  [OK]   {name}")
        except AssertionError as e:
            failed.append((name, str(e)))
            print(f"  [FAIL] {name}: {e}", file=sys.stderr)
        except Exception as e:
            failed.append((name, f"{type(e).__name__}: {e}"))
            print(f"  [ERR]  {name}: {type(e).__name__}: {e}", file=sys.stderr)

    total = len(tests)
    print(f"\nResult: {total - len(failed)}/{total} pass, {len(failed)} fail")
    if failed:
        for name, msg in failed:
            print(f"  - {name}: {msg}", file=sys.stderr)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
