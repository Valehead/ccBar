---
name: session-retro
version: 1.0.0
description: |
  Mid-session retrospective. Reviews the current session for what went wrong,
  what could have been done better, and what to improve next time. Writes the
  retro to ~/.claude/retros/ and proposes durable lessons as memory or
  CLAUDE.md edits for approval. Triggered automatically by ccBar's Stop hook
  at a context threshold, or run manually any time.
license: MIT
compatibility: claude-code
allowed-tools:
  - Read
  - Grep
  - Glob
  - Write
---

# /session-retro

Blunt, evidence-based review of this session so far. Args (when triggered by ccBar): `session_id`, `transcript_path`, `cwd`.

## 1. Source

Review the conversation already in your context. Do NOT Read the transcript file: it is several MB at this size and would pull a large part of the session back into context.

Only if the context shows a compaction summary, use Grep on `transcript_path` for specific evidence: tool errors, the user's corrections ("no", "don't", "wrong", "stop"), reverted edits. Never read it whole.

## 2. Three sections

Each item is concrete and tied to evidence (the tool call, file, or user message):

- **What went wrong**
- **What could have been done better**
- **What to improve next time**

"Nothing notable" is a valid answer for a section. Be blunt, skip padding and praise, and don't invent problems.

## 3. Write the retro

Write it to `~/.claude/retros/YYYY-MM-DD-<cwd leaf>-<first 8 chars of session_id>.md`, using today's date. On a manual run with no session_id, use `HHMM` in place of the session fragment.

## 4. Durable lessons

For each lesson likely to recur, pick the single most appropriate destination:

- **Auto-memory `feedback` entry:** how the user wants Claude to work, personal and cross-session, not already in a CLAUDE.md. Follow the memory file format from your system prompt (one fact per file plus a MEMORY.md pointer).
- **Project `CLAUDE.md`** (in the repo at `cwd`): a convention or gotcha specific to this repo that any session there should follow.
- **Global `~/.claude/CLAUDE.md`:** a rule about how the user works across all projects.

Before proposing, read the target and check for an existing entry that covers it. Propose updating that entry rather than adding a duplicate. One-off mistakes don't become rules. Expect 0 to 2 proposals.

## 5. Output in chat

- A short summary of the retro
- The retro file path
- Numbered proposals, each with the target path and the exact text to add or change

Do NOT write any memory or CLAUDE.md change until the user approves it by number.

## 6. Stop

After reporting, stop. Don't resume the prior task until the user says so.

Write in the user's style: terse, no em dashes.
