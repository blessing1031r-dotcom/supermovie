---
name: supermovie-cut
description: |
  動画の不要区間（無音・フィラー・脱線）を自動検出しカットするスキル。
  Silero VAD + transcript_fixed.json + Claude LLM分析の3層で高精度にカット判定。
  Remotion Sequence で必要区間のみ再生する構成を生成。
  「カット」「無音カット」「不要部分削除」「cut」と言われたときに使用。
argument-hint: [プロジェクトパス]
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent, AskUserQuestion
effort: high
---

# SuperMovie Cut — 動画自動カット

Senior video editor として、音声AI（Silero VAD）+ 文字起こし + LLM分析の
3層でカット判定し、不要区間を精密に除去する。

## ワークフロー概要

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ 1. VAD   │→│ 2. transcript│→│ 3. LLM   │→│ 4. カット  │→│ 5. 適用   │
│ 音声区間  │  │ ギャップ検出 │  │ 内容分析  │  │ リスト統合 │  │ + 検証   │
│ Silero   │  │ words間の間 │  │ 不要判定  │  │ ヒアリング │  │ Remotion │
└──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
```

---

## 前提条件チェックリスト

- [ ] `/supermovie-transcribe` で文字起こし済み
- [ ] `/supermovie-transcript-fix` で誤字修正済み
- [ ] `transcript_fixed.json` が存在し `words` 配列がある
- [ ] `transcript_audio.wav` が存在
- [ ] Python 3.9+ がインストール済み

---

## Phase 1: Silero VAD 音声区間検出

### 1-1. インストール（初回のみ）

```bash
# プロジェクトのvenvを使用（transcribeで作成済み）
source "<PROJECT>/.venv/bin/activate"
pip install silero-vad torchaudio
```

### 1-2. VAD実行スクリプト

`<PROJECT>/vad_runner.py` を生成して実行:

```python
#!/usr/bin/env python3
"""SuperMovie VAD Runner (Silero VAD)"""
import torch
import torchaudio
import json
import sys

audio_path = sys.argv[1]
output_path = sys.argv[2]
min_silence = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5  # 秒

print(f"🔍 VAD解析開始: {audio_path}")

# Silero VADモデルをロード
model, utils = torch.hub.load(
    repo_or_dir='snakers4/silero-vad',
    model='silero_vad',
    force_reload=False,
)
(get_speech_timestamps, _, read_audio, _, _) = utils

# 音声読み込み（16kHz）
wav = read_audio(audio_path, sampling_rate=16000)

# 音声区間検出
speech_timestamps = get_speech_timestamps(
    wav,
    model,
    sampling_rate=16000,
    min_silence_duration_ms=int(min_silence * 1000),
    speech_pad_ms=100,       # 発話前後に100msの余白
    min_speech_duration_ms=250,  # 250ms未満の音声は無視
)

# ミリ秒に変換
speech_segments = []
for ts in speech_timestamps:
    speech_segments.append({
        'start': round(ts['start'] / 16000 * 1000),
        'end': round(ts['end'] / 16000 * 1000),
    })

# 無音区間を算出
silence_segments = []
audio_duration_ms = round(len(wav) / 16000 * 1000)

for i in range(len(speech_segments)):
    if i == 0 and speech_segments[0]['start'] > 0:
        silence_segments.append({
            'start': 0,
            'end': speech_segments[0]['start'],
            'duration': speech_segments[0]['start'],
        })
    if i < len(speech_segments) - 1:
        gap_start = speech_segments[i]['end']
        gap_end = speech_segments[i + 1]['start']
        gap_duration = gap_end - gap_start
        if gap_duration >= min_silence * 1000:
            silence_segments.append({
                'start': gap_start,
                'end': gap_end,
                'duration': gap_duration,
            })

output = {
    'audio_duration_ms': audio_duration_ms,
    'speech_segments': speech_segments,
    'silence_segments': silence_segments,
    'stats': {
        'speech_count': len(speech_segments),
        'silence_count': len(silence_segments),
        'total_speech_ms': sum(s['end'] - s['start'] for s in speech_segments),
        'total_silence_ms': sum(s['duration'] for s in silence_segments),
    }
}

with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

speech_pct = output['stats']['total_speech_ms'] / audio_duration_ms * 100
print(f"✅ 完了: 発話 {len(speech_segments)}区間 ({speech_pct:.1f}%) / 無音 {len(silence_segments)}区間")
```

### 1-3. 実行

```bash
"<PROJECT>/.venv/bin/python3" "<PROJECT>/vad_runner.py" \
  "<PROJECT>/transcript_audio.wav" \
  "<PROJECT>/vad_result.json" \
  0.5
