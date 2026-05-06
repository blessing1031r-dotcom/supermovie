---
name: supermovie-se
description: |
  SuperMovieプロジェクトのテロップデータを分析し、効果音（seData.ts）を
  自動配置するスキル。テロップのスタイル・内容に応じたSE選択、
  バリエーション管理、音量バランス調整を自動実行。
  「SE配置」「効果音つけて」「supermovie se」と言われたときに使用。
argument-hint: [プロジェクトパス]
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
effort: medium
---

# SuperMovie SE — 効果音自動配置

Senior sound designer として、テロップの内容とタイミングを分析し、
動画のテンポを引き立てる効果音を自動配置する。

## ワークフロー概要

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ 1. データ  │→│ 2. SE素材 │→│ 3. 配置   │→│ 4. 書き出し│
│ 読み込み  │  │ 準備     │  │ ロジック  │  │ + 検証    │
└──────────┘   └──────────┘   └──────────┘   └──────────┘
```

---

## 前提条件チェックリスト

- [ ] `/supermovie-subtitles` でテロップ生成済み
- [ ] `src/テロップテンプレート/telopData.ts` にデータあり
- [ ] `public/se/` にSEファイル配置済み
- [ ] /supermovie-image-gen で画像生成済み（任意）
- [ ] src/InsertImage/insertImageData.ts が存在（任意：画像出現タイミングにSE付与）

---

## Phase 1: データ読み込み

### 1-1. テロップデータ
`src/テロップテンプレート/telopData.ts` から全セグメントを取得。

### 1-2. タイトルデータ
`src/Title/titleData.ts` からセグメント境界を取得。

### 1-3. プロジェクト設定
`project-config.json` の `tone` を取得（SE密度の調整に使用）。

---

## Phase 2: SE素材準備

### 2-1. 素材チェック
`public/se/` の中身を確認。空の場合は共通素材をコピー:

```bash
cp /Users/tonodukaren/movie/YT/03_AI×動画編集革命_YT/public/se/* "<PROJECT>/public/se/"
```

### 2-2. SE素材カタログ

```
┌────────────────────────┬────────┬──────────────────────┐
│ ファイル名               │ 略称   │ 用途                  │
├────────────────────────┼────────┼──────────────────────┤
│ パッ (1).mp3            │ POP    │ 汎用アクセント         │
│ 決定ボタンを押す2.mp3    │ BTN-2  │ 決定・強調             │
│ 決定ボタンを押す3.mp3    │ BTN-3  │ 決定バリエーション      │
│ 決定ボタンを押す4.mp3    │ BTN-4  │ 決定バリエーション      │
│ 決定ボタンを押す12.mp3   │ BTN-12 │ 決定バリエーション      │
│ ニュッ1.mp3             │ WSH-1  │ スライド・登場         │
│ ニュッ2.mp3             │ WSH-2  │ スライドバリエーション   │
│ ニュッ3.mp3             │ WSH-3  │ スライドバリエーション   │
│ カーソル移動2.mp3        │ CUR-2  │ 軽いクリック           │
│ カーソル移動7.mp3        │ CUR-7  │ クリックバリエーション   │
│ カーソル移動8.mp3        │ CUR-8  │ クリックバリエーション   │
│ 間抜け3.mp3             │ COMIC  │ コミカル・ツッコミ      │
│ 水滴3.mp3              │ DROP   │ 感動・しみじみ         │
│ 涙のしずく.mp3          │ TEAR   │ 悲しみ・共感           │
│ クイズ不正解1.mp3        │ BUZZ   │ 失敗・ブザー           │
└────────────────────────┴────────┴──────────────────────┘
```

---

## Phase 3: SE配置ロジック

### 3-1. 配置密度（トーン別）

| トーン | SE密度（テロップ比） | 特徴 |
|--------|---------------------|------|
| プロフェッショナル | 30-40% | 控えめ、POP中心 |
| エンタメ | 50-70% | 積極的、バリエーション豊富 |
| カジュアル | 40-50% | 程よい、自然な配置 |
| 教育的 | 25-35% | 最小限、邪魔しない |

### 3-2. スタイル別SE選択マトリクス

```
┌──────────┬──────────────────────────────┬────────┐
│ style    │ SE候補（ランダム選択）          │ volume │
├──────────┼──────────────────────────────┼────────┤
│ normal   │ POP, WSH-1, WSH-2, WSH-3    │ 0.30   │
│ emphasis │ BTN-2, BTN-3, BTN-4, BTN-12 │ 0.35   │
│ warning  │ BUZZ, COMIC                  │ 0.25   │
│ success  │ BTN-2, BTN-3                 │ 0.35   │
│ (感動文脈)│ DROP, TEAR                   │ 0.25   │
└──────────┴──────────────────────────────┴────────┘
```

### 3-3. 配置ルール（優先度順）

**必ずSEを置く（優先度: 高）:**
1. `emphasis` テロップの startFrame → BTN系
2. `warning` テロップの startFrame → BUZZ or COMIC
3. `success` テロップの startFrame → BTN系
4. タイトル切り替わりフレーム → CUR系
5. 画像出現フレーム（insertImageData.tsのstartFrame）→ WSH系（スライド音）

**間引きルールで配置（優先度: 中）:**
5. `normal` テロップ → 2〜3個に1個の頻度で POP or WSH系

**配置しない（優先度: 低）:**
6. 直前のSEから10フレーム以内の場合 → スキップ
7. 動画の最後5秒 → 余韻のため控える

### 3-4. バリエーション管理

**連続回避ルール:**
- 同一SEファイルが **3回連続** しない
- BTN系は2, 3, 4, 12をローテーション
- WSH系は1, 2, 3をローテーション
- CUR系は2, 7, 8をローテーション

**実装: ラウンドロビン方式**
```
BTN_ROTATION = [BTN-2, BTN-3, BTN-4, BTN-12]  // index循環
WSH_ROTATION = [WSH-1, WSH-2, WSH-3]
CUR_ROTATION = [CUR-2, CUR-7, CUR-8]
```

---

## Phase 4: 書き出し＆検証

### 4-1. seData.ts 出力形式

```typescript
import type { SoundEffect } from './SEPlayer';

export const seData: SoundEffect[] = [
  { id: 1, startFrame: 45, file: 'パッ (1).mp3', volume: 0.3 },
  { id: 2, startFrame: 120, file: '決定ボタンを押す2.mp3', volume: 0.35 },
  // ...
];
```

### 4-2. バリデーション（必須）

| チェック項目 | 条件 | 失敗時の対応 |
|-------------|------|------------|
| フレーム重複 | 同一フレームにSEなし | 後のSEを+2フレームずらす |
| 範囲超過 | startFrame < TOTAL_FRAMES | 超過分を削除 |
| ID連番 | 1から連番 | 採番し直し |
| 密度チェック | SE数 / テロップ数 が目標範囲内 | 警告表示（自動修正なし） |
| ファイル存在 | public/se/ に全ファイルあり | 不在ファイルを報告 |

---

## 完了時の報告フォーマット

```
✅ SE配置完了

🔊 SE数: <N>個（テロップ比 <X>%）
   - POP: <n> / BTN: <n> / WSH: <n> / CUR: <n> / BUZZ: <n> / 他: <n>
🎚️ 音量レンジ: 0.25〜0.35

次のステップ:
→ npm run dev でプレビュー確認
→ 気になるSEがあれば seData.ts を手動調整
```

---

## エラーハンドリング

| エラー | 対応 |
|--------|------|
| telopData が空 | `/supermovie-subtitles` の実行を促す |
| SE素材が不足 | 共通素材のコピーを提案 |
| project-config.json なし | デフォルト（カジュアル密度40%）で続行 |

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
/supermovie-image-gen         ← 画像生成 + 配置データ
    ↓ insertImageData.ts
/supermovie-se                ← ★ここ: SE自動配置
    ↓
npm run dev                   ← プレビュー
```
