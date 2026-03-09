"""
tests/test_scraper.py
=====================
Unit tests for the job scraping module.

All network calls are mocked so these tests run offline.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.scraper import (
    _slugify,
    fetch_all_jobs,
    fetch_hn_who_is_hiring,
    fetch_remoteok,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_response(payload, status_code: int = 200):
    mock = MagicMock()
    mock.status_code = status_code
    if isinstance(payload, (dict, list)):
        mock.json.return_value = payload
        mock.text = json.dumps(payload)
    else:
        mock.json.side_effect = ValueError("not json")
        mock.text = str(payload)
    mock.raise_for_status = MagicMock()
    return mock


# ── _slugify ──────────────────────────────────────────────────────────────────

def test_slugify_basic():
    assert _slugify("Hello World!") == "hello-world"


def test_slugify_special_chars():
    assert _slugify("  Python & Go  ") == "python-go"


# ── fetch_remoteok ────────────────────────────────────────────────────────────

REMOTEOK_SAMPLE = [
    {"legal": "Terms apply"},  # disclaimer row — should be skipped
    {
        "id": "12345",
        "position": "Backend Engineer",
        "company": "TestCo",
        "description": "<p>We love Python and Kubernetes.</p>",
        "tags": ["python", "kubernetes"],
        "url": "https://remoteok.com/l/12345",
        "date": "2024-01-01T00:00:00Z",
    },
    {
        "id": "67890",
        "position": "PHP Developer",
        "company": "OldCo",
        "description": "PHP Laravel expert needed.",
        "tags": ["php"],
        "url": "https://remoteok.com/l/67890",
        "date": "2024-01-02T00:00:00Z",
    },
]


@patch("src.scraper._get")
def test_fetch_remoteok_returns_jobs(mock_get):
    mock_get.return_value = _mock_response(REMOTEOK_SAMPLE)
    jobs = fetch_remoteok()
    assert len(jobs) == 2  # disclaimer row excluded


@patch("src.scraper._get")
def test_fetch_remoteok_keyword_filter(mock_get):
    mock_get.return_value = _mock_response(REMOTEOK_SAMPLE)
    jobs = fetch_remoteok(query_keywords=["Python"])
    # Only the Backend Engineer matches
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Backend Engineer"


@patch("src.scraper._get")
def test_fetch_remoteok_job_shape(mock_get):
    mock_get.return_value = _mock_response(REMOTEOK_SAMPLE)
    jobs = fetch_remoteok()
    job = jobs[0]
    required_keys = {"id", "title", "company", "location", "url", "description", "source", "posted_at"}
    assert required_keys.issubset(job.keys())


@patch("src.scraper._get")
def test_fetch_remoteok_html_stripped(mock_get):
    mock_get.return_value = _mock_response(REMOTEOK_SAMPLE)
    jobs = fetch_remoteok()
    # HTML tags should be stripped from description
    assert "<p>" not in jobs[0]["description"]


# ── fetch_hn_who_is_hiring ────────────────────────────────────────────────────

HN_SEARCH_RESPONSE = {
    "hits": [
        {
            "objectID": "99999",
            "author": "whoishiring",
            "title": "Ask HN: Who is Hiring? (January 2024)",
        }
    ]
}

HN_THREAD = {
    "id": 99999,
    "kids": [111, 222, 333],
}

HN_COMMENT_111 = {
    "id": 111,
    "text": "Acme Inc | Backend Engineer | Remote | Python, Go",
    "time": 1704067200,
}

HN_COMMENT_222 = {
    "id": 222,
    "text": "Beta Corp | PHP Developer | Remote",
    "time": 1704067300,
}

HN_COMMENT_333 = {
    "id": 333,
    "text": "",  # empty comment — should be skipped
    "time": 1704067400,
}


@patch("src.scraper._get")
def test_fetch_hn_who_is_hiring_basic(mock_get):
    from urllib.parse import urlparse

    def side_effect(url, **kwargs):
        host = urlparse(url).hostname or ""
        path = urlparse(url).path
        if host == "hn.algolia.com":
            return _mock_response(HN_SEARCH_RESPONSE)
        if "99999" in path:
            return _mock_response(HN_THREAD)
        if path.endswith("/111.json"):
            return _mock_response(HN_COMMENT_111)
        if path.endswith("/222.json"):
            return _mock_response(HN_COMMENT_222)
        if path.endswith("/333.json"):
            return _mock_response(HN_COMMENT_333)
        return _mock_response({})

    mock_get.side_effect = side_effect
    jobs = fetch_hn_who_is_hiring()
    # comment 333 is empty, so only 2 jobs expected
    assert len(jobs) == 2


@patch("src.scraper._get")
def test_fetch_hn_keyword_filter(mock_get):
    from urllib.parse import urlparse

    def side_effect(url, **kwargs):
        host = urlparse(url).hostname or ""
        path = urlparse(url).path
        if host == "hn.algolia.com":
            return _mock_response(HN_SEARCH_RESPONSE)
        if "99999" in path:
            return _mock_response(HN_THREAD)
        if path.endswith("/111.json"):
            return _mock_response(HN_COMMENT_111)
        if path.endswith("/222.json"):
            return _mock_response(HN_COMMENT_222)
        if path.endswith("/333.json"):
            return _mock_response(HN_COMMENT_333)
        return _mock_response({})

    mock_get.side_effect = side_effect
    # Only Python-related jobs
    jobs = fetch_hn_who_is_hiring(query_keywords=["Python"])
    assert len(jobs) == 1
    assert "Acme" in jobs[0]["company"]


# ── fetch_all_jobs (integration/unit) ─────────────────────────────────────────

PREFERENCES_REMOTEOK_ONLY = {
    "target_roles": ["Backend Engineer"],
    "sources": {
        "remoteok": True,
        "hacker_news_who_is_hiring": False,
        "indeed": False,
    },
}


@patch("src.scraper._get")
def test_fetch_all_jobs_deduplication(mock_get):
    """Duplicate job IDs should be removed."""
    # Return the same payload twice (RemoteOK called once, HN disabled)
    mock_get.return_value = _mock_response(REMOTEOK_SAMPLE)
    jobs = fetch_all_jobs(PREFERENCES_REMOTEOK_ONLY)
    ids = [j["id"] for j in jobs]
    assert len(ids) == len(set(ids)), "Duplicate job IDs found"
