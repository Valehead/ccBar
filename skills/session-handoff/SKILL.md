---
name: session-handoff
version: 1.0.0
description: |
  Session handoff. Writes a self-contained plan for the current work to
  ~/.claude/plans/ so a fresh session with no context can pick it up, then
  prints a one-line prompt to paste into that session. Triggered
  automatically by ccBar's Stop hook at a context threshold, or run manually
  any time.
license: MIT
compatibility: claude-code
allowed-tools:
  - Read
  - Grep
  - Glob
  - Write
  - Bash(git status:*)
  - Bash(git log:*)
---

# /session-handoff

Hand the current work to a fresh session. Args (when triggered by ccBar): `session_id`, `transcript_path`, `cwd`, `plans_dir`, `prompt_template` (quoted). On a manual run with no args, use `~/.claude/plans` and `Read and execute this plan at: {path}`.

## 1. Source

Use the conversation already in your context. Do NOT Read the transcript file: it is several MB at this size and would pull a large part of the session back into context.

Only if the context shows a compaction summary, use Grep on `transcript_path` for specific detail the summary hides. Never read it whole. `git status` and `git log` in `cwd` are fine for confirming repo state.

## 2. Write the plan

Write it to `<plans_dir>/<descriptive-kebab-name>.md`. The name describes the work, not the date or session id. If the file exists, append `-2`, `-3`, and so on.

The plan must stand alone for a session with zero context. Sections:

- **Goal:** what the work is for, with the original ask in the user's words where it matters
- **Current state:** what's done, what's changed but uncommitted or only staged, what's verified vs. untested
- **Decisions:** what was decided and why, including options rejected so the next session doesn't relitigate them
- **Files and IDs:** exact files, paths, branches, issue/PR numbers, IDs involved
- **Next steps:** ordered, concrete enough to execute
- **Verify:** exact commands
- **Gotchas:** environment quirks and traps hit in this session
- **Open questions:** anything waiting on the user

Never invent. If something is unknown, the plan says it's unknown.

## 3. Output in chat

- One short line on what the plan covers
- Then `prompt_template` with `{path}` replaced by the plan's absolute path, on its own line so it's easy to copy

Nothing after that line.

## 4. Stop

Don't continue the prior task, don't commit, don't push.

Write in the user's style: terse, no em dashes.
