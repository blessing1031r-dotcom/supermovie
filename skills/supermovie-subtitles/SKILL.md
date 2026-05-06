---
name: supermovie-subtitles
description: |
  transcript_fixed.jsonからテロップ（telopData.ts）とタイトル（titleData.ts）を
  自動生成するスキル。LLM意味分割 + 改行処理 + 読了時間計算 + フォーマット連動で
  視聴者が自然に読めるテロップを生成。文字起こし自体は行わない。
  「テロップ生成」「字幕生成」「supermovie subtitles」と言われたときに使用。
argument-hint: [プロジェクトパス]
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent
effort: high
---

# SuperMovie Subtitles — テロップ＆タイトル自動生成

Senior video subtitle designer として、修正済み文字起こしデータを
LLM意味分割 → 改行処理 → 読了時間チェック → スタイル割当の4段階で
視聴者が自然に読めるテロップに変換する。

**注意: このスキルは文字起こしを行わない。** `/supermovie-transcribe` → `/supermovie-transcript-fix` で事前に完了していること。

## ワークフロー概要

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ 1. 設定   │→│ 2. LLM    │→│ 3. 改行   │→│ 4. 読了時間│→│ 5. スタイル│→│ 6. 検証   │
│ 読み込み  │  │ 意味分割  │  │ 処理     │  │ チェック  │  │ 割り当て  │  │ +書出し  │
└──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
                  ↑ ★核心                ↑ ★核心
```

---

## 前提条件チェックリスト

- [ ] `/supermovie-init` でプロジェクト生成済み
- [ ] `/supermovie-transcribe` → `/supermovie-transcript-fix` 済み
- [ ] `transcript_fixed.json` が存在し `words` 配列がある
- [ ] `project-config.json` が存在（format/tone参照）

---

## Phase 1: 設定読み込み

### 1-1. フォーマット別パラメータ決定

`project-config.json` の `format` から全パラメータを決定:

```
┌─────────────┬───────────┬───────────┬───────────┐
│ パラメータ    │ youtube   │ short     │ square    │
│              │ (16:9)    │ (9:16)    │ (1:1)     │
├─────────────┼───────────┼───────────┼───────────┤
│ 1行最大文字数 │ 18文字    │ 12文字    │ 15文字    │
│ 2行化の閾値  │ 15文字超  │ 10文字超  │ 12文字超  │
│ テロップ最大  │ 36文字    │ 24文字    │ 30文字    │
│ 最大行数     │ 2行       │ 2行       │ 2行       │
│ fontSize     │ 80        │ 60        │ 70        │
│ 読了速度     │ 5文字/秒  │ 4文字/秒  │ 4.5文字/秒 │
│ 最小表示時間  │ 1.5秒     │ 1.5秒     │ 1.5秒     │
│ 最大表示時間  │ 6秒       │ 5秒       │ 5.5秒     │
└─────────────┴───────────┴───────────┴───────────┘
```

### 1-2. その他の設定読み込み

- `Root.tsx` → `FPS`, `TOTAL_FRAMES`
- `project-config.json` → `tone`, `notes`（キーワード）
- `transcript_fixed.json` → `words`, `segments`

---

## Phase 2: 意味分割 (BudouX deterministic + LLM optional plan)

**Codex Phase 2b design (2026-05-04): BudouX first、LLM は optional な補正レイヤー。**
**実行コマンド (skill が orchestrate):** `python3 <PROJECT>/scripts/build_telop_data.py`
- 既定: `template/scripts/budoux_split.mjs` 経由で BudouX が segment を文節列に分解 → max_chars 以内で連結 → telop 単位
- `--baseline` フラグで Phase 1 旧ロジック (24/36 字機械分割) と比較可能
- LLM 経路は将来オプション化、起動時の `telop_plan.json` を script が validate して invalid なら BudouX に戻す設計 (現時点未実装)

### 2-0. なぜ BudouX first か (Phase 2b 起点)

Phase 1 は機械的 24 字制限で「設計できる」が「設計で / きるパイプラインとして」、「素材です」が「素材で / す」のように **意味境界を無視して切れた** (project_supermovie_phase1_lessons.md 弱点 #6 / #7)。
BudouX (Google 製、Markov 風 model) は日本語を意味単位で分割するので、`["ワークフローと", "して", "設計できる", "パイプラインと", ...]` のように 1 phrase = 意味単位を返す。
これを `max_chars` 以内に連結すれば「設計できる」が分かれない。

### 2-1. 入力データ準備

transcript_fixed.json の `words` を `segments` 単位でグループ化。
各 segment.text を BudouX の `loadDefaultJapaneseParser().parse()` に渡す (`scripts/budoux_split.mjs --in input.json --out phrases.json`)。
LLM optional 経路を使う場合のみ、各セグメント（文）を Claude LLMに送る。

### 2-2. LLMへのプロンプト

```
あなたはYouTube動画のテロップ分割の専門家です。
以下のテキストをテロップ表示用に分割してください。

