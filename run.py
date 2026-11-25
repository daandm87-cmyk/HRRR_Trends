"""HRRR Trends driver: run-to-run consistency of HRRR's rotating storms.

Once, for the newest complete run:   python run.py
A past day (newest run given, UTC):  python run.py --run 2026-04-17T17
Live (chase day):                    python run.py --live --hours 6   (rebuilds when a new run lands)

Output (default docs/): index.html viewer, trends.json,
img/base/<region>.png + <region>_lines.png, img/<newest run>/<region>/<window>/<run>.png + agree.png
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK_EVERY = 120                      # live: seconds between looks for a new run
KEEP_SETS = 2                          # live: map sets kept on the site (newest + one before)


# ------------------------------------------------------------------ time labels
def _nth_sunday(year, month, n):
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def central(t_utc):
    """(local datetime, 'CDT'/'CST') for US Central time, from a naive UTC datetime."""
    y = t_utc.year
    start = dt.datetime.combine(_nth_sunday(y, 3, 2), dt.time(8))    # 2 AM CST = 08 UTC
    end = dt.datetime.combine(_nth_sunday(y, 11, 1), dt.time(7))     # 2 AM CDT = 07 UTC
    if start <= t_utc < end:
        return t_utc - dt.timedelta(hours=5), "CDT"
    return t_utc - dt.timedelta(hours=6), "CST"


def _hr(t):
    return t.strftime("%I %p").lstrip("0")


def hour_label(v):
    """The hour ENDING at v (HRRR's UH is the maximum over the past hour)."""
    a, tz = central(v - dt.timedelta(hours=1))
    b, _ = central(v)
    sa, sb = _hr(a), _hr(b)
    if sa[-2:] == sb[-2:]:
        sa = sa[:-3]
    return "%s %s-%s %s  (%s-%sZ)" % (a.strftime("%a"), sa, sb, tz, (v - dt.timedelta(hours=1)).strftime("%H"),
                                       v.strftime("%H"))


def short_hour(v):
    """Start of the hour ending at v, e.g. "6p" for 6-7 PM."""
    a, _ = central(v - dt.timedelta(hours=1))
    return a.strftime("%I%p").lstrip("0").lower()[:-1]       # "6p", "12a"


def span_label(hrs):
    a, tz = central(hrs[0] - dt.timedelta(hours=1))
    b, _ = central(hrs[-1])
    return "All %d hours: %s %s - %s %s %s" % (len(hrs), a.strftime("%a"), _hr(a), b.strftime("%a"), _hr(b), tz)


def run_label(r):
    lt, tz = central(r)
    return "%sZ run" % r.strftime("%H"), "started %s %s %s" % (lt.strftime("%a"), _hr(lt), tz)


# ------------------------------------------------------------------ build one map set
def _render_region(args):
    """Worker: every layer of one region. Returns (region, {window: entry})."""
    reg, x, y, mask, newest, runs, fields_by_window, cnts, out = args
    import render
    import trends
    rr = render.Region(reg, x, y, mask)
    base_dir = os.path.join(out, "img", "base")
    os.makedirs(base_dir, exist_ok=True)
    for name, fn in (("%s.png" % reg, rr.base), ("%s_lines.png" % reg, rr.lines)):
        p = os.path.join(base_dir, name)
        if not os.path.exists(p):
            with open(p, "wb") as fh:
                fh.write(fn())
    entries = {}
    for wid, fields in fields_by_window.items():
        folder = os.path.join(out, "img", newest.strftime("%Y%m%d%H"), reg, wid)
        os.makedirs(folder, exist_ok=True)
        e = {"runs": {}, "agree": 0}
        for k, r in enumerate(runs):
            rid = r.strftime("%Y%m%d%H")
            a = fields.get(r)
            if a is None:
                e["runs"][rid] = None                 # this run doesn't reach the window
                continue
            n = trends.storms(a, rr.mask)
            e["runs"][rid] = n
            if (a >= trends.THRESH).any():
                with open(os.path.join(folder, rid + ".png"), "wb") as fh:
                    fh.write(rr.tracks(a, render.RUN_COLORS[k], newest=k == 0))
        cnt, ncov = cnts[wid]
        if cnt is not None and cnt[rr.mask].max() > 0:
            with open(os.path.join(folder, "agree.png"), "wb") as fh:
                fh.write(rr.agree(cnt, fields.get(runs[0])))
            e["agree"] = ncov
            e["agree_max"] = int(cnt[rr.mask].max())
        entries[wid] = e
    rr.close()
    return reg, entries


def build(store, newest, regions, out, live, workers):
    import render
    import trends
    t0 = time.time()
    runs = trends.runs_for(newest)
    hrs = trends.valid_hours(newest)
    pairs = [(r, trends.fhour(r, v)) for r in runs for v in hrs if trends.covers(r, v)]
    lost = store.fill(pairs)
    store.forget(runs)
    print("HRRR %sZ + 5 runs before: %d hourly UH fields (%d missing) in %.0f s" % (
        newest.strftime("%Y-%m-%d %H"), len(pairs), lost, time.time() - t0), flush=True)
    grid = store.grid
    dx_km = grid.dx / 1000.0
    wins = trends.windows(newest)
    fields_by_window, cnts = {}, {}
    for wid, vs in wins:
        f = trends.window_fields(store, runs, vs)
        fields_by_window[wid] = f
        cnts[wid] = trends.agreement(f, dx_km)
    t1 = time.time()
    jobs = []
    for reg in regions:                      # each worker gets only its region's piece of the grid
        sl = render.region_slice(grid, reg)
        cut = {w: {r: (a[sl] if a is not None else None) for r, a in f.items()} for w, f in fields_by_window.items()}
        ccut = {w: ((c[sl] if c is not None else None), n) for w, (c, n) in cnts.items()}
        jobs.append((reg, grid.x[sl[1]], grid.y[sl[0]], render.region_mask(grid, reg)[sl], newest, runs, cut, ccut, out))
    layers = {}
    if workers > 1:
        with cf.ProcessPoolExecutor(workers) as ex:
            for reg, entries in ex.map(_render_region, jobs):
                layers[reg] = entries
    else:
        for j in jobs:
            reg, entries = _render_region(j)
            layers[reg] = entries
    print("  drew %d regions in %.0f s" % (len(regions), time.time() - t1), flush=True)

    meta = {
        "set": newest.strftime("%Y%m%d%H"),
        "updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "live": live,
        "threshold": trends.THRESH,
        "radius_mi": round(trends.NEIGH_KM / 1.609),
        "runs": [{"id": r.strftime("%Y%m%d%H"), "name": run_label(r)[0], "started": run_label(r)[1],
                  "color": render.RUN_COLORS[k], "ends": hour_label(r + dt.timedelta(hours=trends.fmax(r)))}
                 for k, r in enumerate(runs)],
        "agree_colors": render.AGREE_COLORS,
        "regions": [{"id": r, "name": render.REGIONS[r][0]} for r in regions],
        "windows": [{"id": wid, "label": span_label(vs) if wid == "all" else hour_label(vs[0]),
                     "short": "all" if wid == "all" else short_hour(vs[0])} for wid, vs in wins],
        "layers": layers,
    }
    with open(os.path.join(out, "trends.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    shutil.copy2(os.path.join(HERE, "viewer.html"), os.path.join(out, "index.html"))
    open(os.path.join(out, ".nojekyll"), "w").close()        # GitHub Pages: serve as-is
    prune(out, newest)
    print("  map set %s ready in %.0f s total" % (meta["set"], time.time() - t0), flush=True)


def prune(out, newest):
    """Keep the newest KEEP_SETS map sets (the page may still be showing the one before)."""
    img = os.path.join(out, "img")
    sets = sorted(n for n in os.listdir(img) if n.isdigit())
    for s in sets[:-KEEP_SETS]:
        shutil.rmtree(os.path.join(img, s), ignore_errors=True)


def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def publish():
    """Run the publish hook (the GitHub workflow sets it to push the site)."""
    cmd = os.environ.get("HRRR_TRENDS_PUBLISH")
    if cmd:
        rc = os.system(cmd)
        print("  published" if rc == 0 else "  publish failed (%s)" % rc, flush=True)


def main():
    import render
    import trends
    import data
    ap = argparse.ArgumentParser(description="HRRR Trends: run-to-run consistency of HRRR storm tracks")
    ap.add_argument("--run", help="newest run to show, UTC (YYYY-MM-DDTHH); default: newest complete run")
    ap.add_argument("--live", action="store_true", help="keep going: rebuild whenever a new run lands")
    ap.add_argument("--hours", type=float, default=6, help="live: how long to keep running")
    ap.add_argument("--regions", default=",".join(render.REGIONS))
    ap.add_argument("--out", default=os.path.join(HERE, "docs"))
    ap.add_argument("--workers", type=int, default=max(1, min(4, (os.cpu_count() or 2) - 1)))
    a = ap.parse_args()
    regions = a.regions.split(",")
    os.makedirs(os.path.join(a.out, "img"), exist_ok=True)
    store = trends.Store(render.compute_extent(regions))

    if a.run:
        build(store, dt.datetime.strptime(a.run, "%Y-%m-%dT%H"), regions, a.out, False, a.workers)
        publish()
        return
    stop_at = utcnow() + dt.timedelta(hours=a.hours if a.live else 0)
    current = None
    while True:
        try:
            newest = trends.newest_run(utcnow())
        except data.Missing as e:
            newest = None
            print("  %s" % e, flush=True)
        if newest is not None and newest != current:
            try:
                build(store, newest, regions, a.out, a.live, a.workers)
                publish()
                current = newest
            except Exception as e:                   # e.g. an older run's file not there: try again later
                print("  build for %sZ failed: %s" % (newest.strftime("%H"), e), flush=True)
        if not a.live or utcnow() >= stop_at:
            break
        time.sleep(CHECK_EVERY)
    if current is None:
        sys.exit("no map set was built")


if __name__ == "__main__":
    main()
