# HRRR Trends

**Run-to-run consistency of HRRR storm tracks, for storm chasing.** Every hour a new
HRRR run comes out, and the storms it forecasts shift, appear and disappear from run to
run. This page lays the newest run and the five before it on top of each other, hour
by hour, so you can see at a glance whether the model keeps putting rotating storms in
the same place (a forecast you can lean on) or keeps changing its mind.

Live page (when running): https://daandm87-cmyk.github.io/HRRR_Trends/

## What's on the page

- **All runs on top of each other:** each run's rotating storms (2-5 km updraft
  helicity of 75 m2/s2 or more during the hour) in its own colour, newest in black.
  Click a run to switch it off.
- **Flip through the runs:** oldest to newest, one at a time, so a shift north or a
  slowdown shows up as movement.
- **Agreement between runs:** how many of the six runs have a rotating storm within
  25 miles of each point, with the newest run's storms outlined.
- **Storms per hour table:** the number of separate rotating storms each run has in
  the region, hour by hour, to spot timing trends (earlier, later, more, fewer).
  Click an hour to jump there.

Each hour is the hour the storm tracks cover (HRRR stores the maximum updraft
helicity over the past hour); times in US Central and UTC. Regions: Plains, Midwest,
Iowa, Southern / Central / Northern Plains, Ohio Valley, Southeast. Palettes avoid
red/green contrasts.

## How it works

1. **Newest run:** the latest HRRR run whose 18-hour forecast is posted (about 1.5 hours
   after the run starts), plus the five runs before it.
2. **Field:** `MXUPHL` 2-5 km from the `wrfsfc` files: only that one message is
   downloaded per forecast hour (about 40 KB), via the `.idx` byte ranges.
3. **Storms:** patches of UH of 75 m2/s2 or more, at least two 3 km grid points;
   patches less than about 18 km apart count as one storm in the table.
4. **Agreement:** per run, all points within 40 km (25 miles) of a storm; then counted
   over the runs that reach that hour (00/06/12/18Z runs go to 48 hours, the others 18).

Checked on 2026-04-17 (tornadoes in Minnesota, Wisconsin, Iowa, Illinois, Missouri):
54 % of the 125 tornado reports lie within 40 km of a storm track of the 17Z run in the
same hour, against 22 % and 42 % an hour earlier or later, so the hour labels line up.

## Running it

GitHub Actions, on demand (the `HRRR Trends` workflow):

- **Once:** leave everything blank: the newest complete run and the five before it.
- **Keep updating:** set `hours` (up to 12): rebuilt whenever a new run is complete.
  Jobs longer than ~5 hours hand over to a fresh job by themselves.
- **A past day:** set `run` to the newest run to show, UTC, e.g. `2026-04-17T17`.

Locally:

```
pip install -r requirements.txt
python run.py                          # newest complete run, once
python run.py --run 2026-04-17T17      # a past day
python run.py --live --hours 6
```

The site is written to `docs/` (open it through a local web server, e.g.
`python -m http.server` in that folder). A full build takes well under a minute.

## Data

HRRR from the NOAA Open Data Dissemination program on AWS (`noaa-hrrr-bdp-pds`).
Map outlines: Natural Earth.
