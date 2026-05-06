# SuperMovie x Remotion Codex Plugin Integration

This document defines the first integration boundary between SuperMovie and the
official Remotion Codex plugin. It is a plan and contract, not a replacement proposal.

## Inputs Verified

- SuperMovie local repo owns the plugin manifest, skills, Remotion template,
  Python pipeline scripts, observability gate, and Japanese editing workflow.
- Official Remotion Codex plugin lives at `openai/plugins/plugins/remotion` with
  `.codex-plugin/plugin.json`, `skills/remotion/SKILL.md`, and
  `skills/remotion/rules/*.md`.
- Official plugin manifest name is `remotion`, and its interface describes the
  capability as `Create motion graphics from prompts`.
- Official Remotion skill frontmatter name is `remotion-best-practices`.
- The official plugin README says the source of truth is
  `remotion-dev/remotion/packages/codex-plugin`.

## What Remains SuperMovie-Owned

SuperMovie stays the owner of the end-to-end editing pipeline, generated data
schemas, Japanese production workflow, and repo quality gates.

SuperMovie-owned skills:

- `skills/supermovie-cut/SKILL.md`
- `skills/supermovie-image-gen/SKILL.md`
- `skills/supermovie-init/SKILL.md`
- `skills/supermovie-narration/SKILL.md`
- `skills/supermovie-remotion-bridge/SKILL.md`
- `skills/supermovie-se/SKILL.md`
- `skills/supermovie-skill-creator/SKILL.md`
- `skills/supermovie-slides/SKILL.md`
- `skills/supermovie-subtitles/SKILL.md`
- `skills/supermovie-telop-creator/SKILL.md`
- `skills/supermovie-transcribe/SKILL.md`
- `skills/supermovie-transcript-fix/SKILL.md`

SuperMovie-owned runtime and data contracts:

- `project-config.json`, `transcript.json`, `transcript_fixed.json`,
  `cutData.ts`, `telopData.ts`, `titleData.ts`, `slideData.ts`,
  `insertImageData.ts`, `narrationData.ts`, and `seData.ts`.
- `template/scripts/*.py` pipeline behavior, including timeline mapping,
  observability status JSON, redaction, cost guard, and pure Python lint tests.
- Japanese telop registry, style vocabulary, VOICEVOX narration flow, Gemini
  image generation handoff, SE catalog handoff, and fork-local release flow.

## What Can Be Delegated To The Official Remotion Plugin

The official Remotion plugin should be used as a specialist reference layer for
Remotion and React implementation details. It can inform SuperMovie changes in
these areas without taking ownership of the SuperMovie workflow:

- `rules/sequencing.md` for frame-local sequencing, trimming, and layer timing.
- `rules/timing.md` and `rules/animations.md` for interpolate, spring, easing,
  and animation hygiene.
- `rules/audio.md`, `rules/videos.md`, and `rules/sfx.md` for Remotion media
  primitives, volume, looping, and sound effects.
- `rules/subtitles.md` and `rules/display-captions.md` for caption rendering
  patterns when SuperMovie subtitle contracts need Remotion-specific review.
- `rules/ffmpeg.md`, `rules/silence-detection.md`, and duration/dimension rules
  for media probing approaches before SuperMovie wraps them in its own scripts.
- `rules/compositions.md`, `rules/parameters.md`, and
  `rules/calculate-metadata.md` for Composition and metadata design ideas.

Delegation rule: official Remotion guidance is advisory. SuperMovie data schemas, file paths, Japanese editing runbooks, and tests remain the source of truth inside this repo.

## What Should Move Into Codex Skills

Codex skill migration should be thin and behavior-preserving:

- Keep each SuperMovie phase as a Codex skill entrypoint so users can still run
  init, transcribe, transcript fix, cut, subtitles, slides, narration,
  image-gen, SE, telop creator, and skill creator independently.
- Add a bridge note to Remotion-facing SuperMovie skills that says: load the official `remotion-best-practices` skill only when changing Remotion code or designing new Remotion components.
- Keep pipeline orchestration, schema validation, and generated file contracts
  in SuperMovie skills rather than moving them into the official Remotion skill.
- Use official Remotion rule files as references for implementation review, not
  as a replacement for SuperMovie lint gates.

## Minimal Spike

A minimal spike is worth doing, but only as a local/fork docs-and-lint branch.
The spike should prove the boundary without changing runtime behavior:

1. Add this integration boundary document.
2. Add a pure Python lint test that keeps this document aligned with local
   SuperMovie skill names and the official Remotion plugin concepts listed
   above.
3. Do not replace SuperMovie skills.
4. Do not create upstream PRs.
5. Do not contact upstream maintainers.

Success criteria:

- `python3 template/scripts/test_timeline_integration.py` passes locally.
- fork/main can carry the doc and lint without touching `origin`.
- `skills/supermovie-remotion-bridge/SKILL.md` is a docs-only advisory router.
  A later branch can expand `supermovie-remotion-bridge` only if this boundary
  remains stable.

## Non-Goals

- Replacing SuperMovie with the official Remotion Codex plugin.
- Auto-importing code from `openai/plugins` or `remotion-dev/remotion`.
- Opening, closing, or modifying upstream PRs.
- Making upstream merge status a completion condition.
- Rewriting history, force-pushing, or deleting remote history.
