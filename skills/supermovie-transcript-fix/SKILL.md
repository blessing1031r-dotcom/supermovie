---
name: supermovie-transcript-fix
description: |
  Whisper文字起こしの誤字脱字を自動修正するスキル。
  辞書ベース修正（部分一致対応）+ Claude LLM文脈修正（word境界保持JSON形式）の2段階で
  ワードタイムスタンプを保持したまま高精度に整形。
  「誤字修正」「transcript fix」「文字起こし修正」「整形」と言われたときに使用。
argument-hint: [プロジェクトパス]
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent
effort: high
---

# SuperMovie Transcript Fix — 文字起こし誤字修正

Senior Japanese language editor として、Whisper出力の誤字脱字を
辞書＋文脈AIの2段階で修正し、ワードタイムスタンプを完全に保持する。

## ワークフロー概要

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ 1. 読み込み│→│ 2. 辞書修正│→│ 3. LLM修正│→│ 4. 再マッピング│→│ 5. 検証  │
│ +品質分析 │  │ 部分一致  │  │ word単位JSON│ │ 差分照合   │  │ +学習    │
└──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
```

---

## 前提条件チェックリスト

- [ ] `/supermovie-transcribe` で文字起こし済み
- [ ] `transcript.json` が存在し `words` 配列がある

---

## Phase 1: 読み込み＆品質分析

### 1-1. transcript.json 読み込み

wordsとsegmentsの両方を取得。

### 1-2. 品質スキャン

```
📊 品質スキャン結果:
  総ワード: 523個
  低信頼度（< 0.7）: 47個（9.0%）← 重点修正対象
  低信頼度（< 0.5）: 12個（2.3%）← ほぼ確実に誤り
  セグメント: 38個
  推定修正必要数: 30-50個
```

### 1-3. project-config.json の読み込み（あれば）

- `videoType`: 専門用語の推測に使用
- `notes`: 固有名詞の手がかり（最重要）

---

## Phase 2: 辞書修正（部分一致対応）

### 2-1. typo_dict.json の構造

```json
{
  "replace": {
    "広価": "効果",
    "人口知能": "人工知能",
    "チャットGPT": "ChatGPT",
    "クロードコード": "Claude Code"
  },
  "fillers": {
    "remove": ["えーと", "あのー", "えー", "うーん", "そのー", "ええと"],
    "keep_in_context": ["まあ", "なんか"]
  },
  "preserve": ["AI", "ChatGPT", "Claude", "Remotion", "YouTube"]
}
```

**旧スキルからの改善:**
- `fillers` を `remove`（常に削除）と `keep_in_context`（文脈判断）に分離
- 「まあ」「なんか」は文頭なら削除、文中なら保持

### 2-2. 部分一致置換（旧: 完全一致のみ → 改善）

```
問題: Whisperが「広価的」と1wordで出力すると「広価」の完全一致でヒットしない

解決: 以下の3段階で照合

1. 完全一致: word.text === "広価" → 置換
2. 前方一致: word.text.startsWith("広価") → "広価的" → "効果的" に置換
3. 含有一致: word.text.includes("広価") → 前後を保持して該当部分のみ置換

例:
  word: "広価的に"
  replace: "広価" → "効果"
  結果: "効果的に"（タイムスタンプ保持）
```

### 2-3. フィラー判定（文脈対応）

```
"まあ" の判定ロジック:
  - 文頭（セグメントの最初のword）→ 削除
  - 単独word → 削除
  - 前後にwordがある文中 → 保持（「まあいいか」等の可能性）

"なんか" の判定ロジック:
  - 直後に名詞がある → 保持（「なんか変」）
  - 単独 or 文頭 → 削除
```

### 2-4. 辞書修正の実行結果

```
辞書修正結果:
  完全一致置換: 5個
  部分一致置換: 3個
  フィラー削除: 8個
  フィラー保持（文脈）: 2個
