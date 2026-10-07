"""Folder bulk download: plan the archive layout, then stream it into a zip.

Layout mirrors Bulk import (ZIP): paths are relative to the viewed folder and ``dir/`` is an
empty folder. Planning runs under the caller's company scope (the same predicate as the
single-file download); ``files_not_visible`` is a number only, never a name or an id.
"""
from __future__ import annotations

import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.base import company_scope
from app.models.resources import Attachment, AttachmentDirectory
from app.services.resources_service import AttachmentDirectoryService

logger = logging.getLogger(__name__)

_COPY_NUMBERED_RE = re.compile(r"^(?P<stem>.*) - copy \((?P<n>\d+)\)$")
_COPY_PLAIN_SUFFIX = " - copy"
_PROGRESS_EVERY = 10


def suffix_copy_name(filename: str) -> str:
    """Google-Drive style "- copy" suffix bump for the upload collision flow.

    Splits at the rightmost dot so multi-dot basenames like `archive.tar.gz`
    are treated as base=`archive.tar`, ext=`.gz`. Files without an extension
    (e.g. `README`) get the suffix appended directly. The marker is matched
    on the base only:

      * `"x"` -> `"x - copy"`
      * `"x - copy"` -> `"x - copy (2)"`
      * `"x - copy (N)"` -> `"x - copy (N+1)"`
    """
    if "." in filename:
        base, ext = filename.rsplit(".", 1)
        ext = f".{ext}"
    else:
        base, ext = filename, ""
    m = _COPY_NUMBERED_RE.match(base)
    if m:
        n = int(m.group("n")) + 1
        new_base = f"{m.group('stem')} - copy ({n})"
    elif base.endswith(_COPY_PLAIN_SUFFIX):
        new_base = f"{base} (2)"
    else:
        new_base = f"{base}{_COPY_PLAIN_SUFFIX}"
    return f"{new_base}{ext}"


def _safe_name(name: Optional[str]) -> str:
    cleaned = "".join(c for c in (name or "") if c.isprintable()).strip()
    # Nothing but separators, "." or "..": it would write an empty, current or parent segment.
    if cleaned.replace("/", "").replace("\\", "").strip() in ("", ".", ".."):
        return "_"
    return cleaned.replace("/", "-").replace("\\", "-")


def archive_filename(folder_name: Optional[str], now: datetime) -> str:
    stem = _safe_name(folder_name).strip() if folder_name else ""
    return f"{stem or 'Files'}-{now.strftime('%Y%m%d-%H%M')}.zip"


@dataclass
class ArchiveEntry:
    path: str
    attachment: Optional[Attachment] = None  # None = directory entry


@dataclass
class ArchivePlan:
    entries: List[ArchiveEntry] = field(default_factory=list)
    files_not_visible: int = 0
    folders: int = 0

    @property
    def files(self) -> List[ArchiveEntry]:
        return [e for e in self.entries if e.attachment is not None]


@dataclass
class ArchiveStats:
    files_included: int = 0
    files_failed: int = 0
    bytes: int = 0
    cancelled: bool = False


def _unique_name(name: str, seen: Set[str]) -> str:
    while name.lower() in seen:
        name = suffix_copy_name(name)
    seen.add(name.lower())
    return name


def _entry_name(att: Attachment) -> str:
    return _safe_name(att.stored_filename or att.original_filename)


def _subtree_ids(db: Session, root_ids: List[str]) -> Dict[str, List[str]]:
    svc = AttachmentDirectoryService(db)
    return {
        rid: svc.get_descendant_directory_ids_portable(rid, include_deleted=False)
        for rid in root_ids
    }


def _count_files(db: Session, directory_ids: List[str]) -> int:
    if not directory_ids:
        return 0
    return int(
        db.query(func.count(Attachment.id))
        .filter(Attachment.directory_id.in_(directory_ids), Attachment.is_deleted == False)  # noqa: E712
        .scalar()
        or 0
    )


