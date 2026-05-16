"""Command-line interface for the unified script project."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

from .config import SkillConfig
from .database import Database
from .workflow import TranscriptionWorkflow


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voice-transcription-service",
        description="Unified voice memo transcription workflow with database coordination.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Transcribe explicit files (ingested automatically if missing)
  python -m script.voice_transcription_service transcribe ./audio/recent.m4a --output-dir ./transcripts

  # Fetch targets from the database (recommended for batch jobs)
  python -m script.voice_transcription_service transcribe --from-db --db-status pending --db-days 7 --yes \
      --output-dir ./transcripts --max-files 10

  # Dry-run to preview the candidates selected from DB
  python -m script.voice_transcription_service transcribe --from-db --db-status pending --dry-run
        """,
    )

    parser.add_argument(
        "--config",
        help="Path to YAML configuration file (defaults to config.yaml alongside the module)",
    )
    parser.add_argument(
        "--api-key",
        help="Override OpenAI API key for this run",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Sync command
    sync_parser = subparsers.add_parser(
        "sync",
        help="Discover and ingest audio files from the configured voice memo directory into the database.",
    )
    sync_parser.add_argument(
        "--path",
        help="Override the voice memo directory path from config (defaults to config sources.voice_memo_directory).",
    )
    sync_parser.add_argument(
        "--days",
        type=int,
        help="Only sync files modified within the last N days (defaults to config sources.default_sync_days).",
    )
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview files that would be synced without actually ingesting them.",
    )
    sync_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed sync information.",
    )

    transcribe_parser = subparsers.add_parser(
        "transcribe",
        help="Transcribe audio files, optionally selected via database filters.",
    )

    transcribe_parser.add_argument(
        "files",
        nargs="*",
        help="Explicit audio file paths to process (ingested into DB if missing).",
    )
    transcribe_parser.add_argument(
        "--from-db",
        action="store_true",
        help="Select files for processing via database filters (recommended for batch).",
    )
    transcribe_parser.add_argument(
        "--db-status",
        choices=["pending", "processing", "completed", "error"],
        help="Filter database records by status when using --from-db.",
    )
    transcribe_parser.add_argument(
        "--db-days",
        type=int,
        help="Only include records created within the last N days when using --from-db.",
    )
    transcribe_parser.add_argument(
        "--db-limit",
        type=int,
        help="Maximum number of database records to fetch (use with --from-db).",
    )
    transcribe_parser.add_argument(
        "--db-offset",
        type=int,
        default=0,
        help="Skip the first N database records (use with --from-db).",
    )

    transcribe_parser.add_argument(
        "--output-dir",
        help="Directory where transcripts will be written (defaults to config output directory or ./transcripts).",
    )
    transcribe_parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel workers to use (default: 1).",
    )
    transcribe_parser.add_argument(
        "--max-files",
        type=int,
        help="Maximum number of files allowed in this run (default from safeguards).",
    )
    transcribe_parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm execution when safeguards require explicit acknowledgement.",
    )
    transcribe_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview selected files without invoking the transcription API.",
    )
    transcribe_parser.add_argument(
        "--force",
        action="store_true",
        help="Process files even if they are marked as completed in the database.",
    )
    transcribe_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print verbose output during processing.",
    )

    return parser


def _setup_workflow(config: SkillConfig, verbose: bool) -> TranscriptionWorkflow:
    db_path = config.resolve_database_path(Path(__file__).resolve().parent.parent)
    database = Database(db_path, timeout=config.database_timeout)
    database.ensure_schema()
    return TranscriptionWorkflow(config, database, verbose=verbose)


def _resolve_output_dir(config: SkillConfig, cli_value: str | None) -> Path:
    if cli_value:
        return Path(cli_value).expanduser().resolve()
    if config.default_output_dir:
        return Path(config.default_output_dir).expanduser().resolve()
    return Path.cwd() / "transcripts"


