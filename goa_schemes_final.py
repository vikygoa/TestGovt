import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

BASE = "https://services.goaonline.gov.in/ServiceWizard"
OUT = Path("output")
RAW = OUT / "raw"
OUT.mkdir(parents=True, exist_ok=True)
RAW.mkdir(parents=True, exist_ok=True)

# Do not hard-code session URLs. Goa Online generates /GS/... URLs per browser session.


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def redact_url(url):
    # Keep official path but hide very long session tokens in logs.
    p = urlparse(url)
    parts = p.path.split("/")
    if len(parts) > 2 and parts[1] == "GS" and len(parts[2]) > 20:
        parts[2] = "<SESSION_TOKEN>"
        return p._replace(path="/".join(parts)).geturl()
    return url


def is_download_url(href):
    if not href:
        return False
    h = href.lower()
    return (
        ".pdf" in h
        or "download" in h
        or "attachment" in h
        or "document" in h
        or "file" in h
    )


def extract_pdf_links(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a.get("href"))
        text = clean(a.get_text(" ", strip=True))
        if is_download_url(href) or ".pdf" in href.lower():
            if href not in seen:
                seen.add(href)
                links.append({"label": text, "url": href})
    return links


def extract_scheme_records(html, base_url, sector_name):
    """Best-effort parser for the server-rendered scheme result page.

    Goa Online has changed markup over time, so this intentionally avoids fixed
    element IDs. It identifies headings/cards/tables containing scheme labels and
    preserves all useful text plus official document URLs.
    """
    soup = BeautifulSoup(html, "html.parser")
    all_links = extract_pdf_links(html, base_url)

    # First try obvious repeating scheme containers.
    containers = []
    selectors = [
        "table tr",
        ".scheme",
        ".scheme-card",
        ".card",
        ".panel",
        ".list-group-item",
        "article",
    ]
    for selector in selectors:
        found = soup.select(selector)
        if found:
            containers.extend(found)

    records = []
    seen_titles = set()
    for node in containers:
        txt = clean(node.get_text(" ", strip=True))
        if len(txt) < 20:
            continue
        links = []
        for a in node.find_all("a", href=True):
            href = urljoin(base_url, a.get("href"))
            if is_download_url(href) or ".pdf" in href.lower():
                links.append({"label": clean(a.get_text(" ", strip=True)), "url": href})
        if not links and not re.search(r"scheme|benefit|eligib|document|department", txt, re.I):
            continue

        # Title heuristic: heading first, otherwise first substantial cell/line.
        title = ""
        h = node.find(["h1", "h2", "h3", "h4", "h5", "strong", "b"])
        if h:
            title = clean(h.get_text(" ", strip=True))
        if not title:
            lines = [clean(x) for x in node.stripped_strings]
            title = next((x for x in lines if len(x) >= 8), "")
        key = re.sub(r"\W+", " ", title.lower()).strip()
        if not key or key in seen_titles:
            continue
        seen_titles.add(key)
        records.append({
            "sector": sector_name,
            "title": title,
            "text": txt,
            "documents": links,
        })

    # If the page is a table, parse rows more structurally as a fallback.
    if not records:
        for tr in soup.find_all("tr"):
            cells = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
            if not cells:
                continue
            row_text = " | ".join(cells)
            if len(row_text) < 20:
                continue
            row_links = []
            for a in tr.find_all("a", href=True):
                href = urljoin(base_url, a["href"])
                if is_download_url(href) or ".pdf" in href.lower():
                    row_links.append({"label": clean(a.get_text(" ", strip=True)), "url": href})
            records.append({"sector": sector_name, "title": cells[0], "text": row_text, "documents": row_links})

    # Always preserve document links even if the site's layout is unfamiliar.
    if not records and all_links:
        records.append({
            "sector": sector_name,
            "title": "Scheme documents found",
            "text": "",
            "documents": all_links,
        })

    return records


async def click_know_your_scheme(page):
    candidates = [
        page.get_by_text("Know your scheme", exact=True),
        page.get_by_text("Know Your Scheme", exact=True),
        page.locator("a", has_text=re.compile(r"^Know\s+your\s+scheme$", re.I)),
    ]
    for loc in candidates:
        try:
            if await loc.count():
                await loc.first.click(timeout=15000)
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                return True
        except Exception:
            continue
    return False


