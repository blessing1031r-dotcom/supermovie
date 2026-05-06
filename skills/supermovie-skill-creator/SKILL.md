---
name: supermovie-skill-creator
description: |
  SuperMovieの新しいスキルを一流品質で設計・作成するメタスキル。
  YAML frontmatter、ASCII図、データスキーマ、バリデーションチェックポイントを
  備えたプロフェッショナルなSKILL.mdを自動生成する。
  「新しいスキル作成」「スキル追加」「skill create」と言われたときに使用。
argument-hint: <スキル名> [概要]
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent, AskUserQuestion
effort: high
---

# SuperMovie Skill Creator — 一流スキルデザイナー

Principal skill architect として、SuperMovieエコシステムに
最高品質のスキルを設計・追加する。

## 設計哲学

```
一流のスキルとは:
┌─────────────────────────────────────────────────────┐
│ 1. 読んだ瞬間にやることが分かる（明確性）              │
│ 2. 誰が実行しても同じ結果になる（再現性）              │
│ 3. 失敗した時に何をすべきか分かる（回復可能性）         │
│ 4. 他のスキルとシームレスに連携する（接続性）           │
│ 5. 新しい要件に対応しやすい（拡張性）                  │
└─────────────────────────────────────────────────────┘
```

---

## Phase 1: ヒアリング（必須）

**1回のメッセージで以下を全て聞く:**

```
新しいスキルを設計します。以下を教えてください:

1. スキル名（英語ハイフン区切り）
   → 例: supermovie-bgm, supermovie-render

2. 何をするスキル？（1文で）
   → 例: BGMを動画のムードに合わせて自動選択・配置する

3. いつ使われる？（トリガー条件）
   → 例: テロップとSE配置の後、プレビュー前

4. 入力は何？（必要なファイル・データ）
   → 例: project-config.json, telopData.ts

5. 出力は何？（生成するファイル）
   → 例: SoundEffects/BGM.tsx を更新

6. 前提となるスキルは？
   → 例: /supermovie-init が必須

7. 特別なAPI・ツールが必要？
   → 例: なし / AssemblyAI / ffmpeg
```

---

## Phase 2: スキル設計

ヒアリング結果から以下を設計する:

### 2-1. ワークフロー図（ASCII）
スキルの処理フローをASCII図で設計。最低4フェーズ:
```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ 1. 読込   │→│ 2. 処理   │→│ 3. 生成   │→│ 4. 検証   │
└──────────┘   └──────────┘   └──────────┘   └──────────┘
```

### 2-2. データスキーマ
入出力のJSONまたはTypeScript型定義を明確にする。

### 2-3. バリデーションチェックリスト
各フェーズの成功条件と失敗時の回復手順。

### 2-4. エラーハンドリングテーブル
| エラー | 対応 | のテーブル形式で全パターン網羅。

---

## Phase 3: SKILL.md 生成

以下のテンプレート構造に従ってファイルを生成する:

### 必須セクション（省略不可）

```yaml
---
name: supermovie-<name>
description: |
  <3行の説明>
  <トリガーキーワード>
argument-hint: <引数ヒント>
allowed-tools: <必要なツール>
---

# SuperMovie <Name> — <日本語タイトル>

<ロール定義（1行）>

## ワークフロー概要
<ASCII図>

## 前提条件チェックリスト
<箇条書き>

## Phase 1: <フェーズ名>
## Phase 2: <フェーズ名>
## Phase N: <フェーズ名>

## 完了時の報告フォーマット
<テンプレート>

## エラーハンドリング
<テーブル>
```

### 品質チェックリスト（生成後に自己検証）

| # | チェック項目 | 基準 |
|---|------------|------|
| 1 | YAML frontmatter | name, description, allowed-tools が全てある |
| 2 | ロール定義 | 1行で専門性が伝わる |
| 3 | ワークフロー図 | ASCII図で全体像が一目で分かる |
| 4 | 前提条件 | チェックリスト形式で漏れなし |
| 5 | フェーズ分割 | 各フェーズが独立して理解できる |
| 6 | データスキーマ | 入出力のJSON/TS型が明示されている |
| 7 | バリデーション | 成功条件と失敗時対応が全フェーズにある |
| 8 | エラーテーブル | 起こりうるエラーが網羅されている |
| 9 | 完了報告 | コピペ可能なテンプレート形式 |
| 10 | 次のステップ | 後続スキルへの導線がある |
| 11 | テーブル活用 | 選択肢やマッピングはテーブルで整理 |
| 12 | コード例 | bash/TypeScriptのコードブロックが適切 |

**12項目中10個以上クリアで合格。9個以下は改善してから保存。**

---

## Phase 4: ファイル配置

```bash
mkdir -p .claude/skills/supermovie-<name>
# SKILL.md を書き出し
```

---

## Phase 5: 動作確認

スキルが正しく認識されたか確認:
- `/supermovie-<name>` でオートコンプリートに表示される
- description のキーワードで自動トリガーされる

---

## 既存スキルとの連携マップ

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
/supermovie-se                ← SE自動配置
    ↓
npm run dev                   ← プレビュー
```

新スキルを追加する際は、このフローのどこに位置するかを明確にすること。

---

## 参考: 既存スキルの設計パターン

| パターン | 使用例 | 説明 |
|---------|--------|------|
| SE選択マトリクス | supermovie-se | style × SE候補のテーブル |
| トーン別調整 | supermovie-subtitles | tone → アニメーション比率マッピング |
| ヒアリング → JSON保存 | supermovie-init | ユーザー入力 → project-config.json |
| ラウンドロビン | supermovie-se | バリエーション連続回避 |
| フレーム計算式 | supermovie-subtitles | ms → frame 変換公式 |
