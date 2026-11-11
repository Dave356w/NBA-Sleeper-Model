"""Optional browser regression check against a locally generated site.

Serve site/ on port 8000, or set SLEEPER_SITE_URL to the existing preview URL.
No live sports APIs are called by this test.
"""

import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parents[1]
BASE_URL = os.environ.get("SLEEPER_SITE_URL", "http://localhost:8000").rstrip("/")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        # The Today page reads live scores in the browser; keep this check offline.
        page.route("https://site.api.espn.com/**", lambda route: route.abort())
        for width in [1440, 820, 390, 320]:
            page.set_viewport_size({"width": width, "height": 1000})
            for name in ["index", "results", "performance", "methodology"]:
                page.goto(f"{BASE_URL}/{name}.html", wait_until="networkidle")
                page.locator("h1").wait_for()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
                    name,
                    width,
                )
                assert page.locator(".brand-logo:visible").count() == 1
                assert page.get_by_role("button", name="Switch light or dark theme").is_visible()
                if name == "results" and width < 700:
                    assert page.locator(".results-table").is_hidden()
                    assert page.locator(".results-cards").is_visible()
        page.goto(f"{BASE_URL}/performance.html", wait_until="networkidle")
        data = page.request.get(f"{BASE_URL}/data.json").json()
        season = data["season"]
        if season and season["pooled"]["n"]:
            pooled = season["pooled"]
            assert page.get_by_role("heading", name=f"{season['season']} season record").is_visible()
            text = page.locator("#content").inner_text()
            assert f"{pooled['correct']}–{pooled['winner_count'] - pooled['correct']}" in text
            metric = pooled["versus_market"]["win_probability"]
            if metric["n"]:
                assert f"{metric['model_log_loss']:.4f}" in page.locator("#market-comparison").inner_text()
            assert page.locator("#model-details").get_attribute("open") is None
            assert "xploratory" not in text and "Fixed control" not in text
            page.goto(f"{BASE_URL}/results.html", wait_until="networkidle")
            assert page.locator(".results-table tbody tr").count() == sum(
                r["slate_date"] == season["rows"][-1]["slate_date"] for r in season["rows"]
            )
        page.goto(f"{BASE_URL}/index.html", wait_until="networkidle")
        page.get_by_role("button", name="Latest preview", exact=True).click()
        assert (
            page.get_by_role("button", name="Latest preview", exact=True).get_attribute("aria-pressed")
            == "true"
        )
        before = page.evaluate("document.documentElement.dataset.theme")
        page.get_by_role("button", name="Switch light or dark theme").click()
        assert {before, page.evaluate("document.documentElement.dataset.theme")} == {"light", "dark"}
        for logo in data["games"]:
            for team in [logo["home"], logo["away"]]:
                for theme in ["light", "dark"]:
                    response = page.request.get(f"{BASE_URL}/assets/teams/{team.lower()}-{theme}.webp")
                    assert response.ok, (team, theme)
        assert {f["methodology"] for f in data["forecasts"]} <= {data["methodology"]}
        assert set(data["grades"]) <= {f["id"] for f in data["forecasts"]}
        assert not errors, errors
        browser.close()
    print("Four pages, four widths, pooled season record, local logos and themes: PASS")


if __name__ == "__main__":
    main()
