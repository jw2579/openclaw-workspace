---
name: linkedin_apify_jobs
description: Fetch fresh LinkedIn jobs via the free guest API first, automatically fall back to Apify if LinkedIn anti-bot blocks the scrape, filter low-quality companies, dedupe, rank, optionally insert into Notion, summarize, and support recurring runs.
metadata:
  openclaw:
    requires:
      anyBins: [python3]
    optionalEnv: [NOTION_TOKEN, NOTION_DB_ID, APIFY_TOKEN]
---

# linkedin_apify_jobs

Use this skill to fetch fresh LinkedIn job listings via LinkedIn's free guest API (no API key, no cost), automatically fall back to the prior Apify actor when LinkedIn anti-bot blocks the guest scrape and `APIFY_TOKEN` is available, filter low-quality companies, dedupe by LinkedIn job ID, rank against Jiaxuan's resume/preferences, optionally insert accepted jobs into a Notion database, and generate a report for recurring Discord announcements.

When Notion is enabled, keep the schema lean and decision-focused: company, position, URL, status, star-based score, match reason, location, work mode, posted time, and JD content. Do not recreate a `note` column.

The script now supports automatic multi-region time-of-day behavior in `America/New_York` with runs at `01:00, 05:00, 09:00, 13:00, 17:00, 21:00` EDT.

Per-region fetch profiles:
- `new_york`
  - weekday peak hours: `09, 13, 17` → fetch `55`
  - weekday offpeak hours: `01, 05, 21` → fetch `32`
  - weekend hours: all run slots → fetch `28`
- `california`
  - weekday peak hours: `13, 17, 21` → fetch `80`
  - weekday offpeak hours: `01, 05, 09` → fetch `40`
  - weekend hours: all run slots → fetch `32`
- `us`
  - weekday peak hours: `09, 13, 17, 21` → fetch `105`
  - weekday offpeak hours: `01, 05` → fetch `58`
  - weekend hours: all run slots → fetch `48`

Recommendation caps are derived proportionally from the average per-region fetch count and capped lower than the old 10/5 behavior. Offpeak/weekend jobs must still clear the low-signal threshold and match target software-role titles.

Override for testing with `LINKEDIN_FORCE_MODE=peak|offpeak|weekend`.

## Required reads

Before running the workflow, read these when present:
- `USER.md`
- `memory/resume-jiaxuan.md`
- `MEMORY.md` only in direct/main-session contexts where long-term memory is allowed

## Rules

- Never use nationality, ethnicity, race, or origin based filtering.
- Use only neutral business-rule filtering plus the editable denylist in `data/company_denylist.json`.
- Neutral business-rule signals (staffing/vendor/recruiting) are checked ONLY against company metadata (name, website, description, industries), NOT against job description text.
- Citizenship/PR eligibility filter rejects jobs requiring US citizenship, permanent residency, or security clearance — this is a legal eligibility check, not identity-based.
- Prefer the local helper script `scripts/linkedin_apify_jobs.py` for fetch/filter/dedupe/report work.
- Keep persistent state minimal and private.
- Do not persist secrets into workspace files, reports, logs, or memory.

## Typical run

```bash
python3 scripts/linkedin_apify_jobs.py
```

With Notion integration:
```bash
NOTION_TOKEN=... NOTION_DB_ID=... python3 scripts/linkedin_apify_jobs.py
```

Optional environment overrides:
- `LINKEDIN_REPORT_PATH`
- `LINKEDIN_SEEN_STATE_PATH`
- `LINKEDIN_DENYLIST_PATH`
- `LINKEDIN_COUNT` (override the configured per-region fetch count for smoke tests)
- `LINKEDIN_FORCE_MODE` (`peak|offpeak|weekend` for testing)
- `NOTION_TOKEN` (optional — if provided with NOTION_DB_ID, accepted jobs are inserted into Notion)
- `NOTION_DB_ID` (optional — Notion database ID for job tracking)
- `APIFY_TOKEN` (optional — enables automatic fallback to the legacy Apify actor if LinkedIn guest requests hit anti-bot/captcha)

## Data source

Primary source: LinkedIn's free guest/public API endpoints (no authentication required):
- Search: `https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?...`
- Detail: `https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/<job_id>`

Fallback source when LinkedIn anti-bot blocks detail fetches and `APIFY_TOKEN` is available:
- Apify actor: `curious_coder/linkedin-jobs-scraper`
- Input style: LinkedIn public search URLs + `scrapeCompany=true`

## Search configuration

- Queries: software developer, software engineer, backend engineer, full stack engineer, ai engineer, mobile developer
- Regions searched each run:
  - New York City Metropolitan Area (`geoId=90000070`, `distance=25`)
  - California
  - United States
- Freshness: last 4 hours
- Fetch counts: determined per region by the active EDT run slot (`peak`, `offpeak`, `weekend`)
- Top picks: derived proportionally from the average per-region fetch count, with stricter gating for offpeak/weekend roles
