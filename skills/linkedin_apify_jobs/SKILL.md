---
name: linkedin_apify_jobs
description: Fetch fresh LinkedIn jobs from Apify, filter low-quality companies, dedupe, rank, summarize, and support recurring runs.
metadata:
  openclaw:
    requires:
      env: [APIFY_TOKEN]
      primaryEnv: APIFY_TOKEN
      anyBins: [python3]
---

# linkedin_apify_jobs

Use this skill to fetch fresh LinkedIn job listings via Apify actor `curious_coder/linkedin-jobs-scraper`, filter low-quality companies, dedupe by LinkedIn job ID, rank against Jiaxuan's resume/preferences, and generate a report for recurring announcements.

## Required reads

Before running the workflow, read these when present:
- `USER.md`
- `MEMORY.md`
- `memory/resume-jiaxuan.md`

## Rules

- Never use nationality, ethnicity, race, or origin based filtering.
- Use only neutral business-rule filtering plus the editable denylist in `data/company_denylist.json`.
- Prefer the local helper script `scripts/linkedin_apify_jobs.py` for fetch/filter/dedupe/report work.
- Keep persistent state minimal and private.
- Do not persist secrets into workspace files, reports, logs, or memory.

## Typical run

```bash
APIFY_TOKEN=... python3 scripts/linkedin_apify_jobs.py
```

Optional environment overrides:
- `LINKEDIN_PUBLIC_SEARCH_URL`
- `LINKEDIN_REPORT_PATH`
- `LINKEDIN_SEEN_STATE_PATH`
- `LINKEDIN_DENYLIST_PATH`