```

---

## Phase 3: Claude LLM 文脈修正（word境界保持）

### 3-1. セグメント分割

辞書修正済みのwordsを **15〜25word単位** でセグメント化。
前後 **3word** をオーバーラップさせて文脈接続。

```
セグメント1: [word1 ... word20]
セグメント2:          [word18 ... word40]  ← word18-20が重複
セグメント3:                    [word38 ... word60]
```

### 3-2. Claude への修正プロンプト（★旧スキルとの最大の違い）

**旧問題: テキストだけ返させるとword境界が崩壊する**
**解決: word配列をそのままJSON形式で入出力し、word単位で修正させる**

```
あなたは日本語校正の専門家です。
Whisper音声認識の出力を、word単位で修正してください。

## 絶対ルール
1. 各wordの修正は「text」フィールドの書き換えのみ。start/endは絶対に変更しない
2. wordの追加・削除・分割・結合は禁止。個数を変えない
3. 修正不要なwordはそのまま返す
4. 意味を変えない。内容を追加しない
5. 【保護ワード】は変更禁止: <preserveリスト>

## 修正対象
- 漢字の誤変換（文脈から正しい漢字を判定）
- 同音異義語（「以降」↔「移行」↔「意向」等）
- 助詞の誤り（「は」↔「わ」、脱落した助詞の補完は不可）
- 固有名詞の表記ゆれ

## 動画の文脈
- 種類: <videoType>
- キーワード: <notes>

## 入力（JSON配列）
<words JSON配列>

## 出力形式
以下のJSON形式で返してください。修正したwordのみ listed:
{
  "corrections": [
    { "index": 5, "original": "以降", "corrected": "移行", "reason": "文脈: システム移行" },
    { "index": 12, "original": "加工", "corrected": "下降", "reason": "文脈: 売上下降" }
  ]
}

修正箇所がない場合: { "corrections": [] }
```

**このプロンプト設計の利点:**
- word配列の長さが絶対に変わらない → タイムスタンプ完全保持
- indexで参照 → word境界が崩壊しない
- reason付き → 修正の妥当性を検証可能
- 修正箇所のみ返す → レスポンスが軽い

### 3-3. 並列処理

```
セグメントが5個以上の場合:
  → Agentツールで最大3並列実行
  → 各エージェントは独立したセグメント群を担当

セグメントが4個以下の場合:
  → 直列で順次実行（オーバーヘッド回避）
```

### 3-4. オーバーラップ結合ルール

```
セグメント1の結果: [word18: "移行", word19: "する", word20: "こと"]
セグメント2の結果: [word18: "移行", word19: "する", word20: "事"]

結合ルール:
  - 両方のセグメントで同じ修正 → そのまま採用
  - 修正が異なる場合 → confidence が低い方のwordの修正を採用
  - 片方だけ修正ありの場合 → 修正ありを採用
```

### 3-5. 修正の適用

```python
# corrections を words 配列に適用（Pythonイメージ）
for correction in all_corrections:
    idx = correction['index']
    words[idx]['text'] = correction['corrected']
    words[idx]['confidence'] = 1.0  # LLM修正済みは信頼度最大
```

---

## Phase 4: ワードタイムスタンプ再マッピング

### 4-1. この設計ではほぼ不要

**Phase 3 でword境界を保持するJSON形式を使うため、
タイムスタンプの再計算は基本的に発生しない。**

唯一必要なケース:
- 辞書修正で文字数が大きく変わった場合（例: 「プログラミ」→「プログラミング」）
- → テキスト変更のみ。タイムスタンプはそのまま保持（音声タイミングは変わらない）

### 4-2. segments の再構築

wordsの修正テキストからsegmentsのtextを再構築:

```
元segments[0].text: "広価的にAIで以降する"
  → words修正後: ["効果", "的に", "AIで", "移行", "する"]
  → 再結合: "効果的にAIで移行する"
