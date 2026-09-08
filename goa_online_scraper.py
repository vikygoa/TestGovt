import argparse
import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
LOGS = ROOT / "logs"
OUT.mkdir(exist_ok=True)
LOGS.mkdir(exist_ok=True)

DEFAULT_URL = (
    "https://services.goaonline.gov.in/ServiceWizard"
)

# These are labels seen in/around the Goa Online service catalogue.
# The scraper also discovers additional labels from the live page.
KNOWN_SECTORS = [
    "IT",
    "Educational",
    "Education",
    "Health & Family Welfare",
    "Senior Citizens",
    "Unemployment",
    "Physically/Mentally Challenged",
    "Business",
    "Fishermen/Entrepreneur",
    "Agriculture",
    "Transport",
    "Ex-Servicemen/Welfare",
    "Women and Child Development",
]


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def slug(text):
    value = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return value[:100] or "scheme"


def is_document_url(url):
    low = url.lower()
    return any(x in low for x in [
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip"
    ]) or any(x in low for x in [
        "download", "document", "attachment", "file"
    ])


def is_internal(url):
    try:
        return urlparse(url).netloc.lower().endswith("goaonline.gov.in")
    except Exception:
        return False


async def safe_text(page):
    try:
        return clean(await page.locator("body").inner_text(timeout=15000))
    except Exception:
        return ""


async def snapshot(page, name):
    path = LOGS / f"{name}.html"
    try:
        path.write_text(await page.content(), encoding="utf-8")
        return str(path)
    except Exception:
        return ""


async def collect_links(page):
    rows = []
    try:
        links = await page.locator("a[href]").evaluate_all("""
        els => els.map(a => ({
          text: (a.innerText || a.textContent || '').trim(),
          href: a.href || ''
        }))
        """)
        for x in links:
            text = clean(x.get("text"))
            href = x.get("href", "")
            if not href or href.startswith("javascript:"):
                continue
            rows.append({
                "text": text,
                "url": href,
                "is_document": is_document_url(href)
            })
    except Exception:
        pass
    return rows


async def click_text_if_present(page, text):
    candidates = [
        page.get_by_text(text, exact=True),
        page.get_by_text(text, exact=False),
    ]
    for loc in candidates:
        try:
            count = await loc.count()
            if count:
                for i in range(min(count, 3)):
                    try:
                        await loc.nth(i).scroll_into_view_if_needed()
                        await loc.nth(i).click(timeout=5000)
                        return True
                    except Exception:
                        continue
        except Exception:
            continue
    return False


async def discover_sectors(page):
    # First use visible text from likely buttons/select options/cards.
    discovered = []
    try:
        texts = await page.locator("button, a, option, label, [role=button]").all_inner_texts()
        for t in texts:
            t = clean(t)
            if not t or len(t) > 100:
                continue
            low = t.lower()
            if any(k.lower() in low for k in [
                "education", "health", "unemployment", "agriculture",
                "business", "fisher", "transport", "senior", "women",
                "disability", "ex-servic", "scheme"
            ]):
                discovered.append(t)
    except Exception:
        pass

    # Keep known sector names when they appear in the live page.
    out = []
    seen = set()
    for item in KNOWN_SECTORS + discovered:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


async def extract_scheme_cards(page, sector):
    """Extract candidate scheme links/cards from current results page."""
    rows = []
    seen = set()

    links = await collect_links(page)
    for link in links:
        text = link["text"]
        url = link["url"]
        if not text or len(text) < 3:
            continue
        if not is_internal(url):
            continue
        if is_document_url(url):
            continue

        low = text.lower()
        # Avoid navigation noise.
        if low in {
            "home", "login", "register", "services", "know your scheme",
            "contact us", "back", "next", "previous", "search"
        }:
            continue

        key = (text.lower(), url)
        if key in seen:
            continue
        seen.add(key)

        rows.append({
            "name": text,
            "url": url,
            "sector": sector
        })

    return rows


