from playwright.sync_api import sync_playwright

def dump_html():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        print("Navigating to page...")
        page.goto("https://www.fbr.gov.pk/ShowSROs?Department=Sales+Tax", timeout=60000)
        page.wait_for_timeout(5000)
        
        print("Dumping HTML...")
        with open("page_dump.html", "w", encoding="utf-8") as f:
            f.write(page.content())
            
        print("Done. Saved to page_dump.html")
        browser.close()

if __name__ == "__main__":
    dump_html()