```

### 1-4. 出力: vad_result.json

```json
{
  "audio_duration_ms": 325000,
  "speech_segments": [
    { "start": 500, "end": 3200 },
    { "start": 4100, "end": 8500 }
  ],
  "silence_segments": [
    { "start": 3200, "end": 4100, "duration": 900 }
  ],
  "stats": {
    "speech_count": 45,
    "silence_count": 12,
    "total_speech_ms": 290000,
    "total_silence_ms": 35000
  }
}
```

---

## Phase 2: transcript ギャップ検出

transcript_fixed.json の words 間のギャップからも無音を検出。
VADとは別の観点（Whisperが認識しなかった区間）。

```
words: [...{end: 3200}, {start: 4500}...]
              ↑ gap = 1300ms（Whisperが何も認識しなかった）
```

### 2-1. ギャップ抽出ルール

| ギャップ長 | 判定 | 処理 |
|-----------|------|------|
| < 0.3秒 | 自然な間 | カットしない |
| 0.3〜1.0秒 | 短い間 | 0.15秒に短縮 |
| 1.0〜3.0秒 | 長い間 | 0.3秒に短縮 |
| > 3.0秒 | 明らかな無音 | 0.5秒に短縮 |

---

## Phase 3: Claude LLM 内容分析

transcript_fixed.json のテキストを分析し、**内容的に不要な区間**を判定。

### 3-1. LLMプロンプト

```
あなたは動画編集の専門家です。
以下の文字起こしテキストを分析し、カットすべき区間を判定してください。

## カット対象
1. 言い直し・言い淀み（「あ、違う、えっと〜」）
2. 話の脱線（本題から逸れた雑談が30秒以上続く場合）
3. 繰り返し（同じ内容を2回以上言っている場合、2回目以降）
4. 技術トラブル（「あ、画面見えてます？」「ちょっと待ってください」）

## カットしてはいけないもの
1. 本題の説明
2. 重要なポイントの強調（繰り返しに見えても意図的な強調）
3. 質問への回答
4. 具体的な例え話

## 入力
<segments JSON>

## 出力形式
{
  "cuts": [
    {
      "segmentIndex": 5,
      "reason": "言い直し",
      "severity": "high",
      "text": "あ、違う、えっと..."
    }
  ],
  "keep_note": "セグメント12の繰り返しは意図的な強調のため保持"
}
```

### 3-2. severity判定

| severity | 処理 |
|----------|------|
| `high` | 自動カット推奨（言い直し、技術トラブル） |
| `medium` | ユーザーに確認（脱線、繰り返し） |
| `low` | 情報提供のみ（カットしなくてもOK） |

---

## Phase 4: カットリスト統合 + ヒアリング

### 4-1. 3層の結果を統合

```
VAD無音区間 + transcriptギャップ + LLM内容分析
    ↓ マージ（重複除去）
    ↓
統合カットリスト
```

### 4-2. ユーザーに確認

```
カット分析が完了しました。

📊 分析結果:
  動画長: 5分25秒
  発話率: 89%（VAD検出）
  カット候補: 15箇所

🔴 自動カット推奨（8箇所）:
  [0:32-0:35] 無音 3.0秒 → 0.5秒に短縮
  [1:15-1:18] 言い直し「あ、違う、えっと...」
  [2:40-2:43] 無音 2.8秒 → 0.5秒に短縮
  ...

🟡 確認が必要（4箇所）:
  [3:20-3:55] 脱線？「昨日のニュースで...」(35秒)
  → カットする / 残す / 短縮する？
  ...

🟢 カットしなくてOK（3箇所）:
  [1:50-1:52] 短い間 0.8秒（自然な間）

どう進めますか？
→ 全て推奨通り / 個別に判断 / 無音カットのみ
```

### 4-3. カットモード選択

| モード | 内容 |
|--------|------|
| `auto` | 全推奨カット適用 + medium は保持 |
| `aggressive` | high + medium 全てカット |
| `silence-only` | 無音カットのみ（内容はカットしない） |
| `manual` | 1件ずつ確認 |

---

## Phase 5: Remotion への適用

### 5-1. cutData.ts 生成

カットではなく**残す区間（keep segments）**をリスト化:

```typescript
// src/cutData.ts
export interface CutSegment {
  id: number;
  originalStart: number;  // 元動画のフレーム
  originalEnd: number;
  playbackStart: number;  // カット後の再生フレーム
  playbackEnd: number;
}

