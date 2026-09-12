"""Core workflow orchestration for script."""

from __future__ import annotations

import os
import sys
import tempfile
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from .audio_downsize import downsize
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
        existing_paths = self.database.get_all_file_paths()

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
        dry_run: bool = False,
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
            if dry_run:
                # A preview registers nothing. Ingesting here would leave the
                # file in 'pending' for the next --from-db batch to transcribe
                # for real — a look-only run must not queue work.
                resolved = path.expanduser().resolve()
                record = self.database.get_file_by_path(resolved) or {
                    "filename": resolved.name,
                    "file_path": str(resolved),
                    "status": "not-yet-synced",
                    "created_at": "",
                }
                records[record["file_path"]] = record
                continue
            try:
                record = self.database.ingest_file(path)
                records[record["file_path"]] = record
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
                records[record["file_path"]] = record

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

    def _claim_output_path(self, output_dir: Path, base_str: str, target: Target) -> Path:
        """Reserve a transcript filename that no other memo can take.

        created_at is minute-precision and usually derived from the filename, so
        two same-named memos in different folders land on the same stamp — which
        used to be impossible only because the database keyed on filename. The
        first writer keeps the plain name, later ones get -2, -3; re-transcribing
        a file reuses whatever name it was written under before.

        Every name is claimed in the database as well as on disk: the stub alone
        stops holding a name once the refine stage moves the transcript out, and
        reusing it then would truncate whichever memo took the name meanwhile.
        """

        key = target.record["file_path"]

        previous = target.record.get("output_filename")
        if previous and self.database.claim_output_filename(key, previous):
            return output_dir / previous

        stem = f"voice-memo_{base_str}"
        index = 1
        while True:
            name = stem if index == 1 else f"{stem}-{index}"
            candidate = output_dir / f"{name}{self.config.output_suffix}"
            if not self.database.claim_output_filename(key, candidate.name):
                index += 1
                continue
            try:
                # Atomic claim: parallel workers cannot both win the same name.
                candidate.touch(exist_ok=False)
                return candidate
            except FileExistsError:
                index += 1

    def _prepare_upload(self, path: Path, stack: ExitStack) -> Tuple[Path, Optional[int]]:
        """Pick the file to upload: the original, or a copy small enough to send.

        Returns (path_to_send, bytes_sent_if_recoded). The second value is None
        whenever the original goes as-is, which is what the database stores for
        every file that needed no compression.

        Only reached from `run()`, never from `prepare_targets` — so a
        `--dry-run`, which returns before `run()` is called, spawns no ffmpeg
        and creates no temp directory.
        """

        limit = self.config.max_file_size_bytes
        try:
            size = path.stat().st_size
        except OSError:
            # Missing or unreadable: let transcribe() raise the specific error
            # it already has for that, rather than inventing a second one.
            return path, None

        if size <= limit or not self.config.downsize.enabled:
            return path, None

        settings = self.config.downsize
        # One temp dir per file, not per run: the name is unique even under
        # --workers N, where a name derived from the input would collide
        # between two same-named memos from different folders.
        tmp_dir = Path(
            stack.enter_context(
                tempfile.TemporaryDirectory(prefix="voice-transcription-downsize-")
            )
        )
        reduced = downsize(
            path,
            tmp_dir,
            channels=settings.channels,
            sample_rate=settings.sample_rate,
            bitrate_kbps=settings.bitrate_kbps,
        )
        sent = reduced.stat().st_size

        if sent > limit:
            # One pass only. Looping down the bitrate would burn minutes of CPU
            # guessing at a limit the operator can simply set.
            raise ValueError(
                f"{path.name} is still too large after compressing: "
                f"{size / (1024 * 1024):.2f} MB -> {sent / (1024 * 1024):.2f} MB, "
                f"over the {self.config.effective_max_file_size_mb} MB limit for "
                f"transcription_mode {self.config.transcription_mode!r}. Lower "
                f"downsize.bitrate_kbps or raise the limit."
            )

        # flush: under --workers N nothing would reach the operator until the
        # process exited, and this line explains a minute of apparent silence.
        print(
            f"⤓ {path.name}: {size / (1024 * 1024):.2f} MB exceeds the "
            f"{self.config.effective_max_file_size_mb} MB "
            f"{self.config.transcription_mode} limit; transcribing a "
            f"{settings.channels}-channel {settings.bitrate_kbps} kbps copy "
            f"({sent / (1024 * 1024):.2f} MB)",
            file=sys.stderr,
            flush=True,
        )
        return reduced, sent

    def run(
        self,
        targets: List[Target],
        output_dir: Path,
        workers: int,
    ) -> RunSummary:
        """Execute transcription for all targets (optionally in parallel)."""

        output_dir.mkdir(parents=True, exist_ok=True)
        self.service.ensure_client()

        summary = RunSummary()

        def _process(target: Target) -> Tuple[str, Optional[str]]:
            path = target.file_path
            key = target.record["file_path"]
            try:
                self.database.mark_processing(key)
            except Exception as exc:
                return (target.filename, f"failed-to-mark-processing: {exc}")

            try:
                # Inside this try on purpose: a failed transcode is funnelled
                # into mark_error below like any other per-file failure, with
                # no error path of its own. The stack closes — deleting the
                # temp copy — as soon as the upload is done, and on Ctrl-C too.
                with ExitStack() as stack:
                    send_path, sent_bytes = self._prepare_upload(path, stack)
                    transcript = self.service.transcribe(send_path)

                if not transcript.strip():
                    raise ValueError("Transcription returned empty text")

                created_at = target.record.get("created_at", "")
                if created_at:
                    base_str = datetime.strptime(created_at, ISO_FORMAT).strftime("%Y%m%d_%H%M%S")
                else:
                    try:
                        mtime = datetime.fromtimestamp(path.stat().st_mtime)
                        base_str = mtime.strftime("%Y%m%d_%H%M%S")
                    except Exception:
                        base_str = path.stem

                output_path = self._claim_output_path(output_dir, base_str, target)

                # Write beside the claimed name, then rename into it. Text is
                # buffered, so a failure at flush/close can leave bytes on disk;
                # under the real name that is a truncated transcript the refine
                # stage would happily turn into a note that reads as complete.
                staging_path = output_path.with_name(output_path.name + ".partial")
                # BaseException, not Exception: a Ctrl-C between the stub claim
                # and a finished write would otherwise leave the empty .md this
                # cleanup exists to remove. The bare raise still aborts the run.
                try:
                    with staging_path.open("w", encoding=self.config.output_encoding) as f:
                        f.write(transcript)
                    os.replace(staging_path, output_path)
                except BaseException:
                    staging_path.unlink(missing_ok=True)
                    # Release the stub claimed above. Left behind, an empty .md
                    # sits in the transcript directory for the refine stage to
                    # pick up as if it were a real transcript.
                    if output_path.exists() and output_path.stat().st_size == 0:
                        output_path.unlink()
                    raise

                try:
                    self.database.mark_completed(key, output_path, sent_file_size=sent_bytes)
                except Exception as exc:
                    # The transcript is on disk; only the bookkeeping failed
                    # (a locked database under --workers N). Report it as this
                    # file's failure rather than letting it sink the batch.
                    return (target.filename, f"failed-to-mark-completed: {exc}")
                return (target.filename, None)
            except Exception as exc:
                error_text = str(exc)
                try:
                    self.database.mark_error(key, error_text)
                except Exception as mark_exc:
                    error_text = f"{error_text} (failed to record error: {mark_exc})"
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
