"""
src/scraper.py
==============
Fetches job postings from multiple sources.

Supported sources
-----------------
* RemoteOK     — public JSON API (no auth required)
* Hacker News  — "Who is Hiring?" monthly thread via Algolia API
* Indeed       — HTML scraping (opt-in; must respect robots.txt)

Each function returns a list of dicts with at minimum:

    {
        "id":          str,   # unique job identifier
        "title":       str,
        "company":     str,
        "location":    str,
        "url":         str,
        "description": str,
        "source":      str,
        "posted_at":   str,   # ISO-8601 or empty string
    }
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────

_ua = UserAgent()


def _get(url: str, *, params: dict | None = None, timeout: int = 15) -> requests.Response:
    """Thin GET wrapper with a random User-Agent and polite retry."""
    headers = {"User-Agent": _ua.random}
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            logger.warning("GET %s attempt %d failed: %s", url, attempt + 1, exc)
            if attempt < 2:
                time.sleep(min(2 ** attempt, 4))
    raise RuntimeError(f"Failed to fetch {url} after 3 attempts")


def _slugify(text: str) -> str:
    """Return a lowercase slug safe for use as an identifier."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ──────────────────────────────────────────────────────────────────────────────
# RemoteOK
# ──────────────────────────────────────────────────────────────────────────────

REMOTEOK_API = "https://remoteok.com/api"


def fetch_remoteok(query_keywords: list[str] | None = None) -> list[dict[str, Any]]:
    """
    Fetch jobs from the RemoteOK public API.

    The API returns all current remote jobs; we optionally filter by keyword.
    Docs: https://remoteok.com/api (no auth required).
    """
    logger.info("Fetching jobs from RemoteOK …")
    resp = _get(REMOTEOK_API)
    raw: list[dict] = resp.json()

    # First element is a legal disclaimer dict, not a job
    jobs_raw = [item for item in raw if isinstance(item, dict) and item.get("id")]

    jobs: list[dict[str, Any]] = []
    for item in jobs_raw:
        title = item.get("position", "")
        company = item.get("company", "")
        description = item.get("description", "") or ""
        tags = " ".join(item.get("tags", []))
        full_text = f"{title} {company} {description} {tags}".lower()

        # Keyword filter (if provided)
        if query_keywords:
            if not any(kw.lower() in full_text for kw in query_keywords):
                continue

        jobs.append(
            {
                "id": f"remoteok-{item.get('id', _slugify(title))}",
                "title": title,
                "company": company,
                "location": "Remote",
                "url": item.get("url", f"https://remoteok.com/l/{item.get('id', '')}"),
                "description": BeautifulSoup(description, "lxml").get_text(" ", strip=True),
                "source": "RemoteOK",
                "posted_at": item.get("date", ""),
            }
        )

    logger.info("RemoteOK: %d jobs after filtering", len(jobs))
    return jobs


# ──────────────────────────────────────────────────────────────────────────────
# Hacker News "Who is Hiring?" via Algolia API
# ──────────────────────────────────────────────────────────────────────────────

HN_ALGOLIA_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"


def _find_latest_who_is_hiring_thread() -> int | None:
    """Return the HN item-id of the most recent 'Ask HN: Who is Hiring?' post."""
    params = {
        "query": "Ask HN: Who is Hiring?",
        "tags": "story,ask_hn",
        "hitsPerPage": 5,
    }
    resp = _get(HN_ALGOLIA_SEARCH, params=params)
    hits = resp.json().get("hits", [])
    # Filter to posts that are exactly the monthly hiring thread (author: whoishiring)
    for hit in hits:
        if hit.get("author") == "whoishiring" and "who is hiring" in hit.get("title", "").lower():
            return int(hit["objectID"])
    # Fallback: return first hit
    if hits:
        return int(hits[0]["objectID"])
    return None


