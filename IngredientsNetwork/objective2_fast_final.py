import asyncio
import csv
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

BASE = "https://www.ingredientsnetwork.com"
SEARCH_URL = (
    BASE
    + "/live/search/searchresults46v2.jsp?"
      "site=47&SugType_val=&RecordId_val=&searchtype=all&name="
)

LINKS_JSON = "ingredients_network_links.json"
OUT_CSV = "ingredients_network_final.csv"
CHECKPOINT_JSON = "ingredients_network_checkpoint.json"
FAILED_JSON = "ingredients_network_failed.json"
ANSWERS_TXT = "ingredients_objective2_answers.txt"

# Conservative: one Chromium, several tabs. This avoids the socket explosion
# caused by launching 8 Chromium processes simultaneously.
CONCURRENCY = 5
NAV_TIMEOUT = 45000
PAGE_WAIT_MS = 500
CHECKPOINT_EVERY = 5

COLUMNS = [
    "Company Name", "Company Description", "Sales Markets",
    "Primary Business Activity", "Categories", "Events",
    "Address", "Email", "Telephone", "Website", "Source URL"
]


def clean(v):
    return re.sub(r"\s+", " ", str(v or "")).strip()


def unique_join(values):
    out, seen = [], set()
    for v in values:
        v = clean(v)
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return " | ".join(out)


def empty_row(url, error=""):
    d = {c: "" for c in COLUMNS}
    d["Source URL"] = url
    if error:
        d["_error"] = error
    return d


async def safe_text(page):
    try:
        return await page.locator("body").inner_text(timeout=10000)
    except Exception:
        return ""


async def click_contact_details(page):
    # Some contact data is hidden until this control is clicked.
    patterns = [
        re.compile(r"view all contact information", re.I),
        re.compile(r"contact information", re.I),
    ]
    for pat in patterns:
        try:
            loc = page.get_by_text(pat)
            n = await loc.count()
            for i in range(min(n, 3)):
                el = loc.nth(i)
                try:
                    if await el.is_visible(timeout=500):
                        await el.click(timeout=2500)
                        await page.wait_for_timeout(250)
                        return
                except Exception:
                    pass
        except Exception:
            pass