def _print_preparation_summary(prep, verbose: bool = False) -> None:
    if not prep.to_process:
        print("No files selected for processing.")
    else:
        print("Selected files:")
        for target in prep.to_process:
            print(f"  - {target.file_path} (status={target.status})")

    if prep.skipped and verbose:
        print("\nSkipped files:")
        for path, reason in prep.skipped:
            print(f"  - {path} [{reason}]")


def _print_run_summary(summary, prep_skipped, verbose: bool = False) -> None:
    print("\n=== SUMMARY ===")
    print(f"Completed: {len(summary.processed)}")
    print(f"Failed:    {len(summary.failed)}")
    print(f"Skipped:   {len(prep_skipped)}")

    if verbose:
        if summary.processed:
            print("\nProcessed files:")
            for path in summary.processed:
                print(f"  ✓ {path}")
        if summary.failed:
            print("\nFailed files:")
            for path, error in summary.failed:
                print(f"  ✗ {path}: {error}")
        if prep_skipped:
            print("\nSkipped files:")
            for path, reason in prep_skipped:
                print(f"  ⊘ {path}: {reason}")


def handle_sync(args: argparse.Namespace, config: SkillConfig) -> int:
    """Handle the sync command to discover and ingest files into the database."""
    workflow = _setup_workflow(config, args.verbose)

    voice_memo_dir = args.path if args.path else config.voice_memo_directory
    if not voice_memo_dir:
        print("Error: No voice memo directory specified. Set sources.voice_memo_directory in config or use --path.", file=sys.stderr)
        return 1

    voice_memo_path = Path(voice_memo_dir).expanduser().resolve()
    if not voice_memo_path.exists() or not voice_memo_path.is_dir():
        print(f"Error: Voice memo directory does not exist or is not a directory: {voice_memo_path}", file=sys.stderr)
        return 1

    days = args.days if args.days is not None else config.default_sync_days
    result = workflow.sync_files(voice_memo_path, days=days, dry_run=args.dry_run)

    if args.dry_run:
        print(f"=== DRY RUN: Files that would be synced ===")
    else:
        print(f"=== Sync Complete ===")

    print(f"Found: {result['found']} files")
    print(f"Synced: {result['synced']} files")
    print(f"Skipped: {result['skipped']} files")

    if args.verbose and result['synced_files']:
        print("\nSynced files:")
        for file_path in result['synced_files']:
            print(f"  + {file_path}")

    if args.verbose and result['skipped_files']:
        print("\nSkipped files:")
        for file_path, reason in result['skipped_files']:
            print(f"  - {file_path} ({reason})")

    return 0


def handle_transcribe(args: argparse.Namespace, config: SkillConfig) -> int:
    config.validate()
    workflow = _setup_workflow(config, args.verbose)

    explicit_files: List[Path] = [Path(p).expanduser() for p in args.files]
    effective_db_days = args.db_days if args.db_days is not None else config.default_sync_days

    preparation = workflow.prepare_targets(
        file_paths=explicit_files,
        from_db=args.from_db,
        db_status=args.db_status,
        db_days=effective_db_days,
        db_limit=args.db_limit,
        db_offset=args.db_offset,
        force=args.force,
    )

    workflow.enforce_safeguards(preparation, max_files=args.max_files, require_yes=args.yes)

    if args.dry_run:
        _print_preparation_summary(preparation, verbose=True)
        return 0

    output_dir = _resolve_output_dir(config, args.output_dir)

    summary = workflow.run(
        targets=preparation.to_process,
        output_dir=output_dir,
        workers=max(args.workers, 1),
        force=args.force,
    )

    _print_run_summary(summary, preparation.skipped, verbose=args.verbose)

    return 0 if not summary.failed else 1


def main(argv: List[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        config = SkillConfig.load(args.config)
        if args.api_key:
            config.api_key = args.api_key
        if args.command == "sync":
            return handle_sync(args, config)
        if args.command == "transcribe":
            return handle_transcribe(args, config)
        parser.error(f"Unknown command: {args.command}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
