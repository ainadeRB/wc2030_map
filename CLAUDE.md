# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A single-page Streamlit app (`app.py`) rendering an interactive Kepler.gl-style
map (Folium/Leaflet) of World Cup 2030 Morocco hotels and points of interest
(stadiums, training sites, airports...). Users upload their own Excel files;
there is no database. French is the UI/UX language throughout — keep all
user-facing text, comments, and commit messages in French to match the
existing codebase and the non-technical, French-speaking users.

## Commands

```bash
pip install -r requirements.txt
python scripts/generate_sample_data.py   # (re)generates data/sample_hotels.xlsx and data/sample_poi.xlsx
streamlit run app.py
```

No automated test suite is committed to the repo. Verify changes with
`streamlit.testing.v1.AppTest` ad hoc (write a throwaway script under a
scratch dir, run it, discard it) — this is the only practical way to catch
regressions without a browser, and is how prior changes in this project were
validated (widget presence/absence, filter narrowing, no exceptions on
`at.run()`).

Other scripts (all read `data/hotels.xlsx` / `data/poi.xlsx` by default):
```bash
python scripts/precompute_travel_times.py [--grid-km N]   # bulk-fills data/travel_time_cache.json via routing API
python scripts/estimate_travel_times.py                    # fills data/travel_time_estimates.json when routing quota is exhausted
python scripts/upload_photos_to_cloud.py --cloud-name ... --api-key ... --api-secret ...  # pushes data/photos/ to Cloudinary for the hosted deployment
python scripts/find_unreachable_hotels.py
```

## Architecture

- `app.py` — everything UI-related: sidebar (data upload, filters, map
  settings, distance/travel-time filter), popup/tooltip HTML generation,
  color/size logic wiring, `build_map()` (Folium map assembly), `main()`
  (top-level Streamlit flow). No business logic beyond wiring; delegates to
  `src/`.
- `src/data_loader.py` — Excel → DataFrame. Column names are matched
  fuzzily (`_find_col`, e.g. `Lat`/`Latitude`, `City`/`Ville`) so slightly
  different real-world exports still load. `load_hotels()` reads the
  `bdd_maroc_clean` sheet by name (case/whitespace-insensitive, see
  `_find_sheet`/`HOTELS_SHEET_NAME`) when present, falling back to the first
  sheet otherwise — the real workbook has many other sheets. `load_pois()`
  treats each sheet as one POI layer (sheet name = layer name); a `Type`
  column inside a sheet splits it into further sub-layers.
- `src/routing.py` — real driving times via OSRM (public, no key) or
  OpenRouteService (needs `.streamlit/secrets.toml` → `ORS_API_KEY`, see
  `using_ors()`); disk-cached in `data/travel_time_cache.json`, never
  recomputed for an already-seen origin/destination pair. Falls back to
  estimates (`data/travel_time_estimates.json`, produced by
  `scripts/estimate_travel_times.py`) when a real value is missing.
- `src/geo.py` — haversine distance, map bounds.
- `src/styling.py` — color palettes per categorical value, bubble radius
  scaling.
- `src/photos.py` — resolves photos for a given hotel ID. Local files
  always win over remote. Local convention:
  `data/photos/<ville hôte>/<ID>/*.{jpg,jpeg,png,webp}` (new) or
  `data/photos/<ID>/*` (flat, older convention — both are searched and can
  coexist, see `_find_photo_folder`/`iter_photo_folders`). Remote fallback:
  `data/photos_manifest.json` (hotel ID → list of Cloudinary URLs),
  populated by `scripts/upload_photos_to_cloud.py`, used only when no local
  folder exists for that ID — this is what lets the disk-less hosted
  deployment show photos. **This manifest file is deliberately excluded
  from the downloadable ZIP** (see `.gitattributes` `export-ignore`): the
  offline/local distribution must never fetch anything from the internet,
  only the hosted deployment should.

### Persistence pattern

Real uploaded data (`data/hotels.xlsx`, `data/poi.xlsx`), UI color/size
preferences (`data/ui_prefs.json`), and travel-time caches persist to disk
via `_load_with_persistence()` / `load_ui_prefs()` / `save_ui_prefs()` in
`app.py`, so a browser refresh or app restart doesn't lose them. All of
these paths are gitignored — see the "what's excluded and why" comments in
`.gitignore` itself, which are the authoritative explanation for each entry
(real hotel/POI data, photos, secrets, per-user UI prefs are all treated as
sensitive and must never be committed).

### Booking-note color bands

`get_booking_bands()` / `render_booking_band_editor()` in `app.py` implement
user-editable numeric buckets (e.g. 0-6 / 6-8 / 8-10) each with its own
color, persisted in `ui_prefs.json`. Bands carry a stable internal `id` used
for Streamlit widget keys; `get_booking_bands()` self-heals duplicate/stale
ids on load (defends against a `StreamlitDuplicateElementKey` crash seen in
production from a stale prefs file).

## Two distribution channels — keep both working

1. **Hosted** (Streamlit Community Cloud): deployed straight from the git
   branch. No persistent disk, so photos only work via the Cloudinary
   manifest fallback described above.
2. **Offline/local ZIP**: GitHub "Code → Download ZIP" (or `git archive
   --format=zip -o out.zip HEAD`, which respects `.gitattributes
   export-ignore` the same way), containing `Lancer_l_app.bat` — a
   self-healing Windows one-click launcher (creates a venv, installs
   `requirements.txt`, launches Streamlit). It must work with **zero
   internet dependency beyond the first install**, and zero prior Python
   knowledge. Non-obvious things baked into it from real user testing, don't
   regress them:
   - Uses `python -m pip install ...` and `python -m streamlit run ...`
     rather than bare `pip`/`streamlit` executables — invoking a
     just-written `.exe` directly right after it's created can fail with a
     transient Windows "Access is denied" (antivirus file-lock race);
     routing through `python -m` avoids it.
   - Detects Python via `python --version` (not `where python`, which can
     report success for a non-functional Microsoft Store alias stub), with
     a fallback search across common Anaconda/Miniconda install paths, and
     a last-resort silent per-user download+install of Python 3.12 if
     nothing is found.
   - `.bat` syntax trap: inside a parenthesized `if (...)` block, literal
     `(`, `)`, and `>` characters in an `echo` line must be escaped
     (`^(`, `^)`, `^>`) or they break cmd.exe's block parser. There is no
     Windows environment available to execute-test this file — changes need
     careful manual line-by-line review.
   - `.gitattributes` `export-ignore` controls what's excluded from this ZIP
     (dev-only files, and the sensitive photo manifest — see above).

## Repository visibility

This repo may be public. Real hotel data and photos must never end up
committed (enforced via `.gitignore`) or embedded in files that are
committed (e.g. don't put real URLs/identifiers into anything intended to
be versioned) — this has previously happened by accident via
`photos_manifest.json` shipping in the offline ZIP before it was added to
`export-ignore`. Treat any new file that might be committed as reviewed for
this before adding it.
