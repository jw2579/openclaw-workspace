#!/usr/bin/env python3
"""LinkedIn job pipeline — guest API primary, Apify fallback, optional Notion integration."""
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import time
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import parse, request, error

WORKSPACE = Path(__file__).resolve().parent.parent
DATA_DIR = WORKSPACE / "data"
REPORTS_DIR = WORKSPACE / "reports"
LOGS_DIR = WORKSPACE / "logs"
SEEN_PATH = Path(os.environ.get("LINKEDIN_SEEN_STATE_PATH", DATA_DIR / "seen_linkedin_jobs.json"))
DENYLIST_PATH = Path(os.environ.get("LINKEDIN_DENYLIST_PATH", DATA_DIR / "company_denylist.json"))
REPORT_PATH_ENV = (os.environ.get("LINKEDIN_REPORT_PATH") or "").strip()
REPORT_PDF_PATH_ENV = (os.environ.get("LINKEDIN_REPORT_PDF_PATH") or "").strip()
REPORT_PATH = Path(REPORT_PATH_ENV) if REPORT_PATH_ENV else (REPORTS_DIR / "linkedin_jobs_latest.md")
REPORT_PDF_PATH = Path(REPORT_PDF_PATH_ENV) if REPORT_PDF_PATH_ENV else (REPORTS_DIR / "linkedin_jobs_latest.pdf")
RESUME_PATH = WORKSPACE / "memory" / "resume-jiaxuan.md"
MAX_RESULTS_HIGH = 100
MAX_RESULTS_LOW = 50
LOW_PEAK_MIN_SCORE = float(os.environ.get("LINKEDIN_LOW_PEAK_MIN_SCORE", "5.5"))
ACTOR_ID = os.environ.get("APIFY_ACTOR_ID", "curious_coder/linkedin-jobs-scraper")

ROLE_QUERIES = [
    "software developer",
    "software engineer",
    "backend engineer",
    "full stack engineer",
    "ai engineer",
    "mobile developer",
]

SEARCH_LOCATION = "New York City Metropolitan Area"
SEARCH_GEO_ID = "90000070"
SEARCH_DISTANCE = "25"
SEARCH_FRESHNESS = "r14400"  # last 4 hours

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

NEUTRAL_BAD_SIGNALS = [
    "staffing", "recruiting", "recruitment", "talent solutions", "outsourcing",
    "it services", "consulting services", "consultancy services", "implementation partner",
    "vendor", "resource augmentation", "c2c", "corp-to-corp", "h1b transfer",
    "bench sales", "contract staffing", "offshore development", "managed services",
    "system integrator", "placement services", "employment agency", "headhunter",
]
GOOD_SIGNALS = [
    "python", "django", "fastapi", "react", "flutter", "full stack", "backend",
    "api", "llm", "ai", "product", "mobile", "sql", "data",
]

CITIZENSHIP_PR_PATTERNS = [
    r"u\.?s\.?\s*citizen",
    r"united\s+states\s+citizen",
    r"permanent\s+resident",
    r"green\s+card\s+required",
    r"green\s+card\s+holder",
    r"security\s+clearance\s+required",
    r"us\s+persons?\s+only",
    r"must\s+be\s+a\s+u\.?s\.?\s+person",
    r"must\s+be\s+authorized\s+to\s+work[^\n]{0,120}(citizen|u\.?s\.?\s+person|security\s+clearance)",
    r"(citizen|u\.?s\.?\s+person|security\s+clearance)[^\n]{0,120}authorized\s+to\s+work",
]
CITIZENSHIP_RE = re.compile("|".join(CITIZENSHIP_PR_PATTERNS), re.IGNORECASE)


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

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


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def http_error_body(exc: error.HTTPError) -> Tuple[str, str]:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:
        raw = ""
    snippet = re.sub(r"\s+", " ", raw).strip()[:300]
    return raw, snippet


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def current_new_york_time() -> time.struct_time:
    original_tz = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "America/New_York"
        if hasattr(time, "tzset"):
            time.tzset()
        return time.localtime()
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        if hasattr(time, "tzset"):
            time.tzset()


def get_run_mode() -> str:
    forced = (os.environ.get("LINKEDIN_FORCE_MODE") or "").strip().lower()
    if forced in {"high", "high_peak", "peak"}:
        return "high_peak"
    if forced in {"low", "low_peak", "offpeak", "off_peak"}:
        return "low_peak"
    ny = current_new_york_time()
    weekday = ny.tm_wday  # Monday=0
    hour = ny.tm_hour
    if weekday <= 4 and 9 <= hour < 21:
        return "high_peak"
    return "low_peak"


def get_max_results_for_mode(run_mode: str) -> int:
    override = os.environ.get("LINKEDIN_COUNT")
    if override:
        return int(override)
    return MAX_RESULTS_HIGH if run_mode == "high_peak" else MAX_RESULTS_LOW