## 絶対ルール
1. 1テロップ最大 <MAX_CHARS> 文字（フォーマット: <format>）
2. 意味のまとまりで分割する
3. 以下の位置で切る（優先度順）:
   a. 文末（。！？）
   b. 接続助詞の後（〜して、〜ので、〜から、〜けど、〜ことで）
   c. 読点（、）の後
   d. 「〜を」「〜に」「〜で」「〜が」「〜は」の後
4. 以下の位置では絶対に切らない:
   a. 助詞の直前（「時間 | を」→ NG、「時間を | 」→ OK）
   b. 用言の途中（「自動 | 化する」→ NG）
   c. 固有名詞の途中（「Claude | Code」→ NG）
   d. 数字と助数詞の間（「3 | つ」→ NG）
5. 句読点（。、）はテロップでは省略する

## 入力テキスト
<セグメントテキスト>

## 入力words（タイムスタンプ参照用）
<words JSON配列>

## 出力形式
以下のJSON配列で返してください:
[
  {
    "text": "AIを使って動画編集を自動化することで",
    "wordIndices": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
  },
  {
    "text": "作業時間を大幅に削減できます",
    "wordIndices": [10, 11, 12, 13, 14, 15, 16]
  }
]
```

**wordIndices でタイムスタンプと紐付け:**
- startFrame = words[wordIndices[0]].start のフレーム変換
- endFrame = words[wordIndices[最後]].end のフレーム変換

### 2-3. 並列処理

- セグメント数が10以上 → Agentツールで3並列
- 10未満 → 直列実行

### 2-4. フォールバック（BudouXベース）

LLMが不正なJSONを返した場合 → **BudouXで文節分割しテロップ単位にまとめる**:

```typescript
import { loadDefaultJapaneseParser } from 'budoux';
const parser = loadDefaultJapaneseParser();

// BudouXで文節分割
const phrases = parser.parse(segmentText);
// → ['AIを', '使って', '動画編集を', '自動化する', 'ことで', '作業時間を', '大幅に', '削減できます']

// MAX_CHARS以内でフレーズをまとめてテロップ化
function buildTelops(phrases: string[], maxChars: number): string[] {
  const telops: string[] = [];
  let current = '';
  for (const phrase of phrases) {
    if ((current + phrase).length <= maxChars) {
      current += phrase;
    } else {
      if (current) telops.push(current);
      current = phrase;
    }
  }
  if (current) telops.push(current);
  return telops;
}
```

**BudouXフォールバックの利点:**
- 旧方式（句読点+正規表現）より遥かに自然な分割
- 助詞の途中で切れない（文節単位のため）
- ローカル処理、15-20KB、ミリ秒単位

---

## Phase 3: BudouX改行処理（★核心）

**BudouX（Google開発の文節分割ライブラリ）を使用して、テロップ内の自然な改行位置を決定する。**

### 3-0. BudouXとは

```
従来の形態素解析（MeCab等）: 「動画」「編集」「を」 ← 細かすぎる
BudouX（文節分割）:          「動画編集を」         ← テロップ改行に最適

- Google開発、Apache 2.0ライセンス
- 約15-20KB、超軽量
- npm: budoux（template/package.jsonに追加済み）
- Chrome 119 / Android 14 にもネイティブ搭載
```

### 3-1. 改行判定ルール

```
テロップテキストの文字数が 2行化の閾値 を超えたら改行する

youtube: 15文字超 → 2行
short:   10文字超 → 2行
square:  12文字超 → 2行
```

### 3-2. BudouXによる改行位置の決定

```typescript
import { loadDefaultJapaneseParser } from 'budoux';
const parser = loadDefaultJapaneseParser();

