import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

START_URL = "https://services.goaonline.gov.in/ServiceWizard"
OUT = Path("output")
OUT.mkdir(exist_ok=True)

async def snapshot(page, name):
    try:
        (OUT / f"{name}.html").write_text(await page.content(), encoding="utf-8")
        await page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
    except Exception as e:
        print("Snapshot failed:", name, repr(e))

async def element_summary(page):
    data = {}
    try:
        data["url"] = page.url
        data["title"] = await page.title()
    except Exception as e:
        data["basic_error"] = repr(e)

    try:
        data["selects"] = await page.locator("select").evaluate_all(
            """els => els.map((s,i) => ({
                index:i, id:s.id, name:s.name, value:s.value,
                options:Array.from(s.options).map(o => ({
                    text:(o.textContent || '').trim(), value:o.value
                }))
            }))"""
        )
    except Exception as e:
        data["selects_error"] = repr(e)

    try:
        data["buttons"] = await page.locator(
            "button,input[type=button],input[type=submit],a"
        ).evaluate_all(
            """els => els.map((e,i) => ({
                index:i, tag:e.tagName,
                text:(e.innerText || e.value || '').trim(),
                href:e.href || '',
                id:e.id || '',
                name:e.name || '',
                onclick:e.getAttribute('onclick') || ''
            })).filter(x => x.text || x.href || x.onclick)"""
        )
    except Exception as e:
        data["buttons_error"] = repr(e)

    try:
        data["radios"] = await page.locator(
            "input[type=radio]"
        ).evaluate_all(
            """els => els.map((e,i) => ({
                index:i, id:e.id, name:e.name, value:e.value,
                checked:e.checked,
                parent:(e.parentElement?.innerText || '').trim()
            }))"""
        )
    except Exception as e:
        data["radios_error"] = repr(e)

    return data

async def click_text(page, text, exact=False):
    loc = page.get_by_text(text, exact=exact)
    count = await loc.count()
    print(f'Found "{text}":', count)
    for i in range(count):
        el = loc.nth(i)
        try:
            if await el.is_visible():
                await el.scroll_into_view_if_needed()
                await el.click(force=True, timeout=15000)
                return True
        except Exception as e:
            print(f'Normal click {text} #{i} failed:', repr(e))
    return False

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
            "resource_type": r.resource_type, "post_data": r.post_data
        }))
        page.on("response", lambda r: events["responses"].append({
            "status": r.status, "url": r.url,
            "resource_type": r.request.resource_type
        }))
        page.on("console", lambda m: events["console"].append({
            "type": m.type, "text": m.text
        }))

        print("OPEN ServiceWizard")
        await page.goto(START_URL, wait_until="domcontentloaded", timeout=120000)
        await page.wait_for_timeout(4000)
        await snapshot(page, "01-servicewizard")

        print("CLICK Know your scheme")
        know_clicked = await click_text(page, "Know your scheme", exact=False)
        if not know_clicked:
            know_clicked = await page.evaluate("""
                () => {
                    const els=[...document.querySelectorAll('*')];
                    const e=els.find(x =>
                      (x.innerText||'').trim().toLowerCase()==='know your scheme'
                    );
                    if (!e) return false;
                    e.click(); return true;
                }
            """)
        await page.wait_for_timeout(5000)
        print("Know clicked:", know_clicked)
        print("URL:", page.url)
        await snapshot(page, "02-know-your-scheme")

        data = await element_summary(page)
        (OUT/"03-know-page-structure.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        print("CLICK Proceed to View")
        proceed_clicked = await click_text(page, "Proceed to View", exact=False)

        if not proceed_clicked:
            # Search every clickable element by its own text.
            proceed_clicked = await page.evaluate("""
                () => {
                    const els=[...document.querySelectorAll(
                      'a,button,input[type=button],input[type=submit],span'
                    )];
                    const e=els.find(x =>
                      /proceed\\s+to\\s+view/i.test(
                        (x.innerText || x.value || '').trim()
                      )
                    );
                    if (!e) return false;
                    e.click(); return true;
                }
            """)

        await page.wait_for_timeout(8000)
        print("Proceed clicked:", proceed_clicked)
        print("URL after Proceed:", page.url)
        await snapshot(page, "04-after-proceed")

        data = await element_summary(page)
        (OUT/"05-after-proceed-structure.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Find the sector dropdown without assuming there is only one select.
        sector_index = None
        selects = page.locator("select")
        count = await selects.count()
        print("Select count:", count)

        for i in range(count):
            try:
                texts = await selects.nth(i).locator("option").all_text_contents()
                joined = " | ".join(x.strip() for x in texts)
                print("Select", i, "options:", joined[:500])
                if any(
                    x.strip().lower() in ("educational", "education")
                    for x in texts
                ):
                    sector_index = i
                    break
            except Exception as e:
                print("Select inspection failed", i, repr(e))

        selected = False
        if sector_index is not None:
            sel = selects.nth(sector_index)
            opts = await sel.locator("option").evaluate_all(
                """opts => opts.map(o => ({
                    text:(o.textContent||'').trim(), value:o.value
                }))"""
            )
            target = next(
                (o for o in opts if o["text"].lower() in ("educational","education")),
                None
            )
            if target:
                try:
                    await sel.select_option(target["value"])
                    selected = True
                except Exception as e:
                    print("select_option failed:", repr(e))
            await page.wait_for_timeout(1500)

        print("Educational selected:", selected)
        await snapshot(page, "06-after-educational")

        print("CLICK Display Schemes")
        display_clicked = await click_text(page, "Display Schemes", exact=False)
        if not display_clicked:
            display_clicked = await page.evaluate("""
                () => {
                    const els=[...document.querySelectorAll(
                      'a,button,input[type=button],input[type=submit],span'
                    )];
                    const e=els.find(x =>
                      /display\\s+schemes/i.test(
                        (x.innerText || x.value || '').trim()
                      )
                    );
                    if (!e) return false;
                    e.click(); return true;
                }
            """)

        await page.wait_for_timeout(8000)
        print("Display clicked:", display_clicked)
        print("FINAL URL:", page.url)
        await snapshot(page, "07-after-display")

        final_data = await element_summary(page)
        try:
            final_data["body_text"] = (await page.locator("body").inner_text())[:150000]
        except Exception as e:
            final_data["body_text_error"] = repr(e)

        try:
            final_data["scheme_links"] = await page.locator("a").evaluate_all(
                """els => els.map((a,i)=>({
                    index:i, text:(a.innerText||'').trim(),
                    href:a.href||'', onclick:a.getAttribute('onclick')||''
                })).filter(x => /scheme|apply|notification|application|form|eligib|benefit|document/i.test(
                    [x.text,x.href,x.onclick].join(' ')
                ))"""
            )
        except Exception as e:
            final_data["scheme_links_error"] = repr(e)

        final_data["know_clicked"] = know_clicked
        final_data["proceed_clicked"] = proceed_clicked
        final_data["sector_index"] = sector_index
        final_data["educational_selected"] = selected
        final_data["display_clicked"] = display_clicked

        (OUT/"08-final-results.json").write_text(
            json.dumps(final_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (OUT/"09-network.json").write_text(
            json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        print("DONE - debug files saved")
        await browser.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print("FATAL ERROR:", repr(e))
        raise
