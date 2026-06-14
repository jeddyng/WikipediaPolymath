# Polymath — Vital tab setup

The **Random** tab works on its own with no setup. The **Vital** tab needs a
`vital.json` file next to `index.html`. If that file isn't there, the Vital tab
stays hidden and everything else works normally.

## What vital.json is

Article TITLES for Wikipedia's ~50,000 "Level 5 vital articles" (the topics editors
hand-pick as most important to know), plus a timestamp:

```json
{ "titles": ["3D printing", "COBOL", ...], "updated": "2026-06-14T00:00:00Z" }
```

Titles (not page IDs): the app fetches by title with redirects enabled, so
renames resolve automatically, and the monthly re-scrape picks up new titles anyway.
This skips the slow ID-resolution step, so a full build takes a few minutes, not ~90.
The "updated" timestamp makes the next run incremental — keep it.

## How the app uses it

On load, the app fetches `./vital.json`. If present, the Vital tab appears.
Clicking it shuffles the whole ID list, takes 50, and fetches those summaries in
one API call. "Shuffle 50" reshuffles. No live category crawling at runtime.

## Building / updating vital.json

Run the scraper on your own machine (not a rate-limited sandbox):

```bash
python3 build_vital.py          # create or update ./vital.json
python3 build_vital.py --out path/to/vital.json
python3 build_vital.py --full   # force a full re-scan
```

### Incremental & append-only

- **First run** (no vital.json yet): full scan of all 11 vital categories,
  ~50 serial calls, a few minutes. One time only.
- **Every run after**: fetches ONLY the articles ADDED since the last run (it
  reads the "updated" timestamp and queries category-additions since then).
  A normal monthly run is **~12-15 API calls** — verified: e.g. History added
  16 articles in June, fetched in 1 call instead of re-scanning all 2,586.
- **Append-only** (mostly): it never removes a title just because it left the
  vital list — once-vital is good enough. The one exception is the self-clean below.
- **Duplicates**: the scraper never removes titles, so if an article is renamed
  both its old and new name may end up in the file. This is harmless — the app
  de-dupes by page ID when displaying a batch (two names of the same article share
  one ID, so it shows once). No cleanup step, no wasted API calls.

So: run it once now, then re-run whenever you like (≈monthly) — each later run is
tiny. Commit the updated `vital.json` after each run.

Rare edge case: if Wikipedia ever wholesale-restructures the vital category
system (a one-time event, not Cewbot's routine per-article trickle), one run
would see "everything new" and re-fetch the lot, then return to cheap. Use
`--full` anytime to force a clean rebuild.

## Deploy

Put `index.html` and `vital.json` in the same folder on GitHub Pages. Done.
`build_vital.py` is a local tool — it is NOT deployed.

> The included `vital.json` is a tiny placeholder so the tab renders. Replace it
> by running `build_vital.py`.

## Automating with GitHub Actions (optional)

`update-vital.yml` runs the **incremental** scraper monthly and commits the
updated `vital.json` back to the repo. Setup:

1. **Seed the first vital.json locally** and commit it:
   ```bash
   python3 build_vital.py
   git add vital.json && git commit -m "seed vital.json" && git push
   ```
   (Do the initial full scan locally — GitHub's data-center IPs can
   get rate-limited on a scan that big. The monthly incremental runs are tiny and
   fine on Actions.)

2. **Add the workflow:** put `update-vital.yml` at
   `.github/workflows/update-vital.yml` in your repo.

3. That's it. It will:
   - run 06:00 UTC on the 1st of each month (and on-demand via the Actions tab),
   - check out the repo (so it reads your committed `vital.json` + timestamp),
   - run the incremental scraper (~12-15 calls),
   - commit & push the updated `vital.json` only if it changed.

### Why it needs to commit back

GitHub Actions runners are ephemeral — a fresh VM each run. Incrementalism only
works because the workflow reads the committed `vital.json` (for its `updated`
timestamp) and pushes the updated file back. The workflow has `contents: write`
permission and uses the built-in `GITHUB_TOKEN` for the push — no secrets needed.

### Failure handling

If some topics fail mid-run (e.g. transient rate-limiting), the scraper:
- still saves the IDs that *did* resolve (append-only, so partial progress sticks),
- does **not** advance the `updated` timestamp, so the next run re-covers the
  missed window,
- exits non-zero so the Action shows a warning.

You can always force a clean rebuild from the Actions tab → "Run workflow" →
tick **full**, or locally with `python3 build_vital.py --full`.

## Changelog

Each run also writes a human-readable **`vital_changelog.md`** next to `vital.json`,
recording — newest first — the date, run type, running total, and the new vital
articles added that run, grouped by topic. The GitHub Action commits it alongside
`vital.json`, so you get a browsable history of what entered the vital list each
month. Disable with `--changelog ""` if you don't want it.
