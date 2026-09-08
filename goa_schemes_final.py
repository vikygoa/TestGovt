import asyncio, json, re
from pathlib import Path
from urllib.parse import urljoin
from playwright.async_api import async_playwright

START_URL = "https://services.goaonline.gov.in/ServiceWizard"
OUT = Path("output")
OUT.mkdir(exist_ok=True)

def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1100},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        print("1) Opening ServiceWizard")
        await page.goto(START_URL, wait_until="domcontentloaded", timeout=120000)
        await page.wait_for_timeout(4000)

        # The actual menu item is the span whose text is "Know your scheme".
        # We deliberately click the element found by text, not a generated Wicket ID.
        know = page.get_by_text("Know your scheme", exact=True)
        if await know.count() == 0:
            know = page.get_by_text("Know your scheme", exact=False)
        if await know.count() == 0:
            raise RuntimeError("Could not find 'Know your scheme' on ServiceWizard")

        print("2) Clicking Know your scheme")
        clicked = False
        for i in range(await know.count()):
            try:
                el = know.nth(i)
                if await el.is_visible():
                    await el.scroll_into_view_if_needed()
                    await el.click(force=True, timeout=20000)
                    clicked = True
                    break
            except Exception as e:
                print("click attempt:", repr(e))

        # Some Wicket versions attach the behavior to the parent anchor.
        if not clicked:
            clicked = await page.evaluate("""
                () => {
                    const els=[...document.querySelectorAll('*')];
                    const span=els.find(e =>
                      (e.textContent||'').trim().toLowerCase()==='know your scheme'
                    );
                    if (!span) return false;
                    const a=span.closest('a');
                    (a || span).click();
                    return true;
                }
            """)

        await page.wait_for_timeout(8000)
        print("After Know URL:", page.url)

        if "DeptServices" not in page.url:
            # A last attempt using the exact known menu span.
            await page.evaluate("""
                () => {
                    const els=[...document.querySelectorAll('span')];
                    const e=els.find(x => (x.textContent||'').trim().toLowerCase()==='know your scheme');
                    if (e) (e.closest('a') || e).click();
                }
            """)
            await page.wait_for_timeout(8000)

        if "DeptServices" not in page.url:
            raise RuntimeError("Know Your Scheme did not open the DeptServices page. Current URL: "+page.url)

        (OUT/"know-your-scheme.html").write_text(await page.content(), encoding="utf-8")

        print("3) Finding sector dropdown by its OPTIONS")
        selects = page.locator("select")
        sector = None
        for i in range(await selects.count()):
            opts = await selects.nth(i).locator("option").evaluate_all(
                "opts => opts.map(o => ({text:(o.textContent||'').trim(), value:o.value}))"
            )
            texts = {o["text"].lower() for o in opts}
            score = sum(x in texts for x in [
                "educational", "education", "unemployment",
                "agriculture", "transport", "business",
                "senior citizens", "health and family welfare"
            ])
            if score >= 3:
                sector = selects.nth(i)
                break

        if sector is None:
            raise RuntimeError("Could not find the sector dropdown")

        options = await sector.locator("option").evaluate_all(
            "opts => opts.map(o => ({text:(o.textContent||'').trim(), value:o.value}))"
        )

        # Ignore placeholder/non-sector options.
        sectors=[]
        for o in options:
            t=clean(o["text"])
            if not t or t.lower() in ("select","select sector","--select--","please select"):
                continue
            if t.lower() in ("sectorwise schemes", "schemes applicable to me"):
                continue
            sectors.append(o)

        print("Sectors found:", len(sectors))
        print([x["text"] for x in sectors])

        all_results=[]
        seen_sector_names=set()

        for n, opt in enumerate(sectors, 1):
            name=opt["text"]
            if name.lower() in seen_sector_names:
                continue
            seen_sector_names.add(name.lower())

            print(f"\n4) [{n}/{len(sectors)}] {name}")

            # Re-locate the sector dropdown each time because Wicket AJAX can replace it.
            current_selects=page.locator("select")
            current=None
            for i in range(await current_selects.count()):
                opts2=await current_selects.nth(i).locator("option").all_text_contents()
                low=[clean(x).lower() for x in opts2]
                if "educational" in low and "unemployment" in low:
                    current=current_selects.nth(i)
                    break

            if current is None:
                print("Sector dropdown disappeared; stopping.")
                break

            # Find the same option by text. Value may change between AJAX renders.
            target=current.locator("option").filter(has_text=re.compile("^"+re.escape(name)+"$", re.I))
            if await target.count()==0:
                print("Option not found:",name)
                continue

            try:
                await current.select_option(label=name)
                await current.dispatch_event("change")
            except Exception as e:
                print("Select failed:",repr(e))
                continue

            await page.wait_for_timeout(2500)

            # Find Display Schemes by visible/value text.
            controls=page.locator("button,input[type=button],input[type=submit],a")
            display=None
            for i in range(await controls.count()):
                el=controls.nth(i)
                try:
                    if not await el.is_visible():
                        continue
                    tag=await el.evaluate("(e)=>e.tagName")
                    txt=(await el.inner_text()) if tag in ("BUTTON","A") else (await el.get_attribute("value") or "")
                    if re.search(r"^\s*display\s+schemes\s*$",txt,re.I):
                        display=el
                        break
                except Exception:
                    pass

            if display is None:
                print("Display Schemes not found for",name)
                continue

            before=clean(await page.locator("body").inner_text())
            try:
                await display.click(force=True,timeout=30000)
            except Exception as e:
                print("Display click failed:",repr(e))
                continue

            # Wait for the Wicket AJAX result.
            changed=False
            for _ in range(20):
                await page.wait_for_timeout(1000)
                now=clean(await page.locator("body").inner_text())
                if now != before:
                    changed=True
                    break

            await page.wait_for_timeout(2500)

            html=await page.content()
            body=(await page.locator("body").inner_text())[:300000]
            links=await page.locator("a").evaluate_all("""
            els => els.map((a,i)=>({
              index:i,
              text:(a.innerText||'').trim(),
              href:a.href||'',
              onclick:a.getAttribute('onclick')||''
            })).filter(x=>x.text||x.href)
            """)

            # Only save official links belonging to Goa domains.
            official_docs=[]
            for link in links:
                href=link["href"]
                text=link["text"]
                if not href:
                    continue
                if not re.match(r"^https?://(www\\.)?(goaonline\\.gov\\.in|services\\.goaonline\\.gov\\.in|goa\\.gov\\.in)(/|$)",href,re.I):
                    continue
                if re.search(r"\.pdf($|[?#])|notification|application|declaration|download|form|document",
                            text+" "+href,re.I):
                    official_docs.append(link)

            sector_file=f"sector_{n:02d}.html"
            (OUT/sector_file).write_text(html,encoding="utf-8")

            all_results.append({
                "sector": name,
                "page_url": page.url,
                "body_changed": changed,
                "text": body,
                "links": links,
                "official_document_links": official_docs
            })

        (OUT/"schemes.json").write_text(
            json.dumps({
                "source":"Goa Online Know Your Scheme",
                "source_url":page.url,
                "sector_count":len(all_results),
                "sectors":all_results
            },indent=2,ensure_ascii=False),
            encoding="utf-8"
        )

        print("\nFINISHED")
        print("Sectors processed:",len(all_results))
        print("Output: output/schemes.json")
        print("PDF files were NOT downloaded.")
        await browser.close()

if __name__=="__main__":
    asyncio.run(main())
