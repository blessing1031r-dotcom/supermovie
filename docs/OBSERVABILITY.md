# SuperMovie Observability Contract

本 doc は SuperMovie pipeline の observability に関する最小規約を固定する。Codex 19:50 consult verdict (Tech 改善 Medium #3、`/Users/rokumasuda/0_Daily-Workspace/handoff_2026-05-05_1741_supermovie-phase3-pr-cycle.md:60`) に準拠した doc 起点 PR scope。env var rename (Anthropic rate v0→v1 alias) は PR-D で実装済 (Rate Env Var Convention 参照)。provider price 記載 / external SaaS 前提は本 doc では扱わない。

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

実装済の observability surface (Bash 実測 2026-05-05 22:14、PR #3 + PR-A/B/C merged + PR-D rate alias 実装後):

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

- rate env が未設定: `cost.estimate = null`、`cost.rate_missing = true`、`status = ok` (cost 不明は error 扱いしない)。
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
| 4 | `test_timeline_integration.py` に redaction + status v1 schema regression test 9 件追加 (abs_path / user_content / provider_response_body の raw 漏れ防止、build_status duration_ms / category_override、provider body stderr default redact 等。`secret` class の last-4 mask 専用 test は現状未実装、契約のみ doc 保持) | PR #3 + PR-A/B/C |
| 5 | rate env v0 → v1 alias 実装 (Anthropic 限定後方互換) | PR-D |
| 6 | distributed tracing run_id active emission 実装 (`resolve_run_context()` helper + 7 script propagate + cap validation + 7 件 regression test) | PR-E |
| 7 | pre-API cost abort threshold 実装 (`SUPERMOVIE_COST_USD_ABORT_AT` env + `--cost-abort-at` CLI + `cost_guard_aborted` status_map 追加 + estimate 共通化 + 3 件 regression test) | PR-F |
| 8 | error path tail emit consistency audit (`compare_telop_split` の transcript / typo_dict / telop ts read failure を `_emit_early` 経由化、`preflight_video` の `--write-config` parse / write failure を `_emit` 経由化、`visual_smoke` の `out_dir.mkdir` / `videoConfig.ts read` failure を `_emit_early` 経由化、9 status 追加 + 4 件 regression test) | PR-G |
| 9 | helper-level secret redaction 実装 (`redact_secret()` で last-4 mask + short-value 全 mask + non-string passthrough、`docs/OBSERVABILITY.md:123` secret class contract enforcement、4 件 regression test) | PR-H |
| 10 | human stdout path leak audit (build_slide_data / build_telop_data / voicevox_narration / visual_smoke / preflight_video / generate_slide_plan の 9 `print(f"... {path}")` 経路を `safe_artifact_path()` 経由化、`--unsafe-keep-abs-path` で raw 切替 unified knob、abs_path contract 改訂 + 1 件 regression test) | PR-I |
| 11 | stderr path leak audit (preflight_video `input not found` / visual_smoke `MAIN_VIDEO` / `REMOTION_BIN` / `VIDEO_CONFIG` / png ffprobe / generate_slide_plan `PROJ` を `safe_artifact_path()` 経由化、`--unsafe-keep-abs-path` で raw、stderr も同 contract 適用 + 1 件 regression test) | PR-J |
| 12 | `redact_error_message()` regex 強化 (Windows path `C:\...` / `D:/...` 検出 + 置換、IPv6 / data: URI / mailto: の false-positive 防御維持、複数 path 同時 redact + 3 件 edge case test) | PR-K |

## Test Requirements

- `test_timeline_integration.py` に redaction regression test を実装済 (PR #3、`test_observability_safe_artifact_path_redacts` (abs_path) / `test_observability_user_content_meta_no_raw` (user_content) / `test_observability_redact_provider_body_default_strict` + `test_observability_provider_body_stderr_default_redact` (provider_response_body))。`secret` class の last-4 mask rule 専用 test は未実装 (現 codebase に secret を直接 emit する経路がないため、契約のみ doc に保持)。
- 各 script の `--json-log` smoke test (parse + schema_version 確認) は `test_timeline_integration.py` 内 v1 schema test (`test_observability_build_status_v1_schema` 等) でカバー済。
- cost telemetry の missing rate behavior は `test_generate_slide_plan_skip_preserves_with_bad_env` / `test_generate_slide_plan_rate_rejects_nan_inf` / `test_generate_slide_plan_rate_v0_v1_alias_precedence` (PR-D) で網羅済。

## Open Questions

- (resolved by PR-E、`Trace Context Convention` section 参照: `run_id` は env 優先 + 未設定時 auto-generate、`parent_run_id` / `step_id` は env 優先 + 未設定時 None で auto-generate しない、cap 128 char、cap 超過は error。)
- artifact path の repo-root vs project-root 解釈 (現行 supermovie pipeline は `<PROJECT>` ベース、release repo は repo root ベース)。
- redaction `version` の bump policy (schema_version とは独立、redaction rule 変更時のみ bump)。
- VOICEVOX 以外の local engine が増えた場合の cost 取り扱い (cost null vs 削除)。
