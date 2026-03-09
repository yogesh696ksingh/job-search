"""
tests/test_ranker.py
====================
Unit tests for the GPT-based job ranking module.

OpenAI API calls are mocked so these tests run offline.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.ranker import (
    _build_user_prompt,
    _pre_filter,
    _summarise_preferences,
    classify_role,
    rank_jobs,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

PREFERENCES = {
    "target_roles": ["Software Engineer", "Backend Engineer"],
    "seniority": ["mid", "senior"],
    "preferred_stack": ["Python", "Go", "Kubernetes"],
    "bonus_keywords": ["distributed systems"],
    "disqualifying_keywords": ["PHP", "unpaid"],
    "locations": {
        "remote_ok": True,
        "preferred": ["San Francisco, CA", "Remote"],
    },
    "visa": {"requires_sponsorship": False},
}

GOOD_JOB = {
    "id": "test-1",
    "title": "Senior Backend Engineer",
    "company": "TechCo",
    "location": "Remote",
    "url": "https://example.com/job/1",
    "description": "We use Python, Go, and Kubernetes. Distributed systems experience required.",
    "source": "RemoteOK",
    "posted_at": "2024-01-01",
}

BAD_JOB = {
    "id": "test-2",
    "title": "PHP Developer",
    "company": "OldCo",
    "location": "Remote",
    "url": "https://example.com/job/2",
    "description": "Expert PHP and Laravel developer needed.",
    "source": "RemoteOK",
    "posted_at": "2024-01-01",
}


# ── _pre_filter ───────────────────────────────────────────────────────────────

def test_pre_filter_passes_clean_job():
    assert _pre_filter(GOOD_JOB, PREFERENCES) is True


def test_pre_filter_blocks_disqualified_job():
    assert _pre_filter(BAD_JOB, PREFERENCES) is False


def test_pre_filter_case_insensitive():
    job = {**GOOD_JOB, "description": "UNPAID internship position"}
    assert _pre_filter(job, PREFERENCES) is False


# ── _summarise_preferences ────────────────────────────────────────────────────

def test_summarise_preferences_contains_key_info():
    summary = _summarise_preferences(PREFERENCES)
    assert "Python" in summary
    assert "Remote" in summary
    assert "Backend Engineer" in summary


# ── _build_user_prompt ────────────────────────────────────────────────────────

def test_build_user_prompt_includes_jd():
    prompt = _build_user_prompt(GOOD_JOB, PREFERENCES)
    assert "Senior Backend Engineer" in prompt
    assert "TechCo" in prompt
    assert "Python" in prompt


# ── rank_jobs ─────────────────────────────────────────────────────────────────

def _make_openai_response(score: float, reasoning: str):
    """Build a minimal mock that mimics openai.ChatCompletion response."""
    content = json.dumps({"score": score, "reasoning": reasoning})
    choice = MagicMock()
    choice.message.content = content
    mock_resp = MagicMock()
    mock_resp.choices = [choice]
    return mock_resp


@patch("src.ranker._get_client")
def test_rank_jobs_filters_by_score(mock_get_client):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    # Return 8.5 for GOOD_JOB
    mock_client.chat.completions.create.return_value = _make_openai_response(
        8.5, "Excellent Python/Go match."
    )
    ranked = rank_jobs([GOOD_JOB], PREFERENCES, min_score=6.0)
    assert len(ranked) == 1
    assert ranked[0]["score"] == 8.5


@patch("src.ranker._get_client")
def test_rank_jobs_excludes_low_score(mock_get_client):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.chat.completions.create.return_value = _make_openai_response(
        3.0, "Poor match."
    )
    ranked = rank_jobs([GOOD_JOB], PREFERENCES, min_score=6.0)
    assert ranked == []


@patch("src.ranker._get_client")
def test_rank_jobs_sorted_descending(mock_get_client):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    scores = [7.0, 9.5, 6.5]
    call_count = [0]

    def side_effect(**kwargs):
        s = scores[call_count[0] % len(scores)]
        call_count[0] += 1
        return _make_openai_response(s, "reason")

    mock_client.chat.completions.create.side_effect = side_effect

    jobs = [
        {**GOOD_JOB, "id": "j1", "title": "Job 1"},
        {**GOOD_JOB, "id": "j2", "title": "Job 2"},
        {**GOOD_JOB, "id": "j3", "title": "Job 3"},
    ]
    ranked = rank_jobs(jobs, PREFERENCES, min_score=6.0)
    scored_values = [j["score"] for j in ranked]
    assert scored_values == sorted(scored_values, reverse=True)


@patch("src.ranker._get_client")
def test_rank_jobs_pre_filter_skips_api_call(mock_get_client):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    # BAD_JOB has "PHP" — should be pre-filtered, no API call
    ranked = rank_jobs([BAD_JOB], PREFERENCES, min_score=6.0)
    assert ranked == []
    mock_client.chat.completions.create.assert_not_called()


# ── classify_role ─────────────────────────────────────────────────────────────

@patch("src.ranker._get_client")
def test_classify_role_returns_string(mock_get_client):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    choice = MagicMock()
    choice.message.content = "Backend / Senior"
    mock_client.chat.completions.create.return_value = MagicMock(choices=[choice])

    result = classify_role(GOOD_JOB)
    assert isinstance(result, str)
    assert len(result) > 0


@patch("src.ranker._get_client")
def test_classify_role_fallback_on_error(mock_get_client):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.chat.completions.create.side_effect = Exception("API error")

    result = classify_role(GOOD_JOB)
    assert result == "Unknown"
