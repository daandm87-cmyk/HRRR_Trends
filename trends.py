"""HRRR run-to-run consistency: where the last few HRRR runs put rotating storms.

Field: MXUPHL 2-5 km, the hourly maximum updraft helicity in HRRR's wrfsfc files
(about 40 KB per message, so six runs x 18 hours is a few MB). A "rotating storm"
is a patch of 2-5 km UH >= 75 m2/s2, a common supercell threshold for 3 km models.

For each valid hour (and for all hours together) every run gives:
- its storm tracks (the UH patches), drawn in that run's colour;
- the number of separate rotating storms in each region;
- agreement: how many runs have a rotating storm within 25 miles (40 km) of a point.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import threading

import numpy as np
from scipy import ndimage as ndi

import data
import grids

UH = ("MXUPHL", "5000-2000 m above ground")
THRESH = 75.0              # m2/s2: rotating storm
MIN_CELLS = 2              # patches of 1 grid point (3 km) are noise
MERGE_CELLS = 3            # storm count: patches within 2 x 3 points (~18 km) are one storm
NEIGH_KM = 40.0            # agreement radius (~25 mi)
N_RUNS = 6                 # newest run + the 5 before it
HOURS = 18                 # valid hours shown: the newest run's 1-18 h forecast
EIGHT = np.ones((3, 3), bool)


def fmax(run):
    """Longest forecast hour of a run: 00/06/12/18Z go to 48 h, the others 18 h."""
    return 48 if run.hour % 6 == 0 else 18


def fhour(run, valid):
    return int(round((valid - run).total_seconds() / 3600))


def covers(run, valid):
    return 1 <= fhour(run, valid) <= fmax(run)


def runs_for(newest):
    return [newest - dt.timedelta(hours=k) for k in range(N_RUNS)]


def valid_hours(newest):
    return [newest + dt.timedelta(hours=h) for h in range(1, HOURS + 1)]


def windows(newest):
    """[(id, [valid hours])]: all hours together first, then each hour."""
    hrs = valid_hours(newest)
    return [("all", hrs)] + [(v.strftime("%Y%m%d%H"), [v]) for v in hrs]


def newest_run(now, look_back=6):
    """Newest HRRR run whose 18 h forecast is posted (about 1.5 h after its start)."""
    base = now.replace(minute=0, second=0, microsecond=0)
    for k in range(look_back):
        run = base - dt.timedelta(hours=k)
        try:
            data.read_idx(data.hrrr_url(run, 18, "sfc"))
            return run
        except data.Missing:
            continue
    raise data.Missing("no complete HRRR run in the last %d hours" % look_back)


class Store:
    """Hourly UH fields, cropped to the regions' box, kept between builds."""

    def __init__(self, extent):
        self.extent = extent
        self.grid = None
        self.uh = {}
        self._lock = threading.Lock()

    def _load(self, run, f):
        try:
            path = data.fetch(data.hrrr_url(run, f, "sfc"), [UH])[UH]     # downloads in parallel
        except data.Missing:
            return None
        with self._lock:                       # eccodes decoding isn't thread-safe
            uh, g = data.decode(path)
            if self.grid is None:
                self.grid = grids.AnalysisGrid(g, self.extent, step=1)
        a = self.grid.take(np.nan_to_num(uh, nan=0.0))
        return a.astype(np.float16)

    def fill(self, pairs):
        todo = [p for p in dict.fromkeys(pairs) if p not in self.uh]
        with cf.ThreadPoolExecutor(12) as ex:
            for p, a in zip(todo, ex.map(lambda p: self._load(*p), todo)):
                self.uh[p] = a
        return sum(1 for p in todo if self.uh[p] is None)

    def forget(self, keep_runs):
        keep = set(keep_runs)
        for p in [p for p in self.uh if p[0] not in keep]:
            self.uh.pop(p)


def clean(uh):
    """UH with patches smaller than MIN_CELLS grid points set to 0."""
    lab, n = ndi.label(uh >= THRESH, structure=EIGHT)
    if n == 0:
        return uh
    size = np.bincount(lab.ravel())
    small = size < MIN_CELLS
    small[0] = False
    out = uh.copy()
    out[small[lab]] = 0.0
    return out


def window_fields(store, runs, hrs):
    """{run: max UH over the window's hours (cleaned), or None if the run doesn't reach them}"""
    out = {}
    for r in runs:
        fs = [store.uh.get((r, fhour(r, v))) for v in hrs if covers(r, v)]
        fs = [a for a in fs if a is not None]
        out[r] = clean(np.max(np.stack(fs), axis=0).astype(np.float32)) if fs else None
    return out


def near(uh, dx_km):
    """Points within NEIGH_KM of a rotating storm."""
    m = uh >= THRESH
    if not m.any():
        return m
    return ndi.distance_transform_edt(~m) * dx_km <= NEIGH_KM


def agreement(fields, dx_km):
    """(count of runs with a rotating storm within 40 km, number of runs that reach the window)"""
    have = [a for a in fields.values() if a is not None]
    if not have:
        return None, 0
    cnt = np.zeros(have[0].shape, np.int8)
    for a in have:
        cnt += near(a, dx_km)
    return cnt, len(have)


def storms(uh, mask):
    """Number of separate rotating storms touching the region mask. One storm's hourly
    swath often breaks into a few patches where its UH dips below the threshold, so
    patches less than ~18 km apart count as one storm."""
    m = uh >= THRESH
    if not m.any():
        return 0
    lab, n = ndi.label(ndi.binary_dilation(m, structure=EIGHT, iterations=MERGE_CELLS), structure=EIGHT)
    lab[~m] = 0
    return int(len(np.unique(lab[mask & (lab > 0)])))