async def extract_company(page, url):
    await click_contact_details(page)
    body = await safe_text(page)
    lines = [clean(x) for x in body.splitlines() if clean(x)]

    name = ""
    for selector in ["h1", "main h1", ".company-name", "[class*=company-name]"]:
        try:
            vals = await page.locator(selector).all_inner_texts()
            for x in vals:
                x = clean(x)
                if x and not x.lower().startswith("about "):
                    name = x
                    break
            if name:
                break
        except Exception:
            pass

    if not name:
        try:
            name = clean(re.sub(r"^About\s+", "", await page.title(), flags=re.I))
        except Exception:
            pass

    description = ""
    m = re.search(r"Company description\s*(.*?)\s*Quick facts", body, re.I | re.S)
    if m:
        description = clean(m.group(1))

    def fact(label):
        for i, line in enumerate(lines):
            if line.lower() == label.lower() and i + 1 < len(lines):
                return clean(lines[i + 1])
        return ""

    sales = fact("Sales markets")
    primary = fact("Primary business activity")

    categories = []
    # Prefer the section between the category heading and the next major section.
    m = re.search(
        r"Categories affiliated with .*?\s*(.*?)(?:REQUEST INFORMATION|Contact information|About us)",
        body, re.I | re.S
    )
    if m:
        for x in re.split(r"\n|\|", m.group(1)):
            x = clean(x)
            if x and len(x) < 180:
                categories.append(x)

    # Also use category-like anchors as a fallback.
    if not categories:
        try:
            anchors = await page.locator("a[href]").all_inner_texts()
            for x in anchors:
                x = clean(x)
                if x and 2 < len(x) < 100:
                    categories.append(x)
        except Exception:
            pass

    events = []
    m = re.search(
        r"(?:Upcoming events|Recently at)\s*(.*?)(?:Our Top products|News about |Contact information)",
        body, re.I | re.S
    )
    if m:
        excluded = {
            "book a meeting", "see our exhibitor profile",
            "see full exhibitor list", "upcoming events", "recently at"
        }
        for x in m.group(1).splitlines():
            x = clean(x)
            if (
                x and len(x) > 3
                and x.lower() not in excluded
                and not re.fullmatch(r"\d{1,2}", x)
                and not re.fullmatch(r"\d{4}", x)
                and not re.match(r"^\d{1,2}[-/]\d", x)
            ):
                events.append(x)

    contact = {"Address": "", "Email": "", "Telephone": "", "Website": ""}

    try:
        anchors = await page.locator("a[href]").all()
        for a in anchors:
            try:
                href = (await a.get_attribute("href") or "").strip()
                txt = clean(await a.inner_text())
                low = href.lower()

                if low.startswith("mailto:") and not contact["Email"]:
                    contact["Email"] = href.split(":", 1)[1].split("?", 1)[0]
                elif low.startswith("tel:") and not contact["Telephone"]:
                    contact["Telephone"] = href.split(":", 1)[1]
                elif (
                    "website" in txt.lower()
                    and href.startswith(("http://", "https://"))
                    and "ingredientsnetwork.com" not in href.lower()
                ):
                    contact["Website"] = href
            except Exception:
                pass
    except Exception:
        pass

    m = re.search(
        r"Contact information\s*(.*?)(?:View all contact information|Are you a supplier\?|About us|Privacy)",
        body, re.I | re.S
    )
    if m:
        c_lines = [clean(x) for x in m.group(1).splitlines() if clean(x)]
        labels = ["Address", "Email", "Telephone", "Website"]
        for i, line in enumerate(c_lines):
            for label in labels:
                if line.lower() == label.lower() and i + 1 < len(c_lines):
                    if not contact[label]:
                        contact[label] = c_lines[i + 1]

    # Address fallback: find the block after Address.
    if not contact["Address"]:
        for i, line in enumerate(lines):
            if line.lower() == "address" and i + 1 < len(lines):
                candidate = clean(lines[i + 1])
                if candidate and candidate.lower() not in {
                    "email", "telephone", "website"
                }:
                    contact["Address"] = candidate
                    break

    return {
        "Company Name": name,
        "Company Description": description,
        "Sales Markets": sales,
        "Primary Business Activity": primary,
        "Categories": unique_join(categories),
        "Events": unique_join(events),
        "Address": clean(contact["Address"]),
        "Email": clean(contact["Email"]),
        "Telephone": clean(contact["Telephone"]),
        "Website": clean(contact["Website"]),
        "Source URL": url,
    }


def load_urls():
    with open(LINKS_JSON, "r", encoding="utf-8") as f:
        raw = json.load(f)
    urls = []
    for x in raw:
        if x.get("type") == "COMPANY" and x.get("url"):
            urls.append(x["url"])
    return list(dict.fromkeys(urls))


def load_checkpoint():
    if not os.path.exists(CHECKPOINT_JSON):
        return {}
    try:
        with open(CHECKPOINT_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_checkpoint(results, failed, total):
    payload = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_urls": total,
        "completed": len(results),
        "failed": len(failed),
        "results": results,
    }
    tmp = CHECKPOINT_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, CHECKPOINT_JSON)

    with open(FAILED_JSON, "w", encoding="utf-8") as f:
        json.dump(failed, f, ensure_ascii=False, indent=2)


def write_csv(results):
    rows = [results[u] for u in results if results[u].get("Company Name")]
    # Deduplicate by URL, then by Company Name + URL.
    seen = set()
    clean_rows = []
    for r in rows:
        key = (clean(r.get("Company Name")).lower(), r.get("Source URL", ""))
        if key not in seen:
            seen.add(key)
            clean_rows.append({c: clean(r.get(c, "")) for c in COLUMNS})

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(clean_rows)
    return clean_rows


