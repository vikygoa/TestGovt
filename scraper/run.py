import asyncio, json, re
from pathlib import Path
from playwright.async_api import async_playwright

URL="https://goaonline.gov.in/Appln/Uil/DeptServices?__DocId=IFT&__ServiceId=IFT47"
OUT=Path("output"); OUT.mkdir(exist_ok=True)

async def snap(page,name):
    (OUT/f"{name}.html").write_text(await page.content(),encoding="utf-8")
    await page.screenshot(path=str(OUT/f"{name}.png"),full_page=True)

async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True)
        context=await browser.new_context(
            viewport={"width":1440,"height":1000},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36"
        )
        page=await context.new_page()

        ajax=[]
        responses=[]
        page.on("request",lambda r: (
            ajax.append({
                "method":r.method,"url":r.url,"type":r.resource_type,
                "post_data":r.post_data
            }) if r.resource_type in ("xhr","fetch") else None
        ))
        async def on_response(r):
            if r.request.resource_type in ("xhr","fetch"):
                item={"status":r.status,"url":r.url,"type":r.request.resource_type}
                try:
                    body=await r.text()
                    item["body"]=body[:500000]
                except Exception as e:
                    item["body_error"]=repr(e)
                responses.append(item)
        page.on("response",on_response)

        print("Opening direct Know Your Scheme URL...")
        await page.goto(URL,wait_until="domcontentloaded",timeout=120000)
        await page.wait_for_timeout(5000)
        print("Initial URL:",page.url)
        await snap(page,"01-initial")

        # Confirm the actual controls discovered in the supplied debug.
        print("Sector select:",await page.locator("#id20").count())
        print("Display button:",await page.locator("#id2d").count())

        # Sectorwise radio is radio22 on this page.
        try:
            await page.locator("#id42").check(force=True)
        except Exception:
            pass

        # Select Educational by the real Wicket select ID.
        sector=page.locator("#id20")
        await sector.select_option("7")
        await sector.dispatch_event("change")
        await page.wait_for_timeout(3000)

        print("Sector selected value:",await sector.input_value())
        await snap(page,"02-educational-selected")

        # The Wicket handler is attached to id2d. Click the actual button.
        before=await page.locator("body").inner_text()
        print("Clicking Display Schemes...")
        await page.locator("#id2d").click(force=True,timeout=30000)

        # Wicket AJAX can take time; wait for the loading modal to disappear and
        # for the result area/body to change.
        for n in range(20):
            await page.wait_for_timeout(1000)
            after=await page.locator("body").inner_text()
            if after != before and (
                "schemes found" in after.lower()
                or "scheme" in after.lower()
                or len(after) > len(before)+100
            ):
                print("Page changed after",n+1,"seconds")
                break

        await page.wait_for_timeout(3000)
        print("Final URL:",page.url)
        await snap(page,"03-after-display")

        body=(await page.locator("body").inner_text())[:300000]
        links=await page.locator("a").evaluate_all("""
        els=>els.map((a,i)=>({
          index:i,
          text:(a.innerText||'').trim(),
          href:a.href||'',
          onclick:a.getAttribute('onclick')||'',
          id:a.id||''
        })).filter(x=>x.text||x.href)
        """)

        # Extract visible text blocks around likely scheme cards.
        result={
            "url":page.url,
            "sector_value":await sector.input_value(),
            "sector_text":await sector.locator("option:checked").text_content(),
            "body_text":body,
            "links":links,
            "ajax_requests":ajax,
            "ajax_responses":responses,
        }
        (OUT/"04-result.json").write_text(
            json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8"
        )

        # Save a compact list of likely PDF/document links. We never request them.
        docs=[x for x in links if re.search(
            r'\.pdf($|[?#])|notification|application|declaration|download|form',
            (x.get("href","")+" "+x.get("text","")),re.I
        )]
        (OUT/"05-document-links.json").write_text(
            json.dumps(docs,indent=2,ensure_ascii=False),encoding="utf-8"
        )

        print("Likely document links found:",len(docs))
        print("AJAX responses captured:",len(responses))
        print("DONE")
        await browser.close()

asyncio.run(main())
