"""
tests/test_tracker.py
=====================
Unit tests for the job tracker / deduplication module.
"""

from __future__ import annotations

import csv
import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.tracker import (
    filter_new_jobs,
    get_tracker_summary,
    load_seen_jobs,
    mark_seen,
    save_seen_jobs,
    upsert_tracker,
    _CSV_HEADERS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

JOB_A = {
    "id": "remoteok-1",
    "title": "Backend Engineer",
    "company": "Acme",
    "location": "Remote",
    "source": "RemoteOK",
    "score": 8.5,
    "url": "https://example.com/1",
    "posted_at": "2024-01-01",
}

JOB_B = {
    "id": "remoteok-2",
    "title": "ML Engineer",
    "company": "Beta",
    "location": "Remote",
    "source": "RemoteOK",
    "score": 7.0,
    "url": "https://example.com/2",
    "posted_at": "2024-01-01",
}


# ── load / save seen jobs ─────────────────────────────────────────────────────

def test_load_seen_jobs_returns_empty_for_missing_file(tmp_path):
    result = load_seen_jobs(tmp_path / "nonexistent.json")
    assert result == {}


def test_save_and_load_seen_jobs(tmp_path):
    path = tmp_path / "seen.json"
    data = {"job-1": {"title": "Dev"}}
    save_seen_jobs(data, path)
    loaded = load_seen_jobs(path)
    assert loaded == data


def test_load_seen_jobs_handles_corrupt_file(tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text("{bad json", encoding="utf-8")
    result = load_seen_jobs(path)
    assert result == {}


# ── filter_new_jobs ────────────────────────────────────────────────────────────

def test_filter_new_jobs_returns_unseen():
    seen = {"remoteok-1": {"title": "Backend Engineer"}}
    result = filter_new_jobs([JOB_A, JOB_B], seen)
    assert len(result) == 1
    assert result[0]["id"] == "remoteok-2"


def test_filter_new_jobs_returns_all_when_empty_seen():
    result = filter_new_jobs([JOB_A, JOB_B], {})
    assert len(result) == 2


def test_filter_new_jobs_returns_empty_when_all_seen():
    seen = {"remoteok-1": {}, "remoteok-2": {}}
    result = filter_new_jobs([JOB_A, JOB_B], seen)
    assert result == []


# ── mark_seen ─────────────────────────────────────────────────────────────────

def test_mark_seen_adds_jobs():
    seen: dict = {}
    updated = mark_seen([JOB_A, JOB_B], seen)
    assert "remoteok-1" in updated
    assert "remoteok-2" in updated


def test_mark_seen_preserves_existing():
    seen = {"existing-1": {"title": "Old Job"}}
    updated = mark_seen([JOB_A], seen)
    assert "existing-1" in updated
    assert "remoteok-1" in updated


def test_mark_seen_includes_metadata():
    seen: dict = {}
    updated = mark_seen([JOB_A], seen)
    entry = updated["remoteok-1"]
    assert entry["title"] == "Backend Engineer"
    assert entry["company"] == "Acme"
    assert "first_seen" in entry


# ── upsert_tracker ─────────────────────────────────────────────────────────────

def test_upsert_tracker_creates_file(tmp_path):
    path = tmp_path / "tracker.csv"
    upsert_tracker([JOB_A], path=path)
    assert path.exists()
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["id"] == "remoteok-1"
    assert rows[0]["status"] == "new"


def test_upsert_tracker_does_not_duplicate(tmp_path):
    path = tmp_path / "tracker.csv"
    upsert_tracker([JOB_A], path=path)
    upsert_tracker([JOB_A], path=path)  # second call with same job
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1


def test_upsert_tracker_adds_new_jobs(tmp_path):
    path = tmp_path / "tracker.csv"
    upsert_tracker([JOB_A], path=path)
    upsert_tracker([JOB_B], path=path)
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2


# ── get_tracker_summary ────────────────────────────────────────────────────────

def _write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def test_get_tracker_summary_basic(tmp_path):
    path = tmp_path / "tracker.csv"
    _write_csv(
        path,
        [
            {**{k: "" for k in _CSV_HEADERS}, "id": "1", "status": "new"},
            {**{k: "" for k in _CSV_HEADERS}, "id": "2", "status": "applied", "applied_date": ""},
            {**{k: "" for k in _CSV_HEADERS}, "id": "3", "status": "applied", "applied_date": ""},
        ],
    )
    summary = get_tracker_summary(path)
    assert summary["total"] == 3
    assert summary["by_status"]["new"] == 1
    assert summary["by_status"]["applied"] == 2


def test_get_tracker_summary_follow_up(tmp_path):
    path = tmp_path / "tracker.csv"
    old_date = (date.today() - timedelta(days=10)).isoformat()
    _write_csv(
        path,
        [
            {
                **{k: "" for k in _CSV_HEADERS},
                "id": "1",
                "status": "applied",
                "applied_date": old_date,
                "company": "Acme",
                "title": "Backend Engineer",
            }
        ],
    )
    summary = get_tracker_summary(path)
    assert len(summary["follow_up_needed"]) == 1


def test_get_tracker_summary_no_followup_for_recent(tmp_path):
    path = tmp_path / "tracker.csv"
    recent_date = date.today().isoformat()
    _write_csv(
        path,
        [
            {
                **{k: "" for k in _CSV_HEADERS},
                "id": "1",
                "status": "applied",
                "applied_date": recent_date,
                "company": "Acme",
                "title": "Engineer",
            }
        ],
    )
    summary = get_tracker_summary(path)
    assert summary["follow_up_needed"] == []


def test_get_tracker_summary_empty_file(tmp_path):
    path = tmp_path / "tracker.csv"
    summary = get_tracker_summary(path)
    assert summary["total"] == 0
    assert summary["by_status"] == {}
    assert summary["follow_up_needed"] == []
