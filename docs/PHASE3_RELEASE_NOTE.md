# SuperMovie Phase 3 Release Note (2026-05-04 → 2026-05-05)

`roku/fixture-normalize-recipe` source commit HEAD: `aa244bd` (anchor 自身の document commit は drift 1 intrinsic、CONTEXT_ANCHOR.md §Source commit vs Document commit 規約 参照、Phase 3-V FINAL の元 source `cad6914` から source commit ベースで 4 件積上げ = b1 fixture normalize bundle [9a37873] + Codex 19:39 PR review fix iter [5ce2bc5] + 19:53 re-review fix iter 2 [983edc4] + 20:04 3rd review fix iter 3 [aa244bd]、それぞれに anchor refresh commit が docs-only で続くため `cad6914..aa244bd` raw count は source 4 + anchor refresh 4 = 8 となる、anchor refresh は除外規則で source 進捗から除く)。Codex CODEX_REVIEW_PHASE3V_FINAL 20260505T064250 で「P0/P1/P2 なし、Phase 3-V production 品質で止めてよい」 verdict 後、post-freeze
backlog 第 1〜3 弾 + P3 logging extension + Codex 4 cycle re-review (P5/2nd-batch/P2/P3-slide-plan
全 P0/P1 NONE) を反映、Codex CODEX_FULL_SESSION_REVIEW 20260505T113913 で「過剰実装、
P5 sentinel 以降は黄信号」と判定。さらに b1 fixture normalize bundle (Codex 18:36 verdict + 18:55 review + 19:39 PR review fix iter + 19:53 re-review fix iter 2 + 20:04 3rd review fix iter 3) を追加。

Phase 3-A 〜 Phase 3-V の自走実装結果 + 後続 post-freeze backlog 第 1〜3 弾 + b1 fixture normalize bundle。本 note は
Roku 不在モード中に Claude+Codex 協働で 120 commit (`roku/phase3i-transcript-alignment..HEAD`、
main..HEAD は 138 commit、Bash 実測) を積んだ成果物の release assertion を固定する目的。

## Release-readiness statement (2026-05-05 時点、技術 readiness のみ)

| 項目 | 状態 |
|---|---|
| code 側 P0/P1/P2 (Codex 22+ + post-freeze re-review 通過) | ✅ ゼロ |
| pure python integration smoke (`test:timeline`) | ✅ 43/43 pass (Phase 3 23 + post-freeze 20: sentinel 4 + visual_smoke 4 + json-log 3 + cli mismatch 1 + cost guard 5 + review regression 2 + slide-plan json-log 1) |
| TypeScript lint / tsc (`npm run lint`) | ✅ exit 0 (errors 0、warnings 0、`no-explicit-any` error 化済) |
| React component test (`npm run test:react`) | ✅ 22/22 pass (vitest + jsdom + RTL、4 + 10 + 5 + 3) |
| docs vs git log drift (`regen_phase3_progress.sh --verify`) | ✅ exit 0 (drift 1 = anchor document commit による intrinsic、CONTEXT_ANCHOR.md §Source commit vs Document commit 規約) |
| worktree clean | ✅ untracked なし |
| 実 project visual-smoke / render e2e | [未検証] (Roku 判断領域、main.mp4 fixture 必要) |

Roku 判断領域 (release blocker 候補):
- ★ PR / merge 戦略: phase3f→g→h→i→j は ancestry 連結済み、技術的に階層 merge
  不要。Codex 推奨は `roku/phase3j-timeline` を 1 PR / squash merge。本 branch
  `roku/fixture-normalize-recipe` (HEAD `aa244bd`、b1 fixture normalize bundle 込) では
  `main..HEAD` は **138 commits**、`phase3i..HEAD` は **120 commits** (Bash 実測 20:04)。
  upstream `RenTonoduka/supermovie` (Roku 所有でない、現 gh account `blessing1031r-dotcom`
  は READ only、Bash 実測) のため Codex 推奨は **Option A: fork → blessing1031r-dotcom →
  upstream PR** (CODEX_FULL_SESSION_REVIEW 20260505T113913 §推奨理由、release branch 外、commit history で参照可)。
  ※ Roku 18:06 訂正後の現解釈: completion 条件 = local 再現性、upstream PR は optional_later (CONTEXT_ANCHOR §External Actions Mission frame 注記)。fork-internal PR (本 PR url) で完結可。
