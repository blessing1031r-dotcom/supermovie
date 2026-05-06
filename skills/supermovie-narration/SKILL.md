---
name: supermovie-narration
description: |
  VOICEVOX (ローカル無料 TTS) で transcript_fixed.json から narration.wav を生成し、
  Remotion の <NarrationAudio /> layer で再生するスキル。
  「ナレーション」「TTS」「VOICEVOX」「読み上げ」と言われたときに使用。
argument-hint: [プロジェクトパス] [--speaker N] [--script <path>]
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent
effort: medium
---

# SuperMovie Narration — VOICEVOX 自動ナレーション

Senior video producer として、文字起こし結果から自動ナレーションを生成し、
動画の元音声を差し替える形で動画コンテンツの語り直しを行う。

## Remotion Bridge Note

Remotion の `.tsx` component / Composition / Sequence / Audio / `staticFile` /
animation / media timing を変更・新規設計する場合は、まず
`/supermovie-remotion-bridge` を開く。
bridge skill の判定で必要な場合だけ、official Remotion Codex plugin の
`remotion-best-practices` を参照する。
SuperMovie の schema、file path、Japanese workflow、Python lint gate はこの
repo を source of truth とする。

**前提**: Phase 3-A SlideSequence、Phase 3-B/3-C supermovie-slides 完成後の運用想定。

## 設計起点

Codex Phase 3-D design (2026-05-04, CODEX_PHASE3D_VOICEVOX) 推奨:
- engine 不在で skip (`--require-engine` 指定時のみ exit non-zero)
- Phase 3-C と同じく optional 経路で deterministic フォールバックなし (engine 必須)
- Anthropic API ではなく VOICEVOX ローカル engine、課金ゼロ

## ワークフロー

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ 1. engine│ → │ 2. 入力  │ → │ 3. 合成  │ → │ 4. Remotion │
│   起動確認│    │   解決   │    │   結合   │    │   接合    │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
```

## Phase 1: VOICEVOX engine 起動確認

VOICEVOX engine は localhost:50021 で REST API を提供する。
Roku が以下のいずれかで起動した後に実行:

- VOICEVOX デスクトップアプリ (https://voicevox.hiroshiba.jp/)
- VOICEVOX engine Docker (https://github.com/VOICEVOX/voicevox_engine)

`voicevox_narration.py` は `/version` で自動確認、不在なら skip。

## Phase 2: 入力解決

優先順位:
1. `--script <path>` で plain-text 指定 (1 行 1 chunk)
2. `--script-json <path>` で `{segments: [{text}]}` JSON 指定
3. default: `<PROJECT>/transcript_fixed.json` の `segments[].text` を chunk

## Phase 3: 合成 + 結合 (Phase 3-D legacy + Phase 3-H per-segment)

各 chunk について:
1. `POST /audio_query?text=...&speaker=<id>` → query JSON
2. `POST /synthesis?speaker=<id>` body=query → WAV bytes

**Phase 3-D legacy**: chunk wav を時系列で結合し `public/narration.wav` を生成。

**Phase 3-H per-segment** (default、自動):
- `public/narration/chunk_NNN.wav` を保持 (削除しない)
- 各 chunk の wave header から実 duration 測定 → frame 換算
- `src/Narration/narrationData.ts` を all-or-nothing で生成
  (NarrationSegment[]: id / startFrame / durationInFrames / file / text /
   sourceStartMs / sourceEndMs)
- `public/narration/chunk_meta.json` に fps + total_frames + cut_aware +
  overlaps + segments[] を debug 出力
- partial failure (途中 chunk synthesis 失敗) 時は `narrationData.ts` を空 array に
  reset + 部分 chunk を削除 (二重音声 / 中途半端 timeline 防止)

**Phase 3-I transcript timing alignment** (default、自動):
- transcript_fixed.json segments[].start/end を chunk metadata に保持
- `startFrame` を transcript start_ms から videoConfig.FPS で frame 化
  (旧 Phase 3-H は単純 chunk duration 累積で transcript timing と無関係だった)
- `vad_result.json` がある時は cut-aware mapping (build_slide_data.py の
  `ms_to_playback_frame` と同型関数を内蔵)
- cut で除外された ms 範囲 → 累積 frame fallback + WARN
- 隣接 chunk の overlap (前 chunk 終端 > 現 startFrame) を検出して WARN
  (chunk_meta.json の `overlaps[]` にも記録、render では `<Sequence>` 重複で
  二重再生になり得るため transcript 再分割や `--allow-partial` 検討の signal)
- `--script` (timing なし) / `--script-json` (startMs/endMs optional) は
  累積 fallback で Phase 3-H 互換挙動を維持

FPS は `--fps` 引数 → `<PROJECT>/src/videoConfig.ts` の `export const FPS = N;` →
default 30 の優先順位で解決 (Codex Phase 3-H review P2 #4 + P2 #5: Remotion
が videoConfig.FPS を使う一方で script が project-config.json を読むと両者
ズレるため、videoConfig.ts を一次 source に統一)。`--fps <= 0` は exit 4。

stale chunk + 旧 narration.wav (前回実行の遺物) は synthesis 開始前に必ず
cleanup。legacy `narration.wav` の削除失敗は exit 7 (StaleCleanupError、stale
legacy 再生事故防止)。

## Phase 4: Remotion 接合 (asset gate、手動操作不要)

Phase 3-F asset gate + Phase 3-H per-segment Sequence により
`MainVideo.tsx` 編集は不要。

| 状態 | NarrationAudio | base Video volume |
|------|----------------|-------------------|
| narrationData 空 + narration.wav 不在 | null (skip) | 1.0 (元音声再生) |
| narrationData 空 + narration.wav 存在 (Phase 3-D legacy) | 単一 `<Audio>` 再生 | 0 (mute) |
| narrationData non-empty + 全 chunk 存在 (Phase 3-H) | `<Sequence from durationInFrames>` で chunk ループ | 0 (mute) |

優先順位は narrationData > narration.wav > null。
`voicevox_narration.py` 成功 → `narrationData.ts` 上書き + chunk 配置 → 次回
`npm run dev` / `npm run render` で自動的に per-segment narration 再生 + base mute
に切り替わる。Roku の手作業ゼロ。

実装参照:
- `template/src/Narration/mode.ts` (`getNarrationMode()`: chunks / legacy / none の
  三経路を Set lookup + module-level memo で判定する一元 helper)
- `template/src/MainVideo.tsx` (mode helper 経由で `baseVolume` 判定、
  `none` だけ `volume=1.0`、それ以外は `0`)
- `template/src/Narration/NarrationAudio.tsx` (mode helper 経由で
  `<Sequence>` ループ / 単一 `<Audio>` / `null` を返す)
- `template/src/Narration/types.ts` (NarrationSegment 型)
- `template/src/Narration/narrationData.ts` (script が all-or-nothing + atomic で書換)

## 実行コマンド

```bash
# default (transcript の segments すべて、speaker=3 = ずんだもんノーマル)
python3 <PROJECT>/scripts/voicevox_narration.py

