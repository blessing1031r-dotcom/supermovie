---
name: supermovie-transcribe
description: |
  動画/音声ファイルからワードタイムスタンプ付きの高精度文字起こしを行うスキル。
  OS・GPU・既存ライブラリを自動検出し、最適なエンジンを自動選択。ヒアリングは最小限。
  話者分離が必要な場合のみAssemblyAIを使用。完全ローカル・無料で動作可能。
  「文字起こし」「transcribe」「書き起こし」「transcript」と言われたときに使用。
argument-hint: <動画/音声ファイルパス> [--speakers 話者数]
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent, AskUserQuestion
effort: high
---

# SuperMovie Transcribe — 高精度文字起こし

Senior speech recognition engineer として、ユーザー環境を自動検出し、
最適なエンジンでワードタイムスタンプ付きの正確な書き起こしを生成する。

## ワークフロー概要

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ 1. 自動検出│→│ 2. ヒアリング│→│ 3. 環境構築│→│ 4. 音声抽出│→│ 5. 文字起こし│→│ 6. 検証  │
│ OS/GPU/Lib│  │ 話者数/言語 │  │ venv+install│ │ ffmpeg   │  │ Whisper等 │  │ JSON保存 │
└──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
  ↑自動             ↑最小限            ↑初回のみ                                    │
  聞かない          2項目だけ          2回目以降スキップ                              ↓
                                                                          transcript.json
```

---

## 前提条件チェックリスト

- [ ] ffmpeg がインストール済み
- [ ] Python 3.9+ がインストール済み
- [ ] 動画/音声ファイルが存在

---

## Phase 1: 環境自動検出（ヒアリング不要）

**ユーザーに聞かずに全て自動判定する。**

### 1-1. OS・チップ検出

```bash
# OS判定
OS=$(uname -s)          # Darwin or Linux or MINGW*(Windows)

# Mac: Apple Silicon or Intel
if [ "$OS" = "Darwin" ]; then
  CHIP=$(uname -m)      # arm64 = Apple Silicon, x86_64 = Intel
fi

# Windows: NVIDIA GPU有無
if [[ "$OS" == MINGW* ]] || [[ "$OS" == MSYS* ]]; then
  nvidia-smi > /dev/null 2>&1 && HAS_GPU=true || HAS_GPU=false
fi
```

### 1-2. 既存ライブラリ検出

```bash
# mlx-whisper チェック
python3 -c "import mlx_whisper" 2>/dev/null && HAS_MLX=true || HAS_MLX=false

# faster-whisper チェック
python3 -c "from faster_whisper import WhisperModel" 2>/dev/null && HAS_FASTER=true || HAS_FASTER=false
```

### 1-3. エンジン自動決定ロジック

```
if Mac Apple Silicon && HAS_MLX:
  → mlx-whisper（即座に実行）
elif Mac Apple Silicon && !HAS_MLX:
  → mlx-whisper をインストールして実行
elif HAS_FASTER:
  → faster-whisper（即座に実行）
else:
  → faster-whisper をインストールして実行

※ 話者分離が必要（speakers >= 2）の場合のみ AssemblyAI
```

### 1-4. 検出結果をログ出力

```
🔍 環境検出:
  OS: macOS (Apple Silicon M2)
  mlx-whisper: インストール済み ✓
  faster-whisper: 未インストール
  → エンジン: mlx-whisper (large-v3)
```

---

## Phase 2: ヒアリング（最小限・2項目のみ）

**自動検出できない情報だけ聞く:**

```
文字起こしの設定を確認させてください:

1. 話者は何人？
   → 1人 → ローカルWhisper（無料）
   → 2人以上 → AssemblyAI（話者分離）

2. 言語は？
   → 日本語（デフォルト） / 英語 / 自動検出
