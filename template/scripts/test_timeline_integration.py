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
        test_observability_safe_artifact_path_redacts,
        test_observability_user_content_meta_no_raw,
        test_observability_redact_provider_body_default_strict,
        test_observability_build_status_v1_schema,
        test_observability_build_status_duration_ms_and_category_override,
        test_observability_provider_body_stderr_default_redact,
        test_observability_emit_json_disabled_no_print,
        # PR-E (distributed tracing run_id active emission): 7 件
        test_observability_resolve_run_context_uses_env,
        test_observability_resolve_run_context_generates_when_missing,
        test_observability_resolve_run_context_no_generate,
        test_observability_resolve_run_context_empty_env_fallback,
        test_observability_resolve_run_context_cap_exceeded,
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
        test_compare_telop_split_error_message_redacted,
        test_compare_telop_split_exit_code_propagates,
        test_visual_smoke_out_dir_mkdir_error_emits_tail,
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
