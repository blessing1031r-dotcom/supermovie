---
name: supermovie-init
description: |
  SuperMovie動画編集プロジェクトを自動生成するスキル。
  ヒアリングで動画の方向性を確認 → Remotionプロジェクトを構築。
  「動画プロジェクト作成」「supermovie init」「新しい動画を編集」と言われたときに使用。
argument-hint: <動画ファイルパス> [プロジェクト名]
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent, AskUserQuestion
effort: high
---

# SuperMovie Init — 動画編集プロジェクト自動生成

Senior video production engineer として、ユーザーの動画素材から
最適なRemotionプロジェクトを構築する。**必ずヒアリングから始める。**

## ワークフロー概要

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  1. ヒアリング │ → │ 2. 動画解析  │ → │ 3. プロジェクト│ → │ 4. カスタマイズ│
│  動画の方向性  │    │  FPS/尺検出  │    │  テンプレコピー│    │  設定反映     │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
```

---

## Phase 1: ヒアリング（必須・スキップ不可）

動画ファイルパスを確認した後、**1回のメッセージで以下を全て聞く**。
回答は箇条書きでOKと伝える。

```
プロジェクトを作成する前に、動画の方向性を確認させてください。
（箇条書きでサクッと答えてもらえればOKです）

1. 動画のフォーマットは？
   → YouTube通常（16:9） / ショート動画（9:16） / 正方形（1:1）

2. 動画の種類は？
   → YouTube解説 / セミナー録画 / プロモーション / Vlog / インタビュー / その他

3. ターゲット視聴者は？
   → ビジネスパーソン / 初心者 / エンジニア / 一般層 / その他

4. 動画のトーンは？
   → プロフェッショナル / カジュアル / エンタメ / 教育的

5. テロップのスタイル希望は？（おまかせOK / 30 templates から選択）
   各カテゴリから 1 つ選んでください (デフォルトはトーン別表参照)。

   **メイン (落ち着いた・通常字幕、12 種)**
     白青テロップ / 白青テロップver2 (立体) / 黒文字 / 青文字白背景 / 白黒テロップ /
     白背景グラデ / 白文字黒シャドウ / 白文字黒シャドウゴシック /
     白文字黒シャドウ明朝体 / 白文字黒背景 / 白文字青ピンク背景グラデ / 緑文字白背景

   **強調 (注目・強調、13 種)**
     オレンジグラデーション / 赤文字 / 黄色シャドウ / 黄色文字黒シャドウ /
     金グラデ・紺背景 / 黒文字黄色背景 / 青文字金枠 / 赤文字白背景 /
     白赤テロップ / 白赤テロップver2 / 白文字赤シャドウ / 白緑テロップ / 緑グラデ金シャドウ

   **ネガティブ (警告・否定、5 種)**
     黒紫グラデ / 黒文字白背景 / 残酷テロップ・紺 / 紫文字白背景 / 白文字紫シャドウ

   ※ 全 templates の見た目: `~/tmp/sm-matrix/matrix.png` (Phase 2 visual matrix で生成済)

6. BGMの雰囲気は？
   → アップテンポ / 落ち着いた / なし / おまかせ

7. 特に意識したいこと・注意点は？
   → 例: テンポ重視 / 特定キーワード強調 / 情報量多め
