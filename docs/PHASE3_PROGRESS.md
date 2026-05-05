# SuperMovie Phase 3 Progress (2026-05-04 → 2026-05-05)

Phase 3-A 〜 Phase 3-V + post-freeze 第 1〜3 弾の commit 集約と Codex review 履歴。次セッション着手前の状態把握用。

`docs/PHASE3_PROGRESS.md` は `scripts/regen_phase3_progress.sh` で commit chain
section が auto-gen される。本文 (Phase 別 deliverable / Codex review 履歴 / 残候補)
は手動メンテ。`git log roku/phase3i-transcript-alignment..HEAD --oneline` を一次 source。

## Branch chain

```
main
 ├─ roku/phase3f-asset-gate    : Phase 3-F BGM/Narration asset gate
 ├─ roku/phase3g-visual-smoke  : Phase 3-F hotfix + Phase 3-G visual_smoke 初版 + 8 件 fix
 ├─ roku/phase3h-narration-sequence
 │     : Phase 3-H per-segment <Sequence> + 9 件 fix + vstack letterbox
 ├─ roku/phase3i-transcript-alignment
 │     : Phase 3-I transcript timing alignment + cut-aware mapping
 └─ roku/phase3j-timeline (HEAD)
       : Phase 3-J timeline.py 共通化 + 6 件 fix
       : Phase 3-K core 1 integration smoke test
       : Phase 3-K core 2 build_slide / build_telop transcript validation
       : Phase 3-J review 4 件 fix (P1 partial 含む)
       : Phase 3-L core require_timing strict mode
       : Phase 3-L vi build_slide e2e test + P1 partial fix
       : Phase 3-M core 1 build_telop e2e test (call_budoux stub)
       : Phase 3-M ii build_telop ms_to_playback_frame timeline 統合
       : Phase 3-M iii generate_slide_plan skip + missing inputs test
```

## Phase 別 deliverable サマリ

### Phase 3-F (asset gate, branch: roku/phase3f-asset-gate)
- `template/src/SoundEffects/BGM.tsx` + `Narration/NarrationAudio.tsx`:
  `getStaticFiles()` で public 配下 asset 有無検出、不在時 null
- 不在 OK → render 失敗しない (BGM/narration は optional)

### Phase 3-G (visual smoke, branch: roku/phase3g-visual-smoke)
- `template/scripts/visual_smoke.py`: 3 format × 2 frame の still + ffprobe + grid
- vstack letterbox fix (Codex bg 中の investigation で発見、新規 P1)
- Phase 3-F hotfix: base Video volume 自動 mute when narration.wav present

### Phase 3-H (per-segment Sequence, branch: roku/phase3h-narration-sequence)
- `template/src/Narration/types.ts`: NarrationSegment 型定義
- `template/src/Narration/narrationData.ts`: placeholder (script で all-or-nothing 上書き)
- `template/src/Narration/mode.ts`: getNarrationMode() (chunks / legacy / none 三経路)
- `voicevox_narration.py`: chunk 保持 + wave duration 測定 + atomic 書き込み
  + cleanup_stale_all + StaleCleanupError + wave.Error catch
- `MainVideo.tsx` + `NarrationAudio.tsx`: getNarrationMode() 経由

### Phase 3-I (transcript timing, branch: roku/phase3i-transcript-alignment)
- collect_chunks() を {text, sourceStartMs, sourceEndMs} dict 返しに変更
- write_narration_data() で transcript start_ms から videoConfig.FPS で frame 化
- vad_result.json があれば cut-aware mapping (build_cut_segments_from_vad +
  ms_to_playback_frame)
- 隣接 chunk overlap 検出 + WARN

### Phase 3-J (timeline 共通化, branch: roku/phase3j-timeline)
- `template/scripts/timeline.py`: 4 helper + 2 validation
  - read_video_config_fps / build_cut_segments_from_vad / ms_to_playback_frame /
    load_cut_segments / VadSchemaError / validate_vad_schema /
    TranscriptSegmentError / validate_transcript_segment(s)
- 3 script (voicevox / build_slide / build_telop) で FPS / cut helper を統一
- VAD 部分破損 fail-fast、transcript start>end / 型不正 fail-fast

### Phase 3-K (smoke test, on roku/phase3j-timeline)
- `template/scripts/test_timeline_integration.py`: 14 test ケース
- `template/package.json`: `test:timeline` / `test:react` script、`test = lint && test:timeline && test:react` (Phase 3-S 以降 React vitest も統合)
- `CLAUDE.md` に Visual Smoke + Timeline Test 節
- transcript validation を build_slide / build_telop にも展開 (require_timing=True)

