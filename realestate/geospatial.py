from __future__ import annotations

import json
import math
from collections.abc import Iterable
from typing import Any

EARTH_MILES_PER_DEGREE_LAT = 69.0

Coordinate = tuple[float, float]


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def json_loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def extract_geometry(value: dict[str, Any] | str) -> dict[str, Any]:
    payload = json.loads(value) if isinstance(value, str) else value
    if payload.get("type") == "Feature":
        geometry = payload.get("geometry")
    else:
        geometry = payload
    if not isinstance(geometry, dict) or "type" not in geometry:
        raise ValueError("Expected a GeoJSON geometry or feature.")
    return geometry


def geometry_to_geojson(value: dict[str, Any] | str) -> str:
    return json_dumps(extract_geometry(value))


def feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def point_feature(
    lon: float,
    lat: float,
    properties: dict[str, Any] | None = None,
    feature_id: int | str | None = None,
) -> dict[str, Any]:
    feature: dict[str, Any] = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": properties or {},
    }
    if feature_id is not None:
        feature["id"] = feature_id
    return feature


def geometry_feature(
    geometry: dict[str, Any] | str,
    properties: dict[str, Any] | None = None,
    feature_id: int | str | None = None,
) -> dict[str, Any]:
    feature: dict[str, Any] = {
        "type": "Feature",
        "geometry": extract_geometry(geometry),
        "properties": properties or {},
    }
    if feature_id is not None:
        feature["id"] = feature_id
    return feature


def point_in_geometry(lon: float, lat: float, geometry: dict[str, Any] | str) -> bool:
    geom = extract_geometry(geometry)
    if geom["type"] == "Point":
        point = geom.get("coordinates") or []
        return len(point) >= 2 and float(point[0]) == lon and float(point[1]) == lat
    if geom["type"] in {"LineString", "MultiLineString"}:
        return any(
            haversine_miles(lon, lat, line_lon, line_lat) <= 0.00001
            for line in _iter_lines(geom)
            for line_lon, line_lat in _clean_ring(line)
        )
    if geom["type"] == "Polygon":
        return _point_in_polygon(lon, lat, geom.get("coordinates") or [])
    if geom["type"] == "MultiPolygon":
        return any(_point_in_polygon(lon, lat, polygon) for polygon in geom.get("coordinates") or [])
    raise ValueError(f"Unsupported geometry type for point lookup: {geom['type']}")


def boundary_distance_miles(
    lon: float,
    lat: float,
    geometry: dict[str, Any] | str,
) -> float | None:
    geom = extract_geometry(geometry)
    if geom["type"] == "Point":
        point = geom.get("coordinates") or []
        if len(point) >= 2:
            return haversine_miles(lon, lat, float(point[0]), float(point[1]))
        return None
    if geom["type"] in {"LineString", "MultiLineString"}:
        distances = [
            _distance_to_open_line_miles(lon, lat, line)
            for line in _iter_lines(geom)
            if len(_clean_ring(line)) >= 2
        ]
        return min(distances) if distances else None
    rings = list(_iter_rings(geom))
    if not rings:
        return None
    return min(_distance_to_ring_miles(lon, lat, ring) for ring in rings if len(ring) >= 2)


def distance_to_geometry_miles(
    lon: float,
    lat: float,
    geometry: dict[str, Any] | str,
) -> float | None:
    geom = extract_geometry(geometry)
    if geom["type"] in {"Polygon", "MultiPolygon"} and point_in_geometry(lon, lat, geom):
        return 0.0
    return boundary_distance_miles(lon, lat, geom)


