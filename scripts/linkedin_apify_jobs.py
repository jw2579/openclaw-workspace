#!/usr/bin/env python3
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib import parse, request, error

WORKSPACE = Path(__file__).resolve().parent.parent
DATA_DIR = WORKSPACE / "data"
REPORTS_DIR = WORKSPACE / "reports"
LOGS_DIR = WORKSPACE / "logs"
SEEN_PATH = Path(os.environ.get("LINKEDIN_SEEN_STATE_PATH", DATA_DIR / "seen_linkedin_jobs.json"))
DENYLIST_PATH = Path(os.environ.get("LINKEDIN_DENYLIST_PATH", DATA_DIR / "company_denylist.json"))
REPORT_PATH = Path(os.environ.get("LINKEDIN_REPORT_PATH", REPORTS_DIR / "linkedin_jobs_latest.md"))
ACTOR_ID = os.environ.get("APIFY_ACTOR_ID", "curious_coder/linkedin-jobs-scraper")
DEFAULT_PUBLIC_URL = "https://www.linkedin.com/jobs/search/?keywords=software%20developer&geoId=90000070&distance=25&f_TPR=r14400&sortBy=DD"
PUBLIC_URL = os.environ.get("LINKEDIN_PUBLIC_SEARCH_URL", DEFAULT_PUBLIC_URL)
USER_PATH = WORKSPACE / "USER.md"
MEMORY_PATH = WORKSPACE / "MEMORY.md"
RESUME_PATH = WORKSPACE / "memory" / "resume-jiaxuan.md"
MAX_RESULTS = int(os.environ.get("LINKEDIN_COUNT", "50"))

NEUTRAL_BAD_SIGNALS = [
    "staffing", "recruiting", "recruitment", "talent solutions", "outsourcing",
    "it services", "consulting services", "consultancy services", "implementation partner",
    "vendor", "resource augmentation", "c2c", "corp-to-corp", "h1b transfer",
    "bench sales", "contract staffing", "offshore development", "managed services",
    "system integrator", "placement services", "employment agency", "headhunter"
]
GOOD_SIGNALS = [
    "python", "django", "fastapi", "react", "flutter", "full stack", "backend",
    "api", "llm", "ai", "product", "mobile", "health", "sql", "data"
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def read_text_if_exists(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def build_public_url(raw_url: str) -> str:
    if raw_url:
        parsed = parse.urlparse(raw_url)
        q = dict(parse.parse_qsl(parsed.query, keep_blank_values=True))
    else:
        parsed = parse.urlparse(DEFAULT_PUBLIC_URL)
        q = {}
    q.setdefault("keywords", "software developer")
    q.setdefault("geoId", "90000070")
    q.setdefault("distance", "25")
    q.setdefault("f_TPR", "r14400")
    q.setdefault("sortBy", "DD")
    clean = parsed._replace(
        scheme=parsed.scheme or "https",
        netloc=parsed.netloc or "www.linkedin.com",
        path=parsed.path or "/jobs/search/",
        query=parse.urlencode(q)
    )
    return parse.urlunparse(clean)


def apify_request(token: str, url: str, payload: Dict[str, Any], method: str = "POST") -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method=method,
    )
    with request.urlopen(req, timeout=120) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}


def apify_get(token: str, url: str) -> Dict[str, Any]:
    req = request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    with request.urlopen(req, timeout=120) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}


