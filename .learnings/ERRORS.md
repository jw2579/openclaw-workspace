# Errors

Log command failures, exceptions, API issues, and other unexpected problems here.

## [ERR-20260314-001] gog_gmail_send_scope

**Logged**: 2026-03-14T07:58:00Z
**Priority**: high
**Status**: resolved
**Area**: config

### Summary
`gog gmail send` dry-run worked, but real Gmail send with PDF attachment failed due insufficient OAuth scopes.

### Error
```
Google API error (403 insufficientPermissions): Request had insufficient authentication scopes.
```

### Context
- Operation attempted: send LinkedIn jobs report PDF to Jiaxuan's own Gmail account from the cron/report workflow
- Tool: `gog gmail send`
- Account present in `gog auth list`: `jiaxuan.wu18@gmail.com` (default, gmail)
- Dry-run succeeded, but real send failed
- PDF generation itself succeeded; only the Gmail API send step failed

### Suggested Fix
Re-authenticate or re-add the `gog` Gmail account with scopes that include real Gmail send permission, then re-test `gog gmail send` without `--dry-run`.

### Metadata
- Reproducible: yes
- Related Files: scripts/linkedin_apify_jobs.py, skills/gog/SKILL.md

### Resolution
- **Resolved**: 2026-03-14T08:23:00Z
- **Notes**: Re-authorized `gog` with Gmail send scope; real outbound Gmail send test succeeded.

---

## [ERR-20260314-002] gog_drive_api_disabled

**Logged**: 2026-03-14T08:30:00Z
**Priority**: medium
**Status**: pending
**Area**: config

### Summary
`gog drive upload` failed because the Google Drive API is not enabled for the gog OAuth client project, so Drive-link delivery cannot be relied on right now.

### Error
```
Google API error (403 accessNotConfigured): Google Drive API has not been used in project 854957542426 before or it is disabled.
```

### Context
- Operation attempted: upload generated LinkedIn jobs PDF to Google Drive and return a shareable link
- Tool: `gog drive upload` / `gog drive mkdir`
- Gmail auth already included `drive` scope, so the blocker is the upstream Google project configuration, not local token scope

### Suggested Fix
Enable Google Drive API for the gog OAuth client project, or keep using Gmail attachment delivery + Gmail thread URL as the stable fallback.

### Metadata
- Reproducible: yes
- Related Files: scripts/linkedin_apify_jobs.py, skills/gog/SKILL.md

---
## [ERR-20260314-003] linkedin_multi_region_apify_fallback_abort

**Logged**: 2026-03-14T16:36:00Z
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
In the new multi-region LinkedIn pipeline, a single region's failed Apify fallback request could abort the entire run instead of degrading gracefully to guest API results for that region.

### Error
```
urllib.error.HTTPError: HTTP Error 400: Bad Request
```

### Context
- Operation attempted: smoke-test the new multi-region fetch plan with `LINKEDIN_COUNT=3 LINKEDIN_FORCE_MODE=offpeak`
- Region that triggered it: `us`
- Guest scraping for that region hit LinkedIn anti-bot on detail fetches
- The pipeline then attempted Apify fallback for that one region and received HTTP 400
- Before the fix, that exception bubbled up and killed the whole run

### Suggested Fix
Wrap region-level Apify fallback in a `try/except`, log the fallback failure, and continue the run on the guest results already gathered for that region.

### Metadata
- Reproducible: yes
- Related Files: scripts/linkedin_apify_jobs.py

### Resolution
- **Resolved**: 2026-03-14T16:38:00Z
- **Notes**: Region-level fallback failures now degrade gracefully; smoke test completed successfully with guest results preserved.

---
## [ERR-20260314-004] sessions_send_cross_thread_visibility

**Logged**: 2026-03-14T18:58:00Z
**Priority**: medium
**Status**: resolved
**Area**: config

### Summary
`sessions_send` cannot be used to push messages into an unrelated Discord thread session when session visibility is restricted to the current session tree.

### Error
```
Session send visibility is restricted to the current session tree (tools.sessions.visibility=tree).
```

### Context
- Operation attempted: send a one-off test message (`hello msg`) from the old `#job-alert` thread into the new `Job Tracker` thread session
- Tool: `sessions_send`
- Target session: `agent:main:discord:channel:1482225712093069415`

### Suggested Fix
For cross-thread delivery outside the current session tree, use cron `announce` delivery to the target thread instead of `sessions_send`.

### Metadata
- Reproducible: yes
- Related Files: TOOLS.md

### Resolution
- **Resolved**: 2026-03-14T18:58:00Z
- **Notes**: Switched to a one-shot cron announce for the test message.

---

## [ERR-20260315-001] linkedin_zoneinfo_report_generation

**Logged**: 2026-03-15T18:44:00Z
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
The LinkedIn pipeline could finish fetch/filter/Notion work but then crash while writing the report on Python runtimes without `zoneinfo`.

### Error
```
ModuleNotFoundError: No module named 'zoneinfo'
```

### Context
- Operation attempted: scheduled LinkedIn jobs cron run
- Script: `scripts/linkedin_apify_jobs.py`
- Failure point: `now_edt()` during report generation
- Impact: report + latest alias were not written, so downstream Discord summary delivery could not continue

### Suggested Fix
Wrap `zoneinfo` usage in a compatibility fallback that formats America/New_York time via `time`/`TZ` when `zoneinfo` is unavailable.

### Metadata
- Reproducible: yes
- Related Files: scripts/linkedin_apify_jobs.py

### Resolution
- **Resolved**: 2026-03-15T18:45:00Z
- **Notes**: Added a fallback in `now_edt()` to use `current_new_york_time()` + `time.strftime(...)` when importing `zoneinfo` fails.

---