def haversine_miles(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    radius_miles = 3958.7613
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return radius_miles * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def geometry_centroid(geometry: dict[str, Any] | str) -> Coordinate | None:
    geom = extract_geometry(geometry)
    if geom["type"] == "Point":
        coords = geom.get("coordinates") or []
        return (float(coords[0]), float(coords[1])) if len(coords) >= 2 else None
    if geom["type"] in {"LineString", "MultiLineString"}:
        vertices = [coord for line in _iter_lines(geom) for coord in _clean_ring(line)]
        if not vertices:
            return None
        return (
            sum(lon for lon, _lat in vertices) / len(vertices),
            sum(lat for _lon, lat in vertices) / len(vertices),
        )
    rings = list(_outer_rings(geom))
    if not rings:
        return None
    weighted_lon = 0.0
    weighted_lat = 0.0
    total_area = 0.0
    for ring in rings:
        centroid = _ring_centroid(ring)
        if centroid is None:
            continue
        lon, lat, area = centroid
        weight = abs(area) or 1.0
        weighted_lon += lon * weight
        weighted_lat += lat * weight
        total_area += weight
    if total_area:
        return weighted_lon / total_area, weighted_lat / total_area
    vertices = [coord for ring in rings for coord in _clean_ring(ring)]
    if not vertices:
        return None
    return (
        sum(lon for lon, _lat in vertices) / len(vertices),
        sum(lat for _lon, lat in vertices) / len(vertices),
    )


def geometry_bbox(geometry: dict[str, Any] | str) -> tuple[float, float, float, float] | None:
    coords = list(_iter_points(extract_geometry(geometry)))
    if not coords:
        return None
    lons = [coord[0] for coord in coords]
    lats = [coord[1] for coord in coords]
    return min(lons), min(lats), max(lons), max(lats)


def geometries_intersect_approx(
    left: dict[str, Any] | str,
    right: dict[str, Any] | str,
) -> bool:
    left_geom = extract_geometry(left)
    right_geom = extract_geometry(right)
    left_bbox = geometry_bbox(left_geom)
    right_bbox = geometry_bbox(right_geom)
    if left_bbox is None or right_bbox is None or not _bbox_intersects(left_bbox, right_bbox):
        return False
    for lon, lat in _sample_points(left_geom):
        if point_in_geometry(lon, lat, right_geom):
            return True
    for lon, lat in _sample_points(right_geom):
        if point_in_geometry(lon, lat, left_geom):
            return True
    left_centroid = geometry_centroid(left_geom)
    right_centroid = geometry_centroid(right_geom)
    return bool(
        left_centroid
        and point_in_geometry(left_centroid[0], left_centroid[1], right_geom)
        or right_centroid
        and point_in_geometry(right_centroid[0], right_centroid[1], left_geom)
    )


def circle_polygon(lon: float, lat: float, radius_miles: float, sides: int = 48) -> dict[str, Any]:
    points = []
    for index in range(max(12, sides)):
        angle = 2 * math.pi * index / max(12, sides)
        delta_lat = math.sin(angle) * radius_miles / EARTH_MILES_PER_DEGREE_LAT
        miles_per_lon = _miles_per_degree_lon(lat)
        delta_lon = math.cos(angle) * radius_miles / miles_per_lon if miles_per_lon else 0.0
        points.append([lon + delta_lon, lat + delta_lat])
    points.append(points[0])
    return {"type": "Polygon", "coordinates": [points]}


def _point_in_polygon(lon: float, lat: float, rings: list[list[list[float]]]) -> bool:
    if not rings:
        return False
    exterior = rings[0]
    if not _point_in_ring(lon, lat, exterior):
        return False
    return not any(_point_in_ring(lon, lat, hole) for hole in rings[1:])


def _point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    clean = _clean_ring(ring)
    if len(clean) < 3:
        return False
    previous_lon, previous_lat = clean[-1]
    for current_lon, current_lat in clean:
        crosses = (current_lat > lat) != (previous_lat > lat)
        if crosses:
            slope_lon = (previous_lon - current_lon) * (lat - current_lat) / (
                previous_lat - current_lat
            ) + current_lon
            if lon < slope_lon:
                inside = not inside
        previous_lon, previous_lat = current_lon, current_lat
    return inside


def _iter_rings(geometry: dict[str, Any]) -> Iterable[list[list[float]]]:
    if geometry["type"] == "Polygon":
        yield from geometry.get("coordinates") or []
    elif geometry["type"] == "MultiPolygon":
        for polygon in geometry.get("coordinates") or []:
            yield from polygon


def _iter_lines(geometry: dict[str, Any]) -> Iterable[list[list[float]]]:
    if geometry["type"] == "LineString":
        yield geometry.get("coordinates") or []
    elif geometry["type"] == "MultiLineString":
        yield from geometry.get("coordinates") or []


def _outer_rings(geometry: dict[str, Any]) -> Iterable[list[list[float]]]:
    if geometry["type"] == "Polygon":
        rings = geometry.get("coordinates") or []
        if rings:
            yield rings[0]
    elif geometry["type"] == "MultiPolygon":
        for polygon in geometry.get("coordinates") or []:
            if polygon:
                yield polygon[0]


def _iter_points(geometry: dict[str, Any]) -> Iterable[Coordinate]:
    if geometry["type"] == "Point":
        coords = geometry.get("coordinates") or []
        if len(coords) >= 2:
            yield float(coords[0]), float(coords[1])
        return
    if geometry["type"] in {"LineString", "MultiLineString"}:
        for line in _iter_lines(geometry):
            yield from _clean_ring(line)
        return
    for ring in _iter_rings(geometry):
        yield from _clean_ring(ring)


def _sample_points(geometry: dict[str, Any]) -> Iterable[Coordinate]:
    centroid = geometry_centroid(geometry)
    if centroid:
        yield centroid
    for index, point in enumerate(_iter_points(geometry)):
        if index % 8 == 0:
            yield point


def _clean_ring(ring: list[list[float]]) -> list[Coordinate]:
    clean: list[Coordinate] = []
    for coord in ring:
        if len(coord) >= 2:
            clean.append((float(coord[0]), float(coord[1])))
    if len(clean) > 1 and clean[0] == clean[-1]:
        clean = clean[:-1]
    return clean


def _ring_centroid(ring: list[list[float]]) -> tuple[float, float, float] | None:
    points = _clean_ring(ring)
    if len(points) < 3:
        return None
    doubled_area = 0.0
    centroid_lon = 0.0
    centroid_lat = 0.0
    previous_lon, previous_lat = points[-1]
    for current_lon, current_lat in points:
        cross = previous_lon * current_lat - current_lon * previous_lat
        doubled_area += cross
        centroid_lon += (previous_lon + current_lon) * cross
        centroid_lat += (previous_lat + current_lat) * cross
        previous_lon, previous_lat = current_lon, current_lat
    if abs(doubled_area) < 1e-12:
        return None
    return centroid_lon / (3 * doubled_area), centroid_lat / (3 * doubled_area), doubled_area / 2


def _distance_to_ring_miles(lon: float, lat: float, ring: list[list[float]]) -> float:
    points = _clean_ring(ring)
    if len(points) == 1:
        return haversine_miles(lon, lat, points[0][0], points[0][1])
    if len(points) < 2:
        return math.inf
    closed = points + [points[0]]
    return min(
        _distance_to_segment_miles(lon, lat, lon1, lat1, lon2, lat2)
        for (lon1, lat1), (lon2, lat2) in zip(closed, closed[1:], strict=False)
    )


def _distance_to_open_line_miles(lon: float, lat: float, line: list[list[float]]) -> float:
    points = _clean_ring(line)
    if len(points) == 1:
        return haversine_miles(lon, lat, points[0][0], points[0][1])
    if len(points) < 2:
        return math.inf
    return min(
        _distance_to_segment_miles(lon, lat, lon1, lat1, lon2, lat2)
        for (lon1, lat1), (lon2, lat2) in zip(points, points[1:], strict=False)
    )


def _distance_to_segment_miles(
    lon: float,
    lat: float,
    lon1: float,
    lat1: float,
    lon2: float,
    lat2: float,
) -> float:
    ref_lat = (lat + lat1 + lat2) / 3
    px, py = _project(lon, lat, ref_lat)
    ax, ay = _project(lon1, lat1, ref_lat)
    bx, by = _project(lon2, lat2, ref_lat)
    dx = bx - ax
    dy = by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    closest_x = ax + t * dx
    closest_y = ay + t * dy
    return math.hypot(px - closest_x, py - closest_y)


def _project(lon: float, lat: float, ref_lat: float) -> tuple[float, float]:
    return lon * _miles_per_degree_lon(ref_lat), lat * EARTH_MILES_PER_DEGREE_LAT


def _miles_per_degree_lon(lat: float) -> float:
    return max(0.0001, EARTH_MILES_PER_DEGREE_LAT * math.cos(math.radians(lat)))


def _bbox_intersects(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    left_min_lon, left_min_lat, left_max_lon, left_max_lat = left
    right_min_lon, right_min_lat, right_max_lon, right_max_lat = right
    return not (
        left_max_lon < right_min_lon
        or right_max_lon < left_min_lon
        or left_max_lat < right_min_lat
        or right_max_lat < left_min_lat
    )