```

**省略条件:**
- `$ARGUMENTS` に `--speakers N` がある → 話者数は聞かない
- `project-config.json` に前回設定がある → 全てスキップ

---

## Phase 3: 環境構築（初回のみ・venv使用）

### 3-1. venv作成（システムPython汚染防止）

```bash
# プロジェクト内にvenv作成（既にあればスキップ）
if [ ! -d "<PROJECT>/.venv" ]; then
  python3 -m venv "<PROJECT>/.venv"
fi
source "<PROJECT>/.venv/bin/activate"
```

### 3-2. インストール

**Mac Apple Silicon:**
```bash
"<PROJECT>/.venv/bin/pip" install --upgrade pip
"<PROJECT>/.venv/bin/pip" install mlx-whisper
```

**Mac Intel / Windows:**
```bash
"<PROJECT>/.venv/bin/pip" install --upgrade pip
"<PROJECT>/.venv/bin/pip" install faster-whisper
```

### 3-3. インストール確認

```bash
"<PROJECT>/.venv/bin/python3" -c "import mlx_whisper; print('OK')"
# or
"<PROJECT>/.venv/bin/python3" -c "from faster_whisper import WhisperModel; print('OK')"
```

### 3-4. フォールバックチェーン

```
mlx-whisper インストール失敗（Intel Macの場合）
  → faster-whisper にフォールバック

faster-whisper CUDA エラー（Windows）
  → device='cpu', compute_type='int8' にフォールバック

モデルダウンロード失敗
  → large-v3 → medium にダウングレード

メモリ不足（8GB未満のマシン）
  → medium → small にダウングレード
```

---

## Phase 4: 音声抽出

### 4-1. 入力ファイル判定

```bash
# 拡張子で判定
EXT="${INPUT_FILE##*.}"
case "$EXT" in
  mp4|mov|mkv|webm|avi) TYPE="video" ;;
  mp3|wav|m4a|flac|ogg) TYPE="audio" ;;
  *) echo "未対応形式: $EXT" && exit 1 ;;
esac
```

### 4-2. 音声抽出

```bash
# 音声ストリーム確認（無音動画チェック）
HAS_AUDIO=$(ffprobe -v quiet -select_streams a -show_entries stream=codec_type \
  -of csv=p=0 "<INPUT_FILE>" | head -1)

if [ -z "$HAS_AUDIO" ]; then
  echo "⚠️ この動画に音声トラックがありません"
  exit 1
fi

# 抽出（Whisper最適: 16kHz mono WAV）
ffmpeg -y -i "<INPUT_FILE>" \
  -vn -acodec pcm_s16le -ar 16000 -ac 1 \
  "<PROJECT>/transcript_audio.wav"

# ファイルサイズ・長さ確認
DURATION=$(ffprobe -v quiet -show_entries format=duration \
  -of csv=p=0 "<PROJECT>/transcript_audio.wav")
echo "⏱️ 音声長: ${DURATION}秒"
```

### 4-3. 長時間動画の警告

```
DURATION > 3600秒（1時間）の場合:
  → 「長い動画です。large-v3で約X分かかる見込みです。mediumに下げますか？」と確認

DURATION > 7200秒（2時間）の場合:
  → mediumモデルをデフォルト推奨
  → 分割処理を提案（30分ごとに分割→結合）
```

---

## Phase 5: 文字起こし実行

### 5-1. Pythonスクリプトを別ファイルとして生成

**インラインbashの`-c "..."`は使わない。** 一時Pythonファイルを生成して実行する。

#### mlx-whisper用スクリプト

`<PROJECT>/transcribe_runner.py` を生成:

```python
#!/usr/bin/env python3
"""SuperMovie Transcribe Runner (mlx-whisper)"""
import mlx_whisper
import json
import sys

audio_path = sys.argv[1]
output_path = sys.argv[2]
language = sys.argv[3] if len(sys.argv) > 3 else 'ja'
model_name = sys.argv[4] if len(sys.argv) > 4 else 'mlx-community/whisper-large-v3-mlx'

