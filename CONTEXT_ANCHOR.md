# CONTEXT_ANCHOR — SuperMovie Repo (Codex 主・Claude 従)

本 anchor は Roku 2026-05-05 11:38 確定の役割再定義 + 当時の Codex 命令 (CODEX_NEXT_STEP_INSTRUCTION 20260505T114718、artifact 喪失 / 内容は本 anchor §Verified Snapshot 〜 §Codex Review Protocol に反映済) に基づく作業中継 doc。push/PR 前に必ず最新化、Codex review prompt の先頭で本 doc 参照。欠落・stale なら Codex review で P1 扱い。

## Purpose

- **Codex CLI**: 命令 (priority / 設計 / review)
- **Claude Code**: 実装実行者のみ (方針判断禁止、Codex consult 経由)
- **Roku**: リスク領域判断のみ (push/PR/merge/deploy/外部副作用/段取り/モラル/法的/予定内容)

(Roku 発言 2026-05-05 11:38: 「Codex が命令し、あなたがそれに従い実装、そして Codex がレビューです、OK？」)

## Verified Snapshot (作成時点で Bash 実測、push/PR 前に再更新)

| 項目 | 値 (Bash 実測 2026-05-06 01:32) |
|---|---|
| HEAD (source commit) | `e722b4d` (PR-Q feat: applied_rules sorted(set(...)) 正規化 + 1 件 unit test + Migration step 17) |
| prev source commit | `359c7ff` (= fork/main、PR #19 PR-P entry exit code audit squash merge commit、PR-Q 着手前 base) |
| branch | `roku/redaction-rules-canonicalize` (`fork/main=359c7ff` 起点、PR #1-#19 全 merged 後の **PR-Q** = applied_rules canonicalize cycle) |
| main..HEAD | 1 commit (本 anchor refresh 前の feat、refresh 後 2 commits) |
| roku/phase3i-transcript-alignment..HEAD | merge commit を含むため raw count は意味薄、source 内訳は別 doc 参照 |
| origin remote | `https://github.com/RenTonoduka/supermovie.git` (READ only) |
| origin viewerPermission | READ (Roku gh account `blessing1031r-dotcom` は write 権限なし) |
| fork remote | `https://github.com/blessing1031r-dotcom/supermovie.git` (PR #1-#19 全 merged into fork/main = `359c7ff`、本 PR #20 は **redaction-rules-canonicalize (PR-Q)** applied_rules sorted(set) helper 正規化、fork-internal squash merge 予定) |
| gh auth status | ✓ Logged in (account: blessing1031r-dotcom、scopes: gist read:org repo workflow、Claude Code 側 12:00 / 12:32 / 12:38 / 12:41 で 4 回 valid 確認。Codex `--ephemeral` sandbox 内では token 不可視 = invalid 表示されるが Claude Code 実行環境に影響なし) |
| worktree | clean (cleanup commit `e0f5107` で `docs/reviews/**` 38 files + `docs/roadmap/FUTURE_FEATURES_REQUIREMENTS_v0.md` を release scope から外し済み、future doc は別 worktree `../supermovie-future-features-v0` の `roku/future-features-v0` branch `72a6ef4` に保全済) |
| 7 gate composite | ALL PASS at e722b4d (env / worktree clean / regen drift 1 / **86/86 python smoke (85 PR-P + 1 PR-Q)** / lint exit 0 / React 22/22 / **gate 7 anchor drift = 1 intrinsic OK**、Bash 実測 01:33) |
| b1 fixture e2e (`npm run test:visual-smoke` + `npm run render`) | proj1 で全 PASS (Bash 実測 2026-05-05 18:42-18:55、HEVC HDR DoVi 4K → H.264 SDR 1080x1920 60fps + 2516 frames render、`docs/PHASE3_RELEASE_NOTE.md` `b1 fixture normalize evidence trail` 参照) — PR #1 merged into fork/main |
| obs migration core | helper module `template/scripts/_observability.py` (~150 line) + slide-plan/narration helper 経由 refactor + redaction default strict + 6 regression test 全 PASS (Bash 実測 2026-05-05 20:42) |

## Roku Authorized Decisions (2026-05-05 user prompt 確定)

Roku 「OK、推奨から進めて」(11:46 user prompt) で以下 5 項目の Codex 推奨を一括採用。**ただし項目 1 (push 戦略) は Roku 18:06 訂正で完了条件 = local 再現性 と確定したため、upstream PR 部分は optional_later に再分類されている (§External Actions の Mission frame 注記参照)。本 table は 11:46 時点の history として保持**:

| # | 項目 | Codex 推奨 (採用済み、※ 18:06 後に解釈変更) |
|---|---|---|
| 1 | push 戦略 | **fork → blessing1031r-dotcom → (fork-internal merge or upstream PR)** ※ upstream PR 部分は 18:06 後 optional_later、現 PR は fork-internal で完結 |
| 2 | squash 範囲 | raw `docs/reviews` + `future doc` を release branch から **外す** |
| 3 | release note refresh | RELEASE_NOTE HEAD/commit count を **実測値で更新** |
| 4 | 実 e2e | fork CI 前に **local visual-smoke / render** (Roku 環境 main.mp4 fixture 必要) |
| 5 | future v0 doc | **別 PR** で切り出し (`roku/future-features-v0` branch、別途) |

## Release PR Scope (本 PR `roku/obs-rate-alias` (PR-D) の最終 diff target ※ release scope は historical reference として保持、本 PR-D は rate alias 実装に scope 限定)

**Include** (release tree に含める):
- `template/scripts/` 配下全 (timeline.py / voicevox_narration.py / build_slide_data.py / build_telop_data.py / generate_slide_plan.py / visual_smoke.py / preflight_video.py / budoux_split.mjs / compare_telop_split.py / test_timeline_integration.py)
- `template/src/` 配下全 (Narration / Slides / InsertImage / Title / SoundEffects / テロップテンプレート / メインテロップ / 強調テロップ / ネガティブテロップ / MainVideo.tsx / Root.tsx / videoConfig.ts / index.ts / index.css)
- `template/package.json` / `template/eslint.config.mjs` / `template/vitest.config.ts` / `template/vitest.setup.ts` / `template/tsconfig.json` / `template/remotion.config.ts`
- `scripts/check_release_ready.sh` / `scripts/regen_phase3_progress.sh`
- `docs/PHASE3_PROGRESS.md` / `docs/PHASE3_RELEASE_NOTE.md`
- `CONTEXT_ANCHOR.md` (本 doc)
- `.gitignore`
- `skills/` 配下全 + plugin manifest (`.claude-plugin/plugin.json`)

**Exclude** (release tree から外す、Codex 命令 §C / D):
- `docs/reviews/**` (37+ tracked files / 155,397+ lines、review artifact noise)
- `docs/roadmap/FUTURE_FEATURES_REQUIREMENTS_v0.md` (別 PR で出す、release delta と将来構想の混在防止)

`origin/main` 側には上記 exclude path が存在しないため、`git rm` で release branch から削除すれば final diff から消える (Codex 命令 §B 注記)。

## Required Gates (Draft PR 開始は composite gate のみで可、merge 前に Roku machine で visual-smoke / render 実行必須、Codex 12:08 命令 §A + Codex 4th review verdict)

```bash
# 1. self-driveable composite gate (Draft PR 開始 OK、Claude 自走可)
bash scripts/check_release_ready.sh                 # ALL PASS 必須

# 2. 実 project e2e (merge 前必須、Roku 環境必要、main.mp4 fixture)
cd template
npm run test:visual-smoke                           # 3 format × 2 frame still + dimension regression
npm run render                                       # 実 render 1 周

# 3. final worktree clean check
git status --short                                   # 空必須
```

`visual-smoke` / `render` は composite script の対象外 (`scripts/check_release_ready.sh:30-31`、`template/package.json:34-40`)、Roku 環境で main.mp4 fixture を持つ実 project から実行。

## External Actions (権限分類)

**Mission frame** (Roku 2026-05-05 18:06 訂正): fork-only 再現作業の完了条件は **Roku 環境 / fork / local project で SuperMovie pipeline を再現可能にし実証すること**。upstream PR の create / review / merge は **completion 条件に含まない optional_later** で、実行時は Roku 判断 + upstream repo 権限が必要。下表の `gh pr create --repo RenTonoduka/...` 行は handoff 時点の選択肢の 1 つで、Roku 判断後にしか動かない。

| action | 実行者 | 理由 |
|---|---|---|
| `gh repo fork RenTonoduka/supermovie --clone=false --remote=false` | **Claude 自走可** (Roku 「OK、推奨から進めて」授権済) | 自分の account への fork、外部副作用なし |
| `git remote add fork https://github.com/blessing1031r-dotcom/supermovie.git` | Claude 自走可 | local 設定 |
| `git push -u fork <branch>` (own account への fork push、現 branch 例: `roku/observability-doc`) | Claude 自走可 (auth scope `repo` あり、fork 先は own account) | 自 account への push |
| `gh pr create --repo blessing1031r-dotcom/supermovie --base main --head <branch>` (fork-internal PR、fork-only invariant 整合的) | Claude 自走可 | fork 内 merge 提案、外部 owner 不在、b1 fixture normalize PR #1 + obs-doc PR #2 で実行例あり |
| `gh pr create --repo RenTonoduka/supermovie --head blessing1031r-dotcom:<branch> --base main --title <X> --body-file <Y>` | **Roku 判断** (optional_later、completion 条件外) | upstream への merge 提案 = 段取り判断、外部 owner 経由 |
| PR review / merge | **Roku 判断 + RenTonoduka 操作必要** (optional_later、completion 条件外) | upstream maintainer 権限、destructive action |
| `git push --force` (any branch) | **Roku 明示授権必要** | history rewrite、destructive |
| remote branch delete (`git push fork :branch`) | **Roku 明示授権必要** | destructive |
| PR close / reopen | **Roku 判断** (optional_later、completion 条件外) | upstream PR の段取り |
| repo archive 作成 (raw review artifact 別保管用) | **Roku 判断** | 外部 repo 作成、段取り |
| GitHub re-auth (`gh auth login`) | **Roku 操作必要** (interactive) | Claude bash 経由不可 |
| rollback (`git revert <sha>`) | Claude 自走可 | local 操作 |
| force push rollback | **Roku 明示授権必要** | destructive |
| `template/scripts/normalize_fixture.sh <input>` (b1 fixture transcode + remux) | Claude 自走可 | local 操作、ffprobe gate で Display Matrix 不在を検証 |

## Codex Review Protocol

Codex review prompt の先頭で必ず本 anchor を参照させる:

```
まず `/CONTEXT_ANCHOR.md` を読み、Verified Snapshot / Release PR Scope / Required Gates / External Actions が現状と整合しているか確認してください。
欠落 / stale なら P1 (High) で指摘し、本 review の他項目より優先して fix を促してください。
ただし drift 1 (anchor 自身の document commit が source commit の後ろに 1 つ積まれた intrinsic gap) は P1 扱いしない。詳細は §Source commit vs Document commit 規約 参照。
```

## Source commit vs Document commit 規約 (drift 1 intrinsic / drift 2+ stale 機械判定、Codex 12:54 consult 推奨)

本 anchor §Verified Snapshot の `HEAD` は **source commit** を指す。anchor 自身の commit (= document commit) は含まない。これは `scripts/regen_phase3_progress.sh` と同じ off-by-one 設計で、Codex 12:08 命令「自己参照 commit hash は完全一致できない、PR body では必ず push 直前の Bash 実測値を使う」と整合。

| 用語 | 定義 |
|---|---|
| **source commit** | release assertion を指す最新の non-anchor commit (release scope 内の最終 code / docs commit、anchor 自身を除く) |
| **document commit** | anchor 自身 / release note / progress を refresh する docs commit |
| **drift** | `git rev-list <source_commit>..HEAD --count` (HEAD と source commit の差分 commit 数) |

**判定 rule**:

| drift | 状態 | Codex review verdict |
|---|---|---|
| 0 | anchor commit 前 (regen 中など過渡状態) | OK (anchor 未 publish) |
| **1** | anchor 自身の document commit が source commit の後ろに 1 つ積まれた状態 | **intrinsic、P1 扱いしない** |
| **≥2** | source commit の後に code / docs commit が 2 つ以上積まれた状態 | **stale、P1 扱い、anchor refresh 必要** |

**機械判定** (`scripts/check_release_ready.sh` gate 7 で実装済み、Task #9):
```bash
SOURCE_COMMIT=$(grep -m 1 '^| HEAD' CONTEXT_ANCHOR.md | sed -nE 's/.*`([a-f0-9]{7,})`.*/\1/p' | head -1)
DRIFT=$(git rev-list ${SOURCE_COMMIT}..HEAD --count)
NON_DOCS=$(git diff --name-only ${SOURCE_COMMIT}..HEAD | grep -vE '^(CONTEXT_ANCHOR\.md|docs/)' | wc -l | tr -d ' ')
[ "$DRIFT" -le 1 ] && [ "$NON_DOCS" -eq 0 ]   # true なら intrinsic / 安全 (gate 7 OK)
```

drift > 1 で docs-only でも stale (exit 7)。drift = 1 でも source の後に code commit があれば (= `NON_DOCS` > 0) stale 扱い。`bash scripts/check_release_ready.sh` で gate 7 が drift = 1 + docs-only で OK になることを confirm 済。

## 更新責任

- **更新者**: Claude (Codex 命令経由で各 cycle 末に Verified Snapshot を再実測 + Roku Authorized Decisions / Release PR Scope を更新)
- **更新タイミング**: push/PR/merge 直前 + 各 commit cycle 末
- **gate 化**: 現時点では `check_release_ready.sh` + `git diff --check` + `git status --short` clean で十分 (anchor check の自動化は release scope 外、後続 PR で検討)

## Related Files

- 元 Codex 命令: CODEX_NEXT_STEP_INSTRUCTION 20260505T114718 (artifact 喪失、内容は本 anchor に反映済)
- 元 Codex full review: CODEX_FULL_SESSION_REVIEW 20260505T113913 (cleanup commit `e0f5107` で release branch から外し済み、commit history で参照可)
- セッション handoff: `/Users/rokumasuda/0_Daily-Workspace/handoff_2026-05-05_supermovie-codex-pivot-overage.md`
- Memory 運用ルール: `~/.claude/projects/-Users-rokumasuda/memory/feedback_codex_master_claude_implementer.md`
