# Basemap — Natural Earth 50m land

`frontend/public/basemap/ne-50m-land.json` — 1.6 MB (518 KB gzipped), 1,422 rings,
60,669 points.

## Provenance

*Natural Earth* — `ne_50m_land`, from
[`nvkelso/natural-earth-vector`](https://github.com/nvkelso/natural-earth-vector),
`geojson/ne_50m_land.geojson`.

Authors: Tom Patterson, Nathaniel Vaughn Kelso, and contributors.

**Public domain.** Verbatim from <https://www.naturalearthdata.com/about/terms-of-use/>:

> All versions of Natural Earth raster + vector map data found on this website are in the
> public domain. You may use the maps in any manner, including modifying the content and
> design, electronic dissemination, and offset printing. The primary authors, Tom
> Patterson and Nathaniel Vaughn Kelso, and all other contributors renounce all financial
> claim or exclusive rights to this map data.

This is the licence `backend/scripts/fetch_fixtures.py` accepts, and it is why a real
basemap was possible without a tile service.

## Pinned

```
sha256  e874b27a51d146452be360cafb3cc50c86001074a67d534113e6534682f9826b
```

The digest is asserted in `frontend/tests/geo-map.test.ts`, and
`frontend/.prettierignore` excludes this path so reformatting cannot change it.

**Why pin a public-domain file.** A silent upstream revision would change what an examiner
is shown without changing a line of code, and the two things most likely to be relied on —
the coastline and the zoom floor derived from its resolution — would both move silently.
Updating it is a deliberate act.

## Regenerating

```bash
curl -sSfL https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_land.geojson \
  -o frontend/public/basemap/ne-50m-land.json
sha256sum frontend/public/basemap/ne-50m-land.json
```

If the digest changes, update `BASEMAP_SHA256` in `tests/geo-map.test.ts` — and check the
second assertion in that file first, which measures the median vertex spacing. That number
is what sets the map's zoom floor, so a finer upstream release means `MIN_VIEW_WIDTH_KM` in
`src/lib/geo-map.ts` is wrong and the floor is needlessly restrictive.

## Why 50m and not 110m or 10m

The choice was made by measuring median vertex spacing, which is what decides whether a
coastline is legible at the zoom an examiner actually uses:

| Scale | Size | Points | Median vertex spacing |
|---|---|---|---|
| 110m | 135 KB | 5,143 | **62.8 km** |
| **50m** | **1.6 MB** | **60,669** | **7.6 km** |
| 10m | 9.9 MB | 446,175 | 1.5 km |

110m was tried first and rendered no coastline at all below about 20° of span: Britain's
110m outline is 44 points for the whole island, so at regional zoom it is a few enormous
straight lines. 10m is a 9.9 MB asset for 1.5 km spacing, which is still not street level,
and 6.5× the payload. 50m is the smallest scale at which the outline reads as geography.

## The ceiling, stated plainly

There is no street-level coastline in public-domain data. At 50m the vertices are 7.6 km
apart, so below roughly 15 km across the view there is often **no outline in frame at
all** — measured: a point 10 km from the sea can show solid land, because no vertex falls
inside the frame. Zoom is clamped there rather than drawing a shape the source does not
contain, and the panel's caption says this to the reader.

The graticule, the scale bar and the distances between points are exact at every zoom. Only
the geography is approximate, and only below about 15 km across.

## Why it is bundled rather than fetched

A tile request carries a viewport, and this system's viewport comes from the case's own
coordinates — so a tile service learns where the photographs were taken before anyone has
decided that is acceptable to disclose. Bundling the geometry removes the question entirely:
the only request the page makes is for its own basemap file, over the same origin, carrying
nothing derived from the evidence.