---
name: supermovie-telop-creator
description: |
  ヒアリングベースで新しいテロップスタイルを設計・生成するスキル。
  既存テロップ（白青、赤文字、オレンジグラデ等）を参考に、
  ユーザーの要望や参考画像からオリジナルのテロップ .tsx コンポーネントを生成。
  Remotion Studioでリアルタイムプレビューしながら調整。
  「テロップ作成」「新しいテロップ」「テロップデザイン」「telop create」と言われたときに使用。
argument-hint: [プロジェクトパス] [参考画像パス]
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent, AskUserQuestion
effort: high
---

# SuperMovie Telop Creator — テロップスタイルデザイナー

Senior motion graphics designer として、ユーザーの要望を
視覚的に魅力的なテロップコンポーネントに変換する。

## ワークフロー概要

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ 1. ヒアリング│→│ 2. 設計   │→│ 3. 生成   │→│ 4. プレビュー│→│ 5. 調整   │
│ 要望確認   │  │ パラメータ │  │ .tsx作成  │  │ Remotion  │  │ 微調整    │
└──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
                                                    ↑              │
                                                    └──────────────┘
                                                     繰り返し可能
```

---

## 前提条件チェックリスト

- [ ] SuperMovieプロジェクトが存在（`/supermovie-init` 済み）
- [ ] `npm install` 済み

---

## Phase 1: ヒアリング（必須）

**1回のメッセージで以下を聞く:**

```
新しいテロップスタイルを作成します。教えてください:

1. テロップの用途は？
   → メイン（通常会話）/ 強調 / ネガティブ / ポイント / タイトル / その他

2. どんな雰囲気にしたい？
   → 例: クール / 可愛い / 高級感 / ポップ / 怖い / シンプル / 力強い

3. 色のイメージは？
   → 例: 青系 / 赤×金 / 白黒 / パステル / おまかせ
   → 具体的なカラーコード指定もOK

4. 参考にしたい既存テロップはある？
   ┌────────────────┬────────────────────────────┐
   │ カテゴリ         │ 選択肢                      │
   ├────────────────┼────────────────────────────┤
   │ メインテロップ    │ A: 白青テロップ（シンプル）    │
   │                 │ B: 白青ver2（ダブルストローク） │
   ├────────────────┼────────────────────────────┤
   │ 強調テロップ     │ C: 赤文字（インパクト）        │
   │                 │ D: オレンジグラデーション（高級）│
   ├────────────────┼────────────────────────────┤
   │ ネガティブテロップ │ E: 黒文字白背景（シンプル）    │
   │                 │ F: 残酷紺（筆体）             │
   │                 │ G: 黒紫グラデ（ダーク）        │
   └────────────────┴────────────────────────────┘
   → 「Dをベースに色を変えたい」「AとDを組み合わせ」等もOK

5. フォントの希望は？
   → ゴシック / 明朝 / 筆体 / おまかせ

6. 参考画像がある場合はパスを教えてください（なくてもOK）
```

### 参考画像が提供された場合
Readツールで画像を確認し、以下を分析:
- 文字色・ストローク色
- 背景の有無と色
- グラデーションの方向と色
- シャドウの種類
- フォントの太さ・スタイル

---

## Phase 2: テロップ設計

ヒアリング結果から、テロップの**設計パラメータ**を決定する。

### 2-1. テロップ構造パターン（3種から選択）

```
パターンA: シングルストローク（シンプル）
┌─────────────────────────────────┐
│  [ストローク] → [塗り]           │
│  paintOrder: "stroke fill"      │
│  例: 白青テロップ, 赤文字        │
└─────────────────────────────────┘

パターンB: ダブルストローク（立体感）
┌─────────────────────────────────┐
│  [外側ストローク] → [内側ストローク+塗り] │
│  2層のtext要素を重ねる           │
│  例: 白青ver2, オレンジグラデ, 残酷紺    │
└─────────────────────────────────┘

