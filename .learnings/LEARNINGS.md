# Learnings

Corrections, insights, and knowledge gaps captured during development.

**Categories**: correction | insight | knowledge_gap | best_practice
**Areas**: frontend | backend | infra | tests | docs | config
**Statuses**: pending | in_progress | resolved | wont_fix | promoted | promoted_to_skill

## Status Definitions

| Status | Meaning |
|--------|---------|
| `pending` | Not yet addressed |
| `in_progress` | Actively being worked on |
| `resolved` | Issue fixed or knowledge integrated |
| `wont_fix` | Decided not to address (reason in Resolution) |
| `promoted` | Elevated to CLAUDE.md, AGENTS.md, or copilot-instructions.md |
| `promoted_to_skill` | Extracted as a reusable skill |

## Skill Extraction Fields

When a learning is promoted to a skill, add these fields:

```markdown
**Status**: promoted_to_skill
**Skill-Path**: skills/skill-name
```

Example:
```markdown
## [LRN-20250115-001] best_practice

**Logged**: 2025-01-15T10:00:00Z
**Priority**: high
**Status**: promoted_to_skill
**Skill-Path**: skills/docker-m1-fixes
**Area**: infra

### Summary
Docker build fails on Apple Silicon due to platform mismatch
...
```

---

## [LRN-20260314-001] best_practice

**Logged**: 2026-03-14T14:58:00Z
**Priority**: low
**Status**: resolved
**Area**: config

### Summary
After `git rm` removes a tracked file from the working tree, use `git add -A` (or commit directly) instead of trying to `git add` the deleted path by name again.

### Details
A commit preparation command failed because `reports/linkedin_jobs_latest.md` had already been removed and staged by `git rm`, so `git add reports/linkedin_jobs_latest.md` no longer matched a filesystem path.

### Suggested Action
When staging mixed modifications + deletions, prefer `git add -A` to avoid pathspec mistakes.

### Metadata
- Source: error
- Related Files: .gitignore, reports/linkedin_jobs_latest.md
- Tags: git, staging, deletion

### Resolution
- **Resolved**: 2026-03-14T14:58:00Z
- **Notes**: Switched to `git add -A` flow for the next commit.

---

