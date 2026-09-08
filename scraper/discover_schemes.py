import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

START_URL = "https://services.goaonline.gov.in/ServiceWizard"
OUT = Path("output")
OUT.mkdir(exist_ok=True)

async def save_page(page, name):
    (OUT / f"{name}.html").write_text(await page.content(), encoding="utf-8")
    await page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)

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

        events = {"requests": [], "responses": [], "console": []}
        page.on("request", lambda r: events["requests"].append({
            "method": r.method, "url": r.url,
            "type": r.resource_type, "post_data": r.post_data
        }))
        page.on("response", lambda r: events["responses"].append({
            "status": r.status, "url": r.url,
            "type": r.request.resource_type
        }))
        page.on("console", lambda m: events["console"].append({
            "type": m.type, "text": m.text
        }))

        print("1. Opening ServiceWizard...")
        await page.goto(START_URL, wait_until="domcontentloaded", timeout=120000)
        await page.wait_for_timeout(4000)
        await save_page(page, "01-servicewizard")

        # Click Know your scheme.
        print("2. Finding Know your scheme...")
        candidates = page.get_by_text("Know your scheme", exact=False)
        clicked = False
        for i in range(await candidates.count()):
            el = candidates.nth(i)
            try:
                if await el.is_visible():
                    await el.scroll_into_view_if_needed()
                    await el.click(force=True, timeout=15000)
                    clicked = True
                    break
            except Exception:
                pass

        await page.wait_for_timeout(5000)
        print("Know your scheme clicked:", clicked)
        print("Current URL:", page.url)
        await save_page(page, "02-know-your-scheme")

        # Save buttons/links for the DeptServices page.
        structure = {
            "url": page.url,
            "title": await page.title(),
            "buttons": await page.locator(
                "button,input[type=button],input[type=submit],a"
            ).evaluate_all("""
                els => els.map((e,i)=>({
                    i,
                    tag:e.tagName,
                    text:(e.innerText || e.value || '').trim(),
                    href:e.href || '',
                    id:e.id || '',
                    name:e.name || '',
                    onclick:e.getAttribute('onclick') || ''
                })).filter(x => /proceed|view|scheme/i.test(
                    [x.text,x.href,x.id,x.name,x.onclick].join(' ')
                ))
            """)
        }
        (OUT / "03-proceed-elements.json").write_text(
            json.dumps(structure, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Click Proceed to View. This is the critical step.
        print("3. Clicking Proceed to View...")
        proceed = page.get_by_text("Proceed to View", exact=False)
        proceed_clicked = False
        for i in range(await proceed.count()):
            el = proceed.nth(i)
            try:
                if await el.is_visible():
                    await el.scroll_into_view_if_needed()
                    await el.click(force=True, timeout=20000)
                    proceed_clicked = True
                    break
            except Exception:
                pass

        await page.wait_for_timeout(7000)
        print("Proceed clicked:", proceed_clicked)
        print("URL after Proceed:", page.url)
        await save_page(page, "04-after-proceed")

        # Inspect the actual scheme page.
        actual = {
            "url": page.url,
            "title": await page.title(),
            "selects": await page.locator("select").evaluate_all("""
                els => els.map((s,i)=>({
                    i,id:s.id,name:s.name,value:s.value,
                    options:[...s.options].map(o=>({
                        text:(o.textContent||'').trim(), value:o.value
                    }))
                }))
            """),
            "radios": await page.locator("input[type=radio]").evaluate_all("""
                els => els.map((e,i)=>({
                    i,id:e.id,name:e.name,value:e.value,
                    checked:e.checked,
                    label:(e.parentElement?.innerText||'').trim()
                }))
            """),
            "buttons": await page.locator(
                "button,input[type=button],input[type=submit],a"
            ).evaluate_all("""
                els => els.map((e,i)=>({
                    i,tag:e.tagName,
                    text:(e.innerText||e.value||'').trim(),
                    href:e.href||'',id:e.id||'',name:e.name||'',
                    onclick:e.getAttribute('onclick')||''
                })).filter(x => x.text)
            """),
            "links": await page.locator("a").evaluate_all("""
                els => els.map((a,i)=>({
                    i,text:(a.innerText||'').trim(),
                    href:a.href||'',onclick:a.getAttribute('onclick')||''
                }))
            """)
        }
        (OUT / "05-actual-scheme-page.json").write_text(
            json.dumps(actual, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Automatically try sectorwise + Educational, but only if a matching select exists.
        selected = False
        display_clicked = False

        for i in range(await page.locator("select").count()):
            sel = page.locator("select").nth(i)
            options = await sel.locator("option").all_text_contents()
            if any("Educational" in x or "Education" in x for x in options):
                print("4. Found sector dropdown:", i)
                try:
                    # Prefer Educational for a controlled test.
                    vals = await sel.locator("option").evaluate_all(
                        "opts => opts.map(o => ({text:(o.textContent||'').trim(), value:o.value}))"
                    )
                    target = next(
                        (x for x in vals if "educational" in x["text"].lower()
                         or x["text"].lower() == "education"),
                        None
                    )
                    if target:
                        await sel.select_option(target["value"])
                    else:
                        await sel.select_option(label="Educational")
                    selected = True
                    await page.wait_for_timeout(1000)
                    break
                except Exception as e:
                    print("Select error:", repr(e))

        # Save after sector selection.
        await save_page(page, "06-after-educational-selection")

        # Click Display Schemes.
        display = page.get_by_text("Display Schemes", exact=False)
        for i in range(await display.count()):
            el = display.nth(i)
            try:
                if await el.is_visible():
                    await el.scroll_into_view_if_needed()
                    await el.click(force=True, timeout=20000)
                    display_clicked = True
                    break
            except Exception:
                pass

        await page.wait_for_timeout(8000)
        print("Educational selected:", selected)
        print("Display Schemes clicked:", display_clicked)
        print("Final URL:", page.url)
        await save_page(page, "07-after-display-schemes")

        # Capture all visible scheme-ish text and links.
        result = {
            "final_url": page.url,
            "title": await page.title(),
            "educational_selected": selected,
            "display_schemes_clicked": display_clicked,
            "scheme_links": await page.locator("a").evaluate_all("""
                els => els.map((a,i)=>({
                    i,text:(a.innerText||'').trim(),
                    href:a.href||'',onclick:a.getAttribute('onclick')||''
                })).filter(x => /scheme|apply|notification|application|form|benefit|eligib/i.test(
                    [x.text,x.href,x.onclick].join(' ')
                ))
            """),
            "visible_text": (await page.locator("body").inner_text())[:100000]
        }
        (OUT / "08-scheme-results.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        (OUT / "09-network.json").write_text(
            json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