パターンC: 背景ボックス（背景付き）
┌─────────────────────────────────┐
│  [背景div] → [テキスト]          │
│  SVG不使用、CSS背景              │
│  例: 黒文字白背景                │
└─────────────────────────────────┘
```

### 2-2. パラメータ設計テーブル

| パラメータ | 説明 | 値の例 |
|-----------|------|--------|
| `fontSize` | 文字サイズ | 80（通常）/ 90（強調）/ 110（インパクト） |
| `fontFamily` | フォント | `'Noto Sans JP'`（ゴシック）/ `'Noto Serif JP'`（明朝）/ `'りいてがき筆'`（筆） |
| `fontWeight` | 太さ | 400（筆体） / 900（ゴシック/明朝） |
| `fontColor` / `fill` | 文字色 | 色コード or `url(#gradientId)` |
| `strokeColor` | ストローク色 | 色コード or `url(#gradientId)` |
| `strokeWidth` | ストローク幅 | 10-32px |
| `fillGradient` | 塗りグラデーション | `{start, mid?, end, direction}` |
| `strokeGradient` | 線グラデーション | `{start, mid?, end, direction}` |
| `dropShadow` | 影 | `{offsetX, offsetY, blur, color}[]` 複数重ね可 |
| `glowEffect` | 発光 | `{blur, color, opacity}` |
| `bottomOffset` | 下からの位置 | 80px |
| `background` | 背景（パターンCのみ） | `{color, padding, borderRadius}` |

### 2-3. グラデーション方向

| 方向 | SVG設定 | 効果 |
|------|---------|------|
| 水平 `→` | `x1=0 y1=0 x2=1 y2=0` | 左→右の色変化 |
| 垂直 `↓` | `x1=0 y1=0 x2=0 y2=1` | 上→下の色変化 |
| 斜め `↘` | `x1=0 y1=0 x2=1 y2=1` | 対角線の色変化 |

---

## Phase 3: .tsx コンポーネント生成

### 3-1. ファイル構造

**保存先:** プロジェクトの `src/` 直下にカテゴリフォルダごと

```
src/
├── メインテロップ/
│   ├── 白青テロップ.tsx        ← 既存
│   └── <新テロップ名>.tsx      ← 新規作成
├── 強調テロップ/
├── ネガティブテロップ/
└── テロップテンプレート/        ← 統合テロップシステム
```

### 3-2. コンポーネント共通構造（必ず守る）

```tsx
import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  interpolate,
  Easing,
} from "remotion";

// registry component contract: TelopPlayer から subtitleData を受け取る
export interface SubtitleItem {
  text: string;
  lines: string[];
  start: number;
  end: number;
  startFrame: number;
  endFrame: number;
}

export interface SubtitleData {
  fps: number;
  subtitles: SubtitleItem[];
}

// Props: subtitleData は必須。他はカスタマイズ可能なデフォルト値付き
interface <ComponentName>Props {
  subtitleData: SubtitleData;
  fontSize?: number;
  bottomOffset?: number;
  fontFamily?: string;
  // ...スタイル固有のprops
}

export const <ComponentName>: React.FC<<ComponentName>Props> = ({
  subtitleData,
  fontSize = 80,
  bottomOffset = 80,
  fontFamily = "'Noto Sans JP', sans-serif",
  // ...
}) => {
  const frame = useCurrentFrame();

  // 現在のフレームに対応する字幕を検索
  const currentSubtitle = subtitleData.subtitles.find(
    (sub) => frame >= sub.startFrame && frame <= sub.endFrame
  );
  if (!currentSubtitle) return null;

  // フェードイン/アウト（共通パターン）
  const duration = currentSubtitle.endFrame - currentSubtitle.startFrame;
  const maxFadeDuration = Math.floor(duration / 3);
  const fadeInDuration = Math.min(3, maxFadeDuration);
  // ... opacity計算 ...

  // スケールアニメーション（共通パターン）
  const scale = interpolate(
    frame,
    [currentSubtitle.startFrame, currentSubtitle.startFrame + fadeInDuration],
    [0.95, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp",
      easing: Easing.out(Easing.cubic) }
  );

  // SVGレンダリング（パターンA/B）or divレンダリング（パターンC）
  return (
    <AbsoluteFill style={{
      justifyContent: "flex-end",
      alignItems: "center",
      paddingBottom: bottomOffset,
    }}>
      {/* テロップ本体 */}
    </AbsoluteFill>
  );
};
```

この skeleton では `telopData` を import しない。`TelopPlayer` が現在 segment を
`SubtitleData` に変換し、registry component に `subtitleData` prop として渡す。

### 3-3. SVG filter/shadow テクニック集

```
┌──────────────────────────────────────────────────┐
│ シンプル影:                                        │
│   drop-shadow(6px 12px 0 #4040A0)                │
│                                                   │
│ グロー（発光）:                                     │
│   drop-shadow(0 0 8px white)                      │
│                                                   │
│ 多層影（立体感）:                                    │
│   drop-shadow(3px 4px 0 rgba(0,0,0,1))           │
│   drop-shadow(0 0 5px rgba(255,180,0,1))         │
│   drop-shadow(0 0 10px rgba(255,180,0,0.8))      │
│                                                   │
│ ソフト影:                                          │
│   drop-shadow(0 0 8px rgba(0,0,0,0.4))           │
│   drop-shadow(0 0 15px rgba(0,0,0,0.2))          │
└──────────────────────────────────────────────────┘
```

---

## Phase 4: Remotionプレビュー

### 4-1. プレビュー用データ作成

テロップが正しく表示されるか確認するため、サンプルデータを生成:

```typescript
// プレビュー用: src/にPreviewCompositionを追加
// Root.tsx に一時的なCompositionを追加

// サンプルテロップデータ
const sampleData: SubtitleData = {
  fps: 30,
  subtitles: [
    {
      text: "サンプルテキスト",
      lines: ["サンプルテキスト"],
      start: 0, end: 3,
      startFrame: 0, endFrame: 90,
    },
    {
      text: "2行目のテスト\nこんにちは世界",
      lines: ["2行目のテスト", "こんにちは世界"],
      start: 3.5, end: 7,
      startFrame: 105, endFrame: 210,
    },
  ],
};
```

### 4-2. Root.tsx にプレビューComposition追加

```typescript
<Composition
  id="TelopPreview"
  component={TelopPreviewComponent}
  durationInFrames={300}
  fps={30}
  width={1920}
  height={1080}
/>
```

### 4-3. Remotion Studio起動
```bash
cd <PROJECT> && npm run dev
```

Remotion StudioでComposition「TelopPreview」を選択してプレビュー確認。

---

## Phase 5: 調整ループ

ユーザーにプレビュー結果を確認してもらい、フィードバックに応じて調整:

**よくある調整リクエスト:**

| リクエスト | 調整するパラメータ |
|-----------|-------------------|
| 「もっと太くして」 | `strokeWidth` を +4-6 |
| 「影を強くして」 | `drop-shadow` の blur / opacity を増加 |
| 「色をもう少し明るく」 | カラーコードの明度を上げる |
| 「文字が読みにくい」 | ストローク幅を増加 or 背景を追加 |
| 「もっと派手に」 | グロー追加、グラデーション追加 |
| 「サイズ大きく/小さく」 | `fontSize` を ±10-20 |
| 「フォント変えて」 | `fontFamily` を変更 |
| 「位置を上に」 | `bottomOffset` を増加 |

**調整 → プレビュー → 確認 のサイクルを、ユーザーがOKするまで繰り返す。**

---

## Phase 6: 保存＆統合

### 6-1. ファイル保存
確定したテロップを適切なカテゴリフォルダに保存:
```
src/<カテゴリ>テロップ/<日本語テロップ名>.tsx
```

### 6-2. telopTemplateRegistry.tsx への統合（推奨）
本番利用する場合は `src/テロップテンプレート/telopTemplateRegistry.tsx` に
新規 component を登録する。`templateId` は registry key (= export 名) を使う。

1. 作成した `.tsx` から component を import
2. `telopTemplateRegistry` に `{ category, displayName, Component }` を追加
3. `category` は `main` / `emphasis` / `negative` のいずれか
4. `displayName` は `/supermovie-init` / `/supermovie-subtitles` で選べる日本語名
5. `findTemplateIdByDisplayName()` / `listTemplatesByCategory()` で逆引きされる前提を保つ

`telopStyles.ts` への `template<N>_<name>` 追加は legacy fallback 用の任意対応。
registry 統合を primary とし、TelopPlayer は `templateId` 指定時に registry component を描画する。

### 6-3. プレビューCompositionの削除
Root.tsx から一時的に追加した TelopPreview Composition を削除。

---

## 完了時の報告フォーマット

```
✅ テロップスタイル作成完了

🎨 名前: <テロップ名>
📂 保存先: src/<カテゴリ>テロップ/<ファイル名>.tsx
🏷️ カテゴリ: メイン / 強調 / ネガティブ / ポイント

構造: パターン<A/B/C>（<シングル/ダブルストローク/背景ボックス>）
カスタマイズ可能なprops:
  - fontSize (default: <N>)
  - fontFamily
  - ...

registry統合: 済 / 未
telopStyles.ts legacy統合: 済 / 未（希望時のみ）
```

---

## エラーハンドリング

| エラー | 対応 |
|--------|------|
| プロジェクトが存在しない | `/supermovie-init` の実行を促す |
| npm install 未実施 | `npm install` を実行 |
| Remotion Studioが起動しない | ポート競合チェック、`npx remotion studio --port 3001` |
| フォントが表示されない | Google Fontsからのインポートを提案、またはシステムフォントにフォールバック |
| SVGグラデーションが表示されない | gradientUnits の確認、IDの重複チェック |
| 参考画像が読めない | ファイルパスの確認、対応フォーマット（png/jpg）を案内 |