function addLineBreak(text: string, maxCharsPerLine: number): string {
  // 閾値以下なら1行のまま
  if (text.length <= maxCharsPerLine) return text;

  // BudouXで文節分割
  const phrases = parser.parse(text);
  // 例: ['AIを', '使って', '動画編集を', '自動化する', 'ことで']

  // 2行に分割: できるだけ均等になる位置で分割
  const totalLen = text.length;
  const targetHalf = totalLen / 2;

  let bestSplitIdx = 0;
  let bestDiff = Infinity;
  let accumulated = '';

  for (let i = 0; i < phrases.length - 1; i++) {
    accumulated += phrases[i];
    const diff = Math.abs(accumulated.length - targetHalf);
    if (diff < bestDiff && accumulated.length <= maxCharsPerLine) {
      bestDiff = diff;
      bestSplitIdx = i;
    }
  }

  const line1 = phrases.slice(0, bestSplitIdx + 1).join('');
  const line2 = phrases.slice(bestSplitIdx + 1).join('');

  // 行バランスチェック
  const shorter = Math.min(line1.length, line2.length);
  const longer = Math.max(line1.length, line2.length);
  if (longer > 0 && shorter / longer < 0.4) {
    return text; // バランス悪い → 改行しない
  }

  // 各行が maxCharsPerLine を超えないか確認
  if (line2.length > maxCharsPerLine) {
    return text; // 2行目が長すぎ → 改行しない
  }

  return `${line1}\n${line2}`;
}
```

### 3-3. 行バランスチェック

```
NG: 「AIを使って動画編集を自動化することで」（20文字）
    + 「削減」（2文字）
    → 20:2 はアンバランス ❌

OK: 「AIを使って」（6文字）
    + 「動画編集を自動化することで」（12文字）
    → 6:12 許容範囲（短い行:長い行 = 1:2 以内）✅

ルール: shorter / longer ≥ 0.4（4割以上）
        これを満たさない場合は改行しない
```

### 3-4. BudouXが保証すること

```
✅ 助詞の途中で切れない（文節単位のため）
  「動画編集を | 自動化する」← 「を」の後で切れる（自然）
  「動画編集 | を自動化する」← これは起きない

✅ 固有名詞がまとまる
  「Claude Code」← 1フレーズとして扱われやすい

✅ 用言の途中で切れない
  「自動化する」← 1フレーズ（「自動化 | する」にならない）
```

### 3-5. 改行しないケース

- 文字数が閾値以下 → 1行のまま
- 行バランスが0.4未満 → 1行のまま
- 2行目がmaxCharsPerLine超 → 1行のまま

---

## Phase 4: 読了時間チェック

### 4-1. 読了時間の計算

```
必要表示時間 = テキスト文字数 / 読了速度（文字/秒） + バッファ0.5秒

youtube: 「動画編集を自動化する」(10文字) → 10/5 + 0.5 = 2.5秒
short:   「動画編集を自動化する」(10文字) → 10/4 + 0.5 = 3.0秒
```

### 4-2. 表示時間の調整

```
┌─────────────────────────────────────────────────────┐
│ 計算した必要表示時間 vs 実際のword区間                   │
│                                                      │
│ 実際 > 必要 → そのまま（音声に余裕がある）              │
│ 実際 < 必要 → 以下の対応:                              │
│   a. endFrameを延長（次のテロップのstartFrameまで）     │
│   b. それでも足りない → テロップを2つに分割              │
│   c. 分割できない → 最小表示時間を保証して次に重ねる     │
└─────────────────────────────────────────────────────┘
```

### 4-3. テロップ密度チェック

```
動画全体の表示率 = テロップ表示フレーム数 / TOTAL_FRAMES

