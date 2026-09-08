import asyncio, json, re
from pathlib import Path
from playwright.async_api import async_playwright

URL="https://goaonline.gov.in/Appln/Uil/DeptServices?__DocId=IFT&__ServiceId=IFT47"
OUT=Path("output"); OUT.mkdir(exist_ok=True)

async def save(page,name):
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

        network=[]
        async def response(r):
            if r.request.resource_type in ("xhr","fetch"):
                item={"method":r.request.method,"url":r.url,"status":r.status}
                try: item["post_data"]=r.request.post_data
                except: pass
                try: item["body"]=(await r.text())[:1000000]
                except Exception as e: item["body_error"]=repr(e)
                network.append(item)
        page.on("response",response)

        await page.goto(URL,wait_until="domcontentloaded",timeout=120000)
        await page.wait_for_timeout(5000)
        await save(page,"01-open")

        # Capture every select with its options, regardless of generated ID.
        selects=await page.locator("select").evaluate_all("""
        els=>els.map((s,i)=>({
          index:i,id:s.id,name:s.name,value:s.value,
          aria:s.getAttribute('aria-label')||'',
          parentText:(s.parentElement?.innerText||'').trim().slice(0,1000),
          options:[...s.options].map(o=>({text:(o.textContent||'').trim(),value:o.value}))
        }))
        """)
        (OUT/"02-all-selects.json").write_text(json.dumps(selects,indent=2,ensure_ascii=False),encoding="utf-8")

        # Find sector dropdown by its options, not its Wicket ID.
        sector=None
        for i,s in enumerate(selects):
            texts=[o["text"].lower() for o in s["options"]]
            score=sum(x in texts for x in [
                "educational","health and family welfare","senior citizens",
                "unemployment","agriculture","transport","business"
            ])
            if score>=2:
                sector=i
                break

        if sector is None:
            # Save diagnostic text if the dropdown is absent.
            (OUT/"03-error.txt").write_text(
                "Could not identify sector dropdown by options.\n"+json.dumps(selects,indent=2),
                encoding="utf-8"
            )
            await save(page,"03-sector-not-found")
            print("SECTOR DROPDOWN NOT FOUND")
            print("Selects:",len(selects))
            await browser.close()
            return

        print("Sector dropdown index:",sector)
        sel=page.locator("select").nth(sector)
        opts=await sel.locator("option").evaluate_all(
            "opts=>opts.map(o=>({text:(o.textContent||'').trim(),value:o.value}))"
        )
        educational=next((o for o in opts if o["text"].strip().lower() in ("educational","education")),None)
        if not educational:
            raise RuntimeError("Educational option not found")

        await sel.select_option(educational["value"])
        await sel.dispatch_event("change")
        await page.wait_for_timeout(2500)
        await save(page,"04-educational-selected")

        # Find Display Schemes by visible/value text, not ID.
        controls=page.locator("button,input[type=button],input[type=submit],a")
        display=None
        for i in range(await controls.count()):
            el=controls.nth(i)
            try:
                tag=await el.evaluate("(e)=>e.tagName")
                txt=(await el.inner_text()) if tag in ("BUTTON","A") else (await el.get_attribute("value") or "")
                if re.search(r"^\s*display\s+schemes\s*$",txt,re.I) and await el.is_visible():
                    display=el
                    break
            except: pass

        if display is None:
            raise RuntimeError("Display Schemes control not found")

        before=(await page.locator("body").inner_text())
        await display.click(force=True,timeout=30000)

        # Wait up to 20 sec for Wicket AJAX/result update.
        changed=False
        for _ in range(20):
            await page.wait_for_timeout(1000)
            after=(await page.locator("body").inner_text())
            if after != before:
                changed=True
                break

        await page.wait_for_timeout(3000)
        await save(page,"05-results")

        links=await page.locator("a").evaluate_all("""
        els=>els.map((a,i)=>({
          index:i,text:(a.innerText||'').trim(),href:a.href||'',
          onclick:a.getAttribute('onclick')||'',id:a.id||''
        })).filter(x=>x.text||x.href)
        """)

        # Extract likely document links WITHOUT requesting them.
        documents=[x for x in links if re.search(
            r'pdf|notification|application|declaration|download|form|document',
            (x["text"]+" "+x["href"]+" "+x["onclick"]),re.I
        )]

        result={
            "url":page.url,
            "sector_index":sector,
            "sector_id":await sel.get_attribute("id"),
            "sector_name":await sel.get_attribute("name"),
            "educational_value":educational["value"],
            "display_clicked":True,
            "body_changed":changed,
            "body_text":(await page.locator("body").inner_text())[:300000],
            "all_links":links,
            "document_links":documents,
            "network":network
        }
        (OUT/"06-final.json").write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
        print("SUCCESS")
        print("Final URL:",page.url)
        print("Body changed:",changed)
        print("Document-like links:",len(documents))
        print("AJAX responses:",len(network))
        await browser.close()

asyncio.run(main())