```

### ヒアリング結果 → `project-config.json`

回答を以下のJSON形式で保存（プロジェクトルートに配置）:

```json
{
  "format": "youtube",
  "resolution": { "width": 1920, "height": 1080 },
  "videoType": "YouTube解説",
  "targetAudience": "ビジネスパーソン",
  "tone": "プロフェッショナル",
  "telopStyle": {
    "main": "白青テロップver2",
    "emphasis": "オレンジグラデーション",
    "negative": "黒紫グラデ"
  },
  "bgmMood": "アップテンポ",
  "notes": "テンポ重視、キーワード「AI」を強調",
  "createdAt": "2026-03-25"
}
```

**「おまかせ」の場合のデフォルト選択ロジック (Phase 2 で 30 件から拡張):**

| トーン | メイン | 強調 | ネガティブ |
|--------|--------|------|-----------|
| プロフェッショナル | 白青テロップver2 | オレンジグラデーション | 黒紫グラデ |
| エンタメ | 白青テロップ | 赤文字 | 残酷テロップ・紺 |
| カジュアル | 白文字黒シャドウ | オレンジグラデーション | 黒文字白背景 |
| 教育的 | 白青テロップver2 | 黄色文字黒シャドウ | 黒文字白背景 |
| ニュース調 | 青文字白背景 | 赤文字白背景 | 残酷テロップ・紺 |
| Vlog | 白背景グラデ | 白緑テロップ | 紫文字白背景 |

**displayName → templateId 解決 (registry lookup):**
- 後段 skill (supermovie-subtitles 等) が template を選ぶときは `findTemplateIdByDisplayName(displayName)` を `template/src/テロップテンプレート/telopTemplateRegistry.tsx` から呼ぶ
- 例: `"白青テロップver2"` → `templateId: 'WhiteBlueTeleopV2'`

---

## Phase 2: 動画解析（preflight 必須・rotation/HDR/VFR/SAR 罠ガード）

**ffprobe を素手で読まない。必ず `template/scripts/preflight_video.py` を実行する。**

### 2-1. preflight_video.py を実行

```bash
# template コピー後 (Phase 3-1) でも、コピー前 (~/.claude/plugins/.../template/...) でも実行可
python3 "<PROJECT>/scripts/preflight_video.py" "$VIDEO_PATH" \
  --write-config "<PROJECT>/project-config.json" \
  [--force-format youtube|short|square] \
  [--allow-risk hdr-or-dovi,10bit,vfr,multiple-audio,embedded-subtitle,non-square-sar]