segments[0].text = 再結合テキスト
```

### 4-3. 全文text の再構築

```
output.text = segments.map(s => s.text).join('')
```

---

## Phase 5: 検証＆保存＆学習

### 5-1. バリデーション

| チェック項目 | 条件 | 失敗時の対応 |
|-------------|------|------------|
| word数の一致 | 修正後 == 修正前（フィラー除去分を除く） | 不一致なら元に戻す |
| タイムスタンプ順序 | `words[n].start <= words[n+1].start` | ソート |
| start < end | 全wordで成立 | 警告のみ |
| テキスト空白なし | `word.text.trim().length > 0` | 空wordを削除 |
| 修正率チェック | LLM修正が全体の30%以下 | 30%超は過剰修正の警告 + diff表示 |

### 5-2. 出力ファイル

**transcript_fixed.json** — 修正済み:
```json
{
  "engine": "mlx-whisper",
  "model": "large-v3",
  "language": "ja",
  "duration_ms": 60000,
  "text": "効果的にAIで移行する...",
  "words": [
    { "text": "効果", "start": 1200, "end": 1500, "confidence": 1.0 }
  ],
  "segments": [
    { "text": "効果的にAIで移行する", "start": 1200, "end": 5000 }
  ],
  "fix_meta": {
    "original_file": "transcript.json",
    "total_corrections": 12,
    "dict_corrections": 8,
    "llm_corrections": 7,
    "fillers_removed": 6,
    "fillers_kept": 2
  }
}
```

**transcript_corrections.json** — 修正履歴:
```json
{
  "corrections": [
    { "index": 3, "original": "広価", "corrected": "効果", "phase": "dict", "match": "partial" },
    { "index": 15, "original": "以降", "corrected": "移行", "phase": "llm", "reason": "文脈: システム移行" }
  ],
  "fillers_removed": [
    { "index": 8, "text": "えーと", "start": 2000, "end": 2400 }
  ],
  "fillers_kept": [
    { "index": 22, "text": "まあ", "reason": "文中使用（まあいいか）" }
  ]
}
```

### 5-3. typo_dict.json 学習フィードバック

LLM修正で **2回以上出現** した同じパターンを辞書追加候補として提案:

```
💡 以下の修正パターンを typo_dict.json に追加しますか？
  - "以降" → "移行"（3回出現、全て文脈「システム移行」）
  - "加工" → "下降"（2回出現、全て文脈「売上下降」）

⚠️ 以下は文脈依存のため追加しません:
  - "意向" → "移行"（1回のみ、別文脈では正しい可能性あり）

追加すると次回以降はPhase 2で即座に修正されます。
→ [Y/n]
```

**追加基準:**
- 2回以上の同一パターン → 提案
- 1回のみ → 提案しない（文脈依存の可能性）
- 同音異義語で複数の正解がありうる → 提案しない

---

## 完了時の報告フォーマット

```
✅ 文字起こし修正完了

📊 修正サマリー:
  辞書修正: <N>箇所（完全一致<n> + 部分一致<n>）
  LLM修正: <N>箇所
  フィラー除去: <N>個（保持: <n>個）
  修正率: <X>%（全<total>ワード中）

📝 主な修正:
  - 「広価」→「効果」（辞書・部分一致）
  - 「以降」→「移行」× 3（LLM・文脈）
  - 「えーと」× 6個 除去

📄 保存先:
  transcript_fixed.json（修正済み）
  transcript_corrections.json（修正履歴）

次のステップ:
→ transcript_fixed.json を確認
→ /supermovie-cut で不要区間カット
```

---

## エラーハンドリング

| エラー | 対応 |
|--------|------|
| transcript.json が存在しない | `/supermovie-transcribe` の実行を促す |
| words 配列が空 | 音声なし動画の可能性を通知 |
| LLMが不正なJSON返却 | JSONパースエラーをキャッチ → 再試行（最大2回） |
| LLMがword数を変えた | corrections の index 範囲チェック → 範囲外は無視 |
| LLM修正が過剰（30%超） | 警告 + 修正前後のdiff表示、ユーザーに確認 |
| タイムスタンプ破損 | 元のtranscript.jsonから該当wordを復元 |
| typo_dict.json のJSON構文エラー | 構文を修正して再読み込み、修正内容を通知 |
| セグメントのオーバーラップ矛盾 | confidence比較で解決、同一ならindex小を優先 |

---

## 連携マップ

```
/supermovie-init              ← ヒアリング → プロジェクト作成
    ↓
/supermovie-transcribe        ← 文字起こし（ローカル無料）
    ↓ transcript.json
/supermovie-transcript-fix    ← ★ここ: 誤字修正（辞書 + Claude LLM）
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
