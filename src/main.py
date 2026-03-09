"""
src/main.py
===========
Orchestrator — ties all phases together and is the entry point for the
GitHub Actions daily run.

Usage
-----
    python -m src.main

Environment variables required
-------------------------------
    OPENAI_API_KEY   — OpenAI API key
    RESEND_API_KEY   — Resend API key (for email digest)

Optional overrides
------------------
    PREFERENCES_PATH — path to preferences YAML (default: data/preferences.yaml)
    MASTER_RESUME_PATH — path to master résumé YAML (default: data/master_resume.yaml)
    OUTPUT_DIR       — directory for PDF/text artefacts (default: output/)
    DRY_RUN          — if set to "1", skip email sending and PDF generation
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import yaml

# Ensure the repo root is on sys.path so ``python -m src.main`` works
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.scraper import fetch_all_jobs
from src.ranker import rank_jobs, classify_role
from src.tracker import (
    filter_new_jobs,
    load_seen_jobs,
    mark_seen,
    save_seen_jobs,
    upsert_tracker,
    get_tracker_summary,
)
from src.email_sender import send_digest
from src.resume_generator import generate_tailored_resume
from src.cover_letter import generate_cover_letter

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _load_preferences(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _console_summary(jobs: list[dict]) -> None:
    """Print a ranked table to stdout."""
    print("\n" + "=" * 70)
    print(f"  TOP {len(jobs)} JOB MATCHES")
    print("=" * 70)
    for i, job in enumerate(jobs, start=1):
        score = job.get("score", 0.0)
        category = job.get("category", "")
        print(
            f"\n{i:2}. [{score:.1f}/10]  {job['title']}\n"
            f"     Company  : {job['company']}\n"
            f"     Location : {job['location']}\n"
            f"     Category : {category}\n"
            f"     Source   : {job['source']}\n"
            f"     Reason   : {job.get('reasoning', '')}\n"
            f"     URL      : {job['url']}"
        )
    print("\n" + "=" * 70 + "\n")


# ──────────────────────────────────────────────────────────────────────────────
# Main pipeline
# ──────────────────────────────────────────────────────────────────────────────

def run() -> None:
    preferences_path = Path(
        os.environ.get("PREFERENCES_PATH", str(REPO_ROOT / "data" / "preferences.yaml"))
    )
    master_resume_path = Path(
        os.environ.get("MASTER_RESUME_PATH", str(REPO_ROOT / "data" / "master_resume.yaml"))
    )
    output_dir = Path(os.environ.get("OUTPUT_DIR", str(REPO_ROOT / "output")))
    dry_run = os.environ.get("DRY_RUN", "0") == "1"

    logger.info("Loading preferences from %s", preferences_path)
    preferences = _load_preferences(preferences_path)

    top_n: int = preferences.get("top_n", 5)
    min_score: float = float(preferences.get("min_score", 6.0))
    digest_email: str = preferences.get("digest_email", "")

    # ── Phase 1: Ingestion ────────────────────────────────────────────────────
    logger.info("Phase 1: Fetching jobs …")
    all_jobs = fetch_all_jobs(preferences)
    logger.info("Fetched %d total jobs", len(all_jobs))

    # ── Deduplication ─────────────────────────────────────────────────────────
    seen = load_seen_jobs()
    new_jobs = filter_new_jobs(all_jobs, seen)
    logger.info("%d new (unseen) jobs to process", len(new_jobs))

    if not new_jobs:
        logger.info("No new jobs found — nothing to do today.")
        return

    # ── Phase 1 (cont.): Ranking ──────────────────────────────────────────────
    logger.info("Phase 1: Ranking jobs with GPT …")
    ranked = rank_jobs(new_jobs, preferences, min_score=min_score)

    if not ranked:
        logger.info("No jobs met the minimum score threshold (%.1f). Done.", min_score)
        return

    # Classify roles for richer display
    for job in ranked:
        job["category"] = classify_role(job)

    top_jobs = ranked[:top_n]

    # Print to console (always)
    _console_summary(top_jobs)

    # ── Update tracker ────────────────────────────────────────────────────────
    upsert_tracker(top_jobs)
    # Only mark jobs that were actually ranked (scored above threshold) as seen,
    # so that low-scoring jobs can be re-evaluated if preferences change.
    seen = mark_seen(ranked, seen)
    save_seen_jobs(seen)

    # Print tracker summary
    summary = get_tracker_summary()
    logger.info(
        "Tracker: %d total | %s",
        summary["total"],
        " | ".join(f"{k}: {v}" for k, v in summary["by_status"].items()),
    )
    if summary["follow_up_needed"]:
        logger.info("Follow-up needed:\n  " + "\n  ".join(summary["follow_up_needed"]))

    if dry_run:
        logger.info("DRY_RUN=1 — skipping PDF generation and email.")
        return

    # ── Phase 3 & 4: Résumé + Cover Letter ───────────────────────────────────
    logger.info("Phase 3: Generating tailored résumés and cover letters …")
    for job in top_jobs:
        try:
            pdf_path, ats_keywords = generate_tailored_resume(
                job,
                master_resume_path=master_resume_path,
                output_dir=output_dir,
            )
            job["resume_pdf"] = str(pdf_path)
            job["ats_keywords"] = ats_keywords
        except Exception as exc:
            logger.error("Résumé generation failed for %s: %s", job.get("id"), exc)
            job["resume_pdf"] = ""
            job["ats_keywords"] = []

        try:
            txt_path, pdf_path_cl = generate_cover_letter(
                job,
                preferences=preferences,
                output_dir=output_dir,
            )
            job["cover_letter_txt"] = str(txt_path)
            job["cover_letter_pdf"] = str(pdf_path_cl) if pdf_path_cl else ""
        except Exception as exc:
            logger.error("Cover letter generation failed for %s: %s", job.get("id"), exc)
            job["cover_letter_txt"] = ""
            job["cover_letter_pdf"] = ""

    # ── Phase 2: Email digest ─────────────────────────────────────────────────
    if digest_email:
        logger.info("Phase 2: Sending email digest to %s …", digest_email)
        try:
            msg_id = send_digest(top_jobs, to_email=digest_email)
            logger.info("Digest email sent (ID: %s)", msg_id)
        except Exception as exc:
            logger.error("Failed to send digest email: %s", exc)
    else:
        logger.warning("No digest_email set in preferences — skipping email.")

    logger.info("✅ Daily run complete. %d top jobs processed.", len(top_jobs))


if __name__ == "__main__":
    run()
