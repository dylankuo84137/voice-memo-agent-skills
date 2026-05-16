"""Core workflow orchestration for script."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import SkillConfig
from .database import Database, ISO_FORMAT
from .transcription import TranscriptionService


@dataclass
class Target:
    filename: str
    file_path: Path
    status: str
    record: Dict[str, str]


@dataclass
class PreparationResult:
    to_process: List[Target] = field(default_factory=list)
    skipped: List[Tuple[str, str]] = field(default_factory=list)  # (path, reason)


@dataclass
class RunSummary:
    processed: List[str] = field(default_factory=list)
    failed: List[Tuple[str, str]] = field(default_factory=list)


class TranscriptionWorkflow:
    """High-level orchestrator handling DB lookups, safeguards, and processing."""

    def __init__(self, config: SkillConfig, database: Database, verbose: bool = False):
        self.config = config
        self.database = database
        self.verbose = verbose
        self.service = TranscriptionService(config)

    def sync_files(
        self,
        directory: Path,
        days: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, any]:
        """Discover and ingest audio files from a directory into the database.

        Args:
            directory: Path to the directory to scan for audio files
            days: Only sync files modified within the last N days (None for all files)
            dry_run: If True, only preview files without actually ingesting

        Returns:
            Dictionary with sync statistics and results
        """
        result = {
            'found': 0,
            'synced': 0,
            'skipped': 0,
            'synced_files': [],
            'skipped_files': [],
        }

        cutoff_time = datetime.now() - timedelta(days=days) if days is not None else None
        existing_paths = self.database.get_all_file_paths() if not dry_run else set()

        for file_path in directory.rglob('*'):
            if not file_path.is_file():
                continue
            if not self.config.is_supported_format(file_path.name):
                continue

            result['found'] += 1

            if cutoff_time is not None:
                file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                if file_mtime < cutoff_time:
                    result['skipped'] += 1
                    result['skipped_files'].append((str(file_path), 'outside-time-range'))
                    if self.verbose:
                        print(f"Skipping {file_path.name} (modified {file_mtime.strftime('%Y-%m-%d')})")
                    continue

            if str(file_path) in existing_paths:
                result['skipped'] += 1
                result['skipped_files'].append((str(file_path), 'already-in-database'))
                if self.verbose:
                    print(f"Skipping {file_path.name} (already in database)")
                continue

            if not dry_run:
                try:
                    self.database.ingest_file(file_path)
                    result['synced'] += 1
                    result['synced_files'].append(str(file_path))
                    if self.verbose:
                        print(f"Synced {file_path.name}")
                except Exception as exc:
                    result['skipped'] += 1
                    result['skipped_files'].append((str(file_path), f'ingest-error: {exc}'))
                    if self.verbose:
                        print(f"Failed to sync {file_path.name}: {exc}")
            else:
                result['synced'] += 1
                result['synced_files'].append(str(file_path))

        return result

    def prepare_targets(
        self,
        file_paths: List[Path],
        from_db: bool,
        db_status: Optional[str],
        db_days: Optional[int],
        db_limit: Optional[int],
        db_offset: int,
        force: bool,
    ) -> PreparationResult:
        """Combine explicit file paths and DB query results into a unique target list."""

        records: Dict[str, Dict[str, str]] = {}
        skipped: List[Tuple[str, str]] = []

        for path in file_paths:
            if not path.exists():
                skipped.append((str(path), "file-not-found"))
                continue
            if not self.config.is_supported_format(path.name):
                skipped.append((str(path), "unsupported-format"))
                continue
            try:
                record = self.database.ingest_file(path)
                records[record["filename"]] = record
            except Exception as exc:  # pragma: no cover - unexpected edge cases
                skipped.append((str(path), f"ingest-failed: {exc}"))

        if from_db:
            fetched = self.database.fetch_files(
                status=db_status,
                days=db_days,
                limit=db_limit,
                offset=db_offset,
            )
            for record in fetched:
                path = Path(record["file_path"])
                if not path.exists():
                    skipped.append((record["file_path"], "file-not-found"))
                    continue
                if not self.config.is_supported_format(path.name):
                    skipped.append((record["file_path"], "unsupported-format"))
                    continue
                records[record["filename"]] = record

        targets: List[Target] = []
        for record in records.values():
            targets.append(
                Target(
                    filename=record["filename"],
                    file_path=Path(record["file_path"]),
                    status=record.get("status", "pending"),
                    record=record,
                )
            )

        # Respect skip_completed safeguard unless forced
        if self.config.safeguards.skip_completed_by_default and not force:
            still_pending = []
            for target in targets:
                if target.status == "completed":
                    skipped.append((str(target.file_path), "already-completed"))
                else:
                    still_pending.append(target)
            targets = still_pending

        return PreparationResult(to_process=targets, skipped=skipped)

    def enforce_safeguards(
        self,
        preparation: PreparationResult,
        max_files: Optional[int],
        require_yes: bool,
    ) -> None:
        """Validate safeguards like max file count and confirmation."""

        total = len(preparation.to_process)

        if total == 0:
            return

        max_allowed = max_files or self.config.safeguards.default_max_files
        if total > max_allowed:
            raise RuntimeError(
                f"Refusing to process {total} files (limit {max_allowed}). "
                f"Use --max-files {total} to override explicitly."
            )

        if (
            total > self.config.safeguards.confirmation_threshold
            and not require_yes
        ):
            raise RuntimeError(
                f"{total} files selected. Re-run with --yes to confirm this batch."
            )

    def run(
        self,
        targets: List[Target],
        output_dir: Path,
        workers: int,
        force: bool,
    ) -> RunSummary:
        """Execute transcription for all targets (optionally in parallel)."""

        output_dir.mkdir(parents=True, exist_ok=True)

        summary = RunSummary()

        def _process(target: Target) -> Tuple[str, Optional[str]]:
            path = target.file_path
            try:
                self.database.mark_processing(target.filename)
            except Exception as exc:
                return (target.filename, f"failed-to-mark-processing: {exc}")

            try:
                transcript = self.service.transcribe(path)

                created_at = target.record.get("created_at", "")
                if created_at:
                    base_str = datetime.strptime(created_at, ISO_FORMAT).strftime("%Y%m%d_%H%M%S")
                else:
                    try:
                        mtime = datetime.fromtimestamp(path.stat().st_mtime)
                        base_str = mtime.strftime("%Y%m%d_%H%M%S")
                    except Exception:
                        base_str = path.stem

                output_filename = f"voice-memo_{base_str}{self.config.output_suffix}"
                output_path = output_dir / output_filename

                with output_path.open("w", encoding=self.config.output_encoding) as f:
                    f.write(transcript)

                self.database.mark_completed(target.filename, output_path)
                return (target.filename, None)
            except Exception as exc:
                error_text = str(exc)
                self.database.mark_error(target.filename, error_text)
                return (target.filename, error_text)

        if workers > 1 and len(targets) > 1:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_target = {executor.submit(_process, t): t for t in targets}
                for future in as_completed(future_to_target):
                    target = future_to_target[future]
                    filename, error = future.result()
                    if error:
                        summary.failed.append((str(target.file_path), error))
                    else:
                        summary.processed.append(str(target.file_path))
        else:
            for target in targets:
                filename, error = _process(target)
                if error:
                    summary.failed.append((str(target.file_path), error))
                else:
                    summary.processed.append(str(target.file_path))

        return summary
