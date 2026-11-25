"""Map layers for the viewer, all the same size per region so they stack exactly:
base (land/water), one transparent layer per run with its storm tracks, an agreement
layer, and the county/state lines on top.

Colours: Okabe-Ito per run (no greens/yellows), newest run in black; agreement in a
single-hue purple scale. Colour-vision-deficiency safe.
"""
from __future__ import annotations

import io

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from matplotlib.colors import to_hex
from scipy.ndimage import maximum_filter
from shapely.geometry import box
from PIL import Image

import trends

HRRR_CRS = ccrs.LambertConformal(
    central_longitude=-97.5, central_latitude=38.5, standard_parallels=(38.5, 38.5),
    globe=ccrs.Globe(ellipse="sphere", semimajor_axis=6371229, semiminor_axis=6371229))
PC = ccrs.PlateCarree()
W_IN, DPI = 11.0, 110

# newest first
RUN_COLORS = ["#000000", "#0072B2", "#E69F00", "#CC79A7", "#56B4E9", "#D55E00"]
AGREE_COLORS = [to_hex(plt.get_cmap("Purples")(x)) for x in np.linspace(0.3, 1.0, trends.N_RUNS)]

REGIONS = {
    "plains":     ("Plains", [-106.0, -86.0, 29.0, 47.0]),
    "midwest":    ("Midwest", [-100.0, -84.0, 37.0, 46.5]),
    "iowa":       ("Iowa", [-98.0, -89.0, 39.8, 44.6]),
    "s_plains":   ("Southern Plains", [-104.5, -92.0, 29.5, 38.5]),
    "c_plains":   ("Central Plains", [-104.5, -92.5, 35.5, 43.5]),
    "n_plains":   ("Northern Plains", [-105.0, -93.0, 41.0, 49.0]),
    "ohio_valley": ("Ohio Valley", [-93.0, -80.5, 35.0, 42.0]),
    "southeast":  ("Southeast", [-95.0, -79.0, 29.0, 37.5]),
}


def compute_extent(regions):
    ex = [REGIONS[r][1] for r in regions]
    return (min(e[0] for e in ex) - 1.5, max(e[1] for e in ex) + 1.5,
            min(e[2] for e in ex) - 1.0, max(e[3] for e in ex) + 1.0)


def region_mask(grid, region):
    w, e, s, n = REGIONS[region][1]
    return (grid.lon >= w) & (grid.lon <= e) & (grid.lat >= s) & (grid.lat <= n)


def _aspect(extent):
    pts = HRRR_CRS.transform_points(PC, np.array([extent[0], extent[1], extent[0], extent[1]]),
                                    np.array([extent[2], extent[2], extent[3], extent[3]]))
    return (pts[:, 1].max() - pts[:, 1].min()) / (pts[:, 0].max() - pts[:, 0].min())


def _png(fig, transparent):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, transparent=transparent)
    img = Image.open(io.BytesIO(buf.getvalue()))
    if transparent:
        img = img.convert("RGBA").quantize(colors=64, method=Image.Quantize.FASTOCTREE)
    else:
        img = img.convert("RGB").quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def region_slice(grid, key, pad=40):
    """Grid rows/columns covering a region's map (plus a margin), so contouring stays quick."""
    jj, ii = np.where(region_mask(grid, key))
    return (slice(max(jj.min() - pad, 0), jj.max() + pad + 1),
            slice(max(ii.min() - pad, 0), ii.max() + pad + 1))


class Region:
    """One figure per region, reused for every layer. x, y, mask: the region's piece of
    the grid (see region_slice); every field passed in is already cut to that piece."""

    def __init__(self, key, x, y, mask):
        self.key = key
        self.name, self.extent = REGIONS[key]
        aspect = _aspect(self.extent)
        self.fig = plt.figure(figsize=(W_IN, W_IN * aspect), dpi=DPI)
        self.ax = self.fig.add_axes([0, 0, 1, 1], projection=HRRR_CRS)
        self.ax.set_extent(self.extent, crs=PC)
        self.x, self.y, self.mask = x, y, mask

    def _save(self, artists, transparent=True, frame=False):
        self.ax.spines["geo"].set_visible(frame)
        png = _png(self.fig, transparent)
        for a in artists:
            a.remove()
        return png

    # ---- static layers
    def base(self):
        ax = self.ax
        arts = [ax.add_feature(cfeature.LAND.with_scale("50m"), facecolor="#f4f2ee", zorder=0),
                ax.add_feature(cfeature.OCEAN.with_scale("50m"), facecolor="#dfe7ee", zorder=0),
                ax.add_feature(cfeature.LAKES.with_scale("50m"), facecolor="#dfe7ee", edgecolor="#9fb0bf",
                               lw=0.4, zorder=0)]
        return self._save(arts, transparent=False, frame=True)

    def lines(self):
        ax = self.ax
        w, e, s, n = self.extent
        bb = box(w - 1, s - 1, e + 1, n + 1)
        counties = [g for g in cfeature.NaturalEarthFeature("cultural", "admin_2_counties", "10m").geometries()
                    if g.intersects(bb)]
        arts = [ax.add_geometries(counties, PC, facecolor="none", edgecolor="#9a9a9a", linewidth=0.25, zorder=5),
                ax.add_feature(cfeature.STATES.with_scale("50m"), edgecolor="#3b3b3b", linewidth=0.8, zorder=5),
                ax.add_feature(cfeature.BORDERS.with_scale("50m"), edgecolor="#3b3b3b", linewidth=0.9, zorder=5),
                ax.add_feature(cfeature.COASTLINE.with_scale("50m"), edgecolor="#3b3b3b", linewidth=0.7, zorder=5)]
        return self._save(arts, frame=True)

    # ---- per window
    def _fat(self, uh):
        """Tracks a bit wider than one 3 km grid point, so they show at region scale."""
        return maximum_filter(uh, size=3)

    def tracks(self, uh, color, newest=False):
        f = self._fat(uh)
        ax = self.ax
        cf_ = ax.contourf(self.x, self.y, f, levels=[trends.THRESH, 1e6], colors=[color],
                          alpha=0.32 if newest else 0.62, transform=HRRR_CRS, zorder=2)
        cl = ax.contour(self.x, self.y, f, levels=[trends.THRESH], colors=[color],
                        linewidths=1.7 if newest else 1.1, transform=HRRR_CRS, zorder=3)
        return self._save([cf_, cl])

    def agree(self, cnt, newest_uh):
        ax = self.ax
        c = cnt.astype(np.float32)
        arts = []
        if c.max() > 0:
            arts.append(ax.contourf(self.x, self.y, c, levels=np.arange(0.5, trends.N_RUNS + 1),
                                    colors=AGREE_COLORS, alpha=0.9, transform=HRRR_CRS, zorder=2))
        if newest_uh is not None and (newest_uh >= trends.THRESH).any():
            arts.append(ax.contour(self.x, self.y, self._fat(newest_uh), levels=[trends.THRESH],
                                   colors=["#000000"], linewidths=1.0, transform=HRRR_CRS, zorder=3))
        return self._save(arts)

    def close(self):
        plt.close(self.fig)