def sanitize_pdf_text(text: str) -> str:
    replacements = {
        "⭐": "*",
        "✨": "+",
        "—": "-",
        "–": "-",
        "’": "'",
        "“": '"',
        "”": '"',
        "…": "...",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.encode("latin-1", "replace").decode("latin-1")


def wrap_pdf_lines(text: str, width: int = 96) -> List[str]:
    lines: List[str] = []
    for raw in sanitize_pdf_text(text).replace("\r\n", "\n").split("\n"):
        raw = raw.rstrip()
        if not raw:
            lines.append("")
            continue
        indent = "  " if raw.lstrip().startswith(("-", "*")) else ""
        wrapped = textwrap.wrap(
            raw,
            width=width,
            break_long_words=True,
            drop_whitespace=False,
            subsequent_indent=indent,
        )
        lines.extend(wrapped or [""])
    return lines


def pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_simple_pdf_from_markdown(markdown_text: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = wrap_pdf_lines(markdown_text)
    line_height = 12
    max_lines_per_page = 60
    pages = [lines[i:i + max_lines_per_page] for i in range(0, len(lines), max_lines_per_page)] or [[""]]

    objects: List[bytes] = []
    page_object_nums: List[int] = []
    content_object_nums: List[int] = []

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"")  # placeholder for /Pages
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")

    next_obj_num = 4
    for page_lines in pages:
        page_obj_num = next_obj_num
        content_obj_num = next_obj_num + 1
        page_object_nums.append(page_obj_num)
        content_object_nums.append(content_obj_num)
        next_obj_num += 2

        stream_lines = ["BT", "/F1 10 Tf", f"{line_height} TL", "1 0 0 1 40 760 Tm"]
        for idx, line in enumerate(page_lines):
            if idx > 0:
                stream_lines.append("T*")
            stream_lines.append(f"({pdf_escape(line)}) Tj")
        stream_lines.append("ET")
        stream = "\n".join(stream_lines).encode("latin-1", "replace")

        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_obj_num} 0 R >>"
            ).encode("latin-1")
        )
        objects.append(
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"
        )

    kids = " ".join(f"{n} 0 R" for n in page_object_nums)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_object_nums)} >>".encode("latin-1")

    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{i} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")

    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        pdf.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("ascii")
    )
    out_path.write_bytes(pdf)
    return out_path


