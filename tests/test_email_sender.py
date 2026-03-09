"""
tests/test_email_sender.py
==========================
Unit tests for the email digest module.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.email_sender import _build_html, _build_plain_text, _score_class, send_digest


# ── Fixtures ──────────────────────────────────────────────────────────────────

JOBS = [
    {
        "id": "j1",
        "title": "Backend Engineer",
        "company": "Acme",
        "location": "Remote",
        "source": "RemoteOK",
        "score": 9.0,
        "reasoning": "Great Python/Go match.",
        "url": "https://example.com/j1",
        "ats_keywords": ["Kubernetes", "Kafka"],
    },
    {
        "id": "j2",
        "title": "ML Platform Engineer",
        "company": "Beta",
        "location": "San Francisco, CA",
        "source": "HN Who is Hiring",
        "score": 7.5,
        "reasoning": "Good ML infra alignment.",
        "url": "https://example.com/j2",
        "ats_keywords": [],
    },
]


# ── _score_class ──────────────────────────────────────────────────────────────

def test_score_class_high():
    assert _score_class(8.0) == "score-high"
    assert _score_class(10.0) == "score-high"


def test_score_class_mid():
    assert _score_class(6.0) == "score-mid"
    assert _score_class(7.9) == "score-mid"


def test_score_class_low():
    assert _score_class(5.9) == "score-low"
    assert _score_class(0.0) == "score-low"


# ── _build_html ───────────────────────────────────────────────────────────────

def test_build_html_contains_job_titles():
    html = _build_html(JOBS, "January 01, 2024")
    assert "Backend Engineer" in html
    assert "ML Platform Engineer" in html


def test_build_html_contains_company_names():
    html = _build_html(JOBS, "January 01, 2024")
    assert "Acme" in html
    assert "Beta" in html


def test_build_html_contains_scores():
    html = _build_html(JOBS, "January 01, 2024")
    assert "9.0/10" in html
    assert "7.5/10" in html


def test_build_html_contains_ats_keywords():
    html = _build_html(JOBS, "January 01, 2024")
    assert "Kubernetes" in html
    assert "Kafka" in html


def test_build_html_contains_apply_links():
    html = _build_html(JOBS, "January 01, 2024")
    assert "https://example.com/j1" in html
    assert "https://example.com/j2" in html


def test_build_html_is_valid_html_structure():
    html = _build_html(JOBS, "January 01, 2024")
    assert "<!DOCTYPE html>" in html
    assert "</html>" in html


# ── _build_plain_text ─────────────────────────────────────────────────────────

def test_build_plain_text_contains_job_info():
    text = _build_plain_text(JOBS, "January 01, 2024")
    assert "Backend Engineer" in text
    assert "Acme" in text
    assert "9.0/10" in text
    assert "https://example.com/j1" in text


def test_build_plain_text_has_header():
    text = _build_plain_text(JOBS, "January 01, 2024")
    assert "Daily Job Digest" in text
    assert "January 01, 2024" in text


# ── send_digest ───────────────────────────────────────────────────────────────

@patch.dict("os.environ", {"RESEND_API_KEY": "test-key"})
@patch("src.email_sender.resend.Emails.send")
def test_send_digest_calls_resend(mock_send):
    mock_send.return_value = {"id": "msg-123"}
    msg_id = send_digest(JOBS, to_email="user@example.com")
    assert msg_id == "msg-123"
    mock_send.assert_called_once()


@patch.dict("os.environ", {"RESEND_API_KEY": "test-key"})
@patch("src.email_sender.resend.Emails.send")
def test_send_digest_email_params(mock_send):
    mock_send.return_value = {"id": "msg-456"}
    send_digest(JOBS, to_email="user@example.com", from_email="sender@example.com")
    call_args = mock_send.call_args[0][0]
    assert call_args["to"] == ["user@example.com"]
    assert call_args["from"] == "sender@example.com"
    assert "html" in call_args
    assert "text" in call_args


def test_send_digest_raises_without_api_key():
    import os
    env_backup = os.environ.pop("RESEND_API_KEY", None)
    try:
        with pytest.raises(EnvironmentError, match="RESEND_API_KEY"):
            send_digest(JOBS, to_email="user@example.com")
    finally:
        if env_backup:
            os.environ["RESEND_API_KEY"] = env_backup