```

**抽出される値 (project-config.json `source.*` に nested で書き込まれる):**
- `raw` / `display` (rotation 適用後の表示解像度)
- `rotation.raw` / `rotation.normalized` / `rotation.source` (Display Matrix / tags.rotate / root.rotation 全走査)
- `aspect` / `sar` / `dar` / `inferred_format` (16/9, 9/16, 1/1 を ±3% 許容で判定)
- `fps.r_frame_rate` / `fps.avg_frame_rate` / `fps.vfr_metadata_suspect`
- `codec.name` / `codec.pix_fmt` / `codec.field_order`
- `color.transfer` / `color.primaries` / `color.hdr_suspect` / `color.dovi`
- `streams.video` / `streams.audio` / `streams.subtitle` / `streams.data`
- `risks` (= 検出された罠キー配列)

### 2-2. 罠ガード (絶対ルール)

**index で side_data_list を参照しない。必ず `side_data_type` で全走査する。**

| 罠 | 検出キー | SuperMovie への影響 | 対応 |
|----|---------|---------------------|------|
| iPhone/Android 縦動画 | Display Matrix `rotation` ≠ 0 | format 誤判定 → 動画が画面端で見切れ・上下黒帯 | `display.{width,height}` を canvas 解像度に使う |
| HDR / Dolby Vision | `color_transfer in {smpte2084, arib-std-b67}` / DOVI side_data | render で色破綻、Chromium decode 不能 | `<OffthreadVideo>` (FFmpeg) 必須、tonemap は別タスク |
| 10-bit color | `pix_fmt` に "10" / "p010" | render で色精度ロスや fallback | `<OffthreadVideo>` で扱い、ack で進める |
| VFR (可変 fps) | `r_frame_rate` ≠ `avg_frame_rate` (誤差 > 0.5%) | frame 換算で時間ずれ | render 前に CFR 化 (ffmpeg `-r`) を別タスクで |
| 異常 SAR/DAR | `sample_aspect_ratio` ≠ `1:1`/`0:1` | 横/縦比破綻 | 自動判定停止、Roku 確認 |
| 字幕 track 内蔵 | `streams.subtitle > 0` | 自前テロップと重複 | `-sn` で除去するか別タスクで合成判断 |
| 複数 audio track | `streams.audio > 1` | transcribe が誤った track を取る | `--allow-risk multiple-or-missing-audio` で許可、default track 明示 |
| interlace | `field_order != progressive` | コーミング artifacts | `idet` フィルタ + deinterlace 別タスク |
| 複数 video stream | `streams.video > 1` | primary stream 不明 | 自動判定停止、Roku 確認 |

### 2-3. 同型事故の履歴

- **2026-05-04 Phase 1 minimum test**: iPhone 縦動画 (raw 3840x2160 + rotation -90、display 2160x3840) を `side_data_list[0]` (DOVI) しか見ずに横動画と誤判定 → format='youtube' で render → テロップが画面端で見切れる Roku「不合格」指摘。本 phase の preflight 必須化はこれを起点とした再発防止。

### 2-4. exit code の扱い

| exit | 意味 | skill 側の動き |
|------|------|----------------|
| 0 | proceed (risks なし or `--allow-risk` で全許可) | Phase 3 に進む |
| 2 | 要確認 (risks あり、未許可) | Roku に risks を提示し、`--allow-risk` 指定で再実行を確認 |
| 3 | 入力不正 (動画読めない / video stream 不在) | パスを再確認してもらう |

---

## Phase 3: プロジェクト生成

### 3-1. テンプレートコピー
```bash
cp -r ~/.claude/plugins/supermovie/template "<PROJECT_DIR>"
```

### 3-2. 動画配置
```bash
mkdir -p "<PROJECT_DIR>/public/images/generated" "<PROJECT_DIR>/public/se" "<PROJECT_DIR>/public/BGM"
cp "$VIDEO_PATH" "<PROJECT_DIR>/public/main.mp4"
```

### 3-3. ファイル更新 (videoConfig.ts SSoT を書き換える / Root.tsx は触らない)

**videoConfig.ts (SSoT):**
```typescript
export const FORMAT: VideoFormat = '<chosen_format>'; // preflight 結果から
export const FPS = <render_fps>; // preflight source.fps.render_fps
export const SOURCE_DURATION_FRAMES = <duration_frames>; // 元動画 frame、cut 後は cutData.CUT_TOTAL_FRAMES を使う
export const VIDEO_FILE = 'main.mp4';
```
- 解像度は FORMAT から RESOLUTION_MAP で自動決定 (youtube=1920x1080 / short=1080x1920 / square=1080x1080)
- preflight が `display.{width,height}` と FORMAT_MAP の解像度の不一致を検出した時は Roku に確認

**Root.tsx / telopData.ts / titleData.ts は videoConfig から import しているため自動反映 (touch しない)。**
**telopData.ts の TOTAL_FRAMES は cut phase 完了後に CUT_TOTAL_FRAMES に切替。**

**Title/titleData.ts:**
```typescript
const FPS = <検出値>;
```

**package.json:**
```json
{ "name": "<プロジェクト名>" }
```

---

## Phase 4: ヒアリング結果のカスタマイズ反映

トーンに応じて `telopStyles.ts` のデフォルトマッピングコメントを追記:

| トーン | アニメーション傾向 | charByChar | テンポ |
|--------|-------------------|-----------|--------|
| プロフェッショナル | `fadeOnly` 中心 | 使わない | 落ち着き |
| エンタメ | `slideIn` 多め | 積極使用 | 速い |
| カジュアル | バリエーション豊富 | たまに | 普通 |
| 教育的 | `fadeOnly` 中心 | 使わない | ゆっくり |

---

## Phase 5: セットアップ (Phase 1 検証では skill 内で自動実行しない)

**重要: Phase 1 minimum test では skill が `npm install` / `npx remotion studio` を自動実行しないこと。Phase 4 まで完了したら以下フォーマットで報告して終了:**

```
Phase 4 まで完了。<PROJECT_DIR> を生成しました。
次のコマンドを Roku が手動実行してください:
  cd <PROJECT_DIR> && npm install
  npx remotion studio
```

(Phase 2 以降で本フローを skill 自動実行に戻す方針)

---

## Phase 6: 起動確認 (Phase 1 検証では skill 内で自動実行しない)

Phase 5 と同じ理由で skill 内では実行せず、Roku が手動で `npx remotion studio` を実行する。

---

## 完了時の報告フォーマット

```
✅ プロジェクト作成完了

📁 パス: <PROJECT_DIR>
🎬 動画: <duration>秒 / <fps>fps / <frames>フレーム
🎨 スタイル: <ヒアリング結果サマリー>

次のステップ:
→ /supermovie-transcribe で文字起こし
```

---

## エラーハンドリング

| エラー | 対応 |
|--------|------|
| 動画ファイルが存在しない | パスを再確認してもらう |
| ffprobeがインストールされていない | `brew install ffmpeg` を提案 |
| npm installが失敗 | node_modulesを削除して再実行 |
| テンプレートが見つからない | テンプレートパスを確認 |

---

## 連携マップ

```
/supermovie-init              ← ★ここ: ヒアリング → プロジェクト作成
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