async def wait_for_sector_page(page):
    # Wait for a real sector dropdown, not a guessed ID.
    for _ in range(60):
        try:
            selects = page.locator("select")
            count = await selects.count()
            for i in range(count):
                opts = await selects.nth(i).locator("option").all_text_contents()
                joined = " ".join(opts)
                if "Educational" in joined and "Unemployment" in joined:
                    return selects.nth(i)
        except Exception:
            pass
        await page.wait_for_timeout(500)
    raise RuntimeError("Could not find the Goa Online sector dropdown on the live /GS/ page")


async def main():
    run_meta = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source": BASE,
        "session_url": None,
        "errors": [],
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1365, "height": 900},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        )
        page = await context.new_page()
        network_events = []

        def on_response(resp):
            u = resp.url
            if "/GS/" in u or "goaonline.gov.in" in u:
                network_events.append({"method": resp.request.method, "url": redact_url(u), "status": resp.status})

        page.on("response", on_response)

        try:
            await page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2500)

            clicked = await click_know_your_scheme(page)
            if not clicked:
                # ServiceWizard sometimes exposes a card/DOM fallback after the initial load.
                links = await page.locator("a").all()
                for a in links:
                    try:
                        text = clean(await a.inner_text())
                        if re.fullmatch(r"Know\s+your\s+scheme", text, re.I):
                            await a.click(timeout=10000)
                            await page.wait_for_load_state("domcontentloaded", timeout=30000)
                            clicked = True
                            break
                    except Exception:
                        pass

            sector_select = await wait_for_sector_page(page)
            run_meta["session_url"] = redact_url(page.url)
            run_meta["know_your_scheme_clicked"] = clicked
            run_meta["sector_page_title"] = clean(await page.title())

            options = await sector_select.locator("option").evaluate_all(
                "els => els.map(o => ({value:o.value, label:(o.textContent||'').trim()})).filter(x => x.value && x.label && x.label !== '- Select -')"
            )

            results = []
            for opt in options:
                label = opt["label"]
                value = opt["value"]
                safe = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or value
                try:
                    # Re-find select each time because the page may replace it after POST.
                    sector_select = await wait_for_sector_page(page)
                    await sector_select.select_option(value=value)
                    await page.wait_for_timeout(300)

                    # The real page uses a server-side POST form. Prefer the visible
                    # Display Schemes submit control and retain the same browser context.
                    buttons = page.locator("input[type=submit], button")
                    clicked_display = False
                    for j in range(await buttons.count()):
                        b = buttons.nth(j)
                        try:
                            text = clean(await b.get_attribute("value")) or clean(await b.inner_text())
                            if re.search(r"display\s+schemes", text, re.I):
                                await b.click(timeout=15000)
                                clicked_display = True
                                break
                        except Exception:
                            continue
                    if not clicked_display:
                        raise RuntimeError("Display Schemes control not found")

                    await page.wait_for_load_state("domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(1800)
                    html = await page.content()
                    (RAW / f"{safe}.html").write_text(html, encoding="utf-8")
                    schemes = extract_scheme_records(html, page.url, label)
                    results.extend(schemes)
                except Exception as exc:
                    run_meta["errors"].append({"sector": label, "error": str(exc)})
                    # Continue to the next sector; one broken sector must not kill the run.

            # Deduplicate schemes by sector/title/text and document URL.
            dedup = {}
            for item in results:
                key = (item.get("sector", ""), item.get("title", ""), item.get("text", ""))
                if key not in dedup:
                    dedup[key] = item
                else:
                    existing = dedup[key].setdefault("documents", [])
                    have = {d.get("url") for d in existing}
                    for d in item.get("documents", []):
                        if d.get("url") not in have:
                            existing.append(d)
                            have.add(d.get("url"))

            payload = {
                "source": BASE,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "session_url": run_meta["session_url"],
                "sectors": options,
                "schemes": list(dedup.values()),
                "note": "Official Goa Online session-generated URLs are captured at runtime. PDF/document files are not downloaded; only official links found on the scheme page are stored.",
            }
            (OUT / "schemes.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            (OUT / "run-report.json").write_text(json.dumps(run_meta, ensure_ascii=False, indent=2), encoding="utf-8")
            (OUT / "network-log.json").write_text(json.dumps(network_events, ensure_ascii=False, indent=2), encoding="utf-8")

            print(json.dumps({
                "session_url": run_meta["session_url"],
                "sectors": len(options),
                "schemes": len(payload["schemes"]),
                "errors": len(run_meta["errors"]),
            }, indent=2))
        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
