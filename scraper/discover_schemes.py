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
        ctx=await browser.new_context(
            viewport={"width":1440,"height":1000},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36"
        )
        page=await ctx.new_page()
        events={"requests":[],"responses":[]}
        page.on("request",lambda r:events["requests"].append({
            "method":r.method,"url":r.url,"type":r.resource_type,"post_data":r.post_data
        }))
        page.on("response",lambda r:events["responses"].append({
            "status":r.status,"url":r.url,"type":r.request.resource_type
        }))

        print("OPENING DIRECT KNOW YOUR SCHEME PAGE")
        await page.goto(URL,wait_until="domcontentloaded",timeout=120000)
        await page.wait_for_timeout(5000)
        print("URL:",page.url)
        await snap(page,"01-direct-page")

        controls=await page.locator("a,button,input,select").evaluate_all("""
        els=>els.map((e,i)=>({
          i,tag:e.tagName,text:(e.innerText||e.value||e.textContent||'').trim(),
          value:e.value||'',href:e.href||'',id:e.id||'',name:e.name||'',
          type:e.type||'',onclick:e.getAttribute('onclick')||''
        })).filter(x=>/proceed|view|scheme|sector|display/i.test(
          [x.text,x.value,x.href,x.id,x.name,x.onclick].join(' ')
        ))
        """)
        (OUT/"02-controls.json").write_text(json.dumps(controls,indent=2,ensure_ascii=False),encoding="utf-8")

        # Click the actual Proceed to View control.
        clicked=False
        for i in range(await page.locator("a,button,input").count()):
            el=page.locator("a,button,input").nth(i)
            try:
                txt=((await el.inner_text()) if await el.evaluate("(e)=>e.tagName==='A'||e.tagName==='BUTTON'") else await el.get_attribute("value")) or ""
                if re.search(r"proceed\s*to\s*view",txt,re.I) and await el.is_visible():
                    print("CLICKING:",txt)
                    await el.click(force=True,timeout=20000)
                    clicked=True
                    break
            except Exception:
                pass
        if not clicked:
            # fallback: locate by text
            loc=page.get_by_text("Proceed to View",exact=False)
            for i in range(await loc.count()):
                try:
                    if await loc.nth(i).is_visible():
                        await loc.nth(i).click(force=True,timeout=20000)
                        clicked=True; break
                except Exception: pass

        await page.wait_for_timeout(8000)
        print("PROCEED CLICKED:",clicked)
        print("AFTER PROCEED URL:",page.url)
        await snap(page,"03-after-proceed")

        # Inspect actual sector page.
        selects=await page.locator("select").evaluate_all("""
        els=>els.map((s,i)=>({
          i,id:s.id,name:s.name,value:s.value,
          options:[...s.options].map(o=>({text:(o.textContent||'').trim(),value:o.value}))
        }))
        """)
        (OUT/"04-selects.json").write_text(json.dumps(selects,indent=2,ensure_ascii=False),encoding="utf-8")

        buttons=await page.locator("a,button,input").evaluate_all("""
        els=>els.map((e,i)=>({
          i,tag:e.tagName,text:(e.innerText||e.value||'').trim(),
          value:e.value||'',href:e.href||'',id:e.id||'',name:e.name||'',type:e.type||'',
          onclick:e.getAttribute('onclick')||''
        }))
        """)
        (OUT/"05-after-proceed-controls.json").write_text(json.dumps(buttons,indent=2,ensure_ascii=False),encoding="utf-8")

        # Select Educational in the sector dropdown.
        sector=None
        for i,s in enumerate(selects):
            opts=s.get("options",[])
            if any(("educational" in o["text"].lower() or o["text"].lower()=="education") for o in opts):
                sector=i; break
        selected=False
        if sector is not None:
            loc=page.locator("select").nth(sector)
            opts=await loc.locator("option").evaluate_all("opts=>opts.map(o=>({text:(o.textContent||'').trim(),value:o.value}))")
            target=next((o for o in opts if o["text"].lower() in ("educational","education")),None)
            if target:
                await loc.select_option(target["value"])
                selected=True
        print("SECTOR INDEX:",sector,"EDUCATIONAL SELECTED:",selected)
        await page.wait_for_timeout(1500)
        await snap(page,"06-after-educational")

        # Click Display Schemes.
        display=False
        for i in range(await page.locator("a,button,input").count()):
            el=page.locator("a,button,input").nth(i)
            try:
                tag=await el.evaluate("(e)=>e.tagName")
                txt=((await el.inner_text()) if tag in ("A","BUTTON") else await el.get_attribute("value")) or ""
                if re.search(r"display\s*schemes",txt,re.I) and await el.is_visible():
                    await el.click(force=True,timeout=20000)
                    display=True; break
            except Exception: pass
        await page.wait_for_timeout(8000)
        print("DISPLAY CLICKED:",display,"FINAL URL:",page.url)
        await snap(page,"07-after-display")

        final={
          "url":page.url,
          "title":await page.title(),
          "proceed_clicked":clicked,
          "sector_index":sector,
          "educational_selected":selected,
          "display_clicked":display,
          "body_text":(await page.locator("body").inner_text())[:200000],
          "links":await page.locator("a").evaluate_all("""
            els=>els.map((a,i)=>({
              i,text:(a.innerText||'').trim(),href:a.href||'',onclick:a.getAttribute('onclick')||''
            })).filter(x=>x.text||x.href)
          """)
        }
        (OUT/"08-final.json").write_text(json.dumps(final,indent=2,ensure_ascii=False),encoding="utf-8")
        (OUT/"09-network.json").write_text(json.dumps(events,indent=2,ensure_ascii=False),encoding="utf-8")
        await browser.close()

asyncio.run(main())