async def extract_detail(page, candidate):
    await page.goto(candidate["url"], wait_until="domcontentloaded", timeout=60000)
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    body = await safe_text(page)
    links = await collect_links(page)

    # Try to derive useful headings/sections from visible text.
    section_data = {
        "description": "",
        "benefits": "",
        "eligibility": "",
        "documents_required": []
    }

    # Generic heading-to-text extraction.
    try:
        headings = await page.locator("h1,h2,h3,h4,strong,b,label").all_inner_texts()
        headings = [clean(x) for x in headings if clean(x)]
    except Exception:
        headings = []

    def section_after(label):
        # Conservative: return a short body slice around a section heading.
        idx = body.lower().find(label.lower())
        if idx < 0:
            return ""
        tail = body[idx + len(label):]
        return clean(tail[:1200])

    for label, key in [
        ("benefits", "benefits"),
        ("eligibility", "eligibility"),
        ("description", "description"),
        ("documents required", "documents_required"),
        ("documents", "documents_required"),
    ]:
        value = section_after(label)
        if key == "documents_required":
            if value:
                section_data[key] = [
                    clean(x) for x in re.split(r"[•\n]+", value) if clean(x)
                ][:30]
        elif value and not section_data[key]:
            section_data[key] = value

    downloads = []
    seen_urls = set()
    for link in links:
        url = link["url"]
        if not is_document_url(url):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        label = link["text"] or "Official document"
        low = label.lower()
        dtype = "pdf" if ".pdf" in url.lower() else "document"
        if "application" in low:
            kind = "application_form"
        elif "declaration" in low:
            kind = "declaration_form"
        elif "notification" in low or "scheme" in low:
            kind = "scheme_notification"
        else:
            kind = "document"
        downloads.append({
            "label": label,
            "url": url,
            "type": dtype,
            "kind": kind
        })

    title = candidate["name"]
    try:
        page_title = clean(await page.title())
        if page_title and len(page_title) < 250:
            title = page_title
    except Exception:
        pass

    # Prefer H1 as scheme title if present.
    try:
        h1s = await page.locator("h1").all_inner_texts()
        if h1s and clean(h1s[0]):
            title = clean(h1s[0])
    except Exception:
        pass

    return {
        "id": slug(title),
        "name": title,
        "sector": candidate["sector"],
        "department": "",
        "description": section_data["description"],
        "benefits": section_data["benefits"],
        "eligibility": section_data["eligibility"],
        "documents_required": section_data["documents_required"],
        "downloads": downloads,
        "source_url": page.url,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "raw_text_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }


async def main(url):
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "input_url": url,
        "sectors_seen": [],
        "candidate_count": 0,
        "scheme_count": 0,
        "errors": []
    }

    schemes = []
    candidates = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000},
            locale="en-IN"
        )
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass

            await snapshot(page, "landing")
            landing_text = await safe_text(page)

            # If user supplied the ServiceWizard root, try opening Know your scheme.
            if "know your scheme" not in landing_text.lower() or url.rstrip("/").endswith("ServiceWizard"):
                await click_text_if_present(page, "Know your scheme")
                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass

            await snapshot(page, "know-your-scheme")

            sectors = await discover_sectors(page)
            report["sectors_seen"] = sectors

            # Some dynamic implementations expose a sector dropdown.
            selects = page.locator("select")
            select_count = await selects.count()

            if select_count:
                for si in range(select_count):
                    try:
                        opts = await selects.nth(si).locator("option").all_inner_texts()
                    except Exception:
                        continue

                    for sector in sectors:
                        match = next((o for o in opts if clean(o).lower() == sector.lower()), None)
                        if not match:
                            continue
                        try:
                            await selects.nth(si).select_option(label=match)
                            # Try common action labels.
                            for action in ["Display Schemes", "Search", "Submit", "View Schemes"]:
                                await click_text_if_present(page, action)
                            await page.wait_for_timeout(1200)
                            found = await extract_scheme_cards(page, sector)
                            candidates.extend(found)
                        except Exception as e:
                            report["errors"].append(f"{sector}: {e}")

            # Also attempt direct sector text/click paths.
            for sector in sectors:
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass

                    clicked = await click_text_if_present(page, sector)
                    if clicked:
                        for action in ["Display Schemes", "Search", "Submit", "View Schemes"]:
                            await click_text_if_present(page, action)
                        await page.wait_for_timeout(1200)
                        found = await extract_scheme_cards(page, sector)
                        candidates.extend(found)
                except Exception as e:
                    report["errors"].append(f"sector click {sector}: {e}")

            # De-duplicate candidate links.
            unique = {}
            for c in candidates:
                unique[(c["name"].lower(), c["url"])] = c
            candidates = list(unique.values())
            report["candidate_count"] = len(candidates)

            # Open candidate detail pages. Limit is deliberately high; remove if needed.
            for idx, candidate in enumerate(candidates, start=1):
                try:
                    detail = await extract_detail(page, candidate)
                    schemes.append(detail)
                    print(f"[{idx}/{len(candidates)}] {detail['name']}")
                except Exception as e:
                    report["errors"].append(
                        f"{candidate.get('name')}: {type(e).__name__}: {e}"
                    )

        finally:
            await browser.close()

    # De-duplicate final schemes.
    final = {}
    for s in schemes:
        final[(s["name"].lower(), s["source_url"])] = s
    schemes = list(final.values())

    payload = {
        "source": "Goa Online - Know Your Scheme",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pdf_policy": "links_only_no_download",
        "schemes": schemes
    }
    (OUT / "schemes.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    report["scheme_count"] = len(schemes)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (OUT / "scrape-report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()
    asyncio.run(main(args.url))
