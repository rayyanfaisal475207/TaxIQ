"""
FBR SRO Scraper — Playwright-based
Covers: Income Tax, Sales Tax, Federal Excise, Customs
Range: last 2 years

SETUP (run once):
    pip install playwright --break-system-packages
    playwright install chromium

BEFORE RUNNING — fill in the 3 selectors below using browser DevTools:
    1. Open https://www.fbr.gov.pk/ShowSROs?Department=Sales+Tax in Chrome
    2. F12 -> click the element-picker icon (top-left of DevTools) -> click the
       Department dropdown -> in the Elements panel, copy its `name` or `id`
    3. Do the same for the From-Date field, To-Date field, and Search button
    4. Paste those into SELECTORS below

If step 1 in the chat (checking the Network tab for a direct API/POST call)
turned up a clean JSON or HTML-fragment endpoint instead, don't use this
script — a plain `requests` call to that endpoint will be faster and far
more reliable. Use this only if the site genuinely requires a rendered
browser to produce results.

Usage:
    python fetch_fbr_sros.py --department "Sales Tax" --years 2
    python fetch_fbr_sros.py --department "Income Tax" --years 2
    python fetch_fbr_sros.py --department "Federal Excise" --years 2
    python fetch_fbr_sros.py --department "Customs" --years 2
"""

import argparse
import json
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote

import requests
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.fbr.gov.pk/ShowSROs"
PDF_HOST = "download1.fbr.gov.pk"

# ---- FILL THESE IN AFTER INSPECTING THE PAGE (see docstring above) ----
SELECTORS = {
    "date_from": "input[name='dateFrom']",   # e.g. "#DateFrom" or "input[name='fromDate']"
    "date_to": "input[name='dateTo']",     # e.g. "#DateTo"
    "search_button": "#SearchSRO",  # e.g. "button#btnSearch" or "input[type=submit]"
    "results_table": "table",     # usually fine as-is; adjust if results render elsewhere
}
# -------------------------------------------------------------------

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TaxIQ-DataCollector/1.0)"
}


def scrape_department(department: str, years_back: int, out_dir: Path):
    dept_slug = department.replace(" ", "_")
    dept_out = out_dir / dept_slug
    dept_out.mkdir(parents=True, exist_ok=True)
    meta_path = dept_out / "metadata.jsonl"

    today = date.today()
    from_date = today.replace(year=today.year - years_back)

    url = f"{BASE_URL}?Department={quote(department)}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        page.goto(url, timeout=30000)

        # Fill date range if the selectors are configured
        if SELECTORS["date_from"] != "NEED_VERIFY":
            page.fill(SELECTORS["date_from"], from_date.strftime("%d-%b-%Y"))
            page.fill(SELECTORS["date_to"], today.strftime("%d-%b-%Y"))
            page.click(SELECTORS["search_button"])
            page.wait_for_timeout(8000)
        else:
            print(
                "WARNING: date selectors not configured — attempting to scrape "
                "whatever the page shows by default (may be empty or unfiltered)."
            )

        page.wait_for_timeout(3000)

        entries = []
        page_num = 1
        
        while True:
            rows = page.query_selector_all(f"{SELECTORS['results_table']} tr")
            print(f"[{department}] Page {page_num}: found {len(rows)} table rows")

            for row in rows:
                link_el = row.query_selector("a")
                if not link_el:
                    continue
                href = link_el.get_attribute("href") or ""
                if PDF_HOST not in href and not href.endswith(".pdf"):
                    continue
                row_text = row.inner_text().strip()
                title = link_el.inner_text().strip()
                parts = row_text.split("\t")
                if len(parts) >= 3:
                    sro_number = parts[0].strip()
                    title = parts[1].strip()
                    issue_date_raw = parts[2].strip()
                else:
                    sro_number = None
                    issue_date_raw = None

                entries.append({
                    "department": department,
                    "sro_number": sro_number,
                    "title": title,
                    "issue_date_raw": issue_date_raw,
                    "pdf_url": href if href.startswith("http") else f"https://{PDF_HOST}{href}",
                    "source_page": url,
                })
            
            # Check for Next page button
            next_btn = page.query_selector("li#demoGrid_next:not(.disabled) a")
            if not next_btn:
                break
                
            print(f"[{department}] clicking Next Page...")
            next_btn.click()
            page.wait_for_timeout(4000)
            page_num += 1

        browser.close()

    # Download PDFs + write metadata
    existing_urls = set()
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        existing_urls.add(json.loads(line).get("pdf_url"))
                    except:
                        pass

    with open(meta_path, "a", encoding="utf-8") as meta_f:
        for entry in entries:
            if entry["pdf_url"] in existing_urls:
                continue
            existing_urls.add(entry["pdf_url"])
            
            fname = entry["pdf_url"].split("/")[-1]
            dest = dept_out / fname
            if dest.exists():
                continue  # already downloaded — supports safe re-runs
            try:
                resp = requests.get(entry["pdf_url"], headers=HEADERS, timeout=30)
                resp.raise_for_status()
                dest.write_bytes(resp.content)
                print(f"  saved {fname}")
            except requests.RequestException as e:
                print(f"  FAILED {entry['pdf_url']}: {e}")
                entry["download_error"] = str(e)

            meta_f.write(json.dumps(entry) + "\n")
            time.sleep(1)  # be polite — 1 request/sec

    print(f"[{department}] done. {len(entries)} entries -> {meta_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--department", required=True,
                         choices=["Income Tax", "Sales Tax", "Federal Excise", "Customs"])
    parser.add_argument("--years", type=int, default=2)
    parser.add_argument("--out", default="data/raw/fbr/sros")
    args = parser.parse_args()

    scrape_department(args.department, args.years, Path(args.out))