- 実 project (main.mp4 + node_modules + remotion installed) で
  `npm run test:visual-smoke` と `npm run render` を 1 周通すことが推奨。本 PR では proj1 で b1 fixture (HEVC HDR DoVi 4K) 経由で完走実証 (`b1 fixture normalize evidence trail` section 参照)。
- 5/13 リリース予定なら本 branch を Roku の最終 e2e 後に main へ。

## 主要 deliverable (Phase 3-F 〜 3-Q)

### 1. 基盤 (Phase 3-F〜H)
- BGM/Narration optional asset gate (`getStaticFiles()` で不在 OK、render 失敗しない)
- visual_smoke.py (3 format × 2 frame の still + ffprobe + grid)
- per-segment narration `<Sequence>` (chunk wav 保持 + wave duration 測定 +
  `narrationData.ts` all-or-nothing 生成 + atomic write)

### 2. timeline 共通化 (Phase 3-I/J)
- `template/scripts/timeline.py`: 4 helper + 2 validation 集約
  (`read_video_config_fps` / `build_cut_segments_from_vad` /
  `ms_to_playback_frame` / `load_cut_segments` / `validate_vad_schema` /
  `validate_transcript_segment(s)`)
- 3 script (voicevox / build_slide / build_telop) で FPS / cut helper /
  transcript validation を一元化
- VAD 部分破損 + transcript start>end / 型不正の fail-fast 早期検出

### 3. integration smoke + Studio hot-reload (Phase 3-K/N)
- 20 test ケース (`test_timeline_integration.py`、engine 不要、CI 高頻度可)
- `useNarrationMode()` hook (watchStaticFile + invalidateNarrationMode +
  React state、Studio で Cmd+R 不要、Player/render は no-op fallback)

### 4. write 順序 race fix + rollback 強化 (Phase 3-N/O/P)
- voicevox_narration.py write 順序を **chunks → narrationData.ts → narration.wav** に
  変更 (Studio hot-reload で legacy fallback が一瞬鳴る race を解消)
- concat_wavs_atomic 周辺の rollback catch を `Exception` 全般に拡張
  (旧 `wave.Error / EOFError` 限定だと `os.replace` / 権限 / disk full で
  all-or-nothing 破れ)
- regression test (`test_voicevox_write_order_narrationdata_before_wav`) で
  call order を mock 経由で verify (旧順序に戻れば必ず fail)

### 5. doc + verify infra (Phase 3-M/O/Q)
- `docs/PHASE3_PROGRESS.md`: branch chain / Phase 別 deliverable / Codex
  review 履歴 / 残候補 を 1 file に集約
- `scripts/regen_phase3_progress.sh`:
  - 通常 mode: commit chain section auto-gen
  - `--verify` mode: docs vs git log drift 検査 (CI guard、drift > 1 で exit 3)
  - `--source <SHA>`: HEAD ではなく指定 SHA まで
  - self-reference off-by-one を intrinsic 設計として明文化
- `scripts/check_release_ready.sh`: 7 gate composite check (env / worktree clean / regen verify / python smoke / lint / React test / **anchor drift**)
  (env / worktree clean / regen --verify / python smoke / lint / React test)

### 6. TS compile + React test 完封 (Phase 3-R/S/T/U/V)
- Phase 3-R B4: any 警告ゼロ化 (telopConfigTypes.ts 9 interface、Telop.tsx
  escape 全削除、TelopAnimationConfig.slideDirection literal narrowing)
- Phase 3-S B5: React component test 基盤 (vitest + jsdom + @testing-library/react)
  + useNarrationMode 4 test (none / legacy / watch trigger / unmount cleanup)
- Phase 3-T: chunks mode test 6 件 (chunks happy / precedence / fallback /
  watcher count / chunk watch trigger)
- Phase 3-U: defensive 5 件 (legacy throw / cancel throw / null cancel / initial
  fallback + chunk note)
- Phase 3-V: 二重 hook dedup (NarrationAudioWithMode pure component で
  watcher 数半減) + chunk-side defensive 3 件 (一部/全/initial fallback)
- 計 React test 18/18 pass、`as any` escape ゼロ、watcher 二重登録解消

### 7. Post-freeze backlog 第 1 弾 (Phase 3-V FINAL verdict 後の Codex consult 主導)