export const FPS = 30;
const toFrame = (ms: number) => Math.round(ms / 1000 * FPS);

export const cutData: CutSegment[] = [
  { id: 1, originalStart: toFrame(0), originalEnd: toFrame(32000), playbackStart: 0, playbackEnd: toFrame(32000) },
  { id: 2, originalStart: toFrame(35000), originalEnd: toFrame(75000), playbackStart: toFrame(32500), playbackEnd: toFrame(72500) },
  // 無音3秒が0.5秒に短縮された
];

export const ORIGINAL_DURATION_FRAMES = toFrame(325000);
export const CUT_DURATION_FRAMES = toFrame(298000);  // カット後の総フレーム数
```

### 5-2. MainVideo.tsx の更新

カットを適用する場合、MainVideo.tsx に CutPlayer コンポーネントを追加:

```typescript
// cutDataの各セグメントをSequenceでつなぎ合わせ
// 元動画の該当区間だけを再生する
```

### 5-3. Root.tsx の更新

```typescript
// カット後のフレーム数に更新
const VIDEO_DURATION_FRAMES = CUT_DURATION_FRAMES;
```

### 5-4. telopData.ts のフレーム再計算

**カットによってフレーム番号がずれるため、全テロップのstartFrame/endFrameを再計算。**

```
カット前: テロップA startFrame=1000
カット区間: frame 800-900 が削除された
カット後: テロップA startFrame=900（100フレーム前にずれる）
```

この再計算はcutData.tsのマッピングから機械的に算出可能。

---

## Phase 6: 検証

### 6-1. バリデーション

| チェック項目 | 条件 | 失敗時の対応 |
|-------------|------|------------|
| カット後の動画長 | 元の50%以上残っている | 50%未満は警告（カットしすぎ） |
| テロップフレーム | カット後のフレーム範囲内 | 範囲外テロップは削除 |
| 音声ジャンプ | カット境界で不自然な音声ジャンプがないか | crossfade 0.1秒を挿入 |
| 映像ジャンプ | カット境界で映像が不自然に飛ばないか | 情報提供（手動確認を促す） |

### 6-2. 一時ファイル削除

```bash
rm -f "<PROJECT>/vad_runner.py" "<PROJECT>/vad_result.json"
```

---

## 完了時の報告フォーマット

```
✅ カット完了

✂️ カット結果:
  元の動画: <X>分<Y>秒
  カット後: <X>分<Y>秒（<Z>秒短縮 = <P>%削減）
  カット箇所: <N>箇所
    - 無音短縮: <n>箇所
    - 内容カット: <n>箇所
    - フィラー除去: <n>箇所

📄 生成ファイル:
  cutData.ts（カット区間定義）
  telopData.ts（フレーム再計算済み）

次のステップ:
→ npm run dev でカット結果をプレビュー
→ /supermovie-subtitles でテロップ再生成（カット後のタイミングで）
```

---

## 実行タイミングの注意

カットは subtitles の前に実行するのが推奨。
subtitles の後にカットすると全テロップのフレーム再計算が必要になるため、カット前にやるのが効率的。

---

## エラーハンドリング

| エラー | 対応 |
|--------|------|
| Silero VAD インストール失敗 | transcriptギャップのみで無音検出（VADスキップ） |
| torch がない | `pip install torch torchaudio` を案内 |
| transcript_fixed.json がない | `/supermovie-transcript-fix` を促す |
| カット後の動画が50%未満 | 警告 + カットモードを `silence-only` に変更提案 |
| テロップのフレーム再計算でずれ | cutData.ts のマッピングを再検証 |
| 音声ファイルが16kHzでない | ffmpegで再変換 |

---

## 連携マップ

```
/supermovie-init              ← ヒアリング → プロジェクト作成
    ↓
/supermovie-transcribe        ← 文字起こし（ローカル無料）
    ↓ transcript.json
/supermovie-transcript-fix    ← 誤字修正（辞書 + Claude LLM）
    ↓ transcript_fixed.json
/supermovie-cut               ← ★ここ: 不要区間カット（VAD + LLM分析）
    ↓ cutData.ts
/supermovie-subtitles         ← テロップ＆タイトル生成
    ↓ telopData.ts + titleData.ts
/supermovie-slides            ← スライド生成
    ↓ slideData.ts
/supermovie-narration         ← ナレーション生成
    ↓ narration.wav
/supermovie-image-gen         ← 画像生成 + 配置データ
    ↓ insertImageData.ts
/supermovie-se                ← SE自動配置
    ↓
npm run dev                   ← プレビュー
```
