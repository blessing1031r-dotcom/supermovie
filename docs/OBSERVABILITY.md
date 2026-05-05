# SuperMovie Observability Contract

本 doc は SuperMovie pipeline の observability に関する最小規約を固定する。Codex 19:50 consult verdict (Tech 改善 Medium #3、`/Users/rokumasuda/0_Daily-Workspace/handoff_2026-05-05_1741_supermovie-phase3-pr-cycle.md:60`) に準拠した doc-only PR scope。script migration / env var rename / provider price 記載 / external SaaS 前提は本 doc では扱わない。

## Scope And Non-Goals

### Scope (本 doc が固定する)
- status JSON emission の canonical な仕様 (`--json-log` 動作、stdout/stderr 区別、common fields)
- log redaction の対象種別と適用 rule
- cost telemetry の単位・rate env var convention・missing rate 時の挙動
- migration policy (current v0 → future v1)
- test 要件 (regression 防止)

### Non-Goals (本 doc では扱わない)
- script 実装 (本 PR は doc-only、migration commit は別 PR)
- env var rename (既存 `SUPERMOVIE_RATE_INPUT_PER_MTOK` 系の alias 化以上は別 PR)
- provider 個別 pricing 記載 (network 制約下で未検証、`docs/api-operation-guard` 系参照)
- external SaaS / GCP / credential 前提 (本 PR では required にしない)
- distributed tracing / metrics export 実装 (`run_id` reservation のみ、実装は別 PR)

## Current Surface

実装済の observability surface (Bash 実測 2026-05-05 21:24、PR #3 + PR-A merged 後):

| script | --json-log | cost guard | dry-run | redaction status |
|---|---|---|---|---|
| `generate_slide_plan.py` | ✓ helper 経由 v1 (PR #3 merged) | ✓ ([:161,297](../template/scripts/generate_slide_plan.py)) | ✓ ([:159](../template/scripts/generate_slide_plan.py)) | helper 経由 (HTTP body / LLM raw text default redact、output path safe) |
| `voicevox_narration.py` | ✓ helper 経由 v1 (PR #3 merged) | minimal | — | helper 経由 (chunk text default redact、summary path safe) |
| `compare_telop_split.py` | ✓ helper 経由 v1 + category_override=`kpi-comparison` (PR-A) | — | — | helper 経由 (artifact path safe、`/tmp/` 等 system tmpdir も placeholder) |
| `visual_smoke.py` | ✓ helper 経由 v1 + category_override=`dimension-regression` (PR-A)、summary JSON artifact ([:365](../template/scripts/visual_smoke.py)) は維持 | — | — | helper 経由 (summary / grid path safe) |
| `preflight_video.py` | ✓ helper 経由 v1 (PR-B、既存 stdout source JSON 維持 + --json-log で末尾 v1 tail emit、success category=`preflight-source-meta` / error は STATUS_MAP 詳細 category) | — | — | helper 経由 (write-config path safe) |
| `timeline.py` | — (library 性質、Codex 21:01 step 3 S3-2 で migration 対象外) | — | — | — |
| `build_slide_data.py` | — (PR-C 候補) | — | — | raw title/telop text on stdout |
| `build_telop_data.py` | — (PR-C 候補) | — | — | raw title/telop text on stdout |

v1 helper 経由は generate_slide_plan / voicevox_narration / compare_telop_split / visual_smoke / preflight_video の **5 script** が適用済 (PR #3 + PR-A + PR-B merged 後)。残 2 script (`build_slide_data.py` / `build_telop_data.py`) は PR-C で migration。`timeline.py` は呼び出し元 script 側で status emit される library 設計。

### v0 JSON tail / output gap (Codex 20:08 review O-1, P2 #3 reflect)

現 v0 emission には schema v1 と不整合な点が 2 つある (migration policy section の mapping で扱う):
- `generate_slide_plan.py:380` の output JSON は `path` field に絶対 path を含む。schema v1 では `redaction.applied_rules=[abs_path]` 必須。
- `voicevox_narration.py:778` の summary JSON も同様に absolute path を含む。
- `generate_slide_plan.py:279` の dry-run path は `--json-log` flag なしで JSON を stdout に出す legacy 動作。schema v1 では `--json-log` 強制を要さず legacy として canonical 化する。

## Status JSON Contract

### Emission Contract

- `--json-log` flag が指定された時のみ、stdout の **末尾 1 行** に純 JSON (改行で終端) を emit する。
- human-readable stdout (進捗 message 等) は `--json-log` 有無に関係なく維持する。
- error / warning は `stderr` に出す。`--json-log` 時も stderr 経路は変えない。
- 1 invocation 1 emission を default。multiple emission を採る場合は `schema_version` で variant を区別する。

既存の test 契約 (`test_timeline_integration.py:1167`, `:2173`) は本 contract の正規化版。

### Common Fields (schema v1)

```json
{
  "schema_version": 1,
  "script": "generate_slide_plan",
  "status": "ok|skipped|error|dry_run",
  "ok": true,
  "exit_code": 0,
  "category": "phase3-slide|phase3-narration|...",
  "duration_ms": 1234,
  "counts": { "<domain-specific>": 0 },
  "artifacts": [
    { "path": "<relative or redacted absolute>", "kind": "json|wav|ts|png|..." }
  ],
  "cost": {
    "currency": "USD",
    "estimate": 0.0,
    "rate_source": "env:SUPERMOVIE_RATE_..._PER_MTOK"
  },
  "redaction": {
    "applied_rules": ["api_key", "abs_path", "user_content"],
    "version": 1
  },
  "run_id": "<reserved for future tracing, optional, UUID v4>",
  "parent_run_id": "<optional>",
  "step_id": "<optional>"
}
```

### Status Naming (canonical 値)

| value | 用途 |
|---|---|
| `ok` | 成功完走 |
| `skipped` | precondition 不在等で no-op skip (e.g., engine 不在で voicevox skip、idempotent skip) |
| `error` | 異常終了 (exit_code ≠ 0)、`category` で詳細 |
| `dry_run` | API call せず estimate のみ出力 |

### Stdout And Stderr

| stream | 内容 |
|---|---|
| stdout (human) | 進捗 message、preflight output、最終 summary。`--json-log` 時も維持。 |
| stdout (json tail) | 末尾 1 行に schema v1 JSON。`--json-log` 指定時のみ。 |
| stderr | error message、warning、stack trace、retry attempt。 |

`--json-log` consumer は **stdout の末尾 1 行のみを JSON parse** する想定。途中の human message を JSON parse させない。

### Script Coverage Matrix

current state (v0) は `generate_slide_plan.py` / `voicevox_narration.py` が `status` / `exit_code` を出す最小形 ([generate_slide_plan.py:177](../template/scripts/generate_slide_plan.py), [voicevox_narration.py:533](../template/scripts/voicevox_narration.py))。本 doc 確定後の future v1 で全 script に schema v1 を適用する別 PR を予定。

## Log Redaction Contract

### Sensitive Classes

| class | 例 | source |
|---|---|---|
| secret | `ANTHROPIC_API_KEY` 等 API key、token、credential | env / config / argv |
| user_content | transcript text、segments、telop raw text、prompt body | input file / API request body |
| abs_path | `/Users/<name>/...` machine-local 絶対 path | argparse / sys.argv / output file path |
| provider_response_body | LLM API の response 全文 | network response |

### Redaction Rules (Codex 20:08 review P1 #2 で strict 化)

- secret: 値の最後 4 文字以外をマスク (`sk-...XXXX`)。env name / config key 名は出して可。stderr / json tail / human stdout 全て同 rule。
- user_content (transcript / segments / chunk text / telop raw):
  - human stdout: **default は `length` / `hash` のみ表示**、raw 出力は debug opt-in flag (`--unsafe-show-user-content` 等) 限定。`first-N-chars` 等の partial preview も default では出さない (raw partial も raw の subset とみなす)。現 `voicevox_narration.py:626` chunk text human log は migration 対象。
  - external structured log / json tail: `length` / `hash` のみ、raw 禁止 (default / debug 共通)。
  - debug opt-in 時も secret-bearing input (transcript 内に API key 等) は事前 detection + 削除。
- abs_path:
  - human stdout: 出してよい (local debug 用途)。
  - json tail / artifact path / external log: repo root or project root 相対 path に変換。`~` 展開後の絶対 path には `<HOME>` placeholder を適用。
- provider_response_body (LLM API の raw response):
  - **stderr であっても default は raw 禁止** (request_id / status_code / token_usage の structured summary のみ出す)。raw body は `--unsafe-dump-response` 等 debug opt-in flag 時に限定。
  - secret-bearing header (Authorization / x-api-key 等) は事前 strip。
  - 現 `generate_slide_plan.py:347` (HTTP error response body) / `:351` (non-429 HTTP error で `body[:500]` partial を stderr 出力) / `:366` (LLM raw text on JSON parse error) は stderr 経由で raw を出している経路で、migration 対象。
  - migration test 必須事項: HTTP body / LLM raw text を含む test fixture で、default emission に raw が現れず、`--unsafe-dump-response` 指定時のみ出ることを `test_timeline_integration.py` で regression test 化する。

### Path Policy

artifact `path` field は repo root or project root からの **相対 path** で記録する。絶対 path は `redaction.applied_rules` に `abs_path` を含めて redacted variant を記録するか、user invocation context で必要なら明示 opt-in flag (`--unsafe-keep-abs-path`) を script 側に追加 (本 doc は規約のみ、実装は別 PR)。

### User Content Policy

`build_slide_data.py:388` / `build_telop_data.py:453` のように raw title/telop text を human stdout に出している script は、json tail には length / hash のみを出す形に migration する (別 PR)。raw text を残す合理理由 (debug 等) がある場合は flag 化して default off。

## Cost Telemetry Contract

### Cost JSON Shape

```json
{
  "currency": "USD",
  "estimate": 0.0,
  "rate_source": "env:SUPERMOVIE_RATE_<PROVIDER>_<DIR>_USD_PER_MTOK",
  "rate_input_usd_per_mtok": 1.0,
  "rate_output_usd_per_mtok": 5.0,
  "tokens_input": 1234,
  "tokens_output": 567,
  "rate_missing": false
}
```

### Rate Env Var Convention

- v1 canonical: `SUPERMOVIE_RATE_<PROVIDER>_<DIRECTION>_USD_PER_MTOK`
  - 例: `SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK`、`SUPERMOVIE_RATE_GEMINI_OUTPUT_USD_PER_MTOK`
- v0 (既存): `SUPERMOVIE_RATE_INPUT_PER_MTOK` / `SUPERMOVIE_RATE_OUTPUT_PER_MTOK` ([generate_slide_plan.py:161](../template/scripts/generate_slide_plan.py))
  - v0 alias は **`generate_slide_plan.py` の Anthropic input/output rate 専用** で定義 (Codex 20:08 review P2 #1)。Gemini / Kling 等 future provider には v1 canonical のみを使う。
  - v1 が未設定で v0 が設定されている場合の alias 動作 (Anthropic 限定) を future PR で実装 (本 doc は規約のみ)。

### Provider Notes

| provider | unit | rate kind |
|---|---|---|
| Anthropic API (slide plan) | $/MTok input + $/MTok output | text generation、env 必須 |
| Gemini API (image gen) | $/image | image generation、別 env 系 (本 doc 後の別 PR で固定) |
| VOICEVOX (narration) | local engine、cost なし ([voicevox_narration.py:66](../template/scripts/voicevox_narration.py)) | telemetry 対象外 |
| Kling / 他 (将来) | provider 個別 | 別 doc 化 |

### Missing Rate Behavior

- rate env が未設定: `cost.estimate = null`、`cost.rate_missing = true`、`status = ok` (cost 不明は error 扱いしない)。
- rate が設定されているが invalid (NaN / 負値等): `status = error`、`category = "rate-invalid"`、exit_code 非 0。
- `nan/inf` ガードは既存 `generate_slide_plan.py:120` 系の挙動を canonical とする。

## Migration Policy

| state | criteria |
|---|---|
| v0 (current) | `status` / `exit_code` を出す最小 status JSON、redaction なし、cost rate env は v0 名 |
| v1 (target) | schema_version=1、common fields 全埋め (null OK)、redaction 適用、rate env は v1 名 + v0 alias (Anthropic 限定) |

### v0 → v1 status mapping (Codex 20:08 review P1 #1 で明文化)

既存 v0 status 値を v1 canonical 値に migrate する mapping。実装 (別 PR) はこの table を normative とする。

| v0 status (現行 script) | v1 canonical | v1 category | source |
|---|---|---|---|
| `success` (slide-plan) | `ok` | (空 or domain-specific) | [generate_slide_plan.py:374](../template/scripts/generate_slide_plan.py) |
| `success` (narration) | `ok` | (空 or domain-specific) | [voicevox_narration.py:788](../template/scripts/voicevox_narration.py) |
| `api_key_skipped` | `skipped` | `api_key_missing` | [generate_slide_plan.py:191](../template/scripts/generate_slide_plan.py) |
| `engine_skipped` | `skipped` | `engine_unavailable` | [voicevox_narration.py:551](../template/scripts/voicevox_narration.py) |
| `list_speakers` | `ok` | `list_speakers` | [voicevox_narration.py:559](../template/scripts/voicevox_narration.py) |
| `dry_run` | `dry_run` | (現状通り) | [generate_slide_plan.py:298](../template/scripts/generate_slide_plan.py) |
| `error` variants (`cost_guard_arg_invalid` / `inputs_missing` / `rate_limited` / `api_http_error` / `llm_json_invalid` 等) | `error` | error 種別を `category` に格納 (旧 status 名を category に流用可) | 各 error path ([generate_slide_plan.py:229,235,350,352,367](../template/scripts/generate_slide_plan.py) 等) |

### v0 dry-run JSON legacy

`generate_slide_plan.py:279` の dry-run path は `--json-log` flag なしでも JSON を stdout に出す legacy 動作 (cost estimate を即時出すため)。schema v1 contract では:
- v0 dry-run JSON は `--json-log` 強制を要さず legacy として正規化済とみなす。
- migration helper (`_observability.py`、別 PR) で wrap する場合、dry-run output に schema_version=1 を後付けする選択肢あり (互換性維持)。

### Migration steps (別 PR scope)

1. v0 → v1 schema 互換 helper を `template/scripts/_observability.py` に追加 (新規 file)。helper には上記 status mapping と redaction rule を実装。
2. 既存 2 script (`generate_slide_plan.py` / `voicevox_narration.py`) を helper 経由に refactor。output JSON の abs_path 漏れ ([generate_slide_plan.py:380](../template/scripts/generate_slide_plan.py), [voicevox_narration.py:778](../template/scripts/voicevox_narration.py)) を repo-root 相対 path に変換。chunk text human log redaction を適用。
3. 残 6 script (`preflight_video.py` / `timeline.py` / `visual_smoke.py` / `build_slide_data.py` / `build_telop_data.py` / `compare_telop_split.py`) を v1 化。
4. `test_timeline_integration.py` に redaction regression test 追加 (sensitive class 4 種が json tail に raw で出ないこと、v0 → v1 migration で behavior 互換性が保たれること)。

## Test Requirements

- `test_timeline_integration.py` に redaction regression test (sensitive class 4 種が json tail に raw で出ないこと) を追加 (別 PR)。
- 各 script の `--json-log` を CI で smoke test (parse + schema_version 確認)。
- cost telemetry の missing rate behavior を test fixture で確認。

## Open Questions

- `run_id` / `parent_run_id` / `step_id` の発行責任 (script 内自動 vs 呼出側経由)。本 doc では reservation のみ、決定は別 PR。
- artifact path の repo-root vs project-root 解釈 (現行 supermovie pipeline は `<PROJECT>` ベース、release repo は repo root ベース)。
- redaction `version` の bump policy (schema_version とは独立、redaction rule 変更時のみ bump)。
- VOICEVOX 以外の local engine が増えた場合の cost 取り扱い (cost null vs 削除)。
