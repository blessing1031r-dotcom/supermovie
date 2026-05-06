---
name: supermovie-image-gen
description: |
  テロップ・タイトルの内容を分析し、挿入画像を自動生成・配置するスキル。
  Gemini APIで図解・インフォグラフィック・イメージ画像を生成し、
  insertImageData.tsのタイミングデータも自動作成。動画フォーマット連動。
  「画像生成」「挿入画像」「image gen」「図解作成」と言われたときに使用。
argument-hint: [プロジェクトパス]
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent, AskUserQuestion
effort: high
---

# SuperMovie Image Gen — 挿入画像自動生成・配置

Senior visual content designer として、テロップの内容を分析し、
視聴者の理解を助ける画像を自動生成・最適タイミングに配置する。

## ワークフロー概要

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ 1. 分析   │→│ 2. 画像計画│→│ 3. 生成   │→│ 4. 配置   │→│ 5. 検証   │
│ テロップ  │  │ 何をどこに │  │ Gemini API│  │ Data生成  │  │ プレビュー │
│ +タイトル │  │ ヒアリング │  │ 画像作成  │  │ TS書出し  │  │           │
└──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
```

---

## 前提条件チェックリスト

- [ ] `/supermovie-subtitles` でテロップ＆タイトル生成済み
- [ ] `src/テロップテンプレート/telopData.ts` にデータがある
- [ ] `src/Title/titleData.ts` にデータがある
- [ ] `project-config.json` が存在（format/resolution参照）
- [ ] 環境変数 `GEMINI_API_KEY` がセット済み

---

## Phase 1: コンテンツ分析

### 1-1. データ読み込み

- `telopData.ts` — 全テロップのテキスト・スタイル・タイミング
- `titleData.ts` — セグメント（チャプター）構成
- `project-config.json` — format, tone, notes

### 1-2. 画像候補の自動抽出

テロップとタイトルを分析し、**画像が効果的な箇所**を自動判定:

| 判定基準 | 画像タイプ | 例 |
|---------|----------|-----|
| 数字・データが含まれる | `infographic` | 「3つのポイント」「売上が50%増」 |
| 手順・ステップの説明 | `infographic` | 「まず〜、次に〜、最後に〜」 |
| 比較・対比 | `infographic` | 「AとBの違い」「ビフォーアフター」 |
| 抽象的な概念 | `photo` | 「未来のビジョン」「成功のイメージ」 |
| ツール・サービス紹介 | `infographic` | 「ChatGPTとは」「Remotionの特徴」 |
| ネガティブな問題提起 | `overlay` | 「こんな悩みありませんか？」 |
| タイトル切り替わり | なし（タイトル自体が表示） | — |

### 1-3. 候補リスト生成

```json
[
  {
    "insertAt": { "startFrame": 150, "endFrame": 450 },
    "reason": "「3つのポイント」を説明している箇所",
    "suggestedType": "infographic",
    "promptDraft": "3つのポイントを示す図解。1.○○ 2.○○ 3.○○",
    "priority": "high"
  }
]
```

---

## Phase 2: 画像計画（ヒアリング）

候補リストをユーザーに提示し、確認:

```
テロップ内容を分析しました。以下の箇所に画像を挿入する計画です:

1. [0:05-0:15] 📊 インフォグラフィック（高優先）
   → 「3つのポイント」の図解
   → プロンプト案: "3つのポイントを示すモダンな図解..."

2. [0:30-0:45] 🖼️ イメージ画像（中優先）
   → 「AIの未来」のビジュアル
   → プロンプト案: "未来的なAIテクノロジーのイメージ..."

3. [1:20-1:35] 📊 インフォグラフィック（高優先）
   → 「ステップ1→2→3」のフロー図
   → プロンプト案: "3ステップのフローチャート..."

