#!/usr/bin/env python3
"""Keep source and website release metadata aligned with a release tag."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile


VERSION_PATTERN = re.compile(
    r"^(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})$"
)
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PROJECT_VERSION_PATTERN = re.compile(
    r'(?m)^(\s*MARKETING_VERSION:\s*")[^"]+("\s*)$'
)
STRUCTURED_VERSION_PATTERN = re.compile(
    r'("softwareVersion"\s*:\s*")[^"]+("\s*,?)'
)
HOMEPAGE_LASTMOD_PATTERN = re.compile(
    r"(<loc>https://easelwall\.com/</loc>\s*<lastmod>)[^<]+(</lastmod>)"
)


def replace_exactly_once(
    content: str, pattern: re.Pattern[str], value: str, label: str
) -> str:
    updated, count = pattern.subn(
        lambda match: f"{match.group(1)}{value}{match.group(2)}", content
    )
    if count != 1:
        raise ValueError(f"Expected exactly one {label}; found {count}")
    return updated


@dataclass(frozen=True)
class StagedUpdate:
    target: Path
    temporary: Path
    backup: Path


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _stage_update(target: Path, content: str) -> StagedUpdate:
    """Write replacement and backup siblings without changing the target."""

    mode = stat.S_IMODE(target.stat().st_mode)
    temporary_fd, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    backup: Path | None = None
    try:
        with os.fdopen(temporary_fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)

        backup_fd, backup_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".backup",
        )
        os.close(backup_fd)
        backup = Path(backup_name)
        shutil.copy2(target, backup)
        _fsync_file(backup)
        return StagedUpdate(target=target, temporary=temporary, backup=backup)
    except BaseException:
        temporary.unlink(missing_ok=True)
        if backup is not None:
            backup.unlink(missing_ok=True)
        raise


def _cleanup_staged_updates(updates: list[StagedUpdate]) -> None:
    for update in updates:
        update.temporary.unlink(missing_ok=True)
        update.backup.unlink(missing_ok=True)


def _replace_transactionally(replacements: list[tuple[Path, str]]) -> None:
    """Replace a group of files and restore every changed target on failure."""

    staged: list[StagedUpdate] = []
    replaced: list[StagedUpdate] = []
    try:
        for target, content in replacements:
            staged.append(_stage_update(target, content))
        for update in staged:
            os.replace(update.temporary, update.target)
            replaced.append(update)
    except BaseException as error:
        rollback_errors: list[str] = []
        for update in reversed(replaced):
            try:
                os.replace(update.backup, update.target)
            except OSError as rollback_error:
                rollback_errors.append(f"{update.target}: {rollback_error}")
        if rollback_errors:
            # Preserve failed backups for manual recovery; remove only files
            # that are no longer needed.
            for update in staged:
                update.temporary.unlink(missing_ok=True)
                if update.target not in {
                    failed.target
                    for failed in replaced
                    if failed.backup.exists()
                }:
                    update.backup.unlink(missing_ok=True)
            details = "; ".join(rollback_errors)
            raise RuntimeError(
                f"Release metadata update failed and rollback was incomplete: {details}"
            ) from error
        _cleanup_staged_updates(staged)
        raise
    else:
        _cleanup_staged_updates(staged)


def update_release_metadata(root: Path, version: str, release_date: str) -> None:
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError("Version must use canonical MAJOR.MINOR.PATCH components")
    if not DATE_PATTERN.fullmatch(release_date):
        raise ValueError("Release date must use YYYY-MM-DD")
    try:
        parsed_release_date = date.fromisoformat(release_date)
    except ValueError as error:
        raise ValueError("Release date must use YYYY-MM-DD") from error
    if parsed_release_date.isoformat() != release_date:
        raise ValueError("Release date must use YYYY-MM-DD")

    project_path = root / "project.yml"
    index_path = root / "website" / "index.html"
    sitemap_path = root / "website" / "sitemap.xml"

    # Transform every input before writing any output so a missing marker cannot
    # leave the repository partially updated.
    project = replace_exactly_once(
        project_path.read_text(encoding="utf-8"),
        PROJECT_VERSION_PATTERN,
        version,
        "MARKETING_VERSION setting",
    )
    index = replace_exactly_once(
        index_path.read_text(encoding="utf-8"),
        STRUCTURED_VERSION_PATTERN,
        version,
        "SoftwareApplication softwareVersion",
    )
    sitemap = replace_exactly_once(
        sitemap_path.read_text(encoding="utf-8"),
        HOMEPAGE_LASTMOD_PATTERN,
        release_date,
        "homepage sitemap lastmod",
    )

    _replace_transactionally(
        [
            (project_path, project),
            (index_path, index),
            (sitemap_path, sitemap),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    update_release_metadata(args.root.resolve(), args.version, args.date)
    print(f"Updated release metadata to {args.version} ({args.date})")


if __name__ == "__main__":
    main()