def fetch_hn_who_is_hiring(query_keywords: list[str] | None = None, max_comments: int = 200) -> list[dict[str, Any]]:
    """
    Fetch job postings from the latest HN 'Who is Hiring?' thread.

    Each top-level comment is treated as one job posting.
    """
    logger.info("Fetching jobs from HN 'Who is Hiring?' …")
    thread_id = _find_latest_who_is_hiring_thread()
    if not thread_id:
        logger.warning("Could not find HN 'Who is Hiring?' thread")
        return []

    thread = _get(HN_ITEM_URL.format(thread_id)).json()
    comment_ids: list[int] = (thread.get("kids") or [])[:max_comments]

    jobs: list[dict[str, Any]] = []
    for cid in comment_ids:
        try:
            comment = _get(HN_ITEM_URL.format(cid)).json()
        except RuntimeError:
            continue

        raw_text = comment.get("text", "") or ""
        text = BeautifulSoup(raw_text, "lxml").get_text(" ", strip=True)

        if not text:
            continue

        # Keyword filter
        if query_keywords:
            if not any(kw.lower() in text.lower() for kw in query_keywords):
                continue

        # Try to extract a rough title/company from the first line
        first_line = text.split("|")[0].strip() if "|" in text else text[:80]
        company_guess = first_line.split(" - ")[0].strip() if " - " in first_line else first_line[:40]

        ts = comment.get("time")
        posted_at = (
            datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            if ts
            else ""
        )

        jobs.append(
            {
                "id": f"hn-{cid}",
                "title": first_line[:120],
                "company": company_guess,
                "location": "See description",
                "url": f"https://news.ycombinator.com/item?id={cid}",
                "description": text,
                "source": "HN Who is Hiring",
                "posted_at": posted_at,
            }
        )
        # Be polite with the Firebase API
        time.sleep(0.05)

    logger.info("HN Who is Hiring: %d jobs after filtering", len(jobs))
    return jobs


# ──────────────────────────────────────────────────────────────────────────────
# Indeed — opt-in HTML scraping
# ──────────────────────────────────────────────────────────────────────────────

INDEED_SEARCH_URL = "https://www.indeed.com/jobs"


def fetch_indeed(
    query: str,
    location: str = "Remote",
    max_results: int = 20,
) -> list[dict[str, Any]]:
    """
    Scrape job listings from Indeed.

    ⚠️  Indeed's robots.txt restricts scraping; use this only for personal,
        non-commercial purposes and respect rate limits.  Consider using
        the Indeed Publisher API if you have access.
    """
    logger.info("Fetching jobs from Indeed (query=%r, location=%r) …", query, location)
    params = {
        "q": query,
        "l": location,
        "sort": "date",
        "limit": min(max_results, 50),
    }

    try:
        resp = _get(INDEED_SEARCH_URL, params=params)
    except RuntimeError as exc:
        logger.error("Indeed fetch failed: %s", exc)
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    cards = soup.select("div.job_seen_beacon") or soup.select("div.jobsearch-SerpJobCard")

    jobs: list[dict[str, Any]] = []
    for card in cards[:max_results]:
        title_el = card.select_one("h2.jobTitle span") or card.select_one("a.jobtitle")
        company_el = card.select_one("span.companyName") or card.select_one("span.company")
        location_el = card.select_one("div.companyLocation") or card.select_one("div.location")
        link_el = card.select_one("a[id^='job_']") or card.select_one("a.jobtitle")
        desc_el = card.select_one("div.job-snippet") or card.select_one("div.summary")

        title = title_el.get_text(strip=True) if title_el else ""
        company = company_el.get_text(strip=True) if company_el else ""
        loc = location_el.get_text(strip=True) if location_el else location
        href = link_el.get("href", "") if link_el else ""
        url = f"https://www.indeed.com{href}" if href.startswith("/") else href
        description = desc_el.get_text(" ", strip=True) if desc_el else ""

        if not title:
            continue

        job_id = _slugify(f"{company}-{title}")
        jobs.append(
            {
                "id": f"indeed-{job_id}",
                "title": title,
                "company": company,
                "location": loc,
                "url": url,
                "description": description,
                "source": "Indeed",
                "posted_at": "",
            }
        )

    logger.info("Indeed: %d jobs found", len(jobs))
    return jobs


# ──────────────────────────────────────────────────────────────────────────────
# Unified entry point
# ──────────────────────────────────────────────────────────────────────────────

def fetch_all_jobs(preferences: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Fetch jobs from all enabled sources and return a deduplicated list.

    Parameters
    ----------
    preferences:
        Loaded ``data/preferences.yaml`` dict.
    """
    sources_cfg: dict = preferences.get("sources", {})
    target_roles: list[str] = preferences.get("target_roles", [])
    all_jobs: list[dict[str, Any]] = []

    if sources_cfg.get("remoteok", True):
        all_jobs.extend(fetch_remoteok(query_keywords=target_roles))

    if sources_cfg.get("hacker_news_who_is_hiring", True):
        all_jobs.extend(fetch_hn_who_is_hiring(query_keywords=target_roles))

    if sources_cfg.get("indeed", False):
        for role in target_roles[:2]:  # limit to top 2 roles
            all_jobs.extend(fetch_indeed(query=role, max_results=20))

    # Deduplicate by id
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for job in all_jobs:
        if job["id"] not in seen:
            seen.add(job["id"])
            unique.append(job)

    logger.info("Total unique jobs fetched: %d", len(unique))
    return unique
