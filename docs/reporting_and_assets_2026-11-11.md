# Public reporting and assets — 11 November 2026

The results and performance pages now make the live evaluation their primary view. The existing pooled season record remains visible with explicit replay/live counts, current-version first-snapshot scope and a row for every part. This changes reporting and presentation only.

## Benefits and evidence

- Stage and model controls select distinct live market-comparison cohorts. At the reviewed snapshot, first/adaptive has six graded games and log loss 0.5079; lock/adaptive has two and 0.7671; lock/fixed has two and 0.7008. The old page kept showing the pooled first/adaptive comparison under all of these controls.
- The matched market-favorite benchmark explains what a positive return adds beyond following favorites. The reviewed six live first picks all agree with the market favorite, so both rows correctly show 5–1 and +1.211876 units. The benchmark is calculated for display and never registers a bet.
- Results show team names rather than home/away selection codes, integer American odds, model and fair market probabilities, the final score and net units. Controls precede scorecards. Narrow layouts show game cards; the brand and theme control remain available.
- Calibration parameters, lead-time tables, the defense matrix and fixed-control diagnostics live inside a disclosure. The separate season comparison is inside the replay disclosure, with its scope stated explicitly.
- All 30 team logos have light and dark local WebP assets and a local fallback. Source metadata is in `web/assets/teams/sources.json`; New Orleans resolves to `no.png`. The 60 original PNG variants total 3,537,299 bytes; optimized variants total 254,276 bytes, a 92.8% reduction. Each page loads only the active theme's requested logos, with lazy loading.
- SVG brand mark and outlined wordmarks, SVG navigation, SVG/ICO/PNG favicons, Apple and manifest icons, and a 1200×630 social image form a complete local asset set. Dark and light wordmarks use paths, not an external font.

## Validation contract

Before merging, run the repository's lint, formatting, fixture suite, ledger validation and site build. Regression fixtures cover separate stage/model values, latest-grade corrections, void/pending/retired exclusions, quote-specific sample counts, fair favorite payouts, pick'em/tied-model exclusions, asset packaging and ledger immutability. The optional `tests/browser_smoke.py` uses a locally generated site and covers four viewport widths, filters, logo routes and themes.

Browser inspection checks actual page rendering and behavior. An additional comparison of generated data against the reviewed live snapshot confirms accepted forecasts, grades, existing performance data and original season metrics are unchanged. No scoring, calibration, market-selection threshold, configuration, transaction or active-model file is part of this change.

## Asset provenance

Team images are resized from the ESPN team metadata API's default and dark variants. The source manifest includes provider URLs, retrieval date, source SHA-256 and byte counts. Team identities and images remain their respective owners' marks; these are identification assets for the existing independent dashboard. Brand letter outlines use DejaVu Sans Bold; its license is retained in `docs/asset_licenses/DejaVu.txt`.
