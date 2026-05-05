# SuperMovie Observability Contract

本 doc は SuperMovie pipeline の observability に関する最小規約を固定する。Codex 19:50 consult verdict (Tech 改善 Medium #3) を起点に PR #1-#14 (PR-A..K) を経て確立、最新 fork/main = `db4cb74` 時点で全 surface (json tail / human stdout / stderr / error message / artifact path) で redaction default strict + unified knob (`--unsafe-keep-abs-path`) + 4 sensitive class (secret / user_content / abs_path / provider_response_body) helper 化が完了。env var rename (Anthropic rate v0→v1 alias) は PR-D 実装済、distributed tracing run_id active emission は PR-E 実装済、cost abort threshold は PR-F 実装済、error path tail emit consistency は PR-G 実装済、helper-level secret redaction は PR-H 実装済、human stdout / stderr path leak audit は PR-I/J 実装済、redact_error_message regex 強化 (Windows path / IPv6 / data URI 対応) は PR-K 実装済。provider price 記載 / external SaaS 前提は本 doc では扱わない。

## Scope And Non-Goals

### Scope (本 doc が固定する)
- status JSON emission の canonical な仕様 (`--json-log` 動作、stdout/stderr 区別、common fields)
- log redaction の対象種別と適用 rule
- cost telemetry の単位・rate env var convention・missing rate 時の挙動
- migration policy (legacy v0 → current v1、完了履歴と新規 script 追加時の reference)
- test 要件 (regression 防止)

### Non-Goals (本 doc では扱わない)
- provider 個別 pricing 記載 (network 制約下で未検証、`docs/api-operation-guard` 系参照)
- external SaaS / GCP / credential 前提 (本 PR では required にしない)
- metrics export / 外部 collector (OTel / Datadog 等) 実装 (本 doc は env-driven trace context のみ規定)
- 多 provider への v0 alias 拡張 (Gemini / Kling 等は v1 canonical のみ、Anthropic 限定の後方互換)

(Resolved Non-Goals: script v1 migration → PR-A/B/C で完了。env var rename → PR-D で v0→v1 alias 実装済。distributed tracing run_id active emission → PR-E で 7 script propagate 完了。)

## Current Surface

実装済の observability surface (PR #1-#14 merged 後、最新 fork/main = `db4cb74` 時点):

| script | --json-log | cost guard | dry-run | redaction status |
|---|---|---|---|---|
| `generate_slide_plan.py` | ✓ helper 経由 v1 (PR #3 merged) | ✓ ([:161,297](../template/scripts/generate_slide_plan.py)) | ✓ ([:159](../template/scripts/generate_slide_plan.py)) | helper 経由 (HTTP body / LLM raw text default redact、output path safe) |
| `voicevox_narration.py` | ✓ helper 経由 v1 (PR #3 merged) | minimal | — | helper 経由 (chunk text default redact、summary path safe) |
| `compare_telop_split.py` | ✓ helper 経由 v1 + category_override=`kpi-comparison` (PR-A) | — | — | helper 経由 (artifact path safe、`/tmp/` 等 system tmpdir も placeholder) |
| `visual_smoke.py` | ✓ helper 経由 v1 + category_override=`dimension-regression` (PR-A)、summary JSON artifact ([:365](../template/scripts/visual_smoke.py)) は維持 | — | — | helper 経由 (summary / grid path safe) |
| `preflight_video.py` | ✓ helper 経由 v1 (PR-B、既存 stdout source JSON 維持 + --json-log で末尾 v1 tail emit、success category=`preflight-source-meta` / error は STATUS_MAP 詳細 category) | — | — | helper 経由 (write-config path safe) |
| `timeline.py` | — (library 性質、Codex 21:01 step 3 S3-2 で migration 対象外) | — | — | — |
| `build_slide_data.py` | ✓ helper 経由 v1 (PR-C、category_override=`slide-build`、`--unsafe-show-user-content` で raw title) | — | — | helper 経由 (slide title default redact via `user_content_meta`、artifact path safe) |
| `build_telop_data.py` | ✓ helper 経由 v1 (PR-C、category_override=`telop-build`、`--unsafe-show-user-content` で raw text) | — | — | helper 経由 (telop text default redact via `user_content_meta`、artifact path safe) |

v1 helper 経由は generate_slide_plan / voicevox_narration / compare_telop_split / visual_smoke / preflight_video / build_slide_data / build_telop_data の **7 script** が適用済 (PR #3 + PR-A + PR-B + PR-C merged 後)。`timeline.py` は library 性質で migration 対象外、呼び出し元 script で status emit される設計 (Codex 21:01 step 3 S3-2)。Step 3 完了。

### v0 JSON tail / output gap (legacy 履歴、PR #3 で全解消済)

旧 v0 emission に schema v1 と不整合だった点 3 つの解消履歴:
- `generate_slide_plan.py` の output JSON `path` 絶対 path 漏れ → PR #3 で `safe_artifact_path` (`<HOME>` / `<TMP>` / `<ABS>` placeholder) 適用、`redaction.applied_rules=[abs_path]` 反映済。
- `voicevox_narration.py` の summary JSON `path` 絶対 path 漏れ → PR #3 で同 helper 適用済。
- `generate_slide_plan.py` の dry-run JSON は `--json-log` 強制なしで stdout に出す legacy 動作を維持 (cost estimate 即時 emit 用途)。`--json-log` 指定時は別途 helper 経由で v1 status JSON を末尾 1 行 emit する 2-emission pattern として canonical 化 (PR #3 確定)。

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
  "run_id": "<32-char hex UUID v4、env SUPERMOVIE_RUN_ID 経由 or auto-generate、PR-E で active emission 化>",
  "parent_run_id": "<optional、env SUPERMOVIE_PARENT_RUN_ID 経由のみ、auto-generate なし>",
  "step_id": "<optional、env SUPERMOVIE_STEP_ID 経由のみ、auto-generate なし>"
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

post-migration (v1) では `generate_slide_plan.py` / `voicevox_narration.py` / `compare_telop_split.py` / `visual_smoke.py` / `preflight_video.py` / `build_slide_data.py` / `build_telop_data.py` の **7 script** が helper (`_observability.py`) 経由で schema v1 を emit する (PR #3 + PR-A/B/C merged 後)。`timeline.py` は library 性質で migration 対象外。Current Surface table 参照。

## Log Redaction Contract

### Sensitive Classes

| class | 例 | source |
|---|---|---|
| secret | `ANTHROPIC_API_KEY` 等 API key、token、credential | env / config / argv |
| user_content | transcript text、segments、telop raw text、prompt body | input file / API request body |
| abs_path | `/Users/<name>/...` machine-local 絶対 path | argparse / sys.argv / output file path |
| provider_response_body | LLM API の response 全文 | network response |

### Redaction Rules (Codex 20:08 review P1 #2 で strict 化)

- secret: 値の最後 4 文字以外をマスク (`sk-...XXXX`)。env name / config key 名は出して可。stderr / json tail / human stdout 全て同 rule。helper `redact_secret(value, *, last_n=4, mask_char="*")` で実装 (PR-H、`_observability.py`)。短い value (last_n+1 char 以下) は partial leak 回避のため全 mask、non-string は passthrough。現 codebase に secret 直接 emit 経路はなく、本 helper は将来の secret-bearing emission の contract enforcement として置く。
- user_content (transcript / segments / chunk text / telop raw):
  - human stdout: **default は `length` / `hash` のみ表示**、raw 出力は debug opt-in flag (`--unsafe-show-user-content` 等) 限定。`first-N-chars` 等の partial preview も default では出さない (raw partial も raw の subset とみなす)。`voicevox_narration.py` chunk text human log は PR #3 で `--unsafe-show-user-content` 化済み。
  - external structured log / json tail: `length` / `hash` のみ、raw 禁止 (default / debug 共通)。
  - debug opt-in 時も secret-bearing input (transcript 内に API key 等) は事前 detection + 削除。
- abs_path:
  - human stdout: **default redact** (PR-I で contract 改訂、`safe_artifact_path()` 経由で project-root 相対 / `<HOME>` / `<TMP>` / `<ABS>` placeholder)、`--unsafe-keep-abs-path` で raw 切替 (json tail と同 knob で一貫)。stdout を log capture / pipe / shared screenshot 等で意図せず外部公開する事故への defense-in-depth。
  - json tail / artifact path / external log: repo root or project root 相対 path に変換。`~` 展開後の絶対 path には `<HOME>` placeholder を適用。
- provider_response_body (LLM API の raw response):
  - **stderr であっても default は raw 禁止** (request_id / status_code / token_usage の structured summary のみ出す)。raw body は `--unsafe-dump-response` 等 debug opt-in flag 時に限定。
  - secret-bearing header (Authorization / x-api-key 等) は事前 strip。
  - `generate_slide_plan.py` の HTTP error response body / partial body / LLM raw text 経路 (旧 `:347` / `:351` / `:366`) は PR #3 で provider_response_body redaction 適用済 (`--unsafe-dump-response` opt-in 時のみ raw 出力)。
  - regression test 既存: HTTP body / LLM raw text を含む test fixture で default emission に raw が現れないことを `test_timeline_integration.py` の `test_observability_provider_body_stderr_default_redact` で検証済 (PR #3)。

### Path Policy

artifact `path` field は repo root or project root からの **相対 path** で記録する。絶対 path は `redaction.applied_rules` に `abs_path` を含めて redacted variant を記録する (PR #3 helper `safe_artifact_path` で `<HOME>` / `<TMP>` / `<ABS>` placeholder を機械置換、全 7 script 適用済)。明示 opt-in flag `--unsafe-keep-abs-path` は PR #3 で json tail / artifact path 用に実装、PR-I で human stdout (各 script の `print(f"... {path}")` 経路 / voicevox summary JSON) にも適用範囲拡大、両経路で同 knob 制御 (一貫した default redact / opt-in raw)。

### User Content Policy

`build_slide_data.py` / `build_telop_data.py` の raw title/telop text 経路は PR-C で `--unsafe-show-user-content` opt-in 化済。default は json tail に `user_content_meta` (length / sha256 hash) のみ emit、raw text は flag 指定時のみ stdout/json に出す。

## Cost Telemetry Contract

### Cost JSON Shape

設計上の future canonical schema (nested `cost` object):

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

**現 emission (PR-S 以降)** は dry-run --json-log v1 tail / cost_guard_aborted の v1 tail で **nested cost object + top-level discriminator (backward compat)** の **dual emission** で出す:

- nested `cost`: PR-S で実装。`build_cost_payload(estimate, rate_input, rate_output, ...)` helper 経由で payload 構築、上記 schema に対応。`build_status(cost=cost_dict)` で payload に nested 出力。
- top-level extras (backward compat): `estimated_cost_usd_upper_bound` / `estimated_input_tokens` / `estimated_output_tokens_upper_bound` / `rate_input_per_mtok` / `rate_output_per_mtok` (legacy JSON のみ) / `rate_missing` (PR-N で導入)。downstream parser が nested cost.* に migrate するまでの猶予として残す。
- discriminator 整合性: `cost.rate_missing == top-level rate_missing`、`cost.estimate == top-level estimated_cost_usd_upper_bound` (PR-S 統合 test で verify)。

dry-run legacy JSON (`--json-log` なしの一次 stdout) は依然 top-level form のみ (cost estimate を即時 emit する legacy 動作維持、PR-N で `rate_missing` 追加済)。

下流 parser 推奨: 新規実装は **nested `cost.*` を一次 source** とする。top-level extras は legacy 互換のみのため、将来 PR で removal 予定 (migration 警告は別 PR で出す可能性)。

### Rate Env Var Convention

- v1 canonical: `SUPERMOVIE_RATE_<PROVIDER>_<DIRECTION>_USD_PER_MTOK`
  - 例: `SUPERMOVIE_RATE_ANTHROPIC_INPUT_USD_PER_MTOK`、`SUPERMOVIE_RATE_GEMINI_OUTPUT_USD_PER_MTOK`
- v0 (既存): `SUPERMOVIE_RATE_INPUT_PER_MTOK` / `SUPERMOVIE_RATE_OUTPUT_PER_MTOK` ([generate_slide_plan.py](../template/scripts/generate_slide_plan.py))
  - v0 alias は **`generate_slide_plan.py` の Anthropic input/output rate 専用** で定義 (Codex 20:08 review P2 #1)。Gemini / Kling 等 future provider には v1 canonical のみを使う。
  - **alias 動作実装済 (PR-D)**: `_resolve_decimal()` の precedence は `CLI > v1 canonical > v0 alias > None`。両方設定時は v1 が勝つ。後方互換維持で v0 のみ設定でも動作。test は `test_generate_slide_plan_rate_v0_v1_alias_precedence` で 4 case 検証 (v0 only / v1 only / v1+v0 both / CLI wins)。

### Provider Notes

| provider | unit | rate kind |
|---|---|---|
| Anthropic API (slide plan) | $/MTok input + $/MTok output | text generation、env 必須 |
| Gemini API (image gen) | $/image | image generation、別 env 系 (本 doc 後の別 PR で固定) |
| VOICEVOX (narration) | local engine、cost なし ([voicevox_narration.py:66](../template/scripts/voicevox_narration.py)) | telemetry 対象外 |
| Kling / 他 (将来) | provider 個別 | 別 doc 化 |

### Missing Rate Behavior

- rate env が未設定: 現 emission では top-level `estimated_cost_usd_upper_bound = null` + `rate_missing = true`、`status = ok` (cost 不明は error 扱いしない)。future nested schema では `cost.estimate = null` / `cost.rate_missing = true`。
- rate が設定されているが invalid (NaN / 負値等): `status = error`、`category = "rate-invalid"`、exit_code 非 0。

### Cost Abort Threshold (PR-F、Anthropic API 限定)

- `SUPERMOVIE_COST_USD_ABORT_AT` env / `--cost-abort-at` CLI で USD 単位の上限値を設定すると、`generate_slide_plan.py` は API call 前に `estimated_cost_usd_upper_bound` と比較し、超過時は `status=error / category=cost_guard_aborted / exit_code=10` で abort。
- precedence: CLI > env > None (default 無効)。
- rate 未設定 (`estimated_cost_usd_upper_bound=None`) 時は閾値設定があっても skip (cost 不明で勝手に abort せず通常進行)。
- abort emission payload には `estimated_cost_usd_upper_bound` / `cost_abort_at` / `estimated_input_tokens` / `estimated_output_tokens_upper_bound` を含む。
- 関連 test (PR-F): `test_generate_slide_plan_cost_abort_blocks_api_when_estimate_exceeds` / `test_generate_slide_plan_cost_abort_skipped_when_rate_unset` / `test_generate_slide_plan_cost_abort_cli_overrides_env`。

## Trace Context Convention (PR-E、distributed tracing run_id active emission)

### env precedence

| env var | role | precedence |
|---|---|---|
| `SUPERMOVIE_RUN_ID` | invocation 全体を貫通する trace identifier | 設定 + 非空 → そのまま採用、未設定 → `uuid.uuid4().hex` (32 char) auto-generate |
| `SUPERMOVIE_PARENT_RUN_ID` | upstream (orchestrator) からの caller run_id | 設定 + 非空 → そのまま採用、未設定 → `null` (auto-generate しない) |
| `SUPERMOVIE_STEP_ID` | pipeline 内の論理 step 識別子 | 設定 + 非空 → そのまま採用、未設定 → `null` (auto-generate しない) |

cap: 全 3 field に `MAX_TRACE_CONTEXT_VALUE_LEN = 128` 適用、超過時は `TraceContextError` raise (truncation せず error)。

`parent_run_id` / `step_id` を auto-generate しない理由: 関係性情報なので、呼出元 (orchestrator) が明示注入していない時に偽値を作ると trace tree が破綻するため。

### 7 script propagate 方針 (PR-E 実装)

各 script の `main()` 冒頭で `resolve_run_context()` を 1 回呼び、emit closure / `build_status()` 全 path に `run_id / parent_run_id / step_id` を渡す。`generate_slide_plan.py --dry-run --json-log` は legacy stdout JSON + v1 tail の 2-emission pattern で、v1 tail にも run_id 反映 (Codex 22:47 PR-E review prerequisite)。

`timeline.py` は library 性質で CLI emission を持たないため対象外 (Codex 21:01 step 3 S3-2 維持)。

### 関連 test (PR-E、`test_timeline_integration.py`)

- `test_observability_resolve_run_context_uses_env`: env 設定時の pass-through
- `test_observability_resolve_run_context_generates_when_missing`: env 未設定時の uuid4 32-char hex auto-generate + 連続 call で重複しない
- `test_observability_resolve_run_context_no_generate`: `generate_if_missing=False` で env 未設定 → None
- `test_observability_resolve_run_context_empty_env_fallback`: env 空文字列 → 未設定扱い + auto-generate
- `test_observability_resolve_run_context_cap_exceeded`: cap 超過 → `TraceContextError` raise
- `test_observability_run_id_in_payload`: build_status が non-None で payload に乗せ、None で含めない
- `test_generate_slide_plan_run_id_propagation`: dry-run --json-log 経由 v1 tail に run_id 反映 (e2e import + main() pattern)
- `nan/inf` ガードは既存 `generate_slide_plan.py:120` 系の挙動を canonical とする。

## Migration Policy

(本節は v0 → v1 migration の **完了済 normative spec** として保持。新規 script 追加時 / regression test 修正時の reference。Migration 自体は PR #3 + PR-A/B/C + PR-D で完了済。)

| state | criteria | 適用状況 |
|---|---|---|
| v0 (legacy) | `status` / `exit_code` を出す最小 status JSON、redaction なし、cost rate env は v0 名 | 廃止 (PR #3 で 2 script、PR-A/B/C で 5 script を helper 経由 v1 へ refactor 完了) |
| v1 (current) | schema_version=1、common fields 全埋め (null OK)、redaction 適用、rate env は v1 名 + v0 alias (Anthropic 限定、PR-D 実装済) | 7 script 適用済 (Current Surface table 参照) |

### v0 → v1 status mapping (Codex 20:08 review P1 #1 で明文化、PR #3 + PR-A/B/C で適用済)

既存 v0 status 値を v1 canonical 値に migrate する mapping。新規 script 追加時はこの table を normative とする。

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

`generate_slide_plan.py` の dry-run path は `--json-log` flag なしでも JSON を stdout に出す legacy 動作を維持する (cost estimate を即時出すため)。schema v1 contract では:
- v0 dry-run JSON は `--json-log` 強制を要さず legacy として正規化済とみなす。
- helper (`_observability.py`) は dry-run legacy JSON を wrap しない設計 (PR #3 で確定)。dry-run は cost estimate を即時 stdout に出す独立 path で、`--json-log` 指定時は別途 helper 経由で v1 status JSON を末尾 1 行に emit する 2-emission pattern。

### Migration steps (完了履歴)

| step | 内容 | 完了 PR |
|---|---|---|
| 1 | `template/scripts/_observability.py` helper 新規追加 (status mapping + redaction rule + safe_artifact_path 実装) | PR #3 (987c3d0) |
| 2 | `generate_slide_plan.py` / `voicevox_narration.py` を helper 経由 refactor、abs_path 漏れ + chunk text redaction 適用 | PR #3 (987c3d0) |
| 3 | 残 5 script (`compare_telop_split.py` / `visual_smoke.py` (PR-A) / `preflight_video.py` (PR-B) / `build_slide_data.py` / `build_telop_data.py` (PR-C)) を v1 化。`timeline.py` は library 性質で対象外 (Codex 21:01 step 3 S3-2) | PR-A (4) / PR-B (5) / PR-C (6) |
| 4 | `test_timeline_integration.py` に redaction + status v1 schema regression test 9 件追加 (abs_path / user_content / provider_response_body の raw 漏れ防止、build_status duration_ms / category_override、provider body stderr default redact 等。`secret` class 専用 test は PR-H で追加 (5 件)、§Test Requirements 参照) | PR #3 + PR-A/B/C |
| 5 | rate env v0 → v1 alias 実装 (Anthropic 限定後方互換) | PR-D |
| 6 | distributed tracing run_id active emission 実装 (`resolve_run_context()` helper + 7 script propagate + cap validation + 7 件 regression test) | PR-E |
| 7 | pre-API cost abort threshold 実装 (`SUPERMOVIE_COST_USD_ABORT_AT` env + `--cost-abort-at` CLI + `cost_guard_aborted` status_map 追加 + estimate 共通化 + 3 件 regression test) | PR-F |
| 8 | error path tail emit consistency audit (`compare_telop_split` の transcript / typo_dict / telop ts read failure を `_emit_early` 経由化、`preflight_video` の `--write-config` parse / write failure を `_emit` 経由化、`visual_smoke` の `out_dir.mkdir` / `videoConfig.ts read` failure を `_emit_early` 経由化、9 status 追加 + 4 件 regression test) | PR-G |
| 9 | helper-level secret redaction 実装 (`redact_secret()` で last-4 mask + short-value 全 mask + non-string passthrough、`docs/OBSERVABILITY.md:123` secret class contract enforcement、4 件 regression test) | PR-H |
| 10 | human stdout path leak audit (build_slide_data / build_telop_data / voicevox_narration / visual_smoke / preflight_video / generate_slide_plan の 9 `print(f"... {path}")` 経路を `safe_artifact_path()` 経由化、`--unsafe-keep-abs-path` で raw 切替 unified knob、abs_path contract 改訂 + 1 件 regression test) | PR-I |
| 11 | stderr path leak audit (preflight_video `input not found` / visual_smoke `MAIN_VIDEO` / `REMOTION_BIN` / `VIDEO_CONFIG` / png ffprobe / generate_slide_plan `PROJ` を `safe_artifact_path()` 経由化、`--unsafe-keep-abs-path` で raw、stderr も同 contract 適用 + 1 件 regression test) | PR-J |
| 12 | `redact_error_message()` regex 強化 (Windows path `C:\...` / `D:/...` 検出 + 置換、IPv6 / data: URI / mailto: の false-positive 防御維持、複数 path 同時 redact + 3 件 edge case test) | PR-K |
| 13 | `--unsafe-keep-abs-path` flag 7 script audit (`build_slide_data` / `build_telop_data` / `voicevox_narration` / `visual_smoke` / `compare_telop_split` / `preflight_video` / `generate_slide_plan` の argparse + `args.unsafe_keep_abs_path` 使用を verify、将来 script 追加時の漏れ防止 lint test 1 件追加) | PR-M |
| 14 | `rate_missing` discriminator 追加 (generate_slide_plan dry-run legacy JSON + v1 tail / cost_guard_aborted payload に `rate_missing: bool` 明示、`estimate is None` から推論する fragile downstream parse を回避、§Cost JSON Shape contract enforcement + 2 件 regression test) | PR-N |
| 15 | `compute_rate_missing()` helper sink (`estimate is None ⇔ rate_missing=true` 算出規約を helper に集約、caller (legacy JSON / v1 tail / cost_guard_aborted) の重複式 3 site を `compute_rate_missing(estimate)` 単一呼び出しに統一、single source of truth + 1 件 unit test) | PR-O |
| 16 | entry exit code propagation audit (`build_slide_data` / `build_telop_data` / `preflight_video` の `__main__` block を `main()` 直呼び → `sys.exit(main() or 0)` に統一、`_emit_error` 経由 return int を shell rc に propagate、PR-G fix iter で compare_telop_split で発見された pattern を全 7 script に展開 + 漏れ防止 lint test 1 件) | PR-P |
| 17 | `redaction.applied_rules` canonicalize (`build_status()` で `sorted(set(...))` 正規化、caller の dedup 漏れ / 順序差を helper 側で吸収、downstream diff / regression test 安定性確保 + 1 件 unit test) | PR-Q |
| 18 | `redact_error_message()` URL edge case lock-in test (port `:8080` / query string `?key=value` / fragment `#anchor` / git+ssh:// scheme / URL+abs_path 混在 path 7 case を preserve regression、PR-K の `_ABS_PATH_RE` URL 破壊回避ロジック未被覆 edge を閉じる + 1 件 regression test) | PR-R |
| 19 | nested `cost={...}` schema migration (`build_cost_payload()` helper 追加、`generate_slide_plan` dry-run + cost_guard_aborted で `build_status(cost=cost_dict)` 経由 nested cost emit、top-level extras は backward compat で残す dual emission、§Cost JSON Shape 改訂 + 2 件 regression test) | PR-S |
| 20 | STATUS_MAP static lint test 追加 (`test_observability_status_map_lint`)。AST parse で dict literal 重複 key を検出 (silent overwrite 防止)、value shape (2-tuple) / v1_status 4 値 set / category str-or-non-empty を一括 verify。`map_status()` の defensive fallback `("error", v0_status)` が unknown を silent fallback するため、emit site 追加漏れ系の `must_have` set diff (`test_observability_helper_status_map`) で拾えない内部 contract drift を early fail させる lint 層 + 1 件 regression test | PR-T |
| 21 | `safe_artifact_path()` path collision corner case test 追加 (`test_observability_safe_artifact_path_collision_corners`)。(1) project_root + repo_root 両方 match で project_root 優先、(2) path == project_root 完全一致 → "."、(3) `..` traversal escape は relative_to で root 配下偽装させず placeholder、(4) project_root の trailing slash 無関係性、(5) `proj` vs `proj_extra` 似た prefix non-collision (Path.relative_to の segment 単位判定依存)、(6) repo_root のみ fallback contract、(7) 相対 path 入力 as-is passthrough。出力先一意性 + 監査 trail 健全性 + caller 流儀差吸収を lock-in + 1 件 regression test | PR-U |
| 22 | `safe_artifact_path()` の `~/...` 展開 redaction contract gap fix。旧実装は `Path(s).is_absolute()` ガードで `~` を `expanduser` せず、Python 仕様で `Path("~/x").is_absolute() == False` のため後段 `_lexical_redact(s, home)` も `s.startswith(home)` 判定を通せず literal `~/...` がそのまま漏れていた。fix: `s.startswith("~")` 時のみ `os.path.expanduser` を早期適用して `s_expanded` を後段全経路に渡す。相対 path 入力 (`public/main.mp4` 等) の as-is passthrough invariant (PR-U test 7) は維持。`test_observability_safe_artifact_path_tilde_expansion` で 7 case (root 未指定 / 異 root / `~`-`~` mixed / `~`-abs mixed / unsafe_keep bypass / 非 tilde 相対 passthrough / abs HOME regression) を lock-in + 1 件 regression test | PR-V |
| 23 | `build_status()` 出力 top-level field 順序の deterministic lock-in test 追加 (`test_observability_build_status_top_level_field_order`)。Python 3.7+ の dict insertion order 保持 + json.dumps 順次出力により、status JSON tail の field 順は consumer 側 diff / log grep / regression test 安定性を支える contract の一部。reserved core 10 field の固定順 (schema_version → script → status → ok → exit_code → category → counts → artifacts → cost → redaction)、optional field (duration_ms / run_id / parent_run_id / step_id) の source 宣言順、extras (v0 compat) の caller 順、reserved key 同名 extras の filter (reserved 値が勝つ) を 6 case で lock-in + 1 件 regression test | PR-W |
| 24 | legacy top-level cost extras deprecation warning 追加 (env-gated)。`warn_legacy_cost_extras()` helper + `WARN_LEGACY_COST_EXTRAS_ENV = "SUPERMOVIE_OBSERVABILITY_WARN_LEGACY_COST_EXTRAS"` (default off)、`LEGACY_COST_EXTRAS_KEYS` 5 entry (estimated_input_tokens / estimated_output_tokens_upper_bound / estimated_cost_usd_upper_bound / cost_abort_at / rate_missing)。env="1" 時のみ payload に nested `cost` (truthy) と legacy keys が併存していたら stderr に 1 行 deprecation warning emit、stdout JSON contract は不変。`generate_slide_plan.emit_json` で payload 構築直後に呼び出し、downstream consumer に nested 形式 migration を促す。test 5 case (env unset / "0" / "1" + dual / "1" + nested only / "1" + cost None) で env-gate / dual 検出 / no-op 経路を lock-in + 1 件 regression test | PR-X |
| 25 | `emit_json()` stdout output format lint 追加 (`test_observability_emit_json_format_lint`)。`--json-log` の downstream parser が stdout 末尾を `splitlines()[-1]` → `json.loads()` する pattern を前提にしているため、format invariant を helper-level で lock-in: (1) enabled=True で exactly 1 line + final `\n`、(2) `json.loads` 可能で payload と semantically 等価、(3) embedded control char (`\n` / `\t` / `"` / `\\`) escape で single-line 維持、(4) non-ASCII (日本語) 文字は `ensure_ascii=False` literal 維持で行構造保持、(5) enabled=False で stdout 無音、(6) exit_code propagation。既存 `test_observability_emit_json_disabled_no_print` は parse 成功のみ、行数 / 末尾改行 / 制御文字 escape は本 test で初固定 + 1 件 regression test | PR-Y |
| 26 | `emit_json()` stderr 非混入 invariant lint test 追加 (`test_observability_emit_json_stderr_clean`)。`--json-log` の stdout/stderr 分離契約 (stdout = JSON tail + human v0 出力、stderr = error/warning/debug 専用) を helper-level で固定。`redirect_stderr(StringIO())` 下に enabled / disabled / error-status / non-ASCII / control-char の 5 case で `emit_json()` を呼び、stderr が空のまま、enabled=True で stdout に出力される (false-positive guard) ことを assert。emit_json の `print()` が `file=sys.stderr` 等にリファクタされた時の transport contract drift を early fail + 1 件 regression test | PR-Z |
| 27 | `build_cost_payload()` の NaN / Inf rate defense + `compute_rate_missing()` 拡張。`_coerce_finite_or_none(v)` helper 追加で estimate / rate_input / rate_output が non-finite (NaN / Inf / -Inf / 非数値型) 時に None 正規化。`compute_rate_missing(estimate)` も None | non-finite で True を返すよう拡張。CLI / env 経路は math.isfinite + ValueError reject で early guard 済だが、helper 独立呼び出し / 将来 caller への defense-in-depth。json.dumps default が `NaN` / `Infinity` non-standard token を出す経路 + `allow_nan=False` での突発 ValueError を完全 closure。test 7 case (estimate NaN/Inf/-Inf / rate_input NaN / rate_output Inf / 全 finite regression / 全 None / compute_rate_missing 直接 / 非数値型 str) で contract lock-in + 1 件 regression test | PR-AA |
| 28 | `warn_legacy_cost_extras()` env gate strict opt-in lint 追加 (`test_observability_warn_legacy_cost_extras_env_strict_opt_in`)。PR-X 既存 test は unset / "0" / "1" の 3 値しか被覆しないため、env が "2" / "true" / "TRUE" / "yes" / "on" / 空 string / whitespace / "1\n" / " 1" / "11" / "01" 等の「truthy 風だが厳密に "1" ではない」 値を渡した時に warning が emit されない strict opt-in 契約を 19 invalid value で lock-in + "1" exactly での positive control。リファクタで `bool()` / `.lower() in (...)` / 空文字弾き 等に変えた場合の noise regression を early fail + 1 件 regression test | PR-AB |
| 29 | `emit_json()` の `payload['exit_code']` int 限定 contract fix + lock-in。旧実装 `int(payload.get("exit_code", 0))` は str / float / bool を silent coerce、None / "abc" で uncaught TypeError/ValueError を投げる weak 動作で v1 schema drift を silent 通過させていた。新実装は missing → default 0 維持、bool は int subclass でも reject、それ以外の非 int (str / float / None / list / dict / object) を explicit TypeError で fail-loud、payload 構築側の責務として固定。`test_observability_emit_json_exit_code_int_contract` を追加 (98→99 件)、8 case (正常 int / missing default / bool reject / str reject / float reject / None reject / その他 type reject / reject 経路で stdout 空) で contract lock-in + 1 件 regression test | PR-AC |
| 30 | `build_status(redaction_rules=...)` の str 限定 contract fix + lock-in。旧実装 `sorted(set(redaction_rules)) if redaction_rules else []` は None/empty で `[]` を返すが、`[None]` / `[1]` を silent pass、`["a", None]` で「'<' not supported between instances of 'str' and 'NoneType'」の意味不明 TypeError、bare str (`"abs_path"`) を渡すと iter で char 分解されて `["_","a","b","h","p","s","t"]` という schema drift を起こしていた。新 `_normalize_redaction_rules()` helper を追加: None → []、list/tuple of str → sorted unique、bare str / 非 str entry / dict-set-int 等 → explicit TypeError fail-loud。`test_observability_build_status_redaction_rules_strict` を追加 (99→100 件)、9 case (None / empty list/tuple / dedup / tuple accept / bare str reject / [None] reject / [int] reject / mixed reject / dict-set-int reject) で contract lock-in + 1 件 regression test | PR-AD |
| 31 | `build_status()` の reserved key collision invariant lint 拡張。PR-W (`test_observability_build_status_top_level_field_order` case 5) で `status` 1 件のみ被覆していた `**extras` 経由の reserved key override filter を全 non-signature reserved key (5 件: `schema_version` / `status` / `ok` / `category` / `redaction`) に拡張。signature kwargs (script / v0_status / exit_code / counts / artifacts / cost / redaction_rules / duration_ms / category_override / run_id / parent_run_id / step_id) は Python routing で `**extras` に流れないため対象外。`test_observability_build_status_reserved_key_collision` を追加 (100→101 件)、4 case (各 reserved key 単独 inject / 5 件まとめ inject / unrelated extras passthrough / payload key 順序 invariant) で reserved-wins 契約を全 reserved key に対して lock-in + 1 件 regression test | PR-AE |
| 32 | `build_status(cost=...)` の dict-or-None 限定 contract fix + lock-in。旧実装は cost 引数の型 validation がなく list / str / int / bool / tuple / set 等を payload に素通り、§Cost JSON Shape canonical nested object 契約 + `warn_legacy_cost_extras()` の `payload.get("cost")` truthiness 判定の両方を drift させていた。fix: build_status 入口で `cost is not None and not isinstance(cost, dict)` を explicit TypeError で fail-loud。`test_observability_build_status_cost_dict_strict` を追加 (101→102 件)、8 case (None / 通常 dict / empty dict / list reject / str reject / int+float reject / bool reject / tuple+set+object reject) で contract lock-in。既存 callers (build_cost_payload 経由 dict / None) は backward compatible + 1 件 regression test | PR-AF |
| 33 | `resolve_run_context()` env value cap (`MAX_TRACE_CONTEXT_VALUE_LEN = 128`) の境界値 inclusive lock-in lint 追加 (`test_observability_resolve_run_context_cap_boundary`)。既存 PR-E `test_observability_resolve_run_context_cap_exceeded` は 129 char の reject のみ被覆、127 / 128 char の accept (boundary inclusive contract) は lock されていなかった。`_validate_trace_value` の cap 比較 `> MAX_TRACE_CONTEXT_VALUE_LEN` が `>= ...` 等にリファクタされて 128 char 突然 reject の silent regression を early fail させるため、TRACE_RUN_ID_ENV / TRACE_PARENT_RUN_ID_ENV / TRACE_STEP_ID_ENV 全 3 env の 127 accept / 128 accept / 129 reject の 3 boundary を lock-in (3 env × 3 boundary = 9 case) + 1 件 regression test | PR-AG |
| 34 | `build_status()` の `schema_version: 1` invariant + header hash snapshot lock-in lint 追加 (`test_observability_build_status_schema_version_invariant`)。`schema_version` は v1 schema の root identifier で downstream consumer / log analyzer が「v1 payload」と認識する最上流契約。4 層 lock-in: (1) `SCHEMA_VERSION` constant が int 1 (型 + 値、bool subclass reject)、(2) build_status 出力で全 7 v0 status (success/rate_limited/api_key_skipped/dry_run/cost_guard_aborted/ffprobe_failed/smoke_ok) で schema_version=1 維持、(3) extras inject (schema_version=999) reserved-wins、(4) header 6 field (schema_version/script/status/ok/exit_code/category) の SHA-256 16char snapshot hash (`4e1cd359d000dd2b`)。PR-W (field order) / PR-AE (reserved key) と役割分離、root identifier 単独の独立 lock-in + 1 件 regression test | PR-AH |
| 35 | `redact_provider_body()` の `preview_length` boundary fix + lock-in。旧実装 `min(len(body), max_preview)` は `max_preview=-1` で `preview_length=-1` の semantic violation (range invariant `[0, len(body)]` 外) を起こしていた。fix: `max(0, min(len(body), max_preview))` で下限 clamp、`max_preview=0` の preview 抑止 contract と `max_preview > len(body)` の上限 clamp は維持。`test_observability_redact_provider_body_preview_length_boundaries` を追加 (104→105 件)、8 boundary case (上限 clamp +100/+1/0 / -1 boundary / 1 / 0 抑止 / -1 / -100 negative clamp) で contract lock-in + raw body / sensitive token 非漏洩 invariant 相互強化 + 1 件 regression test | PR-AI |
| 36 | `user_content_meta()` / `redact_provider_body()` の `sha256` field format invariant lock-in lint 追加 (`test_observability_sha256_hash_format_invariant`)。`sha256` は v1 schema の機械的指紋で downstream consumer / log diff / regression test が「16 char lower hex prefix」前提で扱うため、`_hash16()` 内部実装が SHA-256 full hex (64 char) / upper case / 別 encoding に silent drift した場合の format-dependent caller 破壊を early fail。6 層 lock-in: (1) `user_content_meta` sha256 が str / 16 char / `[0-9a-f]{16}`、(2) `redact_provider_body` 同 format、(3) deterministic (同 input → 同 hash)、(4) 既知入力 snapshot (`hello world` → `b94d27b9934d3e08`)、(5) 異 input → 異 hash (algorithm 化崩れ regression detect)、(6) 空文字 / 非 ASCII / control char / emoji 入力でも format 維持 + 1 件 regression test | PR-AJ |
| 37 | `build_status(counts=...)` / `build_status(artifacts=...)` の defensive contract fix + lock-in。旧実装 `counts or {}` / `artifacts or []` は型 validation なしで list / str / int / bool / dict 単体 / list 内 non-dict を silent payload 通過、v1 schema common field の構造 drift を起こしていた。fix: build_status 入口で `counts is not None and not isinstance(counts, dict)` / `artifacts is not None and not isinstance(artifacts, list)` / `not all(isinstance(item, dict) for item in artifacts)` を explicit TypeError で fail-loud (artifacts は entry index 含む詳細 msg)。`test_observability_build_status_counts_artifacts_strict` を追加 (106→107 件)、counts 14 case (accept None / dict / empty dict + reject list/empty list/str/empty str/int/0/True/False/tuple/set/object) + artifacts 13 case (accept None / list-of-dict / empty + dict 単体 reject + non-list 4 (str/empty str/int/tuple) reject + 5 mixed list pattern reject) で contract lock-in (合計 27 case)。既存 callers (build_slide_data / build_telop_data / generate_slide_plan / voicevox 等) は dict / list-of-dict 渡しなので backward compatible + 1 件 regression test | PR-AK |
| 38 | `build_cost_payload(rate_source=...)` の str + `env:` prefix + non-empty env name contract fix + lock-in。旧実装は型 / format validation なしで `""` / `None` / int / bool / list / `"SUPERMOVIE_RATE_X"` (prefix なし) を silent payload 通過、§Cost JSON Shape の env var convention canonical contract を崩していた。fix: build_cost_payload 入口で `isinstance(rate_source, str)` 違反 → TypeError、`startswith("env:")` 違反 + prefix 直後 env name 空 (`len <= len("env:")`) → ValueError で fail-loud。`test_observability_build_cost_payload_rate_source_contract` を追加 (107→108 件)、accept 3 case (default placeholder / 通常 ENV_NAME / minimal `env:X`) + reject 7 type case (None/int/float/bool/list/dict) + reject 5 prefix 欠如 case + reject 2 empty case で contract lock-in。既存 caller (default 値 + `env:SUPERMOVIE_RATE_<PROVIDER>_<DIR>_USD_PER_MTOK` 系) は backward compatible + 1 件 regression test | PR-AL |
| 39 | `build_status(script=...)` の str + non-empty + no-control-char contract fix + lock-in。`script` は v1 status JSON の core identifier (downstream consumer / log filter / dashboard が script 名で payload を bucket する根拠 field)。旧実装は型 / 空文字 / 制御文字 validation なしで `""` / `"   "` (whitespace-only) / `"a\n b"` / `"a\x00b"` / int / bool / list / None を silent payload 通過、emit_json 1-line format invariant (PR-Y) も control char 経由で破壊しうる drift を起こしていた。fix: build_status 入口で (1) `isinstance(script, str)` 違反 → TypeError、(2) `script.strip() == ""` (whitespace-only 含む) → ValueError、(3) `\x00-\x1F` / `\x7F` 制御文字含む → ValueError で fail-loud。`test_observability_build_status_script_identifier_contract` を追加 (108→109 件)、accept 4 case (snake_case identifier / 拡張子付き / 1 char / 日本語 unicode) + reject 7 type case (None/int/float/bool/list/dict) + reject 6 empty/whitespace case + reject 7 control-char case + 7 既存 caller name regression guard で contract lock-in。既存 callers は all snake_case identifier なので backward compatible + 1 件 regression test | PR-AM |
| 40 | `STATUS_MAP` の category string format invariant lint 追加 (`test_observability_status_map_category_format_invariant`)。category は v1 status JSON の bucket field で、downstream consumer / log analyzer / dashboard が同 category 値で payload を集約する根拠。旧 PR-T `test_observability_status_map_lint` は (status, category) tuple shape + 重複 key + 空文字 reject を被覆していたが、format invariant (UPPERCASE / 制御文字 / leading-trailing dash / non-ASCII 特殊) の drift は lock されていなかった。permissive regex `^[a-z](?:[a-z0-9_-]*[a-z0-9])?$` で 46 unique category 全て validate (snake_case + kebab-case 両方 accept、segment case style 統一は別 lint 候補)、加えて 14 drift pattern (UPPERCASE / mixed / leading dash/underscore / trailing dash/underscore / leading digit / whitespace / tab / newline / dot / slash / 特殊文字 / 空文字) を regex 自体が reject することの negative control もまとめて lock-in + 1 件 regression test | PR-AN |
| 41 | `redact_secret()` の `mask_char` strict contract fix + boundary lock-in。旧実装は mask_char に空文字 (`""`) を渡すと `mask_char * N == ""` で last_n char が unmasked で raw 露出する semantic leak (例: `redact_secret("abcdefgh", last_n=4, mask_char="")` → `"efgh"`)。fix: value 検査後に mask_char isinstance str 違反 → TypeError、`not mask_char` (空文字) → ValueError で fail-loud (None / "" / non-str value passthrough は mask_char 検査前で backward compat 維持)。`test_observability_redact_secret_boundary_and_mask_char_strict` を追加 (110→111 件)、14 case (4 boundary len + 2 extreme last_n + 3 1-char value + 4 mask_char strict / leak 防止 / non-str / multi-char accept + 4 既存 passthrough regression guard) で contract lock-in + 1 件 regression test | PR-AO |
| 42 | `build_status(v0_status=...)` の str + non-empty + no-control-char defensive contract fix + lock-in。`v0_status` は `map_status()` lookup key で、未知 status は `("error", v0_status)` defensive fallback で category=v0_status となる仕様 (PR-T 設計)。旧実装は型 / 空文字 / 制御文字 validation なしで `""` / `"   "` / `"a\n b"` / `"a\x00b"` / int / bool / None を silent fallback で category 経路汚染、`list` は dict lookup の unhashable で uncaught TypeError drift。fix: build_status 入口で (1) `isinstance(v0_status, str)` 違反 → TypeError、(2) `v0_status.strip() == ""` (whitespace-only 含む) → ValueError、(3) `\x00-\x1F` / `\x7F` 制御文字 → ValueError で fail-loud。unknown str fallback (PR-T 設計) は維持、PR-AM script identifier 同型。`test_observability_build_status_v0_status_defensive_lint` を追加 (111→112 件)、accept 3 case (success / unknown str / 1 char) + reject 8 type case (None/int/float/bool/list/dict 等) + reject 6 empty/whitespace + reject 7 control-char + 7 既存 v0 emission regression guard で contract lock-in + 1 件 regression test | PR-AP |
| 43 | `emit_json(enabled, payload)` の payload dict 必須 contract fix + lock-in。`--json-log` の末尾行は v1 status JSON object 前提に downstream parser が組まれるため、payload は dict 必須。旧実装は entry で直接 `payload.get("exit_code", 0)` を呼ぶため、non-dict 入力 (None / list / str / int / tuple / set / object) で uncaught AttributeError "X object has no attribute 'get'" が出るだけで、caller の責務違反が分かりにくい drift。fix: emit_json 入口に `isinstance(payload, dict)` 違反 → explicit TypeError で fail-loud (PR-AC exit_code int 検査より前置で payload 自体の型を最優先 validate)。`test_observability_emit_json_payload_must_be_dict` を追加 (112→113 件)、accept 3 case (通常 dict / empty dict / error dict) + reject 14 non-dict case (None / list / str / int / float / bool / tuple / set / object) + reject 経路で stdout 空 invariant (PR-AC 同型) で contract lock-in。既存 caller (build_status() 経由 dict のみ) は backward compatible + 1 件 regression test | PR-AQ |
| 44 | `redact_error_message()` の URL+path 混在 input order independence lint 追加 (`test_observability_redact_error_message_url_path_order_independence`)。error message には URL と HOME 配下 abs path が混在することが多く、caller の文章組み立て順違いで output が drift してはならない (path → `<HOME>` placeholder 化 + URL → preserve invariant)。PR-K (URL 破壊回避 + multiple paths) / PR-R (URL edge case port/query/fragment lock-in) と相補で、両者混在 + 入力順序の組み合わせを未 lock 領域として塞ぐ。4 case (URL→path / path→URL / URL+segment+path / path→URL+segment) で path raw 漏れなし + URL host preserve + 各 case で `<HOME>` placeholder が実際に出る (count >= 1) + A/B C/D 間の `<HOME>` placeholder count + URL host count 一致による order-independence を assert + 1 件 regression test | PR-AR |
| 45 | `redact_error_message()` の `~/...` / `~user/...` tilde path token redact contract 拡張 + URL preserve invariant 維持。旧実装は `_ABS_PATH_RE` のみで `/...` 部分しか拾わず、`~/secret/file.json` を `~<ABS>/file.json` という `~` 残留 leak として出力していた (PR-V `safe_artifact_path` の `~` expansion fix と error_message redact 経路で contract drift)。fix: `_TILDE_PATH_RE` を新設し `_sub_tilde` で `os.path.expanduser` → `_lexical_redact` 経由で `<HOME>/...` placeholder 化、未知 user (`~unknownuser/...`) は `<ABS>/<basename>` に落とす。`_TILDE_PATH_RE` lookbehind は `/` / `=` / `?` / `&` を reject set に含め、URL path segment (`https://example.com/~/x.json` の `~`) と query parameter (`?next=~/x.json`) を誤発火させず PR-K URL preserve invariant を維持 (Codex 04:51 P1 fix)。`_ABS_PATH_RE` lookbehind に `>` + `~` を追加で `<HOME>/...` placeholder 二重 redact 防止 + URL 内 `/~/x.json` の `/x.json` 誤発火防止。`test_observability_redact_error_message_tilde_path_token` を追加 (114→115 件)、12 case (`~/path` / `~/dir-only` / `~/.dotfile` / `~unknownuser/data` / mixed `~`+abs / abs HOME regression / URL+tilde / `~` 単独 + URL path-segment-`~` / query-param-`~` / URL-`~user` preserve / plain abs path regression) で contract lock-in + 1 件 regression test | PR-AS |
| 46 | `build_status(category_override=...)` の str + non-empty + no-control-char defensive contract fix + lock-in。`category_override` は STATUS_MAP lookup の category を bypass して `if category_override is not None: v1_category = category_override` で payload core field に直接代入される経路で、`STATUS_MAP` 側の category format invariant lint (PR-AN) は通常 lookup ペアのみ対象、override 経路は検査されない gap が残っていた。旧実装は型 / 空文字 / 制御文字 validation なしで `""` / `"   "` / `"a\n b"` / `"a\x00b"` / 5 / True / [...] / {...} を silent payload 通過、emit_json 1-line format invariant (PR-Y) も control char 経由で破壊しうる drift。fix: build_status 入口の category_override 代入直前に (1) `isinstance(category_override, str)` 違反 → TypeError、(2) `category_override.strip() == ""` (whitespace-only 含む) → ValueError、(3) `\x00-\x1F` / `\x7F` 制御文字 → ValueError で fail-loud。None は引き続き許容 (STATUS_MAP の category を活かす経路、既存 visual_smoke / build_slide_data / build_telop_data 互換)、PR-AM script identifier / PR-AP v0_status_strict と同型。`test_observability_build_status_category_override_defensive_lint` を追加 (115→116 件)、accept 5 case (kpi-comparison / dimension-regression / preflight-source-meta / slide-build / telop-build の既存 caller 値) + 1 char 最小 + None 経由の STATUS_MAP / fallback / default 省略 3 case + reject 10 type case (int/float/bool/list/dict/tuple/object 等) + reject 5 empty/whitespace + reject 7 control-char で contract lock-in + 1 件 regression test | PR-AT |
| 47 | `warn_legacy_cost_extras(payload)` の payload dict 必須 contract fix + lock-in。`warn_legacy_cost_extras()` は build_status() 出力 dict 前提で `payload.get("cost")` / `for k in LEGACY_COST_EXTRAS_KEYS if k in payload` を直接呼ぶ実装で、entry に型 guard がなく non-dict 入力 (None / list / str / int / tuple / set / object) で uncaught AttributeError "X object has no attribute 'get'" / TypeError "argument of type 'X' is not iterable" を投げて caller 責務違反が見えにくい drift。`emit_json` (PR-AQ) と同型の dict-only contract に統一し、env gate より前に置くことで env=0/unset の no-op 経路でも caller bug を即時検知 (stderr-warning 空 invariant 維持)。fix: warn_legacy_cost_extras 入口に `isinstance(payload, dict)` 違反 → explicit TypeError で fail-loud (env check より前)。`test_observability_warn_legacy_cost_extras_payload_must_be_dict` を追加 (116→117 件)、accept 3 case (no nested cost / empty / nested cost+legacy keys、env off 全 no-op) + reject 14 non-dict case (env off で前置 fail-loud) + stderr 空 invariant + env=1 path の 5 reject + dict regression guard で contract lock-in。既存 caller (build_status() 経由 dict のみ) は backward compatible + 1 件 regression test | PR-AU |

## Test Requirements

- `test_timeline_integration.py` に redaction regression test を実装済:
  - abs_path: `test_observability_safe_artifact_path_redacts` (PR #3)、`test_compare_telop_split_error_message_redacted` (PR-G)、`test_build_slide_data_human_stdout_path_redacted_by_default` (PR-I)、`test_voicevox_narration_summary_path_redacted_by_default` (PR-I fix iter)、`test_generate_slide_plan_stderr_proj_path_redacted` (PR-J)
  - user_content: `test_observability_user_content_meta_no_raw` (PR #3)
  - provider_response_body: `test_observability_redact_provider_body_default_strict` + `test_observability_provider_body_stderr_default_redact` (PR #3)
  - secret: `test_observability_redact_secret_long_value_keeps_last_4` / `_short_value_full_mask` / `_non_string_passthrough` / `_custom_last_n_and_mask_char` / `_last_n_zero_or_negative_full_mask` (PR-H + fix iter、helper-level の last-4 mask + boundary fail-closed を直接 verify)
  - error_message redact: `test_observability_redact_error_message_strips_abs_path` (PR-G fix iter 2)、`_windows_path` / `_ipv6_and_data_uri_safe` / `_multiple_paths_in_one_msg` (PR-K)
- 各 script の `--json-log` smoke test (parse + schema_version 確認) は `test_timeline_integration.py` 内 v1 schema test (`test_observability_build_status_v1_schema` 等) でカバー済。
- cost telemetry の missing rate behavior は `test_generate_slide_plan_skip_preserves_with_bad_env` / `test_generate_slide_plan_rate_rejects_nan_inf` / `test_generate_slide_plan_rate_v0_v1_alias_precedence` (PR-D) で網羅済。

## Open Questions

- (resolved by PR-E、`Trace Context Convention` section 参照: `run_id` は env 優先 + 未設定時 auto-generate、`parent_run_id` / `step_id` は env 優先 + 未設定時 None で auto-generate しない、cap 128 char、cap 超過は error。)
- artifact path の repo-root vs project-root 解釈 (現行 supermovie pipeline は `<PROJECT>` ベース、release repo は repo root ベース)。
- redaction `version` の bump policy (schema_version とは独立、redaction rule 変更時のみ bump)。
- VOICEVOX 以外の local engine が増えた場合の cost 取り扱い (cost null vs 削除)。
