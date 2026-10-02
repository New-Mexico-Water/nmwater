"""GeoJSON for the website: winding, rounding, and the river-view frame the website must recompute."""

import math

import pytest
from shapely.geometry import LineString, Point, Polygon

from nmwater.site import geo


def ring_area(coords):
    return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(coords, coords[1:])) / 2


def test_polygons_are_wound_counter_clockwise_and_rounded():
    cw = Polygon([(0, 0), (0, 1.23456), (1.23456, 1.23456), (1.23456, 0)])          # clockwise
    f = geo.feature(cw, tol=0.0, huc8="1")
    assert f["properties"] == {"huc8": "1"}
    ring = f["geometry"]["coordinates"][0]
    assert ring_area(ring) > 0                                                        # exterior counter-clockwise (RFC 7946)
    assert all(round(c, geo.DECIMALS) == c for pt in ring for c in pt)


def test_lines_points_and_empty_geometry():
    assert geo.feature(LineString([(0, 0), (1, 1)]), kind="river")["geometry"]["type"] == "LineString"
    assert geo.feature(Point(-106.12345, 35.6789), decimals=2)["geometry"]["coordinates"] in ((-106.12, 35.68), [-106.12, 35.68])
    assert geo.feature(LineString()) is None
    assert geo.collection([None, geo.feature(Point(0, 0))])["features"][0]["type"] == "Feature"


def test_river_view_frame_widens_the_extent_to_the_maps_aspect_ratio_and_centres_it():
    state = (-109.05, 31.33, -103.0, 37.0)
    ext = (-106.2, 35.5, -105.9, 35.8)                                              # a small river, square-ish extent
    p, rv = geo.river_view_frame(ext, state)
    assert p.width == 600 and rv[3] / rv[2] == pytest.approx(p.height / 600)         # same aspect ratio as the whole map
    x0, y1 = p.xy(ext[0], ext[1])
    x1, y0 = p.xy(ext[2], ext[3])
    assert rv[0] <= x0 and rv[0] + rv[2] >= x1 and rv[1] <= y0 and rv[1] + rv[3] >= y1
    assert (rv[0] + rv[2] / 2) == pytest.approx((x0 + x1) / 2) and (rv[1] + rv[3] / 2) == pytest.approx((y0 + y1) / 2)
    assert math.isfinite(rv[2])


def test_bounds_of_a_collection():
    fc = geo.collection([geo.feature(LineString([(-107.0, 35.0), (-105.5, 36.25)]))])
    assert geo.bounds_of(fc) == [-107.0, 35.0, -105.5, 36.25]
