#!/usr/bin/env python3
"""
build_vital.py  --  Incremental, append-only builder for Polymath's vital.json

FIRST RUN  (no existing data): fetches the full membership of all 11 vital topic
           categories once and writes the article titles out. ~50 calls, a few min.
LATER RUNS (incremental): fetches ONLY the articles ADDED since the last run
           (using category-add timestamps) and appends their titles.
           A normal monthly run is ~12-15 API calls.

APPEND-ONLY: never removes titles. If an article was ever vital, it stays.

OUTPUT (vital.json):
  { "titles": ["3D printing", "COBOL", ...], "updated": "2026-06-14T00:00:00Z" }
  Keep "updated" so the next run is incremental.

  Stores TITLES (not page IDs): the app fetches articles by title with redirects
  enabled, so renames resolve automatically; and the monthly re-scrape picks up new
  titles anyway. This skips the slow ID-resolution step entirely.

RUN:
  python3 build_vital.py            # create or update ./vital.json
  python3 build_vital.py --out path.json
  python3 build_vital.py --full     # force a full re-scan
"""

import os, json, time, argparse, datetime
import urllib.request, urllib.parse, urllib.error
import sys, time as _time

API = "https://en.wikipedia.org/w/api.php"
UA = "PolymathVitalBuilder/1.0 (personal learning project; runs monthly; contact via your GitHub)"

_START = _time.time()
def log(msg):
    """Print with an elapsed-time prefix and flush immediately so progress is
    visible in real time (no buffering, even when piped to a file)."""
    el = int(_time.time() - _START)
    print(f"[{el//60:02d}:{el%60:02d}] {msg}", flush=True)

TOPICS = [
    "Arts", "Biology and health sciences", "Everyday life", "Geography", "History",
    "Mathematics", "People", "Philosophy and religion", "Physical sciences",
    "Society and social sciences", "Technology",
]
DELAY = 6.0       # seconds between serial calls. Very gentle — stays well under the
                  # so 429s basically never happen. Lower (e.g. --delay 2) only if you
                  # know your IP tolerates it; raise (--delay 6) if you still see any 429.
MAXLAG = 5        # ask servers to defer us when they're busy (prevents 429s preemptively)


def api_get(params, tries=8):
    global DELAY
    params = dict(params)
    params.update({"format": "json", "formatversion": "2", "maxlag": str(MAXLAG)})
    url = API + "?" + urllib.parse.urlencode(params)
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
            # maxlag: the API returns 200 with an error body asking us to wait.
            if isinstance(data, dict) and data.get("error", {}).get("code") == "maxlag":
                wait = 10 * (attempt + 1)
                log(f"    server lagged (maxlag), waiting {wait}s...")
                time.sleep(wait)
                continue
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # A 429 slipped through -> we're going too fast. Permanently raise the
                # base delay so the REST of the run settles into a pace that won't 429.
                if DELAY < 12:
                    DELAY = round(DELAY + 1.5, 1)
                    log(f"    429 hit -> raising delay to {DELAY}s for the rest of the run")
                ra = e.headers.get("Retry-After") if e.headers else None
                try:
                    wait = int(ra) if ra else 0
                except (TypeError, ValueError):
                    wait = 0
                if wait <= 0:
                    wait = min(120, 15 * (attempt + 1))
                log(f"    waiting {wait}s (attempt {attempt + 1}/{tries})...")
                time.sleep(wait)
                continue
            if e.code in (503, 502, 504):
                wait = min(120, 10 * (attempt + 1))
                log(f"    server busy ({e.code}), waiting {wait}s...")
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            wait = min(60, 8 * (attempt + 1))
            log(f"    network hiccup ({e}), waiting {wait}s...")
            time.sleep(wait)
            continue
    raise RuntimeError("Gave up after repeated rate-limit / lag responses")


def fetch_topic_titles(topic, since=None):
    """Article titles in a vital topic category. If `since` given, only those
    ADDED at/after that time (by category-add timestamp). Members are Talk: pages."""
    cat = "Category:Wikipedia level-5 vital articles in " + topic
    titles, cont, page = [], None, 0
    while True:
        params = {
            "action": "query", "list": "categorymembers", "cmtitle": cat,
            "cmlimit": "500", "cmnamespace": "1", "cmprop": "title|timestamp",
        }
        if since:
            params["cmsort"] = "timestamp"
            params["cmdir"] = "older"   # newest -> oldest
            params["cmend"] = since     # stop at last run time
        if cont:
            params["cmcontinue"] = cont
        data = api_get(params)
        for m in data.get("query", {}).get("categorymembers", []):
            t = m.get("title", "")
            if t.startswith("Talk:"):
                titles.append(t[5:])
        page += 1
        cont = data.get("continue", {}).get("cmcontinue")
        if not cont:
            break
        time.sleep(DELAY)
    mode = "new since last run" if since else "full"
    log(f"    {topic}: {len(titles)} titles ({mode}, {page} call{'s' if page != 1 else ''})")
    return titles