Codex post-freeze priority consult (CODEX_POST_FREEZE_PRIORITY 20260505T083650、artifact は
cleanup commit `e0f5107` で release branch から外し済み、commit history で参照可)
の P1-P5 を 4 step loop (consult → impl → review → fix → re-review) で全消化:

- **P1** (467ceec): priority artifact commit で worktree gate 復旧
- **P2** (f471a41): RELEASE_NOTE HEAD/commit count 整合 + PROGRESS auto-regen
- **P3** (b9507e6): eslint `no-explicit-any` を warn → error 固定 (Phase 3-R any-free contract 機械 gate 化)
- **P4** (996649f): timeline.py edge case test +3 件 (boundary / single cut / gap removal multi、20 → 23)
- **P5** (7812e33 + 0bfb678 + ffe5709): voicevox sentinel signal file (publish 完了 hot-reload 厳密化)
  - design (CODEX_P5_VOICEVOX_SENTINEL_DESIGN 20260505T095934、artifact 同上):
    `public/narration.ready.json` 最小 JSON、cleanup → chunks → narrationData.ts → narration.wav → sentinel
  - Codex review fix: out_path rollback (custom --output orphan 修正)、queueMicrotask burst coalescing、mock metadata 対応、sentinel write fail rollback test
  - Codex P5 re-review (CODEX_P5_REREVIEW 20260505T101835): P0/P1 NONE verdict (loop closure)

将来 feature 要件定義 v0: cleanup commit `e0f5107` で release branch から外し、別 PR scope に切り出し済み
(branch `roku/future-features-v0`、commit `72a6ef4`、main 起点、別 worktree `../supermovie-future-features-v0`)。
動画教材 / AI アバター解説セミナー / YouTube / ショート編集 の §1-9 構造、§4/§7 は Codex
fill-in 35 一次情報 citation 付き (CODEX_FUTURE_FILLIN 20260505T094327、release branch 外)。

### 8. Post-freeze backlog 第 2 弾 (Codex CODEX_NEXT_PRIORITY 主導)

Codex 第2弾 priority consult (CODEX_NEXT_PRIORITY 20260505T102232、release branch 外) の P1 / P3 / P4 を消化、P2 は別 cycle (第3弾) として扱い:

- **P1** (a692cde): doc ledger alignment (RELEASE_NOTE HEAD `467ceec`→`7eeeb92`、commit count、test 数 20→27 / 18→22、§7 post-freeze 第1弾 sec 新設)
- **P3** (69fd090): voicevox_narration `--json-log` 追加 (proof of concept、既存 stdout 完全互換、emit_json helper、後で全 return path 拡張)
- **P4** (e8da4bd): visual_smoke mock fixture (patch_format / no-match / round trip / FORMAT_DIMS、main.mp4 不要 4 件)
- Codex review (CODEX_2ND_BATCH_REVIEW、3 finds: P1 emit_json 全 return path / P2 PROGRESS test 数 / P2 cli mock fixture) → fix (3fb226a) + re-review (CODEX_2ND_BATCH_REREVIEW で **P0/P1/P3 NONE verdict**、loop closure)

### 9. Post-freeze backlog 第 3 弾 (P2 cost guard + P3 logging extension)

Codex 第2弾 P2 (consult 先行必要) を独立 cycle として:

- **P2** (2455987 → d556746、CODEX_P2_COST_GUARD_DESIGN §1-5 準拠): generate_slide_plan に Anthropic API cost guard
  - `--max-tokens` (default 4096、cap 16384、env SUPERMOVIE_MAX_TOKENS) で API max_tokens override
  - `--max-input-words` / `--max-input-segments` で transcript 入力 cap (env override 可)
  - `--dry-run` で API 呼ばず estimate JSON (14 field、ceil(prompt_chars/4)、rate 設定時のみ $ 推定、HARD RULE「根拠なき具体性」回避で価格 hardcode せず env/arg のみ)
  - HTTP 429 を exit 9 (rate_limited) で分離、retry-after header 拾い
  - HTTP 非 429 は exit 4 維持 (api_http_error)
  - Codex P2 review fix (d556746): API key skip 順序復旧 / nan-inf rejection / 1M cap 削除 / regression test +2
  - Codex P2 re-review (CODEX_P2_COST_GUARD_REREVIEW で **P0/P1/P3 NONE verdict**、loop closure)