# speaker 指定 (一覧は --list-speakers で確認)
python3 <PROJECT>/scripts/voicevox_narration.py --speaker 1
python3 <PROJECT>/scripts/voicevox_narration.py --list-speakers

# 別スクリプト読み込み
python3 <PROJECT>/scripts/voicevox_narration.py --script narration.txt
python3 <PROJECT>/scripts/voicevox_narration.py --script-json narration.json

# engine 不在で fail させる (CI 用)
python3 <PROJECT>/scripts/voicevox_narration.py --require-engine
```

## 出力

- `<PROJECT>/public/narration.wav` (Phase 3-D legacy、結合済)
- `<PROJECT>/public/narration/chunk_NNN.wav` (Phase 3-H、render 時に必要、常時保持)
- `<PROJECT>/public/narration/chunk_meta.json` (debug、fps + segments[])
- `<PROJECT>/src/Narration/narrationData.ts` (Phase 3-H render 駆動データ)

## エラーハンドリング

| エラー | 対応 |
|--------|------|
| engine 不在 | INFO ログ + exit 0 (skip)、`--require-engine` 時のみ exit 4 |
| transcript_fixed.json 不在 | exit 3 (`--script` で迂回可) |
| `/audio_query` HTTP エラー | chunk skip + WARN、全 chunk 失敗で exit 5 |
| WAV 結合 format mismatch | chunk skip + WARN、可能な範囲で結合 |
| partial chunk failure (--allow-partial なし) | exit 6、部分 chunk + 旧 narration.wav 全削除、narrationData.ts 空 array (all-or-nothing) |
| WAV header 解析失敗 (wave.Error / EOFError) | exit 6、chunk + narration.wav 削除 (Codex Phase 3-H review P2 #6) |
| FPS 不正 (--fps <= 0 / videoConfig.ts FPS regex 失敗) | exit 4 (前者) or default 30 fallback (後者) |
| stale narration.wav 削除失敗 (StaleCleanupError) | exit 7 (Codex Phase 3-H re-review P1 #2 partial 反映、stale legacy 再生事故防止) |
| transcript validation 失敗 (TranscriptSegmentError) | exit 3 (start>end / 型不正 / 非 dict、collect_chunks で early raise、Codex Phase 3-I review P2 #3 反映) |
| vad_result.json 部分破損 (VadSchemaError / OSError / JSONDecodeError) | exit 8 (synthesis 前に fail-fast、stale legacy が legacy mode へ流れる事故防止、Codex Phase 3-I review P1 #2 + Phase 3-J review P1 反映で synthesis 前に validation 移動済) |

## 連携マップ

```
/supermovie-init / transcribe / transcript-fix / cut / subtitles / slides
    ↓ transcript_fixed.json
/supermovie-narration         ← ★ここ: VOICEVOX で 3 出力を atomic 生成
    ├─ public/narration/chunk_NNN.wav  (Phase 3-H、render 時に必要)
    ├─ public/narration/chunk_meta.json (debug、fps + segments[])
    ├─ src/Narration/narrationData.ts   (Phase 3-H render 駆動)
    └─ public/narration.wav              (Phase 3-D legacy fallback)
    ↓
MainVideo.tsx + NarrationAudio.tsx が getNarrationMode() で判定
    1) chunks complete → <Sequence> ループ + base mute
    2) legacy narration.wav 存在 → 単一 <Audio> + base mute
    3) どちらも不在 → null + base 元音声 1.0
    ↓
npm run render
```

**Studio を開いたまま narration.wav を生成した場合は再読み込み推奨**:
`getStaticFiles()` は Studio 起動時の asset リストをキャッシュするため、
新規ファイルは Studio reload (Cmd+R / `r` キー) で反映される
(出典 https://www.remotion.dev/docs/getstaticfiles)。

## VOICEVOX 利用規約

- 商用/非商用利用可、音声ライブラリごとにクレジット表記必須 (https://voicevox.hiroshiba.jp/term/)
- 話者選定 + クレジット明記は Roku 判断領域