def plan_archive(
    db: Session,
    *,
    directory_id: Optional[str],
    folder_ids: List[str],
    attachment_ids: List[str],
) -> ArchivePlan:
    """Resolve the selection into archive entries under the session's company scope."""
    folder_rows = (
        db.query(AttachmentDirectory)
        .filter(AttachmentDirectory.id.in_(folder_ids), AttachmentDirectory.is_deleted == False)  # noqa: E712
        .all()
        if folder_ids
        else []
    )
    selected = {str(f.id): f for f in folder_rows}
    subtrees = _subtree_ids(db, list(selected))

    # Nested selection: a folder inside another selected folder's subtree is written there.
    roots = [
        fid
        for fid in selected
        if not any(fid != other and fid in subtrees[other] for other in selected)
    ]
    covered: Set[str] = {d for rid in roots for d in subtrees[rid]}

    file_rows = (
        db.query(Attachment)
        .filter(Attachment.id.in_(attachment_ids), Attachment.is_deleted == False)  # noqa: E712
        .all()
        if attachment_ids
        else []
    )
    root_files = [a for a in file_rows if not (a.directory_id and str(a.directory_id) in covered)]

    all_dir_ids = sorted(covered)
    dir_rows = (
        db.query(AttachmentDirectory)
        .filter(AttachmentDirectory.id.in_(all_dir_ids))
        .all()
        if all_dir_ids
        else []
    )
    by_id = {str(d.id): d for d in dir_rows}
    children: Dict[str, List[str]] = {}
    for d in dir_rows:
        if d.parent_id and str(d.parent_id) in by_id:
            children.setdefault(str(d.parent_id), []).append(str(d.id))
    files_by_dir: Dict[str, List[Attachment]] = {}
    if all_dir_ids:
        for a in (
            db.query(Attachment)
            .filter(Attachment.directory_id.in_(all_dir_ids), Attachment.is_deleted == False)  # noqa: E712
            .order_by(Attachment.uploaded_at, Attachment.id)
            .all()
        ):
            files_by_dir.setdefault(str(a.directory_id), []).append(a)

    plan = ArchivePlan()

    seen_root: Set[str] = set()
    for a in sorted(root_files, key=lambda x: (x.uploaded_at or datetime.min, str(x.id))):
        plan.entries.append(ArchiveEntry(_unique_name(_entry_name(a), seen_root), a))

    def walk(fid: str, prefix: str) -> None:
        plan.folders += 1
        kids = sorted(children.get(fid, []), key=lambda k: (by_id[k].name or "", k))
        files = files_by_dir.get(fid, [])
        if not kids and not files:
            plan.entries.append(ArchiveEntry(f"{prefix}/", None))
            return
        seen: Set[str] = set()
        for a in files:
            plan.entries.append(ArchiveEntry(f"{prefix}/{_unique_name(_entry_name(a), seen)}", a))
        for k in kids:
            walk(k, f"{prefix}/{_safe_name(by_id[k].name)}")

    for rid in sorted(roots, key=lambda r: (selected[r].name or "", r)):
        walk(rid, _safe_name(selected[rid].name))

    # Not-visible count: numbers only. Same subtree, whole-company view minus the caller's view.
    visible = sum(_count_files(db, subtrees[rid]) for rid in roots)
    visible_files = len(root_files)
    with company_scope(db, None):
        all_subtrees = _subtree_ids(db, roots)
        everything = sum(_count_files(db, ids) for ids in all_subtrees.values())
        direct_all = (
            db.query(Attachment)
            .filter(Attachment.id.in_(attachment_ids), Attachment.is_deleted == False)  # noqa: E712
            .all()
            if attachment_ids
            else []
        )
        direct_all_n = sum(
            1 for a in direct_all if not (a.directory_id and str(a.directory_id) in covered)
        )
    plan.files_not_visible = max(0, (everything - visible) + (direct_all_n - visible_files))
    return plan


def write_archive(
    plan: ArchivePlan,
    read_bytes: Callable[[Attachment], bytes],
    out_fileobj,
    on_progress: Optional[Callable[[int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> ArchiveStats:
    """Write the plan into ``out_fileobj`` as a stored (uncompressed) zip64-capable archive."""
    stats = ArchiveStats()
    processed = 0
    with zipfile.ZipFile(out_fileobj, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for entry in plan.entries:
            if entry.attachment is None:
                zf.writestr(entry.path, b"")
                continue
            if processed and processed % _PROGRESS_EVERY == 0:
                if on_progress:
                    on_progress(processed)
                if should_stop and should_stop():
                    stats.cancelled = True
                    break
            try:
                data = read_bytes(entry.attachment)
                zf.writestr(entry.path, data)
                stats.files_included += 1
            except Exception as exc:  # noqa: BLE001 - one unreadable file must not stop the archive
                stats.files_failed += 1
                logger.warning("bulk download: could not read %s: %s", entry.path, exc)
            processed += 1
        # a partially written entry is impossible here: writestr is all-or-nothing per entry
    if not stats.cancelled and on_progress:
        on_progress(processed)
    try:
        stats.bytes = out_fileobj.tell()
    except Exception:  # noqa: BLE001
        stats.bytes = 0
    return stats
