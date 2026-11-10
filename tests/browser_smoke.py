"""Optional real-browser smoke test; run with the generated site served on port 8000."""

from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parents[1]


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        for name in ["index", "results", "performance", "methodology"]:
            page.goto(f"http://localhost:8000/{name}.html", wait_until="networkidle")
            page.locator("h1").wait_for()
            assert page.locator("h1").inner_text()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.goto("http://localhost:8000/index.html", wait_until="networkidle")
        latest = page.get_by_role("button", name="Latest · untracked", exact=True)
        latest.click()
        assert latest.get_attribute("aria-pressed") == "true"
        page.get_by_role("button", name="Fixed control", exact=True).click()
        assert (
            page.get_by_role("button", name="Fixed control", exact=True).get_attribute("aria-pressed")
            == "true"
        )
        page.get_by_role("button", name="5-hour lock", exact=True).click()
        assert (
            page.get_by_role("button", name="5-hour lock", exact=True).get_attribute("aria-pressed") == "true"
        )
        page.get_by_role("button", name="First snapshot", exact=True).click()
        page.get_by_role("button", name="v1.23", exact=True).click()
        page.get_by_role("button", name="Latest · untracked", exact=True).click()
        (ROOT / ".cache").mkdir(exist_ok=True)
        page.screenshot(path=str(ROOT / ".cache" / "dashboard-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        for name in ["index", "results", "performance", "methodology"]:
            page.goto(f"http://localhost:8000/{name}.html", wait_until="networkidle")
            page.locator("h1").wait_for()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), name
        page.goto("http://localhost:8000/index.html", wait_until="networkidle")
        page.screenshot(path=str(ROOT / ".cache" / "dashboard-mobile.png"), full_page=True)
        page.goto("http://localhost:8000/performance.html", wait_until="networkidle")
        assert page.get_by_role("combobox", name="Methodology version").count() == 0
        assert page.get_by_role("heading", name="Residual defense adjustments").count() == 1
        assert page.get_by_role("heading", name="2026–27 pooled record").count() == 1
        assert "v1.19" not in page.locator("body").inner_text()
        data = page.request.get("http://localhost:8000/data.json").json()
        assert {f["methodology"] for f in data["forecasts"]} <= {"sleeper_only_v1_23"}
        assert set(data["grades"]) <= {f["id"] for f in data["forecasts"]}
        assert not errors, errors
        browser.close()
    print("Four pages, desktop/mobile layout, stage/model controls and JavaScript errors: PASS")


if __name__ == "__main__":
    main()