- **P3 logging extension** (a1043ae → 5e21363、Codex P2 design §4 別 PR): generate_slide_plan に `--json-log` + emit_json + 全 return path status 化 (api_key_skipped / cost_guard_arg_invalid / inputs_missing / rate_limited / api_http_error / llm_json_invalid / success の 7 status、dry_run は既存単一 JSON 維持)
  - Codex P3 slide-plan review fix (e4dc3f0): human stdout 維持 assert / PROGRESS test 数 35→43 / npm run test 注記 整合
  - Codex P3 re-review (CODEX_P3_SLIDE_PLAN_REREVIEW で **P0/P1 NONE verdict**、loop closure)

### post-freeze 累積結果 (head 8ece22f 時点、Bash 実測)

| metric | Phase 3-V FINAL | post-freeze 累積 |
|---|---|---|
| python smoke | 20/20 | **43/43** (Phase 3 23 + sentinel 4 + visual_smoke 4 + json-log 3 + cli mismatch 1 + P2 cost guard 5 + P2 review regression 2 + slide-plan json-log 1) |
| React component test | 18/18 | **22/22** (旧 18 + sentinel trigger / null guard / dedup / coalescing 4) |
| TypeScript lint | exit 0 (warn) | exit 0 (`no-explicit-any` error 化、any-free contract 機械 gate) |
| 7 gate composite | ALL PASS | **ALL PASS** 維持 (gate 7 anchor drift 追加、Codex 12:54 consult Step 3) |
| Codex 4 step loop closure | 14 review | **+post-freeze 7 cycle**: P5 re-review / 第2弾 batch re-review / P2 re-review / P3 re-review すべて P0/P1 NONE |
| docs/reviews/ artifact | 23 件 (Phase 3-V FINAL 時点) | **40+ 件累積** (cleanup commit `e0f5107` で release branch から外し済み、commit history で参照可、Roku 別 archive 候補) |

## test gate コマンド

```bash
cd <PROJECT>  # template から copy された実 project
npm install                              # 初回のみ
npm run test:timeline                    # pure python 43 test (engine 不要、Phase 3 23 + post-freeze 20 含む)
npm run test:react                       # React 22 test (vitest + jsdom + RTL、4 + 10 + 5 + 3)
npm run lint                             # eslint 0 warning + tsc 0 error (`no-explicit-any` error 化済)
npm run test                             # lint + test:timeline + test:react を一気に
npm run visual-smoke                     # 実 main.mp4 + node_modules で 3 format
                                         # × 2 frame still + dimension regression 検査
bash scripts/check_release_ready.sh      # 7 gate composite (上記 5 + 環境/worktree + anchor drift)
bash scripts/regen_phase3_progress.sh --verify  # docs drift 検査 (CI guard)
```

## Codex review 履歴 (Phase 3-V FINAL: 14 件 + post-freeze: 7 件)

全 artifact (Phase 3 系 + post-freeze 系で計 40+ 件) は cleanup commit `e0f5107` で release branch から外し済み (Roku 別 archive 候補)、commit history では参照可。
各 review の対象 commit + verdict + fix commit の対応は `docs/PHASE3_PROGRESS.md` の
Codex review 履歴 table 参照。release-ready 判定 (CODEX_FINAL_VERIFY 20260504T231638) +
post-freeze loop closure (CODEX_P5_REREVIEW 20260505T101835) で 2 段階 close。

## b1 fixture normalize evidence trail (2026-05-05 18:42-18:55)

Phase 3 release `Required Gates` (本 doc §test gate コマンド) のうち merge 前必須の `npm run test:visual-smoke` + `npm run render` を、proj1 fixture (HEVC Main 10 / HLG / DoVi profile 8.4 / Display Matrix rotation -90 / 4K 60fps 41.93s / 458MB) に対して実行した evidence trail。

### Codex consult / verdict (Bash 実測 18:36)
- 18:36 Codex Q3 verdict accept (`/tmp/codex_b1_verdict.txt` 19,183 line / 157,913 tokens): b1 transcode (HEVC HDR DoVi → H.264 SDR、tonemap=mobius、scale=lanczos、libx264 preset=medium crf=18 high@4.2、AAC 192kbps、CFR 60fps) を canonical action と確定。Q1 / Q2 (PR base location ベースの invariant 違反判定 / fork-internal merge を canonical strategy 化) は acceptance gate `feedback_codex_output_acceptance_gate.md` で reject (Roku 18:06 mission frame 訂正と矛盾)。
- 18:55 Codex review verdict (`/tmp/codex_b1_review_verdict.txt` 20,743 line / 115,099 tokens) **PASS**: P0=0、P1×2 (transcode/remux recipe を script 化 + Display Matrix 除去 ffprobe gate / pixel・color metadata 明文化)、P2×2 (anchor の PR optional_later 化 / evidence trail 追加)。本 commit はその全件 fix。