修正・追加・削除があれば教えてください。
OKならこのまま生成します。
```

**ユーザーが調整できるポイント:**
- 画像の追加/削除
- プロンプトの修正
- タイプの変更（infographic ↔ photo ↔ overlay）
- 表示タイミングの変更

---

## Phase 3: Gemini API で画像生成

### 3-1. フォーマット別アスペクト比

project-config.json の `format` に連動:

| format | Gemini API `-a` | 用途 |
|--------|----------------|------|
| `youtube` | `16:9` | 横長（デフォルト） |
| `short` | `9:16` | 縦長 |
| `square` | `1:1` | 正方形 |

### 3-2. タイプ別プロンプトテンプレート

**infographic（図解・データ）:**
```
Create a clean, modern infographic with the following content:
[内容]
Style: minimalist, dark background (#1a1a2e), bright accent colors,
no text (text will be overlaid separately),
aspect ratio: [format], high contrast for video overlay
```

**photo（イメージ画像）:**
```
Photorealistic image of [内容].
Style: cinematic lighting, shallow depth of field,
professional stock photo quality,
aspect ratio: [format]
```

**overlay（問題提起・暗い背景）:**
```
Dark, moody background image representing [内容].
Style: abstract, dark tones with subtle color accents,
suitable as a video overlay with text on top,
aspect ratio: [format]
```

### 3-3. 生成実行

```bash
cd ~/.claude/skills/gemini-api-image && \
python scripts/run.py api_generator.py \
  --prompt "<プロンプト>" \
  -a <アスペクト比> \
  -m pro \
  -o "<PROJECT>/public/images/generated/<filename>.png"
```

**ファイル名規約:**
```
<startSec>s_<type>_<連番>.png
例: 005s_infographic_01.png, 030s_photo_02.png
```

### 3-4. 生成の進捗表示

```
🎨 画像生成中...
  [1/3] 005s_infographic_01.png ... ✅ (12秒)
  [2/3] 030s_photo_02.png ... ✅ (8秒)
  [3/3] 080s_infographic_03.png ... ✅ (15秒)
```

---

## Phase 4: insertImageData.ts 生成

### 4-1. 出力形式

```typescript
import type { ImageSegment } from './types';

const FPS = 30; // Root.tsxの値
const toFrame = (seconds: number) => Math.round(seconds * FPS);

export const insertImageData: ImageSegment[] = [
  {
    id: 1,
    startFrame: toFrame(5),
    endFrame: toFrame(15),
    file: 'generated/005s_infographic_01.png',
    type: 'infographic',
  },
  {
    id: 2,
    startFrame: toFrame(30),
    endFrame: toFrame(45),
    file: 'generated/030s_photo_02.png',
    type: 'photo',
  },
  {
    id: 3,
    startFrame: toFrame(80),
    endFrame: toFrame(95),
    file: 'generated/080s_infographic_03.png',
    type: 'overlay',
  },
];
```

**保存先:** `src/InsertImage/insertImageData.ts`

### 4-2. タイプ別表示ロジック（InsertImage.tsx連携）

| type | 表示方法 |
|------|---------|
| `infographic` | 全画面固定表示 |
| `photo` | 全画面 + Ken Burnsズーム（1.0→1.05） |
| `overlay` | 暗背景(0.7) + 中央配置 |

---

## Phase 5: 検証

### 5-1. バリデーション

| チェック項目 | 条件 | 失敗時の対応 |
|-------------|------|------------|
| 画像ファイル存在 | 全ファイルが `public/images/generated/` にある | 再生成 |
| フレーム重複 | 画像同士が重ならない | 前の画像のendFrameをカット |
| テロップとの共存 | 画像表示中もテロップは読める | overlay時はテロップ位置を確認 |
| 範囲超過 | endFrame ≤ TOTAL_FRAMES | カット |
| 画像枚数の目安 | 動画1分あたり1-3枚 | 多すぎる場合は優先度lowを削除 |

### 5-2. プレビュー確認

```bash
cd <PROJECT> && npm run dev
```

Remotion StudioでMainVideoを確認。画像の表示タイミング・サイズが適切か確認。

---

## 完了時の報告フォーマット

```
✅ 画像生成・配置完了

🎨 生成画像: <N>枚
   - infographic: <n>枚
   - photo: <n>枚
   - overlay: <n>枚
📂 保存先: public/images/generated/

📄 insertImageData.ts 更新済み

次のステップ:
→ npm run dev で画像の表示タイミングを確認
→ /supermovie-se でSE配置（画像出現タイミングにもSE付与）
```

---

## エラーハンドリング

| エラー | 対応 |
|--------|------|
| GEMINI_API_KEY 未設定 | 設定方法を案内。画像なしで続行も提案 |
| Gemini API エラー（レート制限） | 5秒待って再試行。3回失敗で該当画像スキップ |
| 生成画像の品質が低い | プロンプト調整して再生成を提案 |
| telopData.ts が空 | `/supermovie-subtitles` の実行を促す |
| project-config.json なし | デフォルト（youtube 16:9）で続行 |
| gemini-api-image スキルが見つからない | インストール方法を案内 |

---

## 連携マップ

```
/supermovie-init              ← ヒアリング → プロジェクト作成
    ↓
/supermovie-transcribe        ← 文字起こし（ローカル無料）
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
/supermovie-image-gen         ← ★ここ: 画像生成 + 配置データ
    ↓ insertImageData.ts
/supermovie-se                ← SE自動配置
    ↓
npm run dev                   ← プレビュー
```
