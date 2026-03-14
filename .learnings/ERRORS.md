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
