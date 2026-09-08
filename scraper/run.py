import asyncio, json, re
from pathlib import Path
from playwright.async_api import async_playwright

START="https://services.goaonline.gov.in/ServiceWizard"
OUT=Path("output"); OUT.mkdir(exist_ok=True)

async def snap(page,name):
    (OUT/f"{name}.html").write_text(await page.content(),encoding="utf-8")
    await page.screenshot(path=str(OUT/f"{name}.png"),full_page=True)

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        ctx=await browser.new_context(
            viewport={"width":1440,"height":1100},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36"
        )
        page=await ctx.new_page()
        ajax=[]
        page.on("request",lambda r: ajax.append({
            "method":r.method,"url":r.url,"type":r.resource_type,
            "post_data":r.post_data
        }) if r.resource_type in ("xhr","fetch") else None)

        await page.goto(START,wait_until="domcontentloaded",timeout=120000)
        await page.wait_for_timeout(4000)
        await snap(page,"01-servicewizard")

        # IMPORTANT: "Know your scheme" is a Wicket AJAX component.
        # The visible text is inside span #idf; its parent <a> is the actual clickable component.
        know=page.locator("#idf")
        if await know.count()==0:
            raise RuntimeError("Wicket component #idf was not found on ServiceWizard")

        parent=know.locator("xpath=ancestor::a[1]")
        print("Know parent count:",await parent.count())
        print("Know parent html:",(await parent.first.evaluate("(e)=>e.outerHTML"))[:3000])

        before_url=page.url
        await parent.first.scroll_into_view_if_needed()
        await parent.first.click(force=True,timeout=30000)

        # Wait for the Wicket AJAX update to finish.
        for _ in range(20):
            await page.wait_for_timeout(1000)
            if page.url!=before_url or await page.locator("select").count()>0:
                break

        await page.wait_for_timeout(3000)
        print("URL after Know:",page.url)
        await snap(page,"02-after-know")

        info={
          "url":page.url,
          "select_count":await page.locator("select").count(),
          "radio_count":await page.locator("input[type=radio]").count(),
          "body_text":(await page.locator("body").inner_text())[:100000]
        }
        (OUT/"03-after-know-info.json").write_text(json.dumps(info,indent=2,ensure_ascii=False),encoding="utf-8")

        # The AJAX response may replace content without changing the URL.
        # Find the sector select by option content.
        sels=page.locator("select")
        sector=None
        for i in range(await sels.count()):
            opts=await sels.nth(i).locator("option").all_text_contents()
            low=[x.strip().lower() for x in opts]
            if "educational" in low and "unemployment" in low:
                sector=sels.nth(i); print("Sector found at index",i); break

        if sector is None:
            # Save all select outerHTML for diagnosis.
            allhtml=await sels.evaluate_all("els=>els.map(e=>e.outerHTML)")
            (OUT/"04-no-sector.json").write_text(json.dumps(allhtml,indent=2),encoding="utf-8")
            raise RuntimeError("Sector dropdown not found after clicking Know your scheme")

        await sector.select_option(label="Educational")
        await sector.dispatch_event("change")
        await page.wait_for_timeout(2500)
        await snap(page,"04-educational")

        # Find Display Schemes by visible text.
        display=page.get_by_text("Display Schemes",exact=True)
        if await display.count()==0:
            # fallback to input/button whose value/text contains it
            display=page.locator("button,input[type=button],input[type=submit]").filter(
                has_text="Display Schemes"
            )
        if await display.count()==0:
            raise RuntimeError("Display Schemes button not found")

        # Click and wait for Wicket AJAX/network activity.
        before=(await page.locator("body").inner_text())
        await display.first.scroll_into_view_if_needed()
        await display.first.click(force=True,timeout=30000)
        await page.wait_for_timeout(8000)
        await snap(page,"05-results")

        links=await page.locator("a").evaluate_all("""
        els=>els.map((a,i)=>({
          i,text:(a.innerText||'').trim(),href:a.href||'',
          onclick:a.getAttribute('onclick')||''
        })).filter(x=>x.text||x.href)
        """)
        docs=[x for x in links if re.search(
            r'pdf|notification|application|declaration|download|form|document',
            x["text"]+" "+x["href"]+" "+x["onclick"],re.I
        )]

        result={
          "final_url":page.url,
          "body_changed":(await page.locator("body").inner_text())!=before,
          "body_text":(await page.locator("body").inner_text())[:300000],
          "links":links,
          "document_links":docs,
          "ajax_requests":ajax
        }
        (OUT/"06-result.json").write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
        print("FINAL:",page.url)
        print("Documents:",len(docs))
        print("AJAX:",len(ajax))
        await browser.close()

asyncio.run(main())
