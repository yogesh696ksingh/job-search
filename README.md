# 🔍 AI-Powered Job Search System

An end-to-end, AI-driven job search pipeline that runs on a daily GitHub
Actions schedule and delivers a personalised email digest of the best-matching
roles — complete with tailored résumé PDFs and cover letters.

---

## Features

| Phase | What it does |
|-------|-------------|
| **1 · Ingestion** | Scrapes RemoteOK (public API) and the HN "Who is Hiring?" thread. Optional: Indeed HTML scraping. |
| **2 · Ranking** | Each job is scored 0–10 by GPT-4o-mini against your tech stack, seniority, location, and visa preferences. Disqualifying keywords are filtered out before any API call. |
| **3 · Résumé** | GPT-4o selects and rewrites the most relevant bullets from your master résumé for each top job. RenderCV turns it into a PDF. ATS keyword gaps are surfaced. |
| **4 · Cover letter** | GPT-4o-mini generates a personalised cover letter from a Jinja2 template, optionally rendered to PDF via pandoc. |
| **5 · Email digest** | Resend delivers a rich HTML email with ranked jobs, scores, ATS tips, and apply links. |
| **6 · Tracking** | A `data/seen_jobs.json` deduplication file ensures jobs are never re-suggested. A `data/job_tracker.csv` tracks status (new → applied → interview → offer). |

---

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/yogesh696ksingh/job-search.git
cd job-search
pip install -r requirements.txt
```

### 2. Edit your preferences

Open **`data/preferences.yaml`** and fill in:

```yaml
candidate:
  name: "Your Name"
  email: "you@example.com"
  ...

target_roles:
  - "Backend Engineer"
  - "ML Infrastructure Engineer"

preferred_stack:
  - Python
  - Go
  - Kubernetes
  ...
```

### 3. Edit your master résumé

Open **`data/master_resume.yaml`** and replace the sample entries with your
real experience. Keep every bullet you have ever written — the AI will choose
the best ones per job.

### 4. Set secrets

For a local run, create a `.env` file (never commit it):

```bash
OPENAI_API_KEY=sk-...
RESEND_API_KEY=re_...
```

Then load it:

```bash
export $(cat .env | xargs)
```

### 5. Run

```bash
python -m src.main
```

To skip email sending and PDF generation (useful for testing):

```bash
DRY_RUN=1 python -m src.main
```

---

## GitHub Actions (daily automation)

The workflow at **`.github/workflows/daily.yml`** runs at **05:00 UTC** every
day.

### Required secrets

Add these in **Settings → Secrets and variables → Actions**:

| Secret | Description |
|--------|-------------|
| `OPENAI_API_KEY` | OpenAI API key |
| `RESEND_API_KEY` | [Resend](https://resend.com) API key |

The workflow:
1. Fetches jobs from all enabled sources
2. Ranks them with GPT
3. Generates tailored résumé + cover letter PDFs for the top N roles
4. Emails you the digest
5. Commits the updated `seen_jobs.json` and `job_tracker.csv` back to the repo

Generated PDFs are uploaded as a **workflow artefact** (retained for 7 days).

---

## Project Structure

```
job-search/
├── .github/
│   └── workflows/
│       └── daily.yml          # GitHub Actions schedule
├── data/
│   ├── preferences.yaml       # YOUR settings (edit this)
│   ├── master_resume.yaml     # YOUR full résumé (edit this)
│   ├── seen_jobs.json         # auto-maintained deduplication store
│   └── job_tracker.csv        # auto-maintained application tracker
├── src/
│   ├── main.py                # orchestrator entry point
│   ├── scraper.py             # job ingestion (RemoteOK, HN, Indeed)
│   ├── ranker.py              # GPT-based scoring and classification
│   ├── resume_generator.py    # tailored résumé PDF via RenderCV
│   ├── cover_letter.py        # personalised cover letter (txt + PDF)
│   ├── email_sender.py        # Resend HTML digest
│   └── tracker.py             # deduplication + CSV tracker
├── templates/
│   └── cover_letter_template.txt  # Jinja2 cover letter template
├── tests/
│   ├── test_scraper.py
│   ├── test_ranker.py
│   ├── test_tracker.py
│   └── test_email_sender.py
├── output/                    # generated PDFs (git-ignored)
├── requirements.txt
└── .gitignore
```

---

## Configuration Reference (`data/preferences.yaml`)

| Key | Description |
|-----|-------------|
| `candidate` | Your contact details (used in résumé + cover letter) |
| `target_roles` | Job titles to search for |
| `seniority` | `intern`, `junior`, `mid`, `senior`, `staff` |
| `preferred_stack` | Technologies you want (increases score) |
| `bonus_keywords` | Nice-to-have phrases |
| `disqualifying_keywords` | Auto-filter out matching jobs (no API call) |
| `locations.remote_ok` | Whether remote roles are acceptable |
| `locations.preferred` | Preferred cities |
| `visa.requires_sponsorship` | Signals H-1B/sponsorship need to the ranker |
| `sources.*` | Enable/disable each job source |
| `top_n` | How many top jobs to process per run |
| `min_score` | Minimum GPT score (0–10) to include in digest |
| `digest_email` | Recipient address for the daily email |

---

## Running Tests

```bash
pip install pytest
pytest tests/ -v
```

---

## Phased Roadmap

- [x] **Phase 1** — Ingestion (RemoteOK, HN) + GPT ranking → console output
- [x] **Phase 2** — Email digest via Resend
- [x] **Phase 3** — Tailored résumé via RenderCV
- [x] **Phase 4** — Cover letter generation
- [x] **Phase 5** — Deduplication + CSV tracker + GitHub Actions schedule
- [ ] **Phase 6** — Wellfound / LinkedIn integration
- [ ] **Phase 7** — Mock interview prep sheets
- [ ] **Phase 8** — Notion/Airtable tracker sync

---

## Important Notes

* **You stay in control.** The system drafts everything; you review and click
  "Apply" manually.
* **Respect ToS.** The Indeed scraper is opt-in and must only be used for
  personal, non-commercial purposes. Prefer official APIs where available.
* **No secrets in code.** All API keys are supplied via environment variables
  or GitHub Secrets.
