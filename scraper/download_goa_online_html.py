import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

BASE_URL = "https://services.goaonline.gov.in/ServiceWizard"
OUT = Path("output")
OUT.mkdir(exist_ok=True)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1000},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        requests = []
        responses = []
        console = []

        page.on("request", lambda r: requests.append({
            "method": r.method,
            "url": r.url,
            "resource_type": r.resource_type,
            "post_data": r.post_data
        }))
        page.on("response", lambda r: responses.append({
            "status": r.status,
            "url": r.url,
            "resource_type": r.request.resource_type
        }))
        page.on("console", lambda m: console.append({
            "type": m.type,
            "text": m.text
        }))

        print("Opening:", BASE_URL)
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=120000)
        await page.wait_for_timeout(5000)

        # Save initial page.
        (OUT / "01-servicewizard.html").write_text(await page.content(), encoding="utf-8")
        await page.screenshot(path=str(OUT / "01-servicewizard.png"), full_page=True)

        # Record all anchors and elements containing "scheme".
        elements = await page.locator("a,button,[role='button'],input,li").evaluate_all("""
        els => els.map((e, i) => ({
            i,
            tag: e.tagName,
            text: (e.innerText || e.value || '').trim(),
            href: e.href || '',
            onclick: e.getAttribute('onclick') || '',
            id: e.id || '',
            cls: e.className || ''
        })).filter(x => /scheme/i.test(
            [x.text,x.href,x.onclick,x.id,x.cls].join(' ')
        ))
        """)
        (OUT / "02-scheme-elements.json").write_text(
            json.dumps(elements, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        clicked = False

        # Try visible text first.
        locators = [
            page.get_by_text("Know your scheme", exact=True),
            page.get_by_text("Know Your Scheme", exact=True),
            page.get_by_text("Know your scheme", exact=False),
        ]

        for loc in locators:
            try:
                count = await loc.count()
                for i in range(count):
                    item = loc.nth(i)
                    if await item.is_visible():
                        print("Clicking:", await item.inner_text())
                        await item.scroll_into_view_if_needed()
                        await item.click(timeout=15000, force=True)
                        clicked = True
                        break
                if clicked:
                    break
            except Exception as e:
                print("Click attempt failed:", repr(e))

        if not clicked:
            # Last-resort DOM click on an element whose visible/textual attributes mention scheme.
            try:
                clicked = await page.evaluate("""
                () => {
                    const all = [...document.querySelectorAll('*')];
                    const el = all.find(e =>
                        /know\\s+your\\s+scheme/i.test((e.innerText || '').trim()) &&
                        e.children.length === 0
                    );
                    if (!el) return false;
                    el.click();
                    return true;
                }
                """)
                print("DOM fallback clicked:", clicked)
            except Exception as e:
                print("DOM fallback failed:", repr(e))

        if clicked:
            await page.wait_for_timeout(8000)

        # Save post-click page regardless of whether click worked.
        (OUT / "03-after-click.html").write_text(await page.content(), encoding="utf-8")
        await page.screenshot(path=str(OUT / "03-after-click.png"), full_page=True)

        # Inspect forms/selects and all links on the resulting page.
        page_data = {
            "final_url": page.url,
            "title": await page.title(),
            "clicked_know_your_scheme": clicked,
            "selects": await page.locator("select").evaluate_all("""
                els => els.map((s, i) => ({
                    i,
                    id: s.id,
                    name: s.name,
                    value: s.value,
                    options: [...s.options].map(o => ({text:o.text, value:o.value}))
                }))
            """),
            "links": await page.locator("a").evaluate_all("""
                els => els.map((a, i) => ({
                    i,
                    text: (a.innerText || '').trim(),
                    href: a.href || '',
                    onclick: a.getAttribute('onclick') || ''
                }))
            """),
            "buttons": await page.locator("button,input[type=button],input[type=submit]").evaluate_all("""
                els => els.map((b, i) => ({
                    i,
                    tag: b.tagName,
                    type: b.type || '',
                    text: (b.innerText || b.value || '').trim(),
                    id: b.id || '',
                    name: b.name || '',
                    onclick: b.getAttribute('onclick') || ''
                }))
            """)
        }
        (OUT / "04-page-structure.json").write_text(
            json.dumps(page_data, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        (OUT / "05-network.json").write_text(
            json.dumps({"requests": requests, "responses": responses}, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        (OUT / "06-console.json").write_text(
            json.dumps(console, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        print("Final URL:", page.url)
        print("Clicked Know your scheme:", clicked)
        print("Saved HTML, screenshots, page structure and network logs.")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
  
