#!/usr/bin/env bash
# Phase 3 progress note の commit chain section を git log から再生成する helper.
# Codex Phase 3-M review Part B 候補 vi 実装、Phase 3-Q で --verify mode 追加。
#
# Usage:
#   bash scripts/regen_phase3_progress.sh                 # 通常 regen
#   bash scripts/regen_phase3_progress.sh --verify        # docs vs git log 一致検査のみ (write しない、CI 用)
#   bash scripts/regen_phase3_progress.sh --source <SHA>  # HEAD ではなく指定 SHA まで
#   BASE=<branch> bash scripts/regen_phase3_progress.sh   # base branch 上書き
#
# 動作:
#   - git log "${BASE}..${SOURCE}" --oneline を取得
#   - docs/PHASE3_PROGRESS.md の "## 全 commit count" セクションを書換
#   - "## " の次 section 直前まで replace
#
# 自己参照 commit hash 問題 (Codex Phase 3-P review P2 反映):
#   - 本 script で regen → docs commit を作ると、その docs commit 自体は次回
#     regen まで chain に出ない (docs は HEAD-1 までを反映する形)
#   - これは intrinsic、circular update を避けるための設計
#   - --verify mode で「docs に書いてある commit count」と
#     「git log BASE..source の実 count」の差が 0 or 1 なら OK、それ以上で fail
#
# 制約:
#   - 現 branch を `git rev-parse --abbrev-ref HEAD` で動的取得、Phase 3-J 系 + 後続 PR (obs-* / 他) 対応
#   - 手動編集 section (Branch chain / Phase 別 deliverable / Codex review 履歴 /
#     未着手 / 残候補) は touch しない
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

PROGRESS_MD="docs/PHASE3_PROGRESS.md"
BASE_BRANCH="${BASE:-roku/phase3i-transcript-alignment}"
SOURCE_REF="HEAD"
VERIFY_ONLY=0

while [ $# -gt 0 ]; do
    case "$1" in
        --verify) VERIFY_ONLY=1; shift ;;
        --source) SOURCE_REF="$2"; shift 2 ;;
        *) echo "Unknown arg: $1" >&2; exit 64 ;;
    esac
done

if [ ! -f "$PROGRESS_MD" ]; then
    echo "ERROR: $PROGRESS_MD not found" >&2
    exit 1
fi

if ! git rev-parse --verify "$BASE_BRANCH" >/dev/null 2>&1; then
    echo "ERROR: base branch $BASE_BRANCH not found" >&2
    exit 2
fi

if ! git rev-parse --verify "$SOURCE_REF" >/dev/null 2>&1; then
    echo "ERROR: source ref $SOURCE_REF not found" >&2
    exit 2
fi

ACTUAL_COUNT=$(git rev-list --count "${BASE_BRANCH}..${SOURCE_REF}")

if [ "$VERIFY_ONLY" = "1" ]; then
    # docs に書かれている commit count を抽出 (line: "最新 N 件")
    DOC_COUNT=$(grep -oE '最新 [0-9]+ 件' "$PROGRESS_MD" | head -1 | grep -oE '[0-9]+' || echo "0")
    DIFF=$((ACTUAL_COUNT - DOC_COUNT))
    if [ "$DIFF" -lt 0 ]; then
        DIFF=$((-DIFF))
    fi
    echo "docs: $DOC_COUNT commits, git: $ACTUAL_COUNT commits, diff: $DIFF"
    if [ "$DIFF" -gt 1 ]; then
        echo "ERROR: doc commit count drift (>1) - run scripts/regen_phase3_progress.sh" >&2
        exit 3
    fi
    if [ "$DIFF" = "1" ]; then
        echo "INFO: 1 commit drift (likely the docs commit itself or a single new commit), within tolerance"
    fi
    exit 0
fi

COMMITS_FILE=$(mktemp)
git log "${BASE_BRANCH}..${SOURCE_REF}" --oneline > "$COMMITS_FILE"
COMMIT_COUNT="$ACTUAL_COUNT"
NOW=$(date +%Y-%m-%d_%H:%M)
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"

python3 - "$PROGRESS_MD" "$COMMITS_FILE" "$COMMIT_COUNT" "$NOW" "$SOURCE_REF" "$CURRENT_BRANCH" <<'EOF'
import re
import sys
from pathlib import Path

progress_path = Path(sys.argv[1])
commits_path = Path(sys.argv[2])
count = sys.argv[3]
now = sys.argv[4]
source_ref = sys.argv[5]
current_branch = sys.argv[6]

content = progress_path.read_text(encoding="utf-8")
commits = commits_path.read_text(encoding="utf-8").rstrip("\n")

# Update HEAD label in branch chain (└─ <branch> (HEAD))
content = re.sub(
    r"└─ [^\s(]+ \(HEAD\)",
    f"└─ {current_branch} (HEAD)",
    content,
)

new_section = f"""## 全 commit count ({current_branch} branch、最新 {count} 件)

```
{commits}
```

(更新: {now}、source={source_ref}、`scripts/regen_phase3_progress.sh` で auto-gen。
本 script で regen → docs commit する形のため、docs 上の commit chain は
docs commit を作る前の HEAD を反映する設計 (off-by-one は intrinsic、
`--verify` mode で count drift を CI 検査可)。)

"""

pattern = re.compile(r"## 全 commit count.*?(?=^## )", re.DOTALL | re.MULTILINE)
if not pattern.search(content):
    pattern = re.compile(r"## 全 commit count.*\Z", re.DOTALL)

new_content = pattern.sub(new_section, content, count=1)
progress_path.write_text(new_content, encoding="utf-8")
print(f"regenerated: {progress_path}")
print(f"commit count: {count}")
print(f"source ref: {source_ref}")
EOF

rm -f "$COMMITS_FILE"
echo "diff:"
git diff "$PROGRESS_MD" | head -30 || true