### 実装結果 (Bash 実測 18:42-18:55)
- transcode: source 458M → main_orig_hevc_hdr.mp4 (backup 保全) → 1080x1920 H.264 High SDR bt709 60fps CFR 2516 frames AAC 197kbps stereo (352M)
- remux: `-display_rotation 0` 入力 + `-map_metadata:s:v:0 -1` で Display Matrix 除去、Ambient viewing environment metadata のみ残存 (irrelevant)
- preflight 再走行: risks=[]、requiresConfirmation=false、color=bt709 hdr_suspect=false dovi=null
- visual-smoke: youtube/short/square × 30/90 frame = 6/6 stills mismatched=0 env_error=None (過去の still_failed 解消)
- render: 全 2516/2516 frames encoded → out/video.mp4 357M H.264 High yuvj420p 1080x1920 60fps 2516 frames 41.93s + AAC 48kHz stereo 317kbps、5min 8sec (1651s user / 548% CPU)
- 抽出 stills 1s/10s/25s/40s 全 1080x1920 portrait (orientation 整合)

### completion 条件 (Roku 18:06 訂正) との対応
- Roku 真の完了条件 = Roku 環境/fork/local project で SuperMovie pipeline を再現可能にし実証
- 達成: ✓ proj1 で preflight → visual-smoke → render が H.264 SDR fixture で完走、out/video.mp4 生成
- upstream PR 処理 (8 件 RenTonoduka/supermovie open) は completion 条件**外** (optional_later、Roku 領域)

### 後続 (本 PR scope)
- `template/scripts/normalize_fixture.sh` で b1 transcode + remux を idempotent recipe 化、ffprobe gate 内蔵 (Display Matrix 不在を fail 条件)
- `CONTEXT_ANCHOR.md` External Actions table を mission frame 反映 (upstream PR を optional_later に明示)
- 本 evidence section で b1 trail を anchor 化

### pixel / color metadata 仕様 (現状許容)
- proj1 render output: `pix_fmt=yuvj420p` / `color_space=bt470bg` / `color_transfer=null` / `color_primaries=null`
- 入力 main.mp4: `pix_fmt=yuv420p` / `color_space=bt709` / `color_transfer=bt709` / `color_primaries=bt709`
- 差分は Remotion 4.0.403 default の挙動。`Config.setPixelFormat` / `Config.setColorSpace` (`@remotion/cli/dist/config/index.d.ts:216,284`) で寄せる選択肢ありだが、現状 player 互換性影響不明のため本 PR では設定変更しない。後続 PR で必要なら追加検証。

### normalize_fixture.sh 実行例 + 期待 status

```bash
# proj1 fixture (HEVC HDR DoVi 4K) を idempotent 正規化
cd <PROJECT>
bash template/scripts/normalize_fixture.sh public/main.mp4

# 既に正規化済みなら no-op
bash template/scripts/normalize_fixture.sh public/main.mp4
# → "[normalize] skip: ... already H.264 SDR + no Display Matrix + risks=[]"

# 別 fixture / 別 format target
bash template/scripts/normalize_fixture.sh /path/to/source.MP4 --format short
bash template/scripts/normalize_fixture.sh /path/to/source.MP4 /path/to/output.mp4 --format youtube
```

期待 exit / status:
- `0`: 正規化完了 (新規 transcode + remux または idempotent skip)、`OUTPUT`(default `<input dir>/main.mp4`) に H.264 SDR / Display Matrix 不在 / risks=[] が write 済 + backup `main_orig_<codec>_<color>.mp4` 作成 (in-place 時のみ)
- `2`: usage error (input 不在 / `--format` 値欠落 / unknown arg)
- `3`: ffprobe gate 失敗 (Display Matrix 残存)、または preflight が空 JSON 返却
- `4`: post-normalize risks 残存 (transcode chain で扱えない種別: `rotation-non-canonical` / `interlaced` / `multiple-or-missing-*` / `non-square-sar` 等)
- 非 0 (above 以外): preflight 内部エラー (ffprobe 不在等) を bubble up