async def scrape_one(browser, url, semaphore):
    async with semaphore:
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await page.wait_for_timeout(PAGE_WAIT_MS)
            row = await extract_company(page, url)

            # Retry once if the page returned no company name.
            if not row.get("Company Name"):
                await page.reload(wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                await page.wait_for_timeout(500)
                row = await extract_company(page, url)

            if not row.get("Company Name"):
                row["_error"] = "No company name extracted"
            return row
        except Exception as e:
            return empty_row(url, f"{type(e).__name__}: {e}")
        finally:
            try:
                await page.close()
            except Exception:
                pass


async def scrape_all(browser, urls):
    checkpoint = load_checkpoint()
    results = checkpoint.get("results", {}) if checkpoint else {}
    failed = []

    # Never trust malformed checkpoint entries.
    if not isinstance(results, dict):
        results = {}

    remaining = [u for u in urls if u not in results]
    print(f"\nTotal URLs: {len(urls)}")
    print(f"Already checkpointed: {len(results)}")
    print(f"Remaining: {len(remaining)}")
    print(f"Concurrency: {CONCURRENCY}")

    sem = asyncio.Semaphore(CONCURRENCY)
    completed_since_save = 0

    for start in range(0, len(remaining), CONCURRENCY * 2):
        batch = remaining[start:start + CONCURRENCY * 2]
        tasks = [asyncio.create_task(scrape_one(browser, u, sem)) for u in batch]

        for task in asyncio.as_completed(tasks):
            row = await task
            url = row["Source URL"]
            if row.get("Company Name"):
                results[url] = row
            else:
                failed.append({
                    "url": url,
                    "error": row.get("_error", "unknown")
                })

            completed_since_save += 1
            done = len(results)
            processed = len(results) + len([x for x in failed])

            print(f"  checkpoint progress: {processed}/{len(urls)} | saved profiles: {done}")

            if completed_since_save >= CHECKPOINT_EVERY:
                save_checkpoint(results, failed, len(urls))
                write_csv(results)
                completed_since_save = 0

    save_checkpoint(results, failed, len(urls))
    rows = write_csv(results)
    return results, failed, rows


async def click_show_more(page, max_clicks=120):
    previous = -1
    stable = 0
    for _ in range(max_clicks):
        try:
            count = await page.locator("a[href]").count()
            # Count actual company/product result URLs.
            result_count = 0
            for i in range(count):
                try:
                    a = page.locator("a[href]").nth(i)
                    href = await a.get_attribute("href") or ""
                    txt = clean(await a.inner_text())
                    full = urljoin(BASE, href)
                    if (
                        re.search(r"-(?:comp|prod)\d+\.html$", full, re.I)
                        and re.search(r"\b(?:COMPANY|PRODUCT)\b", txt, re.I)
                    ):
                        result_count += 1
                except Exception:
                    pass

            print(f"    visible result cards: {result_count}")

            if result_count == previous:
                stable += 1
            else:
                stable = 0
            previous = result_count

            more = page.get_by_text(re.compile(r"^SHOW MORE RESULTS$", re.I))
            if not await more.count():
                break

            clicked = False
            for i in range(min(await more.count(), 3)):
                try:
                    el = more.nth(i)
                    if await el.is_visible(timeout=500):
                        await el.scroll_into_view_if_needed()
                        await el.click(timeout=5000)
                        await page.wait_for_timeout(1000)
                        clicked = True
                        break
                except Exception:
                    pass

            if not clicked or stable >= 2:
                break

        except Exception:
            break

    return previous


async def count_type_filter(browser, category):
    page = await browser.new_page()
    try:
        await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        await page.wait_for_timeout(1200)

        trigger_name = category.lower().replace(" ", "-")
        trigger = page.locator(f'a[data-category-trigger="{trigger_name}"]')
        if not await trigger.count():
            trigger = page.get_by_text(category, exact=True)

        if not await trigger.count():
            print(f"  FILTER NOT FOUND: {category}")
            return None

        await trigger.first.click(timeout=5000)
        await page.wait_for_timeout(1500)

        count = await click_show_more(page)
        return max(count or 0, 0)
    except Exception as e:
        print(f"  FILTER ERROR {category}: {type(e).__name__}: {e}")
        return None
    finally:
        await page.close()


def calculate_category_counts(rows):
    def cat(row):
        return (row.get("Categories") or "").lower()

    herbs = sum(
        bool(re.search(r"herbs\s*(?:,|&)\s*spices", cat(r), re.I))
        for r in rows
    )
    physical = sum(
        bool(re.search(
            r"physical formats|dried\s*-\s*air dried|extracted|liquid|powdered",
            cat(r), re.I
        ))
        for r in rows
    )
    cognitive = sum(
        bool(re.search(r"cognitive\s*(?:&|and)\s*mental health", cat(r), re.I))
        for r in rows
    )
    return herbs, physical, cognitive


async def main_async():
    print("\n==========================================")
    print("INGREDIENTS NETWORK - ROBUST CHECKPOINT SCRAPER")
    print("==========================================")
    print("Single Chromium + 5 tabs; checkpointing every 5 records.")
    print("Safe to stop and rerun: completed profiles are reused.\n")

    if not os.path.exists(LINKS_JSON):
        raise FileNotFoundError(
            f"{LINKS_JSON} not found. Keep the previously collected company URL file "
            "in this folder."
        )

    urls = load_urls()
    print(f"Loaded company URLs: {len(urls)}")

    async with async_playwright() as p:
        # One browser process only = much lower socket/process pressure.
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--disable-background-networking",
                "--no-first-run",
                "--disable-extensions",
            ],
        )

        try:
            results, failed, rows = await scrape_all(browser, urls)

            print("\n------------------------------------------")
            print("PROFILE SCRAPE FINISHED")
            print("------------------------------------------")
            print(f"Successful profiles: {len(rows)}")
            print(f"Failed/missing profiles: {len(failed)}")
            print(f"CSV: {OUT_CSV}")
            print(f"Checkpoint: {CHECKPOINT_JSON}")

            print("\nCounting required site filters...")
            ingredients = await count_type_filter(browser, "Ingredients")
            finished = await count_type_filter(browser, "Finished Products")

        finally:
            await browser.close()

    herbs, physical, cognitive = calculate_category_counts(rows)

    answers = f"""INGREDIENTS NETWORK - OBJECTIVE 2
=================================

1. Total ingredients: {ingredients if ingredients is not None else "NOT AVAILABLE"}
2. Total finished products: {finished if finished is not None else "NOT AVAILABLE"}
3. Companies with Herbs and Spices: {herbs}
4. Companies with physical delivery formats: {physical}
5. Companies in Cognitive & Mental Health: {cognitive}

SCRAPE SUMMARY
--------------
Company URLs discovered: {len(urls)}
Company profiles successfully scraped: {len(rows)}
Failed/missing profiles: {len(failed)}
CSV: {OUT_CSV}
Checkpoint: {CHECKPOINT_JSON}
"""

    with open(ANSWERS_TXT, "w", encoding="utf-8") as f:
        f.write(answers)

    print("\n==========================================")
    print("OBJECTIVE 2 COMPLETE")
    print("==========================================")
    print(f"CSV:               {OUT_CSV}")
    print(f"Answers:           {ANSWERS_TXT}")
    print(f"Checkpoint:        {CHECKPOINT_JSON}")
    print(f"Companies scraped: {len(rows)}")
    print(f"Failed:            {len(failed)}")
    print(f"Ingredients:       {ingredients}")
    print(f"Finished Products: {finished}")
    print(f"Herbs & Spices:    {herbs}")
    print(f"Physical Formats:  {physical}")
    print(f"Cognitive Health:  {cognitive}")
    print("==========================================")


if __name__ == "__main__":
    asyncio.run(main_async())