推奨: 60-80%（常に表示ではなく、息つく間がある）
90%超: 警告「テロップが多すぎます。間を空けることを推奨」
50%未満: 警告「テロップが少ないかもしれません」
```

---

## Phase 5: スタイル + templateId 自動割り当て (Phase 2 で registry 統合)

**Codex Phase 2 design 推奨 (2026-05-04): LLM は意味分割のみ、style 判定は deterministic、templateId は config lookup。**

### 5-0. style → templateId 解決ロジック

各 telop に **`style`** (deterministic 判定、後述 5-1) と **`templateId`** (registry 参照) の両方を出力する。
templateId は project-config.json の `telopStyle.{main, emphasis, negative}` (displayName) を `findTemplateIdByDisplayName()` (`telopTemplateRegistry.tsx`) で解決する。

| style | telopStyle 参照先 | 例 (デフォルト) |
|-------|-------------------|-----------------|
| `normal` | `telopStyle.main` | `'WhiteBlueTeleopV2'` (= 白青テロップver2) |
| `emphasis` | `telopStyle.emphasis` | `'OrangeGradation'` |
| `warning` | `telopStyle.negative` | `'BlackPurpleGradation'` |
| `success` | `telopStyle.emphasis` (fallback、SE では別 SE 扱い) | `'OrangeGradation'` |

`success` は `style` フィールドとして残す (supermovie-se が SE 選択で別 sound 扱い)。templateId は emphasis と同じ。

### 5-1. スタイル配分テーブル (deterministic 判定)

| 判定条件 | style | animation | 比率目安 | legacy template |
|---------|-------|-----------|---------|-----------------|
| 通常の文 | normal | fadeOnly | 60-70% | 2 |
| 疑問文（？） | normal | slideIn | 5-10% | 2 |
| 強調キーワード含む | emphasis | slideIn | 10-15% | 1 or 6 |
| ネガティブ表現 | warning | slideIn | 5-10% | 4 or 5 |
| ポジティブ表現 | success | fadeOnly | 5-10% | 3 |
| 最重要メッセージ | emphasis | charByChar | 1-3% | 1 |

**legacy `template` (1..6) は telopId が解決できない時の fallback として TelopSegment にも残す**。templateId が指定されていれば TelopPlayer が registry 経路を優先する (`telopTypes.ts` 参照)。

### 5-2. キーワード辞書

**強調（emphasis）:**
```
重要, ポイント, すごい, 注目, 革命, 最強, 必見, 衝撃, 驚き,
本質, 秘密, 真実, 鍵, コツ, 裏技, 必須, 絶対
+ project-config.json の notes のキーワード
```

**ネガティブ（warning）:**
```
問題, 失敗, 難しい, 危険, 注意, やばい, 最悪, 損, 無駄,
間違い, リスク, 落とし穴, 罠, 搾取, 地獄
```

**ポジティブ（success）:**
```
解決, 成功, できる, 簡単, 効果, 結果, 実現, 達成, 完成,
自動化, 効率, 時短, 無料, 利益, 成長
```

### 5-3. トーン別アニメーション調整

| トーン | fadeOnly | slideIn | charByChar | slideFromLeft |
|--------|---------|---------|-----------|--------------|
| プロフェッショナル | 70% | 25% | 0% | 5% |
| エンタメ | 40% | 35% | 10% | 15% |
| カジュアル | 50% | 25% | 5% | 20% |
| 教育的 | 75% | 20% | 0% | 5% |

### 5-4. 句読点の除去

テロップ表示用テキストから句読点を除去:
```
「今日は、AIについてお話しします。」
→ 「今日はAIについてお話しします」

例外: ！？は残す（感情表現として有効）
```

---

## Phase 6: タイトルデータ生成

transcript_fixed.json の `segments` を俯瞰し、話題の転換点を検出。

### 分割基準
- 5秒以上の無音
- 話題の切り替わり（「次に」「それでは」「ここからは」等）
- 質問から回答への転換
- 目安: 5〜15セグメント

### タイトル生成ルール
- 最大15文字
- キャッチーかつ内容を的確に表現
- `TitleSegment` 型で出力

---

## Phase 7: ファイル書き込み＆検証

### 7-1. telopData.ts 出力

```typescript
import type { TelopSegment } from './telopTypes';

export const FPS = 30;
export const TOTAL_FRAMES = 9750;