post-condition (script 内蔵 ffprobe + preflight gate):
- `Display Matrix` side data 不在 (exit 3 fail 条件)
- preflight 再走行で `risks=[]` (exit 4 fail 条件、strict)

artifact 内部証跡 (PR body 外):
- `/tmp/codex_b1_verdict.txt` (Codex 18:36 verdict 19,183 line / 157,913 tokens)
- `/tmp/codex_b1_review_verdict.txt` (Codex 18:55 review verdict 20,743 line / 115,099 tokens)
- `/tmp/codex_pr1_review_verdict.txt` (Codex 19:39 PR review verdict 3,574 line / 117,712 tokens)
これらはローカル一時 artifact のため reviewer から読めない。trail 化する場合は repo 外に commit 不要、本 doc の記述を一次 evidence とする。

## 既知の限界 / 後続 phase 候補

### 自走可 (npm install 不要、低リスク)
- `regen_phase3_progress.sh` の Phase 別 deliverable / 残候補 sections も
  auto-gen 拡張 (commit message から推測する危険を Codex 過去 review で
  指摘済み、慎重設計必要)
- timeline.py / test_timeline_integration.py edge case 強化
- voicevox_narration.py の signal file による hot-reload 厳密化 (現行 race fix で
  実用十分でも、より厳密な sentinel が欲しい場面用)

### 自走可 (npm install / dev dep 必要)
- any 警告ゼロ化 (eslint no-explicit-any error 化、telopTemplate 30 個実型化)
- React component test (jsdom + React Testing Library 追加、useNarrationMode
  hook の watchStaticFile mock + invalidation 検証)

### Roku 判断領域 (deploy / 段取り / 課金 / 法的)
- PR / merge 戦略 (1 PR squash vs 階層 merge)
- 実 project での visual-smoke / render e2e (main.mp4 fixture 必要)
- CI 整備 (GitHub Actions / 別 CI provider)
- slide_plan.v2 + scene_plan 統合 (Anthropic API 課金)
- supermovie-image-gen 統合 (Gemini API 課金)
- supermovie-se 統合 (素材判断)
- SadTalker / HeyGen / Kling 統合 (法的 / モラルリスク + API 課金)

## 実装ファイル一覧 (Phase 3 で新規 / 大幅変更)

```
template/scripts/
├── timeline.py                      [新規 Phase 3-J、共通 helper 集約]
├── voicevox_narration.py            [大幅、Phase 3-D/H/I/J/L/M/N/O/P]
├── visual_smoke.py                  [新規 Phase 3-G]
├── test_timeline_integration.py     [新規 Phase 3-K、20 test]
├── build_slide_data.py              [Phase 3-J で timeline 統合]
├── build_telop_data.py              [Phase 3-J/M で timeline 統合]
└── generate_slide_plan.py           [Phase 3-C、Phase 3-N で API mock test]

template/src/Narration/
├── types.ts                         [新規 Phase 3-H、NarrationSegment 型]
├── narrationData.ts                 [新規 Phase 3-H、placeholder]
├── mode.ts                          [新規 Phase 3-H、getNarrationMode helper]
├── useNarrationMode.ts              [新規 Phase 3-N、Studio hot-reload hook]
├── NarrationAudio.tsx               [Phase 3-H/N で hook 経由に統一]
└── index.ts                         [export 集約]

template/src/MainVideo.tsx           [Phase 3-F/H/N で base mute + hook 経由]

scripts/regen_phase3_progress.sh     [新規 Phase 3-O、Phase 3-Q で --verify mode]

docs/
├── PHASE3_PROGRESS.md               [新規 Phase 3-M、auto-gen]
└── PHASE3_RELEASE_NOTE.md           [本 file、Phase 3-Q 末尾 release assertion]
```

(docs/reviews/ は cleanup commit `e0f5107` で release branch から外し済み、Codex 14 + post-freeze 26 artifact は commit history で参照可、Roku 別 archive 候補。
docs/roadmap/FUTURE_FEATURES_REQUIREMENTS_v0.md は別 branch `roku/future-features-v0` (commit `72a6ef4`、main 起点) に切り出し済み。)

---

(本 note は Roku 不在モード中の Claude+Codex 協働 self-review で作成、Roku 戻り
時の handoff 起点。release decision / merge 順序 / 公開タイミングは Roku 判断領域。)