### Phase 3-L (require_timing + Phase 3-J review fix, on roku/phase3j-timeline)
- timeline.validate_transcript_segment(require_timing) + validate_transcript_segments
- voicevox は optional 維持 (Phase 3-I 累積 fallback 互換)
- VAD load synthesis 前移動 + NARRATION_DIR.mkdir() 順序 fix (P1 partial)
- collect_chunks の validate を `.get(...).strip()` 前に移動 (P2 #1)
- build_telop で validate_vad_schema 経由 (P2 #2)
- SKILL.md に exit 3 / exit 8 追記 (P3)
- assert → RuntimeError raise (`python -O` safe)

### Phase 3-M (cut helper 完全統合 + smoke 拡張, on roku/phase3j-timeline)
- build_telop ms_to_playback_frame を timeline 経由 (find_cut_segment_for_ms 残置)
- build_telop e2e test (call_budoux stub)
- generate_slide_plan skip + missing inputs test
- docs/PHASE3_PROGRESS.md + docs/reviews/ 9 件 commit (Codex artifact)

### Phase 3-N (Studio hot-reload + API mock 完成, on roku/phase3j-timeline)
- generate_slide_plan API mock test (urllib monkey-patch、success / HTTP error / invalid JSON)
- Codex Phase 3-M review P2×3 fix (PHASE3_PROGRESS 更新 / API key save+restore /
  test isolation 強化)
- src/Narration/useNarrationMode.ts 新規 (watchStaticFile + invalidateNarrationMode +
  React state、Player/render では try/catch で no-op fallback)
- mode.ts に invalidateNarrationMode export 追加
- MainVideo / NarrationAudio が hook 経由に統一

### Phase 3-O (validation + auto-gen + race fix, on roku/phase3j-timeline)
- build_slide_data --plan validation 経路 test (Codex P2 #3 解消、fallback / strict 両 path)
- scripts/regen_phase3_progress.sh: PHASE3_PROGRESS commit chain 自動再生成 helper
- voicevox_narration.py write 順序を chunks → narrationData.ts → narration.wav に
  変更 (Codex Phase 3-N P2 hot-reload race 解消、Studio で chunks 経路が先に
  成立して legacy fallback が一瞬鳴る window を消す)

## Codex review 履歴

| review file | 対象 commit | verdict |
|---|---|---|
| CODEX_REVIEW_PHASE3F_20260504T205513 | Phase 3-F | P1×3 + P2×2 + P3×3、Phase 3-G で全 fix |
| CODEX_PHASE3G_NEXT_20260504T205513 | Phase 3-G design | visual_smoke + temp project + ffprobe |
| CODEX_REVIEW_PHASE3G_20260504T211444 | Phase 3-G 初版 | P1×3 + P2×3 + P3×2、Phase 3-G fix で全 close |
| CODEX_REVIEW_PHASE3G_FIX_20260504T212554 | Phase 3-G fix | token 切れで verdict 不完全、investigation で vstack バグ実証 |
| CODEX_REVIEW_PHASE3H_20260504T213301 | Phase 3-H | P1×2 + P2×4 + P3×3、Phase 3-H fix で全 close |
| CODEX_REVIEW_PHASE3H_FIX_AND_NEXT_20260504T214514 | Phase 3-H fix | 7/9 ✅ + ⚠️ partial 2 件 + 新規 P2/P3×3、residual 5 fix |
| CODEX_REVIEW_PHASE3I_AND_NEXT_20260504T215824 | Phase 3-I | P1×2 + P2×2 + P3×2、Phase 3-J で全 fix |
| CODEX_REVIEW_PHASE3J_AND_NEXT_20260504T221120 | Phase 3-J | P1×1 + P2×2 + P3×1、Phase 3-J review fix で close |
| CODEX_REVIEW_PHASE3J_FIX_AND_3L_20260504T222048 | Phase 3-J fix | 4/5 ✅ + P1 partial (NARRATION_DIR.mkdir 順序)、即 fix 済 |
| CODEX_REVIEW_PHASE3L_AND_3M_20260504T222846 | Phase 3-L vi + P1 partial fix | P2 #1 (cleanup contract コメント) + P2 #2 (test isolation)、94bc3d5 で全 fix |
| CODEX_REVIEW_PHASE3M_AND_3N_20260504T223552 | Phase 3-M comprehensive | P0/P1 なし、P2×3 (PHASE3_PROGRESS 不正確 / API key restore / API mock schema validation 未踏)、Phase 3-N 推奨 ii Studio hot-reload、f34abf3 で P2 #1+#2 / 6c8fb00 で P2 #3 fix |
| CODEX_REVIEW_PHASE3N_AND_3O_20260504T224734 | Phase 3-N + 3-O i/ii | P2 #1 (hot-reload race) + P3 #1 (PHASE3_PROGRESS body stale)、本 commit で全 fix |

## 未着手 / 残候補

### 自走可
- any 警告ゼロ化 (TS-side、eslint-config-flat 4.x、telopTemplate 30 個全 typing 必要、
  npm install 走らせる必要あり)
- Phase 3-O ii (PHASE3_PROGRESS auto-gen) は Phase 別 deliverable / Codex review 履歴 /
  残候補 sections も auto-gen するなら拡張余地あり
- voicevox_narration.py の sentinel 化 (現行 race fix で十分でも、より厳密な
  signal file を narrationData.ts 後に書く形も Codex 言及)

### Roku 判断領域 (deploy / 段取り / 課金 / 法的)
- ★ PR / merge 戦略 (roku/phase3j-timeline は phase3i / phase3h / phase3g / phase3f を
  ancestry 連結済み、`git merge-base --is-ancestor` 各 branch → HEAD で exit 0 確認済。
  Codex 推奨は 1 PR / squash merge / fork → upstream PR、CONTEXT_ANCHOR.md §External Actions 参照)
- Phase 3-G visual_smoke を実 project で end-to-end 検証 (main.mp4 fixture 必要)
- CI 整備 (GitHub Actions / 別 CI provider、test:timeline + lint 自動化)
- slide_plan.v2 + scene_plan 統合 (Anthropic API 課金)
- supermovie-image-gen 統合 (Gemini API 課金)
- supermovie-se 統合 (素材判断)
- SadTalker / HeyGen / Kling 統合 (法的 / モラルリスク + API 課金)

## 全 commit count (roku/phase3j-timeline branch、最新 122 件)

```
1bc7bf5 fix(normalize_fixture)+docs(phase3): archive collision + inferred fallback strict + release note stale (Codex 20:14 4th review P2)
a429783 docs(phase3): refresh anchor/note/progress to aa244bd / 138 / 120 — Codex 20:04 3rd review fix iter 3 反映 + release note 冒頭 stale 解消
aa244bd fix(normalize_fixture): early --format validation + RISKS-aware idempotent skip + stale backup collision (Codex 20:04 3rd review P2)
aad745b docs(phase3): refresh anchor/progress to 983edc4 / 136 / 118 — Codex 19:53 PR re-review fix iter 2 反映
983edc4 fix(normalize_fixture)+docs(phase3): path normalize + mktemp -d temp dir + strict post-risks (Codex 19:39 PR re-review fix iter 2)
acd15dd docs(phase3): refresh anchor/progress to 5ce2bc5 / 134 / 116 — Codex 19:39 PR review fix iter 反映
5ce2bc5 fix(normalize_fixture)+docs(phase3): preflight exit 2 tolerance + arg/temp/anchor harden (Codex 19:39 PR review fix)
41f9ef6 docs(phase3): refresh anchor/progress to 9a37873 / 132 / 114 — anchor drift = 1 intrinsic 復帰 + b1 e2e PASS 行追加
9a37873 scripts+docs(phase3): b1 fixture normalize recipe + anchor mission frame patch (Codex 18:55 P1/P2 fix)
8310a4c docs(phase3): refresh anchor/note to cad6914 / 130 / 112 + PROGRESS regen — gate 7 を pass させる anchor refresh (前 commit の scripts 修正 cad6914 が non-docs のため drift 1 intrinsic 復帰)
cad6914 docs+gate(phase3): gate 7 SKIP→exit 7 (anchor 欠落/parse 失敗 P1 扱い) + anchor fork remote 行 + 6 gate→7 gate 表記 統一 + refresh anchor/note to f6e89ef / 129 / 111 (Codex hardening review P1×2 + P2 fix)
f6e89ef docs(phase3): refresh anchor/note to 7e4b328 / 128 / 110 + PROGRESS regen — gate 7 (anchor drift) を pass させる anchor refresh、§Source commit vs Document commit 規約 drift 1 intrinsic 復帰
7e4b328 docs+gate(phase3): check_release_ready.sh に gate 7 (anchor drift) 追加 + anchor §機械判定 を実装済 に書き換え + refresh anchor/note to 6e89ef5 / 127 / 109 + PROGRESS regen (Codex 12:54 consult Step 3)
6e89ef5 docs(phase3): add Source vs Document commit 規約 to anchor (drift 1 intrinsic / drift 2+ stale 機械判定 sketch、Codex 12:54 consult 推奨 Step 1) + refresh anchor/note to 4e21826 / 126 / 108 + PROGRESS regen
4e21826 docs(phase3): refresh anchor + release note to 4528a6b / 125 commits + Required Gates 文言を Draft PR 開始 / merge 前必須で区別 + gh auth Codex sandbox 観察 + PROGRESS regen (drift 1)
4528a6b docs(phase3): refresh anchor + release note to 7438df5 / 124 commits + PROGRESS regen (drift 1)
7438df5 docs(phase3): close stale P1 (PROGRESS scope A-V + post-freeze, ancestry 連結済 PR 戦略, RELEASE_NOTE test 27→43 統一)
bfeb5ad docs(phase3): refresh anchor + release note to 31dd9cc / 122 commits + remove stale reviews/ tree + PROGRESS regen (drift 1 intrinsic)
31dd9cc docs(phase3): PROGRESS auto-regen for stale ref fix commit (drift 1)
264636f docs(phase3): replace stale CODEX artifact path references with branch+commit format
8517307 docs(phase3): refresh anchor + release note + progress to e0f5107 (main..HEAD 119)
e0f5107 docs(release): remove review artifacts and future roadmap from PR scope
d755449 docs: add context anchor
1bc6bab docs(phase3): PROGRESS auto-regen for full session review (drift 0)
afa7680 docs(phase3): Codex full session review + RELEASE_NOTE HEAD/commit count を実態に整合
e78bb88 docs(phase3): PROGRESS auto-regen for RELEASE_NOTE post-freeze addendum (drift 0)
4f84fb6 docs(phase3): RELEASE_NOTE §8/§9 で post-freeze 第2弾 + 第3弾 + P3 ext addendum 反映
8ece22f docs(phase3): PROGRESS auto-regen for P3 re-review fix (drift 0)
5ee8de7 docs(phase3): P3 slide-plan re-review fix (artifact commit + PROGRESS npm run test 整合)
5e21363 docs(phase3): PROGRESS auto-regen for P3 slide-plan review fix (drift 0)
e4dc3f0 fix(slide-plan): P3 logging review 3 件 fix (P2 artifact / P3 stdout assert / P3 PROGRESS 数)
f321da3 docs(phase3): PROGRESS auto-regen for slide-plan json-log (drift 0)
a1043ae feat(slide-plan): P3 --json-log logging を generate_slide_plan に展開 (Codex P2 design §4)
15a405c docs(phase3): PROGRESS auto-regen for P2 re-review (drift 0)
f7a7e8f docs(reviews): Codex P2 cost guard re-review (loop closure verdict P0/P1/P3 NONE)
cfc223d docs(phase3): PROGRESS auto-regen for P2 review fix (drift 0)
d556746 fix(slide-plan): Codex P2 cost guard review 3 件 fix (P1 skip 順序 / P2 nan-inf / P3 1M cap)
dca2738 docs(phase3): PROGRESS auto-regen for P2 cost guard (drift 0)
2455987 feat(slide-plan): P2 Anthropic API cost guard (max-tokens / dry-run / 429 分離 / input cap)
ffe15a3 docs(phase3): PROGRESS auto-regen for 第2弾 re-review artifact (drift 0)
251ca73 docs(reviews): Codex 第2弾 re-review artifact (loop closure verdict P0/P1/P3 NONE)
4d43509 docs(phase3): PROGRESS auto-regen for 第2弾 review fix (drift 0)
3fb226a fix(post-freeze): Codex 第2弾 batch review 3 件 fix (P1 全 return path / P2 PROGRESS 数 / P2 cli fixture)
7efd3cd docs(phase3): PROGRESS auto-regen for P3 json-log (drift 0)
69fd090 feat(narration): P3 --json-log で voicevox observability (Codex 第2弾 priority、proof of concept)
8fa1029 docs(phase3): PROGRESS auto-regen for P4 visual_smoke fixture (drift 0)
e8da4bd test(visual-smoke): P4 mock fixture 4 件 (patch_format / no-match / round trip / FORMAT_DIMS)
a692cde docs(phase3): P1 doc ledger alignment (Codex 第2弾 priority、HEAD/test count/post-freeze addendum)
7eeeb92 docs(phase3): PROGRESS auto-regen for P5 re-review artifact (drift 0)
ffe5709 docs(reviews): Codex P5 re-review artifact (P5 cycle loop closure verdict P0/P1 NONE)
da517f8 docs(phase3): PROGRESS auto-regen for P5 review fix (drift 0)
0bfb678 fix(narration): Codex P5 review 4 件 fix (P1 out_path rollback / P2 burst coalescing / dedup mock / write fail test)
77f8689 docs(phase3): PROGRESS auto-regen for P5 commit (drift 0)
7812e33 feat(narration): P5 voicevox sentinel signal file (publish 完了 hot-reload 厳密化)
af8b2ba docs(phase3): PROGRESS auto-regen for review fix commit (drift 0)
534287c docs: Codex post-freeze review 3 件 fix (P1 worktree / P2 v0 状態整合 / P3 PROGRESS test 数)
a85bdb1 docs(phase3): PROGRESS auto-regen for v0 fill-in commit (drift 0)
794e3bc docs(roadmap): 将来 feature 要件定義 v0 に Codex fill-in integrate (§4/§7 + §9 References)
9b3c0ff docs(phase3): PROGRESS auto-regen for v0 roadmap commit (drift 0)
eb55209 docs(roadmap): 将来 feature 要件定義 v0 起草 (動画教材 / アバター / YouTube / ショート)
21dd075 docs(phase3): PROGRESS auto-regen for P3/P4 (drift 0 復旧)
996649f test(timeline): edge case coverage 拡張 (boundary / single cut / gap removal multi)
b9507e6 chore(lint): no-explicit-any を warn → error に固定 (Phase 3-R any-free contract 機械 gate 化)
f471a41 docs(phase3): RELEASE_NOTE HEAD/commit count を 467ceec/58 に整合 + PROGRESS auto-regen
467ceec docs(reviews): Codex post-freeze priority consult artifact
c25767a docs(phase3): regen 57 commits
a659be6 docs(reviews): Codex PR/squash/gate draft artifact
712b0d3 docs(phase3): regen 55 commits
18fa679 docs(phase3): RELEASE_NOTE を Phase 3-R/S/T/U/V 反映に update
ad15fd2 docs(phase3): regen 53 commits
75145de docs(reviews): Codex Phase 3-V final assessment artifact
89fc78c docs(phase3): regen 51 commits
397c584 feat(narration): 二重 hook dedup + chunk-side defensive test (Phase 3-V)
dd7f9e4 docs(phase3): regen 49 commits
2d7d96a fix(test): defensive test の lint error 修正 (eslint-disable for unused mock signature args)
35c21e5 docs(phase3): regen 47 commits
b8d0c0e test(narration): defensive path test for useNarrationMode (Phase 3-U)
f2e7a65 docs(phase3): regen 45 commits
2326f29 test(narration): chunks mode 経路の React component test 追加 (Phase 3-T)
b2f5cc4 docs(phase3): regen 42 commits
668b256 feat(release): check_release_ready.sh に React component test gate (Phase 3-S B5 統合)
3b73578 feat(test): React component test 基盤 + useNarrationMode 4 test (Phase 3-S B5)
6dfc0ce docs(phase3): regen commit chain to 40
53e422e feat(telop): any 警告ゼロ化 (Phase 3-R / Codex 推奨 B4)
f7e291c docs(phase3): regen commit chain to 38 (post lint fix + Codex 3-R artifact)
e84c3a9 docs(reviews): Codex Phase 3-R consult artifact (resume after AFK)
214ce30 chore(gitignore): template/package-lock.json 追加
7763fdb fix(lint): insertImageData / titleData の unused toFrame 解消
00d62c4 docs(phase3): regen commit chain to 33
155f396 chore(gitignore): __pycache__/ + *.pyc 追加
5dc2fb7 docs(phase3): regen commit chain to 31
a1c615e feat(release): check_release_ready.sh に optional lint gate (Codex 最終推奨)
b2f8974 docs(phase3): regen commit chain to 29
e31eafe feat(release): check_release_ready.sh composite gate (Phase 3-Q)
c40ed7f docs(phase3): regen commit chain to 27
f9bd729 docs(phase3): release-ready note + final Codex verify artifact
d71c503 docs(phase3): regen commit chain to 25 + release-readiness artifact
5a10f21 docs(reviews): Codex Phase 3-P review + 3-Q consult artifact
bce03e0 feat(docs): regen_phase3_progress.sh --verify mode + self-reference doc (Phase 3-Q ii)
32a6bfa docs(phase3): regen commit chain to 22 commits
d41ec9c fix(narration): Codex Phase 3-O fix re-review P1 + P2 #2 actual code fix
b70b592 fix(narration): Codex Phase 3-O fix re-review P1 + P2×2 + P3 全 fix
aacc5dc test(narration): write 順序 race fix の regression test (Phase 3-O follow-up)
9876e61 docs(phase3): regen commit chain section to 18 commits
a5fcb80 fix(narration): Studio hot-reload race + PHASE3_PROGRESS body update (Phase 3-N review fix)
d10cd92 feat(docs): PHASE3_PROGRESS.md auto-gen helper (Phase 3-O ii)
6c8fb00 test(timeline): build_slide_data --plan validation 経路 (Phase 3-O i / Codex P2 #3)
1d27892 feat(narration): Studio hot-reload via watchStaticFile (Phase 3-N ii)
f34abf3 fix(timeline): Codex Phase 3-M review P2 #1 + #2 fix
ae3d2e8 test(timeline): generate_slide_plan API HTTP error + invalid JSON path (Phase 3-N i+)
8abdb2b test(timeline): generate_slide_plan Anthropic API mock test (Phase 3-N i)
94bc3d5 fix(timeline): Codex Phase 3-L re-review P2 2 件 fix
47e6c39 docs(phase3): Phase 3 progress note + Codex review artifacts (Phase 3-M v)
bed46b7 test(timeline): generate_slide_plan skip + missing inputs subtest (Phase 3-M iii)
350dff7 refactor(telop): ms_to_playback_frame を timeline 経由に統一 (Phase 3-M ii)
3c765e3 test(timeline): build_telop_data e2e + bad transcript subtest (Phase 3-M core 1)
96e5215 fix(narration): P1 partial NARRATION_DIR.mkdir 順序 + Phase 3-L vi 展開
a9019c7 feat(timeline): require_timing strict mode + validate_transcript_segments helper (Phase 3-L core)
e2a1a39 fix(timeline): Codex Phase 3-J review 4 件 fix (P1×1 + P2×2 + P3×1)
41b5ef2 fix(timeline): transcript validation を build_slide / build_telop にも展開 (Phase 3-K core 2)
398ea94 test(timeline): pure python integration smoke test (Phase 3-K core 1)
66e2aeb feat(timeline): timeline.py 共通化 + Phase 3-I review 6 件 fix (Phase 3-J)
```

(更新: 2026-05-05_20:16、source=HEAD、`scripts/regen_phase3_progress.sh` で auto-gen。
本 script で regen → docs commit する形のため、docs 上の commit chain は
docs commit を作る前の HEAD を反映する設計 (off-by-one は intrinsic、
`--verify` mode で count drift を CI 検査可)。)

## Test gates

```bash
cd <PROJECT> (template から copy された実 project)
npm run test           # eslint + tsc + pure python integration smoke
npm run test:timeline  # pure python integration smoke 単独 (engine 不要、CI 高頻度可)
npm run visual-smoke   # 実 main.mp4 + node_modules 必要、3 format × 2 frame still
```

`test:timeline` は **43 test ケース** (Phase 3-A〜3-V 23 + post-freeze 第1弾 voicevox sentinel
4 + 第2弾 visual_smoke 4 + json-log success/skip/strict 3 + cli mismatch+restore 1 + 第3弾 P2
cost guard 5 + P2 review regression 2 + slide-plan json-log 1 累積、最新 P3 logging extension
反映時点) で timeline.py / 4 script + visual_smoke の連鎖を engine 不要で高速検証 (新規
commit 後の regression 早期検出用)。test 一覧は `scripts/test_timeline_integration.py` の
`main()` 末尾参照。`npm run test` は実際には `lint && test:timeline && test:react` を順次
実行 (template/package.json:41 で確認、Phase 3-S 以降 React vitest も統合)。test:react は
`vitest run --config vitest.config.ts` 経由で 22 React test 実行 (旧説明「別 script」表記を
更新)。
