---

## name: refine-memo-for-obsidian
description: Refine voice-memo transcripts into Obsidian-style markdown notes
allowed-tools: Read, Write, Grep, Glob, Bash

# Memo Transcript Refinement

## Instructions

Refine voice-memo transcripts one file at a time into Obsidian-style markdown notes.

**Before anything else, load the working paths from the repo-root `.env` file**
(read `.env`, or copy `.env.example` to `.env` first if it is missing). These
variables define every directory this skill touches — never hardcode paths:

- `RAW_TRANSCRIPT_DIR`: directory where voice-memo transcripts to be processed
- `OBSIDIAN_VAULT_DIR`: directory where the refined markdown notes should be saved
- `NOTE_PROPERTY_DIR`: directory holding the canonical tag list (`tag-list.md`)

A leading `~` in any value expands to the user's home directory.

You will ONLY use tags from the `NOTE_PROPERTY_DIR` directory.

## Target File Structure Example

The obsidian style markdown note will contains two parts:

1. YAML metadata
2. Note Summary
3. Content

Example:

```
---
title: "Evaluating Long-Context Question & Answer Systems"
recorded: 2025-06-22 #voice-memo recording date
created: 2025-08-25 #obsidian note creation date
description: "Evaluation metrics, how to build eval datasets, eval methodology, and a review of several benchmarks." #one sentence description
tags:
  - "ai-eval" #proper note tag or tags
---

## Note Summary

<Summarized content outline>

<Transform oral style into writing style. Paragraphing for readability. No section heading needed.>
```

## Procedures

**Step-1: Target Confirm**
List files under `RAW_TRANSCRIPT_DIR` and confirm with the user on the target.
Then you will process one file at a time.

**Step-2: Move Raw File**
Move the target files to the `OBSIDIAN_VAULT_DIR` directory.

**Step-3: Transcript Refine**
Use File Structure Example as reference, update your target file one by one.

**Step-4: Rename with Title**
Rename the refined note so its subject is readable at a glance, keeping the
original timestamp prefix intact:

```
voice-memo_<YYYYMMDD_HHMMSS>_<title>.md
```

- `<title>` is the frontmatter `title` value, without its surrounding quotes.
- Strip the characters `\ / : * ? " < > | # ^ [ ]` (they break filesystems or
  Obsidian wikilinks) — drop them, or use the full-width form when it reads
  better (`：`, `？`). Full-width punctuation is safe and needs no change.
- Collapse runs of whitespace to one space; trim leading/trailing spaces, dots
  and dashes.
- Cap `<title>` at 80 characters; cut on a word/clause boundary, no ellipsis.
- If the target name already exists, append `-2`, `-3`, … before `.md`.

The `title` frontmatter keeps the full untruncated title — the filename is the
readable label, not the source of truth.

### QA

**What if I did not find any raw transcripts to be processed?**

It means that the voice-memo haven't been transcribed into text file.
You should refer to skill 'voice-memo-process' to process audio file first. 

Skill Directory Ref:
[.claude/skills/voice-memo-process/SKILL.md](.claude/skills/voice-memo-process/SKILL.md)