print(f"🎙️ 文字起こし開始: {audio_path}")
print(f"📦 モデル: {model_name}")
print(f"🌐 言語: {language}")

result = mlx_whisper.transcribe(
    audio_path,
    path_or_hf_repo=model_name,
    language=language if language != 'auto' else None,
    word_timestamps=True,
    verbose=False,
)

words = []
for seg in result.get('segments', []):
    for w in seg.get('words', []):
        text = w.get('word', '').strip()
        if not text:
            continue
        words.append({
            'text': text,
            'start': round(w['start'] * 1000),
            'end': round(w['end'] * 1000),
            'confidence': round(w.get('probability', 0.0), 3),
        })

segments_list = result.get('segments', [])
duration_ms = round(segments_list[-1]['end'] * 1000) if segments_list else 0

output = {
    'engine': 'mlx-whisper',
    'model': model_name.split('/')[-1],
    'language': result.get('language', language),
    'duration_ms': duration_ms,
    'text': result.get('text', '').strip(),
    'words': words,
    'segments': [
        {
            'text': seg['text'].strip(),
            'start': round(seg['start'] * 1000),
            'end': round(seg['end'] * 1000),
        }
        for seg in segments_list
    ],
}

with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"✅ 完了: {len(words)}ワード, {len(output['segments'])}セグメント, {duration_ms/1000:.1f}秒")
```

#### faster-whisper用スクリプト

`<PROJECT>/transcribe_runner.py` を生成:

```python
#!/usr/bin/env python3
"""SuperMovie Transcribe Runner (faster-whisper)"""
from faster_whisper import WhisperModel
import json
import sys

audio_path = sys.argv[1]
output_path = sys.argv[2]
language = sys.argv[3] if len(sys.argv) > 3 else 'ja'
model_size = sys.argv[4] if len(sys.argv) > 4 else 'large-v3'

print(f"🎙️ 文字起こし開始: {audio_path}")
print(f"📦 モデル: {model_size}")
print(f"🌐 言語: {language}")

try:
    model = WhisperModel(model_size, device='auto', compute_type='auto')
except Exception:
    print("⚠️ GPU利用不可。CPUモードで再試行...")
    model = WhisperModel(model_size, device='cpu', compute_type='int8')

lang_arg = language if language != 'auto' else None
segments_iter, info = model.transcribe(
    audio_path,
    language=lang_arg,
    word_timestamps=True,
    vad_filter=True,
)

words = []
segments = []
full_text = ''
count = 0

for segment in segments_iter:
    count += 1
    if count % 10 == 0:
        print(f"  処理中... {count}セグメント完了")
    segments.append({
        'text': segment.text.strip(),
        'start': round(segment.start * 1000),
        'end': round(segment.end * 1000),
    })
    full_text += segment.text
    for w in (segment.words or []):
        text = w.word.strip()
        if not text:
            continue
        words.append({
            'text': text,
            'start': round(w.start * 1000),
            'end': round(w.end * 1000),
            'confidence': round(w.probability, 3),
        })

duration_ms = segments[-1]['end'] if segments else 0

output = {
    'engine': 'faster-whisper',
    'model': model_size,
    'language': info.language,
    'duration_ms': duration_ms,
    'text': full_text.strip(),
    'words': words,
    'segments': segments,
}

with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"✅ 完了: {len(words)}ワード, {len(segments)}セグメント, {duration_ms/1000:.1f}秒")
```

### 5-2. 実行コマンド

```bash
"<PROJECT>/.venv/bin/python3" "<PROJECT>/transcribe_runner.py" \
  "<PROJECT>/transcript_audio.wav" \
  "<PROJECT>/transcript.json" \
  "ja" \
  "large-v3"
