"""
utils/geometry.py
-----------------
Pure geometry helpers. No YOLO, no camera, no drawing -- just math.

The single most important idea in this file:

    A person is judged by their FEET, not by their whole bounding box.

A standing person's bounding box is tall. If we asked "does the box overlap the
restricted zone?", a person standing safely *behind* a machine would trigger a
false alarm, because their head and torso visually overlap the zone even though
their feet are metres away. So instead we reduce each person to one point --
the bottom-center of the box -- which approximates where they contact the floor.
"""

import cv2
import numpy as np


def get_bottom_center(bbox):
    """
    Reduce a bounding box to the person's approximate ground contact point.

        bbox = (x1, y1, x2, y2)   top-left and bottom-right corners

        person_x = (x1 + x2) / 2   -> horizontal middle of the body
        person_y = y2              -> the bottom edge, i.e. the feet

    Returns (x, y) as floats.
    """
    x1, y1, x2, y2 = bbox
    person_x = (x1 + x2) / 2.0
    person_y = float(y2)
    return (person_x, person_y)


def to_polygon_array(points):
    """
    Convert a list of (x, y) points into the int32 numpy array that the OpenCV
    polygon functions expect. Returns None if there are not enough points.
    """
    if points is None or len(points) < 3:
        return None
    return np.array(points, dtype=np.int32).reshape((-1, 1, 2))


def signed_distance_to_polygon(point, polygon):
    """
    Distance in pixels from `point` to the polygon edge.

        > 0  ->  point is INSIDE  (value = distance to nearest edge)
        = 0  ->  point is exactly ON the edge
        < 0  ->  point is OUTSIDE (value = -distance to nearest edge)

    Returning a distance rather than a plain True/False is what makes
    hysteresis possible: we can require a person to be, say, 6 px clear of the
    boundary before we change our mind, instead of flip-flopping on the line.
    """
    if polygon is None:
        return float("-inf")
    px, py = float(point[0]), float(point[1])
    return float(cv2.pointPolygonTest(polygon, (px, py), True))


def point_in_polygon(point, polygon):
    """
    Plain inside/outside test. Points exactly on the edge count as inside.
    Used for the raw, un-debounced check.
    """
    if polygon is None:
        return False
    px, py = float(point[0]), float(point[1])
    # measureDist=False -> returns +1 inside, 0 on the edge, -1 outside.
    return cv2.pointPolygonTest(polygon, (px, py), False) >= 0


def polygon_area(polygon):
    """Area of the polygon in square pixels. 0.0 if the polygon is invalid."""
    if polygon is None:
        return 0.0
    return float(abs(cv2.contourArea(polygon)))


def is_valid_polygon(points, min_points=3, min_area=500.0):
    """
    A usable restricted zone needs enough points to enclose space, and enough
    area that it was clearly intentional rather than a stray double-click.

    Returns (ok, reason). `reason` is an empty string when ok is True.
    """
    if points is None or len(points) < min_points:
        have = 0 if points is None else len(points)
        return False, "Need at least {} points (got {})".format(min_points, have)

    polygon = to_polygon_array(points)
    area = polygon_area(polygon)
    if area < min_area:
        return False, "Zone too small ({:.0f} px^2, need {:.0f})".format(area, min_area)

    return True, ""


def polygon_centroid(points):
    """
    A point roughly in the middle of the polygon, used for placing its label.
    We use the average of the vertices: it is cheap, never divides by zero, and
    is good enough for text placement.
    """
    if not points:
        return (0, 0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (int(sum(xs) / len(xs)), int(sum(ys) / len(ys)))


def clamp_point(x, y, width, height):
    """Keep a clicked point inside the frame, so nothing is drawn off-screen."""
    cx = max(0, min(int(x), max(0, width - 1)))
    cy = max(0, min(int(y), max(0, height - 1)))
    return (cx, cy)


def euclidean_distance(a, b):
    """Straight-line distance between two (x, y) points."""
    dx = float(a[0]) - float(b[0])
    dy = float(a[1]) - float(b[1])
    return (dx * dx + dy * dy) ** 0.5
