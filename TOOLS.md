# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

### Integrations

- Notion credentials are stored locally at `.secrets/notion.env` for internal reuse across threads.
- Do not echo secret values back into chat unless explicitly asked.

### LinkedIn Job Report Routing

- Old Discord thread session: `agent:main:discord:channel:1481436478125903983` (`#job-alert`)
- Active delivery target: `agent:main:discord:channel:1482225712093069415` (`Job Tracker`)
- Current cron pattern: deliver only to `Job Tracker` via cron `announce`; do not mirror back to the old thread.

Add whatever helps you do your job. This is your cheat sheet.