def detect_gog_account() -> str:
    explicit = (os.environ.get("GOG_ACCOUNT") or os.environ.get("LINKEDIN_REPORT_GMAIL_TO") or "").strip()
    if explicit:
        return explicit
    try:
        result = subprocess.run(
            ["gog", "auth", "list", "--plain"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ""
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[1] == "default" and parts[2] == "gmail":
            return parts[0].strip()
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if parts:
            return parts[0].strip()
    return ""


def parse_tabbed_kv(text: str) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for line in text.splitlines():
        if "\t" not in line:
            continue
        key, value = line.split("\t", 1)
        data[key.strip()] = value.strip()
    return data


def gmail_thread_url(account: str, thread_id: str) -> str:
    if not thread_id:
        return ""
    try:
        result = subprocess.run(
            ["gog", "gmail", "url", thread_id, "--account", account, "--plain"],
            check=True,
            capture_output=True,
            text=True,
        )
        parts = result.stdout.strip().split("\t", 1)
        if len(parts) == 2:
            return parts[1].strip()
    except Exception:
        return ""
    return ""


def send_report_gmail(
    report_text: str,
    pdf_path: Path,
    stamp: str,
    fetch_meta: Dict[str, Any],
    fetched: int,
    filtered_count: int,
    already_seen: int,
    remaining: int,
    selected: int,
    recommended: List[Tuple[Dict[str, Any], float, List[str]]],
    recommended_star_map: Dict[str, str],
) -> Tuple[str, str, str, str]:
    account = detect_gog_account()
    if not account:
        return "not_configured", "No gog Gmail account is configured", "", ""

    subject = f"LinkedIn jobs report — {stamp.replace('T', ' ')[:16]} UTC"
    body_lines = [
        "Attached is your latest LinkedIn jobs report PDF.",
        "",
        f"Generated: {stamp}",
        f"Source: {fetch_meta.get('source_label', 'LinkedIn guest API')}",
        f"Fetched: {fetched}",
        f"Filtered: {filtered_count}",
        f"Already seen: {already_seen}",
        f"Remaining candidates: {remaining}",
        f"Selected: {selected}",
        "",
        "Top picks:",
    ]
    for idx, (job, _, _) in enumerate(recommended[:5], start=1):
        job_id = get_job_id(job)
        star = recommended_star_map.get(job_id, "")
        body_lines.append(f"{idx}. {star} {job.get('title')} — {job.get('companyName')}")

    try:
        result = subprocess.run(
            [
                "gog", "gmail", "send",
                "--account", account,
                "--to", account,
                "--subject", subject,
                "--body", "\n".join(body_lines),
                "--attach", str(pdf_path),
                "--no-input",
                "--plain",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        kv = parse_tabbed_kv(result.stdout)
        thread_id = kv.get("thread_id", "")
        thread_url = gmail_thread_url(account, thread_id)
        detail = account
        if thread_id:
            detail += f" | thread_id={thread_id}"
        return "sent", detail, thread_id, thread_url
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        return "failed", detail[:300], "", ""


# ---------------------------------------------------------------------------
# LinkedIn guest API — search + detail fetch
# ---------------------------------------------------------------------------

def linkedin_search_url(query: str, start: int = 0) -> str:
    params = {
        "keywords": query,
        "location": SEARCH_LOCATION,
        "geoId": SEARCH_GEO_ID,
        "distance": SEARCH_DISTANCE,
        "f_TPR": SEARCH_FRESHNESS,
        "sortBy": "DD",
        "start": str(start),
    }
    return "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?" + parse.urlencode(params)


def linkedin_public_search_url(query: str) -> str:
    params = {
        "keywords": query,
        "location": SEARCH_LOCATION,
        "geoId": SEARCH_GEO_ID,
        "distance": SEARCH_DISTANCE,
        "f_TPR": SEARCH_FRESHNESS,
        "sortBy": "DD",
    }
    return "https://www.linkedin.com/jobs/search/?" + parse.urlencode(params)


def apify_request(token: str, url: str, payload: Optional[Dict[str, Any]], method: str = "POST") -> Dict[str, Any]:
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


def http_get(url: str, timeout: int = 25, retries: int = 3) -> str:
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        req = request.Request(url, headers={"User-Agent": UA}, method="GET")
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            lowered = body.lower()
            if "captcha" in lowered or "security verification" in lowered or "verify you are human" in lowered:
                raise RuntimeError("LinkedIn request failed: captcha/anti-bot page returned")
            return body
        except error.HTTPError as exc:
            raw, snippet = http_error_body(exc)
            detail = f"HTTP {exc.code} {exc.reason}"
            lowered = raw.lower()
            if exc.code == 429:
                detail += " (rate-limited)"
            if "captcha" in lowered or "security verification" in lowered or "verify you are human" in lowered:
                detail += " (captcha/anti-bot page)"
            if snippet:
                detail += f" | {snippet}"
            last_error = RuntimeError(f"LinkedIn request failed: {detail}")
        except Exception as exc:
            last_error = exc

        if attempt < retries:
            message = str(last_error).lower() if last_error else ""
            if "captcha" in message or "rate-limited" in message or "timed out" in message:
                time.sleep(3.0 * attempt)
                continue
            break

    raise RuntimeError(str(last_error) if last_error else "LinkedIn request failed")


def parse_job_cards(html: str) -> List[Dict[str, str]]:
    jobs: List[Dict[str, str]] = []
    for m in re.finditer(r"<li>(.*?)</li>", html, re.S):
        s = m.group(1)
        idm = re.search(r"jobPosting:(\d+)", s)
        hrefm = re.search(r'href="([^"]*linkedin\.com/jobs/view/[^"]+)"', s)
        titlem = re.search(r"<h3[^>]*>\s*(.*?)\s*</h3>", s, re.S)
        compm = re.search(r'job-search-card-subtitle"[^>]*>\s*(.*?)\s*</a>', s, re.S)
        locm = re.search(r'job-search-card__location"[^>]*>\s*(.*?)\s*</span>', s, re.S)
        timem = re.search(r'<time[^>]*datetime="([^"]*)"[^>]*>\s*(.*?)\s*</time>', s, re.S)
        if not (idm and hrefm and titlem and compm):
            continue
        jobs.append({
            "id": idm.group(1),
            "jobUrl": unescape(hrefm.group(1)).replace("&amp;", "&"),
            "title": re.sub(r"<[^>]+>", "", unescape(titlem.group(1))).strip(),
            "companyName": re.sub(r"<[^>]+>", "", unescape(compm.group(1))).strip(),
            "location": re.sub(r"<[^>]+>", "", unescape(locm.group(1))).strip() if locm else "",
            "postedLabel": re.sub(r"<[^>]+>", "", unescape(timem.group(2))).strip() if timem else "",
            "postedAt": timem.group(1).strip() if timem else "",
        })
    return jobs


def extract_jd_text(job_html: str) -> str:
    m = re.search(r'<div class="show-more-less-html__markup[^>]*>([\s\S]*?)</div>', job_html)
    if not m:
        return ""
    txt = m.group(1)
    txt = re.sub(r"<script[\s\S]*?</script>", "", txt)
    txt = re.sub(r"<style[\s\S]*?</style>", "", txt)
    txt = txt.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    txt = re.sub(r"</p>", "\n\n", txt)
    txt = re.sub(r"</li>", "\n", txt)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = unescape(txt)
    lines = [ln.strip() for ln in txt.splitlines()]
    cleaned = []
    for ln in lines:
        lo = ln.lower()
        if not ln:
            cleaned.append("")
            continue
        if re.search(r"\b\d+[+,]?\s+applicants?\b", lo):
            continue
        if re.search(r"\b(posted|reposted|\d+\s+(day|days|hour|hours|week|weeks)\s+ago)\b", lo):
            continue
        if lo in {"about the job", "job description"}:
            continue
        cleaned.append(ln)
    txt = "\n".join(cleaned)
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    if len(txt) > 18000:
        txt = txt[:18000] + "\n\n[Truncated]"
    return txt


def extract_employment_type(job_html: str) -> str:
    m = re.search(r'employment-type"[^>]*>\s*(.*?)\s*</span>', job_html, re.S)
    if m:
        return re.sub(r"<[^>]+>", "", unescape(m.group(1))).strip()
    return ""


def fetch_jobs_guest_api(max_results: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Fetch jobs from LinkedIn guest API across all role queries."""
    all_cards: Dict[str, Dict[str, Any]] = {}  # dedupe by id
    errors: List[str] = []

    for query in ROLE_QUERIES:
        url = linkedin_search_url(query)
        try:
            html = http_get(url)
            cards = parse_job_cards(html)
            for card in cards:
                if card["id"] not in all_cards:
                    all_cards[card["id"]] = card
            time.sleep(1.0)  # polite delay between queries
        except Exception as e:
            errors.append(f"search '{query}': {e}")

        if len(all_cards) >= max_results:
            break

    detail_attempted = min(len(all_cards), max_results)

    # Fetch individual JD for each card
    jobs: List[Dict[str, Any]] = []
    for card in list(all_cards.values())[:max_results]:
        try:
            detail_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{card['id']}"
            detail_html = http_get(detail_url)
            jd_text = extract_jd_text(detail_html)
            emp_type = extract_employment_type(detail_html)
            job = {
                "id": card["id"],
                "title": card["title"],
                "companyName": card["companyName"],
                "location": card["location"],
                "jobUrl": card["jobUrl"],
                "descriptionText": jd_text,
                "employmentType": emp_type or "Unknown",
                "postedLabel": card.get("postedLabel") or "",
                "postedAt": card.get("postedAt") or "",
            }
            jobs.append(job)
            time.sleep(0.5)  # polite delay between detail fetches
        except Exception as e:
            errors.append(f"detail {card['id']}: {e}")

    anti_bot_errors = sum(1 for err in errors if "captcha/anti-bot" in err or "rate-limited" in err)
    meta = {
        "mode": "linkedin_guest_api",
        "source_label": "LinkedIn guest API (free, no API key)",
        "queries": len(ROLE_QUERIES),
        "raw_cards": len(all_cards),
        "detail_attempted": detail_attempted,
        "anti_bot_errors": anti_bot_errors,
        "errors": errors,
    }
    return jobs, meta


def fetch_jobs_apify(token: str, max_results: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    public_urls = [linkedin_public_search_url(query) for query in ROLE_QUERIES]
    actor_input = {
        "urls": public_urls,
        "scrapeCompany": True,
        "count": max_results,
        "splitByLocation": False,
    }
    encoded_actor = parse.quote(ACTOR_ID, safe="")
    sync_error = "unexpected sync payload"
    sync_url = f"https://api.apify.com/v2/acts/{encoded_actor}/run-sync-get-dataset-items?format=json&clean=true"
    try:
        items = apify_request(token, sync_url, actor_input)
        if isinstance(items, list):
            return items, {
                "mode": "apify_fallback",
                "source_label": "Apify fallback (triggered after LinkedIn anti-bot)",
                "actor": ACTOR_ID,
                "public_urls": len(public_urls),
                "errors": [],
                "fallback_used": True,
                "fetch_mode": "sync",
            }
        if isinstance(items, dict) and isinstance(items.get("data"), list):
            return items["data"], {
                "mode": "apify_fallback",
                "source_label": "Apify fallback (triggered after LinkedIn anti-bot)",
                "actor": ACTOR_ID,
                "public_urls": len(public_urls),
                "errors": [],
                "fallback_used": True,
                "fetch_mode": "sync_wrapped",
            }
    except error.HTTPError as exc:
        _, sync_error = http_error_body(exc)
    except Exception as exc:
        sync_error = str(exc)

    run_url = f"https://api.apify.com/v2/acts/{encoded_actor}/runs"
    run = apify_request(token, run_url, actor_input)
    run_data = run.get("data", run)
    run_id = run_data.get("id")
    if not run_id:
        raise RuntimeError(f"Apify fallback failed to start after sync failure: {sync_error}")

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
        raise RuntimeError(f"Apify fallback did not succeed. status={status} run_id={run_id}")

    dataset_id = final.get("defaultDatasetId")
    if not dataset_id:
        raise RuntimeError(f"Apify fallback succeeded but no defaultDatasetId was returned. run_id={run_id}")

    items_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?format=json&clean=true"
    items = apify_get(token, items_url)
    if not isinstance(items, list):
        raise RuntimeError(f"Unexpected Apify dataset payload type: {type(items).__name__}")

    meta = {
        "mode": "apify_fallback",
        "source_label": "Apify fallback (triggered after LinkedIn anti-bot)",
        "actor": ACTOR_ID,
        "public_urls": len(public_urls),
        "errors": [],
        "fallback_used": True,
        "fetch_mode": "async",
        "run_id": run_id,
        "dataset_id": dataset_id,
    }
    return items, meta


def should_use_apify_fallback(jobs: List[Dict[str, Any]], meta: Dict[str, Any]) -> bool:
    anti_bot_errors = int(meta.get("anti_bot_errors") or 0)
    detail_attempted = int(meta.get("detail_attempted") or 0)
    if anti_bot_errors <= 0:
        return False
    if anti_bot_errors >= 3:
        return True
    if detail_attempted and anti_bot_errors >= max(1, detail_attempted // 3):
        return True
    if detail_attempted and len(jobs) <= max(5, detail_attempted // 2):
        return True
    return False


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def extract_company_text(job: Dict[str, Any]) -> str:
    """Extract ONLY company metadata fields for neutral signal checking."""
    fields = [
        job.get("companyName"),
        job.get("companyWebsite"),
        job.get("companyDescription"),
        job.get("industries"),
    ]
    return normalize("\n".join(str(x) for x in fields if x))


def has_health_biotech_signal(job: Dict[str, Any]) -> bool:
    company_text = extract_company_text(job)
    jd_text = normalize(job.get("descriptionText") or job.get("description") or "")
    strong_phrases = [
        "healthcare", "health care", "health-tech", "health tech", "biotech", "biotechnology",
        "life sciences", "life-sciences", "pharma", "therapeutics", "drug discovery",
        "revenue cycle", "patient care", "health systems", "clinical workflow", "medical device",
    ]
    if any(sig in company_text for sig in strong_phrases):
        return True
    return any(sig in jd_text for sig in strong_phrases)


def extract_full_text(job: Dict[str, Any]) -> str:
    """Extract all text including JD for ranking and other checks."""
    fields = [
        job.get("title"), job.get("companyName"), job.get("companyDescription"),
        job.get("descriptionText"), job.get("description"), job.get("location"),
        job.get("employmentType"), job.get("companyWebsite"), get_job_url(job),
        job.get("postedLabel"), job.get("postedAt"), job.get("postedDate"), job.get("timeAgo"),
    ]
    return "\n".join(str(x) for x in fields if x)


def get_job_url(job: Dict[str, Any]) -> str:
    for key in ["jobUrl", "jobPostingUrl", "link"]:
        value = job.get(key)
        if value:
            return str(value).strip()
    return ""


def has_valid_job_url(job: Dict[str, Any]) -> bool:
    url = get_job_url(job)
    return bool(url and re.search(r"https?://([a-z0-9-]+\.)*linkedin\.com/jobs/view/", url, re.I))


def get_posted_display(job: Dict[str, Any]) -> str:
    for key in ["postedLabel", "timeAgo", "postedAt", "postedDate"]:
        value = (job.get(key) or "").strip() if isinstance(job.get(key), str) else job.get(key)
        if value:
            return str(value).strip()
    return ""


def citizenship_pr_filter(job: Dict[str, Any]) -> bool:
    """Return True if the job requires US citizenship/PR/clearance."""
    jd = job.get("descriptionText") or job.get("description") or ""
    return bool(CITIZENSHIP_RE.search(jd))


def company_filter_reason(job: Dict[str, Any], denylist: Dict[str, Any]) -> str:
    company = job.get("companyName") or ""
    website = job.get("companyWebsite") or ""
    company_n = normalize(company)
    website_n = normalize(website)

    # Denylist exact match (company name only)
    for name in denylist.get("exact_names", []):
        if company_n == normalize(name):
            return f"company denylist exact match: {name}"

    # Denylist partial match (company name)
    for part in denylist.get("name_contains", []):
        if normalize(part) in company_n:
            return f"company denylist partial match: {part}"

    # Denylist website match
    for part in denylist.get("website_contains", []):
        if normalize(part) in website_n:
            return f"company website denylist match: {part}"

    # Stealth/confidential/undisclosed — CAN check full text
    full_text_n = normalize(extract_full_text(job))
    for part in ["stealth", "confidential", "undisclosed"]:
        if part in company_n or part in full_text_n:
            return f"low-transparency signal: {part}"

    # Neutral business-rule signals — ONLY check company metadata, NOT full JD
    company_text_n = extract_company_text(job)
    for signal in NEUTRAL_BAD_SIGNALS:
        if signal in company_n or signal in website_n or signal in company_text_n:
            return f"neutral business-rule staffing/vendor signal: {signal}"

    # Citizenship/PR eligibility check
    if citizenship_pr_filter(job):
        return "citizenship_pr_required"

    return ""


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def get_job_id(job: Dict[str, Any]) -> str:
    for key in ["id", "jobId", "linkedinJobId"]:
        value = job.get(key)
        if value:
            return str(value)
    for key in ["jobUrl", "jobPostingUrl", "link", "applyUrl"]:
        value = job.get(key)
        if value:
            m = re.search(r"/(\d{6,})", str(value))
            if m:
                return m.group(1)
    return ""


def rank_job(job: Dict[str, Any], resume_text: str) -> Tuple[float, List[str]]:
    reasons: List[str] = []
    score = 0.0
    title = normalize(job.get("title", ""))
    text = normalize(extract_full_text(job))

    if any(t in title for t in ["software engineer", "software developer", "backend", "full stack", "full-stack", "mobile developer", "ai engineer"]):
        score += 3.0
        reasons.append("title aligns with target software roles")

    # Seniority penalty for staff/principal (small penalty, not a filter)
    if any(t in title for t in ["staff", "principal"]):
        score -= 0.5
        reasons.append("seniority stretch penalty (staff/principal)")

    for signal in GOOD_SIGNALS:
        if signal in text:
            score += 0.45

    if has_health_biotech_signal(job):
        score += 0.9
        reasons.append("health/biotech domain fits your health-tech background")

    if "full time" in text or "full-time" in text:
        score += 0.6
        reasons.append("full-time role")

    if "new york" in text or "remote" in text or "hybrid" in text:
        score += 0.4

    if get_posted_display(job):
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

    return score, reasons[:5]


def render_star_value(value: float) -> str:
    full = int(value)
    half = (value - full) >= 0.5
    return "⭐" * full + ("✨" if half else "")


def assign_star_value(rank_index: int, total: int) -> float:
    ladders = {
        1: [5.0],
        2: [5.0, 4.5],
        3: [5.0, 4.5, 4.0],
        4: [5.0, 4.5, 4.0, 3.5],
        5: [5.0, 4.5, 4.0, 3.5, 3.0],
    }
    if total in ladders and rank_index < len(ladders[total]):
        return ladders[total][rank_index]
    ladder = [5.0, 4.5, 4.5, 4.0, 4.0, 3.5, 3.5, 3.0, 3.0, 2.5]
    return ladder[min(rank_index, len(ladder) - 1)]


def choose_top_n(recommended_count: int, run_mode: str) -> int:
    if run_mode == "low_peak":
        return min(5, recommended_count)
    return 10 if recommended_count >= 10 else min(5, recommended_count)


def select_recommended(candidates: List[Tuple[Dict[str, Any], float, List[str]]], run_mode: str) -> List[Tuple[Dict[str, Any], float, List[str]]]:
    if run_mode == "low_peak":
        suitable = [candidate for candidate in candidates if candidate[1] >= LOW_PEAK_MIN_SCORE]
        return suitable[:choose_top_n(len(suitable), run_mode)]
    return candidates[:choose_top_n(len(candidates), run_mode)]


# ---------------------------------------------------------------------------
# Notion integration (optional)
# ---------------------------------------------------------------------------

def notion_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def notion_api(url: str, headers: Dict[str, str], payload: Optional[Dict] = None,
               method: str = "GET") -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        _, snippet = http_error_body(exc)
        detail = f"HTTP {exc.code} {exc.reason}"
        if snippet:
            detail += f" | {snippet}"
        raise RuntimeError(f"Notion API {method} {url} failed: {detail}") from exc


def notion_rich_text(text: str) -> List[Dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        return []
    return [{"type": "text", "text": {"content": text[:2000]}}]


def notion_existing_urls(db_id: str, headers: Dict[str, str]) -> set:
    existing: set = set()
    cursor: Optional[str] = None
    while True:
        payload: Dict[str, Any] = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        result = notion_api(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            headers,
            payload=payload,
            method="POST",
        )
        for page in result.get("results", []):
            url_prop = (((page.get("properties") or {}).get("URL") or {}).get("url") or "").strip()
            if url_prop:
                existing.add(url_prop)
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return existing


def ensure_job_tracker_schema(db_id: str, headers: Dict[str, str]) -> None:
    db = notion_api(f"https://api.notion.com/v1/databases/{db_id}", headers)
    props = db.get("properties", {})

    removal_patch: Dict[str, Any] = {}
    if "note" in props:
        removal_patch["note"] = None
    if "Score" in props and props["Score"].get("type") != "rich_text":
        removal_patch["Score"] = None
    if removal_patch:
        notion_api(
            f"https://api.notion.com/v1/databases/{db_id}",
            headers,
            payload={"properties": removal_patch},
            method="PATCH",
        )
        db = notion_api(f"https://api.notion.com/v1/databases/{db_id}", headers)
        props = db.get("properties", {})

    patch: Dict[str, Any] = {}
    if "Score" not in props:
        patch["Score"] = {"rich_text": {}}
    if "Match reason" not in props:
        patch["Match reason"] = {"rich_text": {}}
    if "Location" not in props:
        patch["Location"] = {"rich_text": {}}
    if "Posted" not in props:
        patch["Posted"] = {"rich_text": {}}
    if "Work mode" not in props:
        patch["Work mode"] = {
            "select": {
                "options": [
                    {"name": "Remote", "color": "green"},
                    {"name": "Hybrid", "color": "yellow"},
                    {"name": "On-site", "color": "blue"},
                ]
            }
        }

    if patch:
        notion_api(
            f"https://api.notion.com/v1/databases/{db_id}",
            headers,
            payload={"properties": patch},
            method="PATCH",
        )


def jd_to_children(jd_text: str) -> List[Dict[str, Any]]:
    if not jd_text.strip():
        return []
    chunks: List[str] = []
    for para in [p.strip() for p in jd_text.split("\n\n") if p.strip()]:
        while len(para) > 1800:
            chunks.append(para[:1800])
            para = para[1800:]
        chunks.append(para)
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": c}}]},
        }
        for c in chunks[:80]
    ]


def infer_work_mode(job: Dict[str, Any]) -> Optional[str]:
    text = normalize(extract_full_text(job))
    if "hybrid" in text:
        return "Hybrid"
    if "on-site" in text or "onsite" in text or "in-office" in text or "in office" in text:
        return "On-site"
    if "remote" in text:
        return "Remote"
    return None


def notion_insert_job(job: Dict[str, Any], score: float, reasons: List[str], star_text: str,
                      db_id: str, headers: Dict[str, str], existing_urls: Optional[set] = None) -> str:
    """Insert a job into Notion. Returns inserted|exists|failed|invalid."""
    company = (job.get("companyName") or "Unknown")[:200]
    title = (job.get("title") or "Untitled")[:2000]
    url = get_job_url(job)
    if not has_valid_job_url(job):
        print(f"  Notion insert skipped for {company} - {title}: invalid LinkedIn URL", file=sys.stderr)
        return "invalid"
    if existing_urls is not None and url in existing_urls:
        print(f"  Notion insert skipped for {company} - {title}: URL already exists in database", file=sys.stderr)
        return "exists"

    match_reason = "; ".join(reasons[:3]).strip()
    jd_text = job.get("descriptionText") or ""
    location = (job.get("location") or "").strip()
    work_mode = infer_work_mode(job)
    posted = get_posted_display(job)

    properties: Dict[str, Any] = {
        "Name": {"title": [{"type": "text", "text": {"content": company}}]},
        "URL": {"url": url},
        "position": {"rich_text": notion_rich_text(title)},
        "Status": {"status": {"name": "Not started"}},
        "Score": {"rich_text": notion_rich_text(star_text)},
        "Match reason": {"rich_text": notion_rich_text(match_reason)},
    }
    if location:
        properties["Location"] = {"rich_text": notion_rich_text(location)}
    if work_mode:
        properties["Work mode"] = {"select": {"name": work_mode}}
    if posted:
        properties["Posted"] = {"rich_text": notion_rich_text(posted)}

    payload = {
        "parent": {"database_id": db_id},
        "properties": properties,
        "children": jd_to_children(jd_text),
    }
    try:
        result = notion_api("https://api.notion.com/v1/pages", headers, payload=payload, method="POST")
        if result.get("object") == "page":
            if existing_urls is not None:
                existing_urls.add(url)
            return "inserted"
        return "failed"
    except Exception as e:
        print(f"  Notion insert failed for {company} - {title}: {e}", file=sys.stderr)
        return "failed"


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main() -> int:
    ensure_dirs()

    # Notion setup (optional)
    notion_token = os.environ.get("NOTION_TOKEN")
    notion_db_id = os.environ.get("NOTION_DB_ID")
    notion_enabled = bool(notion_token and notion_db_id)
    notion_status = "not configured"

    if notion_enabled:
        try:
            nh = notion_headers(notion_token)
            ensure_job_tracker_schema(notion_db_id, nh)
            notion_status = "connected"
            print("Notion integration: connected and verified.", file=sys.stderr)
        except Exception as e:
            notion_enabled = False
            notion_status = f"failed: {e}"
            print(f"WARNING: Notion integration unavailable: {e}", file=sys.stderr)
    else:
        print("WARNING: NOTION_TOKEN or NOTION_DB_ID not set. Notion integration disabled.", file=sys.stderr)

    denylist = load_json(DENYLIST_PATH, {"exact_names": [], "name_contains": [], "website_contains": [], "notes": []})
    seen = load_json(SEEN_PATH, {"jobs": {}})
    seen_jobs = seen.setdefault("jobs", {})
    resume_text = normalize(read_text_if_exists(RESUME_PATH))

    run_mode = get_run_mode()
    max_results = get_max_results_for_mode(run_mode)
    apify_token = os.environ.get("APIFY_TOKEN")

    # Fetch jobs from free LinkedIn guest API first, then fall back to Apify if anti-bot blocks bite hard.
    print(f"Fetching jobs from LinkedIn guest API... (mode={run_mode}, count={max_results})", file=sys.stderr)
    jobs, fetch_meta = fetch_jobs_guest_api(max_results)
    fetched = len(jobs)
    print(f"Fetched {fetched} jobs across {fetch_meta['queries']} queries.", file=sys.stderr)
    if fetch_meta.get("errors"):
        for err in fetch_meta["errors"]:
            print(f"  fetch error: {err}", file=sys.stderr)

    guest_meta = fetch_meta
    if should_use_apify_fallback(jobs, fetch_meta):
        if apify_token:
            print("LinkedIn anti-bot threshold hit; switching to Apify fallback...", file=sys.stderr)
            jobs, fetch_meta = fetch_jobs_apify(apify_token, max_results)
            fetch_meta["guest_attempt"] = guest_meta
            fetched = len(jobs)
            print(f"Apify fallback fetched {fetched} jobs.", file=sys.stderr)
        else:
            print("WARNING: LinkedIn anti-bot threshold hit but APIFY_TOKEN is not available; staying on guest API results.", file=sys.stderr)

    already_seen = 0
    filtered_out: List[Tuple[Dict[str, Any], str]] = []
    filter_reasons_count: Dict[str, int] = {}
    candidates: List[Tuple[Dict[str, Any], float, List[str]]] = []

    for job in jobs:
        if not has_valid_job_url(job):
            filtered_out.append((job, "invalid_or_missing_linkedin_job_url"))
            filter_reasons_count["invalid_link"] = filter_reasons_count.get("invalid_link", 0) + 1
            continue
        job_id = get_job_id(job)
        if not job_id:
            filtered_out.append((job, "missing linkedin job id"))
            filter_reasons_count["missing_id"] = filter_reasons_count.get("missing_id", 0) + 1
            continue
        if job_id in seen_jobs:
            already_seen += 1
            continue
        reason = company_filter_reason(job, denylist)
        if reason:
            filtered_out.append((job, reason))
            # Categorize filter reason
            if "citizenship_pr" in reason:
                cat = "citizenship_pr"
            elif "denylist" in reason:
                cat = "denylist"
            elif "neutral" in reason:
                cat = "neutral_signals"
            elif "low-transparency" in reason:
                cat = "low_transparency"
            else:
                cat = "other"
            filter_reasons_count[cat] = filter_reasons_count.get(cat, 0) + 1
            continue
        score, reasons = rank_job(job, resume_text)
        candidates.append((job, score, reasons))

    candidates.sort(key=lambda x: x[1], reverse=True)
    recommended = select_recommended(candidates, run_mode)
    recommended_star_map = {
        get_job_id(job): render_star_value(assign_star_value(idx, len(recommended)))
        for idx, (job, _, _) in enumerate(recommended)
    }

    # Notion insertion (before report, so we can include status)
    notion_inserted = 0
    notion_failed = 0
    notion_skipped_existing = 0
    existing_notion_urls: Optional[set] = None
    if notion_enabled and recommended:
        existing_notion_urls = notion_existing_urls(notion_db_id, nh)
        print(f"Inserting {len(recommended)} jobs into Notion...", file=sys.stderr)
        for job, score, reasons in recommended:
            star_text = recommended_star_map.get(get_job_id(job), render_star_value(3.0))
            status = notion_insert_job(job, score, reasons, star_text, notion_db_id, nh, existing_notion_urls)
            if status == "inserted":
                notion_inserted += 1
            elif status == "exists":
                notion_skipped_existing += 1
            elif status in {"failed", "invalid"}:
                notion_failed += 1
            time.sleep(0.3)  # rate limit courtesy

    # Update seen state
    stamp = now_iso()
    for job, score, reasons in candidates:
        job_id = get_job_id(job)
        seen_jobs[job_id] = {
            "first_seen_at": stamp,
            "title": job.get("title"),
            "companyName": job.get("companyName"),
        }
    save_json(SEEN_PATH, seen)

    # Generate report
    lines: List[str] = []
    lines.append("# LinkedIn Jobs Report\n")
    lines.append(f"- Generated at: {stamp}")
    lines.append(f"- Run mode: {run_mode}")
    lines.append(f"- Data source: {fetch_meta.get('source_label', 'LinkedIn guest API (free, no API key)')}")
    lines.append(f"- Search queries: {', '.join(ROLE_QUERIES)}")
    lines.append(f"- Location: {SEARCH_LOCATION} (geoId={SEARCH_GEO_ID}, distance={SEARCH_DISTANCE})")
    lines.append(f"- Freshness: last 4 hours")
    lines.append(f"- Fetch limit: {max_results}")
    lines.append(f"- Jobs fetched: {fetched}")
    lines.append(f"- Fetch errors: {len(fetch_meta.get('errors', []))}")
    if fetch_meta.get("mode") == "apify_fallback":
        lines.append(f"- Fallback used: yes (actor={fetch_meta.get('actor')}, mode={fetch_meta.get('fetch_mode')})")
        guest_attempt = fetch_meta.get("guest_attempt") or {}
        lines.append(f"- Guest attempt before fallback: raw_cards={guest_attempt.get('raw_cards', 0)}, detail_attempted={guest_attempt.get('detail_attempted', 0)}, anti_bot_errors={guest_attempt.get('anti_bot_errors', 0)}")
    else:
        lines.append("- Fallback used: no")
    lines.append(f"- Already seen skipped: {already_seen}")
    lines.append(f"- Filtered out: {len(filtered_out)}")
    if filter_reasons_count:
        breakdown = ", ".join(f"{k}={v}" for k, v in sorted(filter_reasons_count.items()))
        lines.append(f"  - Filter breakdown: {breakdown}")
    lines.append(f"- New candidates kept: {len(candidates)}")
    if run_mode == "low_peak":
        lines.append(f"- Low-peak recommendation threshold: score >= {LOW_PEAK_MIN_SCORE:.1f}, cap 5")
    email_enabled = env_flag("LINKEDIN_EMAIL_REPORTS", default=False)
    save_local_reports = env_flag("LINKEDIN_SAVE_LOCAL_REPORTS", default=False)
    email_status = "disabled"
    email_detail = "LINKEDIN_EMAIL_REPORTS not enabled"
    email_thread_id = ""
    email_thread_url = ""

    if notion_enabled:
        lines.append(f"- Notion: {notion_inserted} inserted, {notion_skipped_existing} already existed, {notion_failed} failed")
    else:
        lines.append(f"- Notion: {notion_status}")

    fetch_issues: List[str] = []
    if fetch_meta.get("mode") == "apify_fallback":
        guest_attempt = fetch_meta.get("guest_attempt") or {}
        for err in guest_attempt.get("errors", []):
            fetch_issues.append(f"guest attempt: {err}")
        for err in fetch_meta.get("errors", []):
            fetch_issues.append(f"apify fallback: {err}")
    else:
        fetch_issues.extend(fetch_meta.get("errors", []))

    if fetch_issues:
        lines.append("")
        lines.append("## 0. Fetch issues\n")
        for err in fetch_issues:
            lines.append(f"- {err}")
    lines.append("")

    lines.append("## 1. New recommended jobs\n")
    if recommended:
        for idx, (job, score, reasons) in enumerate(recommended, start=1):
            job_id = get_job_id(job)
            url = get_job_url(job)
            company = job.get("companyName") or "Unknown company"
            title = job.get("title") or "Untitled role"
            location = job.get("location") or "Unknown location"
            star_text = recommended_star_map.get(job_id, render_star_value(3.0))
            lines.append(f"### {idx}. {title} — {company}")
            lines.append(f"- Rating: {star_text}")
            lines.append(f"- Location: {location}")
            if get_posted_display(job):
                lines.append(f"- Posted: {get_posted_display(job)}")
            if infer_work_mode(job):
                lines.append(f"- Work mode: {infer_work_mode(job)}")
            lines.append(f"- Employment type: {job.get('employmentType') or 'Unknown'}")
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
        if run_mode == "low_peak":
            lines.append("No suitable low-peak recommendations this run.\n")
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

    provisional_report = "\n".join(lines).rstrip() + "\n"
    pdf_status = "not_generated"
    local_report_status = "not_saved"
    temp_pdf_path: Optional[Path] = None
    if email_enabled:
        try:
            pdf_path = REPORT_PDF_PATH
            if not save_local_reports and not REPORT_PDF_PATH_ENV:
                temp_pdf_path = Path(tempfile.mkstemp(prefix="linkedin-jobs-", suffix=".pdf")[1])
                pdf_path = temp_pdf_path
            write_simple_pdf_from_markdown(provisional_report, pdf_path)
            pdf_status = str(pdf_path)
            email_status, email_detail, email_thread_id, email_thread_url = send_report_gmail(
                provisional_report,
                pdf_path,
                stamp,
                fetch_meta,
                fetched,
                len(filtered_out),
                already_seen,
                len(candidates),
                len(recommended),
                recommended,
                recommended_star_map,
            )
        except Exception as e:
            email_status = "failed"
            email_detail = str(e)

    lines.append("## 4. Delivery\n")
    lines.append(f"- Gmail report email: {email_status}")
    lines.append(f"- Gmail detail: {email_detail}")
    if email_thread_id:
        lines.append(f"- Gmail thread ID: {email_thread_id}")
    if email_thread_url:
        lines.append(f"- Gmail thread URL: {email_thread_url}")
    if email_enabled:
        lines.append(f"- PDF report: {pdf_status}")
    lines.append("")

    final_report = "\n".join(lines).rstrip() + "\n"
    if save_local_reports or REPORT_PATH_ENV:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(final_report)
        local_report_status = str(REPORT_PATH)

    if temp_pdf_path and temp_pdf_path.exists():
        try:
            temp_pdf_path.unlink()
            pdf_status = "emailed_and_deleted"
        except Exception:
            pass

    # Console summary for Discord announce
    summary_titles = [f"{job.get('title')} @ {job.get('companyName')}" for job, _, _ in recommended[:5]]
    notion_line = ""
    if notion_enabled:
        notion_line = f" | notion_inserted={notion_inserted} | notion_exists={notion_skipped_existing}"
    elif notion_token or notion_db_id:
        notion_line = " | notion=connection_failed"
    else:
        notion_line = " | notion=not_configured"
    email_line = f" | gmail={email_status}"
    link_line = f" | gmail_url={email_thread_url}" if email_thread_url else ""
    report_line = f" | local_report={local_report_status}"

    source_mode = fetch_meta.get("mode", "linkedin_guest_api")
    print(
        f"LinkedIn jobs run ok | mode={run_mode} | source={source_mode} | fetched={fetched} | filtered={len(filtered_out)} | already_seen={already_seen} | remaining={len(candidates)} | selected={len(recommended)}{notion_line}{email_line}{report_line}"
    )
    if email_thread_url:
        print(f"Gmail report URL: {email_thread_url}")
    if summary_titles:
        print("Top picks: " + " | ".join(summary_titles))
    else:
        print("Top picks: none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