export const telopData: TelopSegment[] = [
  {
    id: 1,
    startFrame: 45,
    endFrame: 120,
    text: 'AIを使って\n動画編集を自動化することで',  // ★改行入り
    style: 'normal',
    animation: 'fadeOnly',
  },
  {
    id: 2,
    startFrame: 122,
    endFrame: 210,
    text: '作業時間を\n大幅に削減できます',  // ★改行入り
    style: 'success',
    animation: 'fadeOnly',
  },
];
```

**保存先:** `src/テロップテンプレート/telopData.ts`

### 7-2. titleData.ts 出力

**保存先:** `src/Title/titleData.ts`

### 7-3. バリデーション

| チェック項目 | 条件 | 失敗時の対応 |
|-------------|------|------------|
| フレーム順序 | startFrame < endFrame | endFrameを+1補正 |
| フレーム重複 | テロップ同士が重ならない | 前のendFrameをカット |
| 範囲超過 | endFrame ≤ TOTAL_FRAMES | カット |
| 読了時間 | 表示時間 ≥ 文字数/読了速度+0.5 | endFrame延長 or 分割 |
| 最小表示時間 | duration ≥ 1.5秒 | 延長 |
| 1行文字数 | 各行 ≤ 1行最大文字数 | 改行位置を再調整 |
| 行バランス | 短い行/長い行 ≥ 0.4 | 改行位置を再調整 |

### 7-4. KPI ゲート (Codex Phase 2b Q3、BudouX 統合 verify 用)

`python3 scripts/compare_telop_split.py <baseline.ts> <new.ts>` で baseline (旧 24 字機械分割) と new (BudouX) を比較する:

| KPI | ゲート | 意味 |
|-----|-------|------|
| `hard_word_split_count` | == 0 | telop 境界が transcript の word.text 途中に入った件数 |
| `linebreak_inside_preserve_count` | == 0 | preserve 語 (Claude / Code 等) の途中で改行 |
| `single_char_telops` | new ≤ baseline | 1 字単独 telop |
| `two_char_tail_telops` | new ≤ baseline | 改行後 2 行目が 2 字以下 |
| `frame_overlap_count` | == 0 | 隣接 telop の frame 範囲重複 |

**実測 (2026-05-04 0503_テスト素材.MP4 / 短尺 short)**:
- baseline: telop 15 / single_char 0 / two_char_tail 1 / overlap 0
- new (BudouX + phrase-aware linebreak): telop 15 / two_char_tail 0 / overlap 0、`hard_word_split` のみ 0→1 (mlx-whisper 音節分割と BudouX phrase 不一致による形式違反、視覚影響なし)
- 視覚改善: 「設計で/きる」→「設計できる」保持、「素材で/す」→「素材です」保持
| ID連番 | 1から連番 | 採番し直し |
| テキスト空 | text.length > 0 | 空エントリ削除 |
| 句読点除去 | 。、が残っていない | 除去 |
| テロップ密度 | 60-80% | 範囲外は警告 |

---

## 完了時の報告フォーマット

```
✅ テロップ＆タイトル生成完了

📝 テロップ: <N>個
   - normal: <n> / emphasis: <n> / warning: <n> / success: <n>
   - 1行テロップ: <n>個 / 2行テロップ: <n>個
📏 平均文字数: <X>文字/テロップ
⏱️ 平均表示時間: <X>秒/テロップ
📊 テロップ密度: <X>%
🏷️ タイトル: <N>セグメント

次のステップ:
→ /supermovie-image-gen で画像生成
→ npm run dev でプレビュー確認
```

---

## エラーハンドリング

| エラー | 対応 |
|--------|------|
| transcript_fixed.json なし | `/supermovie-transcribe` → `/supermovie-transcript-fix` を促す |
| transcript.json のみ（fix未実施） | `/supermovie-transcript-fix` を促す |
| words 配列が空 | 音声なし動画の可能性を通知 |
| project-config.json なし | デフォルト（youtube, プロフェッショナル）で続行 |
| LLM意味分割が不正JSON | 機械的分割にフォールバック |
| 読了時間が全体的に不足 | テロップ数を減らす提案 |
| テロップ密度 90%超 | 間引き提案（低優先度テロップを非表示に） |

---

## 連携マップ

```
/supermovie-init              ← ヒアリング → プロジェクト作成
    ↓
/supermovie-transcribe        ← 文字起こし（ローカル無料）
    ↓ transcript.json
/supermovie-transcript-fix    ← 誤字修正（辞書 + Claude LLM）
    ↓ transcript_fixed.json
/supermovie-subtitles         ← ★ここ: テロップ＆タイトル生成
    ↓ telopData.ts + titleData.ts
/supermovie-slides            ← スライド生成
    ↓ slideData.ts
/supermovie-narration         ← ナレーション生成
    ↓ narration.wav
/supermovie-image-gen         ← 画像生成 + 配置データ
    ↓
/supermovie-se                ← SE自動配置
    ↓
npm run dev                   ← プレビュー
```
