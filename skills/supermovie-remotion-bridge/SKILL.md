---
name: supermovie-remotion-bridge
description: |
  SuperMovie と official Remotion Codex plugin の境界を確認する advisory skill。
  Remotion code / component / Composition 変更時だけ `remotion-best-practices` を参照し、
  SuperMovie の schema・file path・Japanese workflow・lint gate を source of truth に保つ。
  「Remotion bridge」「remotion-best-practices」「official Remotion」と言われたときに使用。
argument-hint: [変更したいRemotion領域]
allowed-tools: Read, Glob, Grep, Agent
effort: medium
---

# SuperMovie Remotion Bridge — official Remotion advisory router

SuperMovie owner として、official Remotion Codex plugin を workflow 置換ではなく
Remotion 実装レビュー用の advisory layer として使う。

## Purpose

この skill は SuperMovie の各 phase を実行しない。Remotion `.tsx` component /
Composition / Sequence / Audio / `staticFile` / animation / media timing を変更する前に、
どの知識を SuperMovie 側に残し、どの観点だけ official Remotion guidance に委譲するかを整理する。

## Source Of Truth

| 領域 | Owner |
|------|-------|
| Pipeline order / generated data schema / file path | SuperMovie |
| Japanese production workflow / phase handoff | SuperMovie |
| `python3 template/scripts/test_timeline_integration.py` quality gate | SuperMovie |
| Remotion component hygiene / media primitive review | official `remotion-best-practices` advisory |
| upstream PR / maintainer contact | optional_later |

## Phase 1: Scope Check

対象変更が Remotion 実装に触れるかを確認する。

Remotion guidance を参照する条件:
- `.tsx` component を新規作成または変更する
- `Root.tsx` Composition を変更する
- `Sequence` / `Audio` / `staticFile` / `Video` / `interpolate` / `spring` を変更する
- frame timing / media duration / layer order を変更する

参照しない条件:
- Python script の schema validation / redaction / cost guard だけを変更する
- `transcript.json` / `project-config.json` など data contract だけを変更する
- SuperMovie skill docs の phase handoff だけを変更する

## Phase 2: Advisory Loading

Remotion 実装変更に該当する場合だけ、official Remotion Codex plugin の
`remotion-best-practices` を参照する。

参照時の境界:
- official guidance は Remotion API / animation / media primitive の review 観点に限定する
- SuperMovie の file path / generated data shape / Japanese editing runbook は変更しない
- official plugin の code や rules を SuperMovie に自動 import しない
- upstream PR 作成・close/reopen・maintainer contact は行わない

## Phase 3: SuperMovie Contract Review

変更対象ごとに SuperMovie 側 contract を先に読む。

| 対象 | 先に読む contract |
|------|-------------------|
| Composition / render target | `template/src/Root.tsx`, `template/package.json` |
| Main layer order | `template/src/MainVideo.tsx` |
| Telop registry component | `template/src/テロップテンプレート/telopTemplateRegistry.tsx`, `TelopPlayer.tsx` |
| Slide layer | `template/src/Slides/SlideSequence.tsx`, `slideData.ts` |
| Image layer | `template/src/InsertImage/ImageSequence.tsx`, `insertImageData.ts` |
| Narration | `template/src/Narration/NarrationAudio.tsx`, `mode.ts` |
| SE / BGM | `template/src/SoundEffects/SESequence.tsx`, `BGM.tsx` |

## Phase 4: Output Format

```
Remotion bridge decision:
- Remotion guidance needed: yes / no
- SuperMovie-owned contracts read:
  - <file>
- official `remotion-best-practices` references:
  - <rule / concern>
- non-goals:
  - no upstream PR
  - no source author / maintainer contact
  - no schema/file path ownership transfer
- local quality gate:
  - python3 template/scripts/test_timeline_integration.py
```

## Error Handling

| 状態 | 対応 |
|------|------|
| official Remotion skill が利用できない | SuperMovie contract と既存 Remotion code を優先し、未参照として明示 |
| SuperMovie contract と official guidance が衝突 | SuperMovie contract を優先し、official guidance は advisory として扱う |
| upstream 変更が必要に見える | optional_later に分類し、fork/local の docs・lint・patch で代替する |
| runtime 変更が必要 | 関連 template file と Python lint gate を同じ branch で更新する |
