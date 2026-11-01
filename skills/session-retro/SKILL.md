---
name: session-retro
version: 2.0.0
description: |
  Session insights report. Finds what cost this session time, technical or
  not: decisions that reversed, terminology churn, misread intent, corrections
  the user had to repeat, and why the handoff wasn't taken when ccBar offered
  one. Writes the report to ~/.claude/retros/ and proposes durable lessons as
  memory or CLAUDE.md edits for approval. Triggered automatically by ccBar's
  Stop hook after the handoff threshold, or run manually any time.
license: MIT
compatibility: claude-code
allowed-tools:
  - Read
  - Grep
  - Glob
  - Write
---

# /session-retro

Blunt, evidence-based insights from this session so far. Args (when triggered by ccBar): `session_id`, `transcript_path`, `cwd`, and `handoff_fired_at` (tokens) when ccBar's handoff already fired this session.

## 1. Source

Review the conversation already in your context. Do NOT Read the transcript file: it is several MB at this size and would pull a large part of the session back into context.

Only if the context shows a compaction summary, use Grep on `transcript_path` for specific evidence: tool errors, the user's corrections ("no", "don't", "wrong", "stop", "I meant", "actually", "again"), renames, reverted edits. Never read it whole.

## 2. Find what cost time

Most of a session's lessons are not technical. They're about what words meant, what was decided, and how the user and Claude worked together. Scan with each lens:

- **Skipped handoff** (only when `handoff_fired_at` is set): ccBar offered a handoff and the session kept going. Look at what happened after the handoff plan was written. Was the work mid-step, so a cut would have cost more than it saved? Did the user choose to continue? Did scope grow, or did the session drift into side work? Say plainly whether continuing was worth it and where a clean cut was. If the handoff plan was written in the immediately previous response, the user hasn't had a chance to act yet: say so in one line instead.
- **Decisions that moved:** a product, design, naming or technical decision that was made, then reopened or reversed. Note what it finally settled on, how many rounds it took, and what question or fact, raised at the start, would have settled it in one.
- **Language and intent:** terms the user and Claude used differently, wording that churned (labels, copy, names), asks read too literally or too loosely, things the user had to say twice.
- **Working style:** asked when it should have acted, or acted when it should have asked; did more or less than asked; length or tone the user pushed back on.
- **Technical misses:** wrong assumptions about code, tools or environment; verification that failed or was skipped; tool errors that cost a retry.

Also note an approach the user explicitly confirmed when it wasn't the obvious choice. That's a lesson too. Routine success isn't.

Keep the three to five findings that cost the most. Don't pad to fill a lens, and don't invent problems.

## 3. Write the retro

Write it to `~/.claude/retros/YYYY-MM-DD-<cwd leaf>-<first 8 chars of session_id>.md`, using today's date. On a manual run with no session_id, use `HHMM` in place of the session fragment.

    # Retro: <what the session was for, one line>

    ## Headline
    <One to three sentences: the biggest cost this session and its cause. If nothing notable, say so and stop here.>

    ## Skipped handoff
    <Only when handoff_fired_at is set.>

    ## Findings
    ### <Lens>: <short name>
    - **What happened:** <evidence: the user's words quoted briefly, or the file or tool call>
    - **Cost:** <rough turns or time lost>
    - **Lesson:** <what to do differently, as a rule someone could follow>

    ## Confirmed
    <Only if any.>

Order findings by cost, highest first. Quote the user verbatim and briefly; never paste code, secrets or long passages.

## 4. Durable lessons

For each lesson likely to recur, pick the single most appropriate destination:

- **Auto-memory `project` entry:** a product, design or terminology decision the session settled after back-and-forth, including what was rejected, so later sessions don't reopen it. If the session worked from a project decisions log (a PM handoff or product docs), propose a row there instead.
- **Auto-memory `feedback` entry:** how the user wants Claude to work, personal and cross-session, not already in a CLAUDE.md.
- **Workspace or project `CLAUDE.md`:** the nearest existing `CLAUDE.md` walking up from `cwd`, preferring one above the git repo root when it exists. Never propose creating a new `CLAUDE.md` inside a git repo. Before proposing an edit to one inside a repo, check `~/.claude/CLAUDE.md` and any ancestor `CLAUDE.md` for a rule against repo-level `CLAUDE.md` files; if there is one, use the next destination up.
- **Global `~/.claude/CLAUDE.md`:** a rule about how the user works across all projects.

Memory entries follow the memory file format from your system prompt (one fact per file plus a MEMORY.md pointer).

Before proposing, read the target and check for an existing entry that covers it. Propose updating that entry rather than adding a duplicate. If the lesson already exists as a rule, say the session didn't follow it instead of proposing a duplicate. One-off mistakes don't become rules. Expect 0 to 3 proposals.

## 5. Output in chat

- The headline
- The retro file path
- Numbered proposals, each with the target path and the exact text to add or change

Do NOT write any memory, CLAUDE.md or decisions-log change until the user approves it by number.

## 6. Stop

After reporting, stop. Don't resume the prior task until the user says so.

Write in the user's style, following any writing rules in their CLAUDE.md files or memory.
