---

## name: refine-memo-for-obsidian
description: Refine voice-memo transcripts into Obsidian-style markdown notes
allowed-tools: Read, Write, Grep, Glob, Bash

# Memo Transcript Refinement

## Instructions

Refine voice-memo transcripts one file at a time into Obsidian-style markdown notes.

You have two project filepaths to manage your work:

- `~/Documents/voice-memo-agent-skills/raw-transcript`: directory where voice-memo transcripts to be processed
- `~/Documents/mdNote/Voice-Memo-Vault/`: directory where the refined markdown notes should be saved

You will ONLY use tags from `~/Documents/voice-memo-agent-skills/note-property`

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
List files under 'raw-transcript' and confirm with the user on the target.
Then you will process one file at a time.

**Step-2: Move Raw File**
Move the target files to `~/Documents/mdNote/Voice-Memo-Vault/` directory.

**Step-3: Transcript Refine**
Use File Structure Example as reference, update your target file one by one.

### QA

**What if I did not find any raw transcripts to be processed?**

It means that the voice-memo haven't been transcribed into text file.
You should refer to skill 'voice-memo-process' to process audio file first. 

Skill Directory Ref:
[.claude/skills/voice-memo-process/SKILL.md](.claude/skills/voice-memo-process/SKILL.md)