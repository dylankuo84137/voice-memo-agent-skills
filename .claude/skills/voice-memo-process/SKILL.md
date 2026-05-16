---
name: voice-memo-process
description: Process my personal voice-memo into texts, including voice-memo metadata sychronization, audio-to-text transcription, and transcribed audio database management.
allowed-tools: Read, Grep, Glob
---

# Voice-memo Processing Skill

## Instructions on Major Workflow

Once the User asked you process its voice-memo, you will follow the steps below carefully.

## Script Tool Available

You will use a script tool named 'voice_transcription_service' to help you complete the job.

For how to use the tool, you will read [script/voice_transcription_service/README.md](script/voice_transcription_service/README.md) to learn.

If you need to update tool config, you can update the config file: [script/voice_transcription_service/config.yaml](script/voice_transcription_service/config.yaml)

### Step-1 Voice-memo Metadata Sychronization

To begin the process, you will sychronize voice-memo database for target file identification and status management.

If the User did not specific a time range, you will use the script tool to get a summary of un-processed voice memo and ask the User to tell you which voice-memos to process.

### Step-2 Audio-to-text transcription

Once target voice-memos are determined, run the unified CLI to transcribe and update DB status atomically.

For security, if the number of files exceed a batch processing limits, gain grants from the User.

### Step-3 Database Update

Status updates are written back to the database automatically (processing/completed/error with output paths).

Double check to ensure the process is successfully completed.

Summarize your job and report to the user, and ask whether the user would like to further refine the results into Obsidian style markdown notes.

If the user would like to further refine the results, use skill named 'refine-memo-for-obsidian'. 

Skill Ref:
[.claude/skills/refine-memo-for-obsidian/SKILL.md](.claude/skills/refine-memo-for-obsidian/SKILL.md)

## Config

Beside the major process, you could also help the user to adjust script config to help the user out.
Before any config adjustment, you will explain the use for each config the user would like to change.
Provide your advice for user consideration.

## Meta Info

version: 1.2.0
author: Tony Huang (<https://www.youtube.com/@tonyhhq>)
date-updated: 2025-10-24