def fetch_jobs(token: str, actor_input: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    encoded_actor = parse.quote(ACTOR_ID, safe="")
    sync_url = f"https://api.apify.com/v2/acts/{encoded_actor}/run-sync-get-dataset-items?format=json&clean=true"
    try:
        items = apify_request(token, sync_url, actor_input)
        if isinstance(items, list):
            return items, {"mode": "sync"}
        if isinstance(items, dict) and isinstance(items.get("data"), list):
            return items["data"], {"mode": "sync_wrapped"}
    except error.HTTPError as e:
        sync_error = e.read().decode("utf-8", errors="replace")
    except Exception as e:
        sync_error = str(e)
    run_url = f"https://api.apify.com/v2/acts/{encoded_actor}/runs"
    run = apify_request(token, run_url, actor_input)
    run_data = run.get("data", run)
    run_id = run_data.get("id")
    if not run_id:
        raise RuntimeError(f"Apify async run failed to start after sync failure: {sync_error}")
    poll_url = f"https://api.apify.com/v2/actor-runs/{run_id}"
    final = None
    for _ in range(60):
        final = apify_get(token, poll_url).get("data", {})
        status = final.get("status")
        if status in {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}:
            break
        time.sleep(5)
    status = (final or {}).get("status")
    if status != "SUCCEEDED":
        raise RuntimeError(f"Apify async run did not succeed. status={status} run_id={run_id}")
    dataset_id = final.get("defaultDatasetId")
    if not dataset_id:
        raise RuntimeError(f"Apify run succeeded but no defaultDatasetId was returned. run_id={run_id}")
    items_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?format=json&clean=true"
    items = apify_get(token, items_url)
    if not isinstance(items, list):
        raise RuntimeError(f"Unexpected dataset items payload type: {type(items).__name__}")
    return items, {"mode": "async", "run_id": run_id, "dataset_id": dataset_id}


def extract_text(job: Dict[str, Any]) -> str:
    fields = [
        job.get("title"), job.get("companyName"), job.get("companyDescription"),
        job.get("descriptionText"), job.get("description"), job.get("location"),
        job.get("employmentType"), job.get("workplaceType"), job.get("industries"),
        job.get("companyWebsite"), job.get("jobUrl"), job.get("jobPostingUrl")
    ]
    return "\n".join(str(x) for x in fields if x)


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def get_job_id(job: Dict[str, Any]) -> str:
    for key in ["id", "jobId", "linkedinJobId"]:
        value = job.get(key)
        if value:
            return str(value)
    for key in ["jobUrl", "jobPostingUrl", "applyUrl"]:
        value = job.get(key)
        if value:
            m = re.search(r"/(\d{6,})", str(value))
            if m:
                return m.group(1)
    return ""


def company_filter_reason(job: Dict[str, Any], denylist: Dict[str, Any]) -> str:
    company = job.get("companyName") or ""
    website = job.get("companyWebsite") or ""
    text = normalize(extract_text(job))
    company_n = normalize(company)
    website_n = normalize(website)
    for name in denylist.get("exact_names", []):
        if company_n == normalize(name):
            return f"company denylist exact match: {name}"
    for part in denylist.get("name_contains", []):
        if normalize(part) in company_n:
            return f"company denylist partial match: {part}"
    for part in denylist.get("website_contains", []):
        if normalize(part) in website_n:
            return f"company website denylist match: {part}"
    for part in ["stealth", "confidential", "undisclosed"]:
        if part in company_n or part in text:
            return f"low-transparency signal: {part}"
    for signal in NEUTRAL_BAD_SIGNALS:
        if signal in company_n or signal in website_n or signal in text:
            return f"neutral business-rule staffing/vendor signal: {signal}"
    return ""


def rank_job(job: Dict[str, Any], resume_text: str) -> Tuple[float, List[str]]:
    reasons = []
    score = 0.0
    title = normalize(job.get("title", ""))
    text = normalize(extract_text(job))
    if any(t in title for t in ["software engineer", "software developer", "backend", "full stack", "full-stack"]):
        score += 3.0
        reasons.append("title aligns with target software roles")
    if any(t in title for t in ["senior", "staff", "principal"]) and "software" not in title and "backend" not in title:
        score -= 0.3
    for signal in GOOD_SIGNALS:
        if signal in text:
            score += 0.45
    if "full time" in text or "full-time" in text:
        score += 0.6
        reasons.append("full-time role")
    if "new york" in text or "remote" in text or "hybrid" in text:
        score += 0.4
    if job.get("postedAt") or job.get("postedDate") or job.get("timeAgo"):
        score += 0.5
        reasons.append("recent listing metadata present")
    desc_len = len(job.get("descriptionText") or job.get("description") or "")
    if desc_len > 400:
        score += 0.5
        reasons.append("job description has enough detail to evaluate")
    if resume_text:
        resume_signals = ["python", "django", "fastapi", "react", "flutter", "sql", "llm", "mobile", "health"]
        hits = [s for s in resume_signals if s in text and s in resume_text]
        score += min(len(hits) * 0.6, 3.0)
        if hits:
            reasons.append("resume overlap: " + ", ".join(hits[:5]))
    return score, reasons[:4]


def stars(score: float) -> str:
    if score >= 7.5:
        return "★★★★★"
    if score >= 6.0:
        return "★★★★☆"
    if score >= 4.5:
        return "★★★☆☆"
    if score >= 3.0:
        return "★★☆☆☆"
    return "★☆☆☆☆"


def choose_top_n(recommended_count: int) -> int:
    return 10 if recommended_count >= 10 else min(5, recommended_count)


def main() -> int:
    ensure_dirs()
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print("ERROR: APIFY_TOKEN is missing. Set it in-memory for this run, e.g. APIFY_TOKEN=... python3 scripts/linkedin_apify_jobs.py", file=sys.stderr)
        return 2
    public_url = build_public_url(PUBLIC_URL)
    actor_input = {
        "urls": [public_url],
        "scrapeCompany": True,
        "count": MAX_RESULTS,
        "splitByLocation": False,
    }
    denylist = load_json(DENYLIST_PATH, {"exact_names": [], "name_contains": [], "website_contains": [], "notes": []})
    seen = load_json(SEEN_PATH, {"jobs": {}})
    seen_jobs = seen.setdefault("jobs", {})
    resume_text = normalize(read_text_if_exists(RESUME_PATH))

    jobs, fetch_meta = fetch_jobs(token, actor_input)
    fetched = len(jobs)
    already_seen = 0
    filtered_out: List[Tuple[Dict[str, Any], str]] = []
    candidates: List[Tuple[Dict[str, Any], float, List[str]]] = []

    for job in jobs:
        job_id = get_job_id(job)
        if not job_id:
            filtered_out.append((job, "missing linkedin job id"))
            continue
        if job_id in seen_jobs:
            already_seen += 1
            continue
        reason = company_filter_reason(job, denylist)
        if reason:
            filtered_out.append((job, reason))
            continue
        score, reasons = rank_job(job, resume_text)
        candidates.append((job, score, reasons))

    candidates.sort(key=lambda x: x[1], reverse=True)
    top_n = choose_top_n(len(candidates))
    recommended = candidates[:top_n]

    stamp = now_iso()
    for job, score, reasons in candidates:
        job_id = get_job_id(job)
        seen_jobs[job_id] = {
            "first_seen_at": stamp,
            "title": job.get("title"),
            "companyName": job.get("companyName"),
        }
    save_json(SEEN_PATH, seen)

    lines: List[str] = []
    lines.append("# LinkedIn Jobs Report\n")
    lines.append(f"- Generated at: {stamp}")
    lines.append(f"- Public search URL: {public_url}")
    lines.append(f"- Actor: {ACTOR_ID}")
    lines.append(f"- Fetch mode: {fetch_meta.get('mode')}")
    lines.append(f"- Jobs fetched: {fetched}")
    lines.append(f"- Already seen skipped: {already_seen}")
    lines.append(f"- Filtered out: {len(filtered_out)}")
    lines.append(f"- New candidates kept: {len(candidates)}\n")

    lines.append("## 1. New recommended jobs\n")
    if recommended:
        for idx, (job, score, reasons) in enumerate(recommended, start=1):
            job_id = get_job_id(job)
            url = job.get("jobUrl") or job.get("jobPostingUrl") or job.get("applyUrl") or ""
            company = job.get("companyName") or "Unknown company"
            title = job.get("title") or "Untitled role"
            location = job.get("location") or "Unknown location"
            lines.append(f"### {idx}. {title} — {company}")
            lines.append(f"- Rating: {stars(score)} ({score:.2f})")
            lines.append(f"- Location: {location}")
            lines.append(f"- Employment type: {job.get('employmentType') or 'Unknown'}")
            lines.append(f"- Workplace type: {job.get('workplaceType') or 'Unknown'}")
            lines.append(f"- LinkedIn job ID: {job_id}")
            if url:
                lines.append(f"- Link: {url}")
            if reasons:
                lines.append(f"- Why it ranked: {'; '.join(reasons)}")
            summary = (job.get("descriptionText") or job.get("description") or "").strip().replace("\n", " ")
            if summary:
                lines.append(f"- Summary: {summary[:500]}{'...' if len(summary) > 500 else ''}")
            lines.append("")
    else:
        lines.append("No new recommended jobs this run.\n")

    lines.append("## 2. Filtered-out jobs with reasons\n")
    if filtered_out:
        for job, reason in filtered_out[:50]:
            lines.append(f"- {(job.get('title') or 'Untitled role')} — {(job.get('companyName') or 'Unknown company')}: {reason}")
    else:
        lines.append("- None")
    lines.append("")

    lines.append("## 3. Count of already-seen jobs skipped\n")
    lines.append(f"- {already_seen}")
    lines.append("")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines).rstrip() + "\n")

    summary_titles = [f"{job.get('title')} @ {job.get('companyName')}" for job, _, _ in recommended[:5]]
    print(
        f"LinkedIn jobs run ok | fetched={fetched} | filtered={len(filtered_out)} | already_seen={already_seen} | remaining={len(candidates)} | selected={len(recommended)} | report={REPORT_PATH}"
    )
    if summary_titles:
        print("Top picks: " + " | ".join(summary_titles))
    else:
        print("Top picks: none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
