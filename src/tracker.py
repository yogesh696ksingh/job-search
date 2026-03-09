"""
src/tracker.py
==============
Tracks jobs that have already been processed so we don't re-suggest them.

Storage: a simple JSON file at ``data/seen_jobs.json``.
Each entry maps job-id → {first_seen, score, title, company, url}.

Also provides a CSV tracker for detailed status tracking (applied, heard back,
interviews, etc.).
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
SEEN_JOBS_PATH = REPO_ROOT / "data" / "seen_jobs.json"
TRACKER_CSV_PATH = REPO_ROOT / "data" / "job_tracker.csv"

_CSV_HEADERS = [
    "id",
    "title",
    "company",
    "location",
    "source",
    "score",
    "url",
    "first_seen",
    "status",          # new / applied / interview / offer / rejected / declined
    "applied_date",
    "notes",
]


# ──────────────────────────────────────────────────────────────────────────────
# Seen-jobs deduplication
# ──────────────────────────────────────────────────────────────────────────────

def load_seen_jobs(path: Path = SEEN_JOBS_PATH) -> dict[str, Any]:
    """Return the seen-jobs mapping from disk (empty dict if file not found)."""
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read seen_jobs file: %s", exc)
    return {}


def save_seen_jobs(seen: dict[str, Any], path: Path = SEEN_JOBS_PATH) -> None:
    """Persist the seen-jobs mapping to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, ensure_ascii=False)


def filter_new_jobs(
    jobs: list[dict[str, Any]],
    seen: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return only jobs whose id is not in *seen*."""
    return [j for j in jobs if j["id"] not in seen]


def mark_seen(jobs: list[dict[str, Any]], seen: dict[str, Any]) -> dict[str, Any]:
    """
    Add all jobs in *jobs* to *seen* and return the updated mapping.
    Does NOT persist to disk (call :func:`save_seen_jobs` explicitly).
    """
    now = datetime.now(tz=timezone.utc).isoformat()
    for job in jobs:
        seen[job["id"]] = {
            "first_seen": now,
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "score": job.get("score"),
            "url": job.get("url", ""),
        }
    return seen


# ──────────────────────────────────────────────────────────────────────────────
# CSV application tracker
# ──────────────────────────────────────────────────────────────────────────────

def _load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _save_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def upsert_tracker(
    jobs: list[dict[str, Any]],
    path: Path = TRACKER_CSV_PATH,
) -> None:
    """
    Add new jobs to the CSV tracker (status = "new").
    Existing rows are left untouched.
    """
    existing_ids = {row["id"] for row in _load_csv(path)}
    new_rows: list[dict[str, str]] = []
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    for job in jobs:
        if job["id"] not in existing_ids:
            new_rows.append(
                {
                    "id": job.get("id", ""),
                    "title": job.get("title", ""),
                    "company": job.get("company", ""),
                    "location": job.get("location", ""),
                    "source": job.get("source", ""),
                    "score": str(job.get("score", "")),
                    "url": job.get("url", ""),
                    "first_seen": now,
                    "status": "new",
                    "applied_date": "",
                    "notes": "",
                }
            )

    if new_rows:
        all_rows = _load_csv(path) + new_rows
        _save_csv(all_rows, path)
        logger.info("Tracker: added %d new job(s) to %s", len(new_rows), path)
    else:
        logger.info("Tracker: no new jobs to add")


def get_tracker_summary(path: Path = TRACKER_CSV_PATH) -> dict[str, Any]:
    """
    Return a summary dict describing the current state of the tracker.

    Example::

        {
            "total": 42,
            "by_status": {"new": 10, "applied": 20, "interview": 5, ...},
            "follow_up_needed": ["Acme Corp — Backend Engineer (applied 2024-01-01)", ...],
        }
    """
    rows = _load_csv(path)
    by_status: dict[str, int] = {}
    follow_up: list[str] = []

    from datetime import date, timedelta

    today = date.today()
    cutoff = today - timedelta(days=7)

    for row in rows:
        status = row.get("status", "new")
        by_status[status] = by_status.get(status, 0) + 1

        applied_str = row.get("applied_date", "")
        if status == "applied" and applied_str:
            try:
                applied_date = date.fromisoformat(applied_str)
                if applied_date <= cutoff:
                    follow_up.append(
                        f"{row.get('company', '')} — {row.get('title', '')} "
                        f"(applied {applied_str})"
                    )
            except ValueError:
                pass

    return {
        "total": len(rows),
        "by_status": by_status,
        "follow_up_needed": follow_up,
    }