```

### 5-3. AssemblyAI（話者2人以上の場合のみ）

AssemblyAIは既存スキルのロジックと同じ。ただし出力スキーマを統一:
- `words[].speaker` フィールドを追加
- `speakers` 配列を追加

---

## Phase 6: 検証＆保存

### 6-1. transcript.json 出力スキーマ

```json
{
  "engine": "mlx-whisper",
  "model": "whisper-large-v3-mlx",
  "language": "ja",
  "duration_ms": 60000,
  "text": "全文テキスト...",
  "words": [
    {
      "text": "こんにちは",
      "start": 1200,
      "end": 1800,
      "confidence": 0.95
    }
  ],
  "segments": [
    {
      "text": "こんにちは、今日はAIについてお話しします",
      "start": 1200,
      "end": 5400
    }
  ]
}
```

### 6-2. バリデーション

| チェック項目 | 条件 | 失敗時の対応 |
|-------------|------|------------|
| words が空でない | `words.length > 0` | エラーログ確認、音声レベル確認 |
| タイムスタンプ順序 | `words[n].start <= words[n+1].start` | ソートして修正 |
| start < end | 全wordで成立 | end = start + 50 に修正 |
| 信頼度範囲 | `0 <= confidence <= 1` | クランプ |
| duration整合性 | 最後のword.end ≒ ffprobeのduration | duration_msを更新 |
| テキスト空白 | text.length > 0 | 無音動画の可能性を通知 |

### 6-3. 一時ファイル削除

```bash
rm -f "<PROJECT>/transcribe_runner.py"
# transcript_audio.wav は後続スキルで使う可能性があるため残す
```

### 6-4. 環境設定の保存

`project-config.json` に追記:
```json
{
  "transcribe": {
    "os": "darwin-arm64",
    "engine": "mlx-whisper",
    "model": "large-v3",
    "language": "ja",
    "venv": ".venv"
  }
}
```

---

## 完了時の報告フォーマット

```
✅ 文字起こし完了

🔍 環境: macOS Apple Silicon / mlx-whisper / large-v3
📝 テキスト: <最初の80文字>...
🔤 ワード数: <N>個（低信頼度 <n>個 = <X>%）
📋 セグメント数: <N>個
⏱️ 音声長: <duration>秒

📄 保存先: <PROJECT>/transcript.json

次のステップ:
→ /supermovie-transcript-fix で誤字修正
```

---

## エラーハンドリング

| エラー | 対応 |
|--------|------|
| ffmpeg 未インストール | `brew install ffmpeg` (Mac) / `choco install ffmpeg` (Win) を案内 |
| Python 3.9未満 | バージョンアップを案内 |
| venv作成失敗 | `python3 -m ensurepip` → 再試行 |
| pip install 失敗 | pip upgrade → 再実行。それでもダメならエラーログ全文表示 |
| mlx-whisper がIntel Macで失敗 | faster-whisper に自動フォールバック |
| faster-whisper CUDA エラー | `device='cpu', compute_type='int8'` に自動フォールバック |
| モデルダウンロード失敗 | ネットワーク確認 → `medium` モデルで再試行 |
| メモリ不足 | `medium` → `small` に自動ダウングレード |
| 音声なし動画 | ffprobeで音声ストリーム確認、ユーザーに通知 |
| 長時間動画（1h超） | 所要時間見積もり表示、モデルダウングレード提案 |
| ASSEMBLYAI_API_KEY 未設定 | ローカルエンジンを提案（話者1人なら不要と案内） |
| 文字起こし結果が空 | 音声レベル確認、言語設定確認、モデル変更提案 |

---

## 連携マップ

```
/supermovie-init              ← ヒアリング → プロジェクト作成
    ↓
/supermovie-transcribe        ← ★ここ: 文字起こし（ローカル無料）
    ↓ transcript.json
/supermovie-transcript-fix    ← 誤字修正（辞書 + Claude LLM）
    ↓ transcript_fixed.json
/supermovie-cut               ← 不要区間カット（VAD + LLM分析）
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