def load_existing(path):
    """Return (set_of_titles, last_updated_iso_or_None)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return set(), None
    if isinstance(data, dict):
        # Current format: {"titles":[...], "updated": "..."}.
        # Also tolerate an old {"ids":[...]} file by ignoring it (forces a fresh full run).
        if "titles" in data:
            return set(data.get("titles", [])), data.get("updated")
        return set(), None
    # bare array -> treat as titles, no timestamp (forces full run)
    try:
        return set(str(x) for x in data), None
    except TypeError:
        return set(), None


def save(path, titles, updated_iso):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"titles": sorted(titles), "updated": updated_iso}, f, ensure_ascii=False, separators=(",", ":"))
    log(f"Wrote {path}: {len(titles)} titles, {os.path.getsize(path)//1024} KB, updated {updated_iso}")


def write_changelog(path, run_iso, added_titles, total, title_topic, is_full, failed):
    """Prepend a dated section to a human-readable Markdown changelog.

    Records: timestamp, run type, running total, how many added, and the new titles
    grouped by the topic they came from. Newest entry goes at the top.
    """
    date = run_iso[:10]
    lines = []
    lines.append(f"## {date}  ({'full build' if is_full else 'incremental'})")
    lines.append("")
    lines.append(f"- Run at: {run_iso}")
    lines.append(f"- Total titles in list: **{total}**")
    lines.append(f"- New this run: **{len(added_titles)}**")
    if failed:
        lines.append(f"- ⚠️ Topics that failed (will retry next run): {', '.join(failed)}")
    lines.append("")

    if added_titles:
        # Group the new titles by topic.
        by_topic = {}
        for t in added_titles:
            by_topic.setdefault(title_topic.get(t, "(unknown)"), []).append(t)
        for topic in sorted(by_topic):
            ts = sorted(by_topic[topic])
            lines.append(f"### {topic} (+{len(ts)})")
            for t in ts:
                lines.append(f"- {t}")
            lines.append("")
    else:
        lines.append("_No new titles this run._")
        lines.append("")
    lines.append("---")
    lines.append("")
    new_section = "\n".join(lines)

    header = "# Polymath vital-articles changelog\n\nNewest runs first. Each section lists the vital articles added that run.\n\n"
    # Read existing (minus its header) and prepend the new section.
    old_body = ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read()
        # strip the old header if present
        if existing.startswith(header):
            old_body = existing[len(header):]
        else:
            old_body = existing
    except FileNotFoundError:
        old_body = ""

    with open(path, "w", encoding="utf-8") as f:
        f.write(header + new_section + old_body)
    log(f"Updated changelog: {path} (+{len(added_titles)} this run)")


def main():
    global DELAY
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="vital.json")
    ap.add_argument("--changelog", default="vital_changelog.md",
                    help="Markdown changelog path (set empty to disable)")
    ap.add_argument("--full", action="store_true", help="force a full re-scan")
    ap.add_argument("--delay", type=float, default=DELAY,
                    help="seconds between calls (default %(default)s, very gentle to avoid rate limits)")
    args = ap.parse_args()
    DELAY = max(0.0, args.delay)
    log(f"Pace: 1 call every {DELAY}s (slow & steady to avoid rate limits), maxlag={MAXLAG}s")
    est_full = int(55 * DELAY / 60) + 1
    log(f"(A full run is ~50 calls -> roughly {est_full} min at this pace. Incremental runs are ~12-15 calls.)")

    existing, last_updated = load_existing(args.out)
    run_started = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    incremental = bool(last_updated) and not args.full

    if incremental:
        log(f"Incremental run: additions since {last_updated}\nExisting: {len(existing)} IDs\n")
    else:
        why = "forced --full" if args.full else "no prior timestamp"
        log(f"Full run ({why}). Existing: {len(existing)} IDs\n")

    new_titles = []
    title_topic = {}   # title -> topic it was first seen in (for the changelog)
    failed = []
    for topic in TOPICS:
        try:
            ts = fetch_topic_titles(topic, since=last_updated if incremental else None)
            new_titles.extend(ts)
            for t in ts:
                title_topic.setdefault(t, topic)
        except Exception as e:
            log(f"  !! {topic} failed: {e} (skipping; will retry next run)")
            failed.append(topic)
        time.sleep(DELAY)

    new_set = set(new_titles)
    merged = existing | new_set
    # Titles genuinely new to the file this run (for the changelog).
    actually_added = sorted(new_set - existing)
    added = len(actually_added)
    log(f"\n{len(new_titles)} titles fetched ({len(new_set)} unique).")
    log(f"New titles added: {added}. Total now: {len(merged)}")

    # Note: append-only. We never remove titles. If an article is renamed, both old
    # and new names may sit in the file — harmless, because the app de-dupes by page ID
    # at display time (two names of the same article share one ID, shown once).

    # Only advance the "updated" timestamp if EVERY topic succeeded. On a partial
    # failure, keep the previous timestamp so the next run re-covers the same window
    # (the titles that DID succeed are still appended — append-only, no harm in overlap).
    if failed:
        stamp = last_updated  # may be None on a failed first run -> stays full next time
        log(f"WARNING: {len(failed)} topic(s) failed ({', '.join(failed)}). "
              f"Keeping previous timestamp so they retry next run.")
    else:
        stamp = run_started

    save(args.out, merged, stamp)
    if args.changelog:
        write_changelog(args.changelog, run_started, actually_added, len(merged),
                        title_topic, is_full=not incremental, failed=failed)
    if incremental and added == 0 and not failed:
        log("(Nothing new since last run -- you're up to date.)")

    # Non-zero exit on failure so CI can surface it (but the partial result is saved).
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
