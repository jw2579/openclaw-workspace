# Job Search Preferences

## Target roles
- Software Developer
- Software Engineer
- Backend Engineer
- Full Stack Engineer
- AI Engineer
- Mobile Developer

## Search area
- geoId=90000070 (New York City Metropolitan Area)
- distance=25

## Freshness window
- Last 4 hours

## Data source
- LinkedIn free guest API (no API key, no cost) is the primary source
- Apify fallback remains available if LinkedIn guest requests hit anti-bot/captcha and APIFY_TOKEN is available
- Guest API became the primary source on 2026-03-14

## Hard filters
- Skip stealth/confidential/undisclosed companies
- Skip staffing, recruiting, outsourcing, IT services, implementation partner, vendor, resource augmentation, C2C, H1B transfer style companies (checked against company metadata only, NOT job description text)
- Citizenship/PR eligibility filter: reject jobs requiring US citizenship, permanent residency, or security clearance
- Do not use nationality or ethnicity based filtering

## Seniority policy
- Recommend mid-level, junior, and unlabeled seniority roles
- Small ranking penalty (-0.5) for staff/principal titles (not filtered out)

## Output style
- Only show new jobs not seen before
- Rank against my resume and preferred stack
- Report top 5-10 new matches first depending on result quality
- Rate them with stars and give reasons

## Notion integration
- Enabled (optional) via NOTION_TOKEN and NOTION_DB_ID env vars
- Accepted jobs inserted into Notion database with company, position, URL, status, star-based score, match reason, posted time, and full JD as child content
- If Notion is not configured, pipeline runs as report-only
