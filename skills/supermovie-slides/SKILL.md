---
name: supermovie-slides
description: |
  transcript_fixed.json と project-config.json から SlideSegment[] を生成し、
  src/Slides/slideData.ts に書き出すスキル。
  Phase 3-A SlideSequence layer の空 placeholder を実データに変える。
  「スライド生成」「slide」「supermovie-slides」と言われたときに使用。
argument-hint: [プロジェクトパス] [--mode topic|segment]
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent
effort: medium
---

# SuperMovie Slides — スライド自動生成

Senior video editor として、transcript_fixed.json の segments から話題区間を抽出し、
Remotion `SlideSequence` 用の `SlideSegment[]` データを生成する。

## Remotion Bridge Note

Remotion の `.tsx` component / Composition / Sequence / Audio / `staticFile` /
animation / media timing を変更・新規設計する場合は、まず
`/supermovie-remotion-bridge` を開く。
bridge skill の判定で必要な場合だけ、official Remotion Codex plugin の
`remotion-best-practices` を参照する。
SuperMovie の schema、file path、Japanese workflow、Python lint gate はこの
repo を source of truth とする。

**前提**: Phase 3-A で SlideSequence / Slide / types / slideData (空) が template に追加済み。

## ワークフロー概要

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ 1. 入力読込  │ → │ 2. 話題分割  │ → │ 3. スライド  │ → │ 4. ファイル │
│  transcript  │    │  segments を │    │   生成       │    │   書き出し   │
│  config      │    │  topic 単位に│    │  Slide[] へ  │    │  + verify    │
└──────────────┘   └──────────────┘   └──────────────┘   └──────────────┘
```

## Phase 1: 入力データ読込

- `<PROJECT>/transcript_fixed.json` から `segments[]` (start/end ms + text) と `words[]`
- `<PROJECT>/project-config.json` から `format` (youtube/short/square)、`tone`
- (任意) `<PROJECT>/src/cutData.ts` から CutSegment[] (cut 後 frame に変換するため)
- (任意) `<PROJECT>/src/Title/titleData.ts` から TitleSegment[] (タイトルとの重複回避)

## Phase 2: 話題分割 (deterministic first)

Codex Phase 3B design (2026-05-04) 推奨: deterministic first、LLM は別 phase で optional plan。

### 2-1. 話題区間抽出 (mode=topic、推奨)

連続する segments を「話題」単位にグループ化する:
- 隣接 segments の境界が 1.5 秒以上の無音 (VAD silence) であれば話題区切り
- または 4-5 segments で 1 group (機械的、フォールバック)

各話題 group の代表 text を以下で抽出:
- **title**: 先頭 segment の text 冒頭 12-15 字 (format に応じて trim)
- **subtitle**: 任意。最も重要な segment.text の続き 20-30 字
- **bullets**: group 内の各 segment.text の冒頭 12-18 字を 1 bullet に。最大 5 個

### 2-2. segment 単位 (mode=segment、シンプル fallback)

1 transcript segment = 1 slide。短い動画や test 用。
- title = segment.text の冒頭
- bullets なし
- 表示時間 = segment 全長

## Phase 3: SlideSegment 生成

`SlideSegment` schema (`src/Slides/types.ts`):
```typescript
{ id, startFrame, endFrame, title, subtitle?, bullets?, align?, backgroundColor?, textColor?, videoLayer? }
```

frame 計算:
- transcript の word.start (ms) → cutData 経由で playback frame に変換
- cutData が存在しない場合は単純に `ms / 1000 * FPS`

トーン別の見た目:
| トーン | align | backgroundColor | bullet emphasis 比率 |
|--------|-------|------------------|----------------------|
| プロフェッショナル | center | `rgba(20, 26, 44, 0.92)` | 0-1 / slide |
| エンタメ | left | `#101a2c` | 1-2 / slide |
| カジュアル | left | `rgba(40, 30, 60, 0.9)` | 1 / slide |
| 教育的 | left | `#0f2540` | 1-2 / slide |

videoLayer:
- 通常 `'visible'` (動画は背景のまま、スライドは半透明オーバーレイ)
- フルスクリーンタイトル時のみ `'hidden'` を検討

## Phase 4: ファイル書き出し + verify

- 出力先: `<PROJECT>/src/Slides/slideData.ts`
- バリデーション:
  - frame 範囲が CUT_TOTAL_FRAMES 内
  - title が空でない
  - bullets が 0-5 個
  - 隣接 slide が overlap しない
- 既存 `slideData.ts` を `slideData.backup.ts` として退避

## 実行コマンド

```bash
# Phase 3-B (deterministic、default)
python3 <PROJECT>/scripts/build_slide_data.py
python3 <PROJECT>/scripts/build_slide_data.py --mode topic

# Phase 3-C (LLM optional plan、ANTHROPIC_API_KEY 必須):
ANTHROPIC_API_KEY=sk-ant-... python3 <PROJECT>/scripts/generate_slide_plan.py \
  --output <PROJECT>/slide_plan.json
python3 <PROJECT>/scripts/build_slide_data.py --plan <PROJECT>/slide_plan.json
# --strict-plan で plan 検証失敗時 exit 2 (default は warning + deterministic fallback)
```

## Phase 3-C: LLM optional plan (Codex CODEX_PHASE3C_LLM_PLAN_20260504T201229)

**LLM 経路は word index ベースの plan を返し、frame は build script が変換する**。
これにより LLM が frame 計算をミスっても整合性が保たれる。

### slide_plan.json schema

```json
{
  "version": "supermovie.slide_plan.v1",
  "slides": [
    {
      "id": 1,
      "startWordIndex": 0,
      "endWordIndex": 30,
      "title": "短い見出し",
      "subtitle": "任意",
      "bullets": [{ "text": "要点", "emphasis": true }],
      "align": "left",
      "videoLayer": "visible"
    }
  ]
}
```

### validation ルール (build_slide_data.py)

- `version` 完全一致 (`supermovie.slide_plan.v1`)
- `slides` が配列、`id` 昇順
- `0 <= startWordIndex <= endWordIndex < len(words)`
- 隣接 slide の word range が overlap しない
- `title` 非空 + format 別 max 文字数以内
- `bullets` ≤ 5、各 `text` ≤ format 別 max
- `align` ∈ {"center","left"}、`videoLayer` ∈ {"visible","dimmed","hidden"}

invalid 時のデフォルト挙動: warning 出力 + deterministic (topic mode) fallback。
`--strict-plan` 指定時は exit 2 で停止。

### 設計の根拠

LLM に frame を返させない理由は、word index → frame 変換に必要な情報 (cutData / VAD 適用、playback timeline 計算) は script 側にしかなく、LLM 推測だと cut 後 frame の整合が崩れるため。
Anthropic 公式 structured outputs で JSON schema 出力を強制可能 (https://platform.claude.com/docs/en/build-with-claude/structured-outputs)。

## 完了時の報告フォーマット

```
✅ slideData.ts 生成完了

📊 入力:
  segments: <N>個
  topic groups: <M>個

📝 出力 slides: <K> 個
  例: 「<title 1>」(frame X-Y)、...

📄 保存先: src/Slides/slideData.ts

次のステップ:
→ npm run render で動画確認
→ supermovie-image-gen でインフォグラフィック追加
```

## 連携マップ

```
/supermovie-init / transcribe / transcript-fix / cut / subtitles
    ↓ transcript_fixed.json + cutData.ts
/supermovie-slides            ← ★ここ: SlideSegment[] 生成
    ↓ slideData.ts → SlideSequence layer (Phase 3-A)
/supermovie-image-gen / se
    ↓
npm run render
```
