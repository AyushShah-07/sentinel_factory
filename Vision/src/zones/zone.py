"""
src/zones/zone.py
-----------------
Multi-zone safety perimeter management.

Responsibilities:
    - Create SAFE / WARNING / DANGER polygons
    - Handle polygon drawing through OpenCV mouse input
    - Validate polygons
    - Assign unique zone IDs
    - Classify person position
    - Draw zones on frames
    - Provide zone information to WebServer
"""

import cv2
import numpy as np

from utils.geometry import (
    clamp_point,
    is_valid_polygon,
    point_in_polygon,
    polygon_area,
    polygon_centroid,
    signed_distance_to_polygon,
    to_polygon_array,
)


STATE_IDLE = "IDLE"
STATE_DEFINING = "DEFINING"
STATE_READY = "READY"


class RestrictedZone:
    """Base polygon zone."""

    LABEL_SCALE = 0.6
    LABEL_THICKNESS = 2
    LABEL_PAD = 8

    def __init__(
        self,
        min_points=3,
        min_area=500.0,
        point_radius=6,
        thickness=2,
        fill_alpha=0.22,
        color=(0, 165, 255),
        alert_color=(60, 60, 255),
    ):
        self.min_points = min_points
        self.min_area = min_area
        self.point_radius = point_radius
        self.thickness = thickness
        self.fill_alpha = fill_alpha
        self.color = color
        self.alert_color = alert_color

        self.state = STATE_IDLE
        self.points = []
        self._polygon = None
        self.hover_point = None
        self.last_message = ""

        self._frame_width = 0
        self._frame_height = 0

    # ------------------------------------------------------------------
    # STATE
    # ------------------------------------------------------------------

    @property
    def is_ready(self):
        return self.state == STATE_READY and self._polygon is not None

    @property
    def is_defining(self):
        return self.state == STATE_DEFINING

    @property
    def polygon(self):
        return self._polygon

    @property
    def point_count(self):
        return len(self.points)

    @property
    def area(self):
        if self._polygon is None:
            return 0.0
        return polygon_area(self._polygon)

    def set_frame_size(self, width, height):
        self._frame_width = width
        self._frame_height = height

    # ------------------------------------------------------------------
    # OPERATOR ACTIONS
    # ------------------------------------------------------------------

    def start_definition(self):
        self.state = STATE_DEFINING
        self.points = []
        self._polygon = None
        self.hover_point = None
        self.last_message = (
            "Click to add points. ENTER = finish, C = cancel."
        )

    def clear(self):
        self.state = STATE_IDLE
        self.points = []
        self._polygon = None
        self.hover_point = None
        self.last_message = "Restricted area cleared."

    def undo_last_point(self):
        if self.state == STATE_DEFINING and self.points:
            self.points.pop()
            self.last_message = (
                "Removed last point ({} left).".format(
                    len(self.points)
                )
            )

    def finish_definition(self):
        if self.state != STATE_DEFINING:
            return False

        ok, reason = is_valid_polygon(
            self.points,
            self.min_points,
            self.min_area,
        )

        if not ok:
            self.last_message = "Invalid zone: {}".format(reason)
            return False

        self._polygon = to_polygon_array(self.points)
        self.state = STATE_READY
        self.hover_point = None

        self.last_message = (
            "Restricted area set ({} points).".format(
                len(self.points)
            )
        )

        return True

    # ------------------------------------------------------------------
    # MOUSE
    # ------------------------------------------------------------------

    def handle_mouse(
        self,
        event,
        x,
        y,
        flags,
        param=None,
    ):
        if self.state != STATE_DEFINING:
            return

        point = clamp_point(
            x,
            y,
            self._frame_width or x + 1,
            self._frame_height or y + 1,
        )

        if event == cv2.EVENT_MOUSEMOVE:
            self.hover_point = point

        elif event == cv2.EVENT_LBUTTONDOWN:

            if self.points and self.points[-1] == point:
                return

            self.points.append(point)

            self.last_message = (
                "Point {} added at {}.".format(
                    len(self.points),
                    point,
                )
            )

        elif event == cv2.EVENT_RBUTTONDOWN:
            self.undo_last_point()

    # ------------------------------------------------------------------
    # GEOMETRY
    # ------------------------------------------------------------------

    def contains(self, point):
        if not self.is_ready:
            return False

        return point_in_polygon(
            point,
            self._polygon,
        )

    def signed_distance(self, point):
        if not self.is_ready:
            return float("-inf")

        return signed_distance_to_polygon(
            point,
            self._polygon,
        )

    # ------------------------------------------------------------------
    # PERSON CLASSIFICATION
    # ------------------------------------------------------------------

    def classify_person(self, person):
        """
        Determine the safety zone for a detected person.

        Uses the person's feet/bottom-center point.
        """

        if person is None:
            return None, None

        feet = person.get("feet")

        if feet is None:
            person["zone"] = None
            person["zone_id"] = None
            return None, None

        try:
            x, y = feet
            point = (float(x), float(y))

        except (TypeError, ValueError):
            person["zone"] = None
            person["zone_id"] = None
            return None, None

        zone = self.classify_zone(point)

        if zone is None:
            person["zone"] = None
            person["zone_id"] = None
            return None, None

        person["zone"] = zone.zone_type
        person["zone_id"] = zone.zone_id

        return zone.zone_type, zone.zone_id

    # ------------------------------------------------------------------
    # DRAWING
    # ------------------------------------------------------------------

    def draw(
        self,
        frame,
        violated=False,
        label_offset=(0, 0),
    ):
        height, width = frame.shape[:2]

        self.set_frame_size(
            width,
            height,
        )

        if self.state == STATE_IDLE:
            return frame

        color = (
            self.alert_color
            if violated
            else self.color
        )

        if self.state == STATE_DEFINING:
            self._draw_in_progress(frame)
            return frame

        overlay = frame.copy()

        cv2.fillPoly(
            overlay,
            [self._polygon],
            color,
        )

        cv2.addWeighted(
            overlay,
            self.fill_alpha,
            frame,
            1.0 - self.fill_alpha,
            0,
            frame,
        )

        cv2.polylines(
            frame,
            [self._polygon],
            True,
            color,
            self.thickness,
            lineType=cv2.LINE_AA,
        )

        for point in self.points:

            cv2.circle(
                frame,
                point,
                max(1, self.point_radius - 2),
                color,
                -1,
                lineType=cv2.LINE_AA,
            )

        self._draw_zone_label(
            frame,
            color,
            violated,
            label_offset,
        )

        return frame

    def _draw_in_progress(self, frame):

        color = self.color

        if len(self.points) >= 2:

            pts = np.array(
                self.points,
                dtype=np.int32,
            ).reshape((-1, 1, 2))

            cv2.polylines(
                frame,
                [pts],
                False,
                color,
                self.thickness,
                lineType=cv2.LINE_AA,
            )

        if self.points and self.hover_point:

            cv2.line(
                frame,
                self.points[-1],
                self.hover_point,
                color,
                1,
                lineType=cv2.LINE_AA,
            )

            if len(self.points) >= 2:

                cv2.line(
                    frame,
                    self.hover_point,
                    self.points[0],
                    (150, 150, 150),
                    1,
                    lineType=cv2.LINE_AA,
                )

        for index, point in enumerate(self.points):

            cv2.circle(
                frame,
                point,
                self.point_radius,
                color,
                -1,
                lineType=cv2.LINE_AA,
            )

            cv2.circle(
                frame,
                point,
                self.point_radius,
                (255, 255, 255),
                1,
                lineType=cv2.LINE_AA,
            )

            cv2.putText(
                frame,
                str(index + 1),
                (
                    point[0] + 9,
                    point[1] - 9,
                ),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

    def label_geometry(self, offset=(0, 0)):

        if not self.points:
            return None

        label = self._zone_label_text()

        center = polygon_centroid(
            self.points
        )

        (text_w, text_h), _ = cv2.getTextSize(
            label,
            cv2.FONT_HERSHEY_SIMPLEX,
            self.LABEL_SCALE,
            self.LABEL_THICKNESS,
        )

        origin = (
            center[0]
            - text_w // 2
            + int(offset[0]),

            center[1]
            + text_h // 2
            + int(offset[1]),
        )

        pad = self.LABEL_PAD

        rect = (
            origin[0] - pad,
            origin[1] - text_h - pad,
            origin[0] + text_w + pad,
            origin[1] + pad,
        )

        return label, origin, rect

    def _draw_zone_label(
        self,
        frame,
        color,
        violated,
        offset=(0, 0),
    ):

        geometry = self.label_geometry(offset)

        if geometry is None:
            return

        label, origin, rect = geometry

        plate_color = (
            color
            if violated
            else (20, 20, 20)
        )

        cv2.rectangle(
            frame,
            (rect[0], rect[1]),
            (rect[2], rect[3]),
            plate_color,
            -1,
        )

        cv2.putText(
            frame,
            label,
            origin,
            cv2.FONT_HERSHEY_SIMPLEX,
            self.LABEL_SCALE,
            (255, 255, 255),
            self.LABEL_THICKNESS,
            cv2.LINE_AA,
        )

    def _zone_label_text(self):
        return "RESTRICTED AREA"


# ======================================================================
# ZONE ID FACTORY
# ======================================================================


class ZoneIdFactory:

    def __init__(self):
        self._counters = {}
        self._issued = set()

    def next_id(self, zone_type):

        while True:

            count = (
                self._counters.get(zone_type, 0)
                + 1
            )

            self._counters[zone_type] = count

            zone_id = "{}_{}".format(
                zone_type,
                count,
            )

            if zone_id not in self._issued:

                self._issued.add(zone_id)

                return zone_id

    @property
    def issued(self):
        return set(self._issued)


# ======================================================================
# SAFETY ZONE
# ======================================================================


class SafetyZone(RestrictedZone):

    def __init__(
        self,
        zone_type,
        zone_id,
        name,
        risk_level,
        color,
        show_tag=True,
        **kwargs,
    ):

        super().__init__(
            color=color,
            **kwargs,
        )

        self.zone_id = zone_id
        self.zone_type = zone_type
        self.name = name
        self.risk_level = risk_level
        self.show_tag = show_tag

    @property
    def tag(self):
        return self.zone_id

    def finish_definition(self):

        ok = super().finish_definition()

        if ok:

            self.last_message = (
                "{} set ({} points).".format(
                    self.zone_id,
                    len(self.points),
                )
            )

        return ok

    def describe(self):

        return {
            "zone_id": self.zone_id,
            "zone_type": self.zone_type,
            "zone_name": self.name,
            "points": [
                [
                    int(x),
                    int(y),
                ]
                for x, y in self.points
            ],
        }

    def _zone_label_text(self):

        if self.show_tag and self.zone_id:
            return self.zone_id

        return self.name


# ======================================================================
# HELPERS
# ======================================================================


def _rects_overlap(a, b):

    return not (
        a[2] <= b[0]
        or b[2] <= a[0]
        or a[3] <= b[1]
        or b[3] <= a[1]
    )


# ======================================================================
# ZONE MANAGER
# ======================================================================


class ZoneManager:
    """
    Manages all SAFE / WARNING / DANGER polygons.
    """

    def __init__(self, cfg):

        self.cfg = cfg

        self.priority = tuple(
            cfg.ZONE_PRIORITY
        )

        self.last_message = ""

        self.ids = ZoneIdFactory()

        self._zones = []
        self._defining = None
        self._last_finished = None

        self._frame_width = 0
        self._frame_height = 0

        self._common = {
            "min_points": cfg.MIN_POLYGON_POINTS,
            "min_area": cfg.MIN_POLYGON_AREA,
            "point_radius": cfg.POLYGON_POINT_RADIUS,
            "thickness": cfg.POLYGON_THICKNESS,
            "alert_color": cfg.COLOR_ZONE_ALERT,
        }

    # ------------------------------------------------------------------
    # ACCESS
    # ------------------------------------------------------------------

    @property
    def zone_types(self):

        cfg = self.cfg

        return (
            cfg.ZONE_SAFE,
            cfg.ZONE_WARNING,
            cfg.ZONE_DANGER,
        )

    @property
    def zones(self):
        return list(self._zones)

    @property
    def all_zones(self):

        ordered = []

        for zone_type in self.priority:
            ordered.extend(
                self.zones_of_type(zone_type)
            )

        return ordered

    def zones_of_type(self, zone_type):

        return [
            zone
            for zone in self._zones
            if zone.zone_type == zone_type
        ]

    def zone(self, zone_type):

        candidates = self.zones_of_type(
            zone_type
        )

        ready = [
            zone
            for zone in candidates
            if zone.is_ready
        ]

        if ready:
            return ready[-1]

        return (
            candidates[-1]
            if candidates
            else None
        )

    def zone_by_id(self, zone_id):

        for zone in self._zones:

            if zone.zone_id == zone_id:
                return zone

        return None

    def type_of(self, zone_id):

        zone = self.zone_by_id(zone_id)

        if zone is None:
            return None

        return zone.zone_type

    def iter_ready(self):

        for zone_type in self.priority:

            for zone in self.zones_of_type(
                zone_type
            ):

                if zone.is_ready:
                    yield zone

    @property
    def ready_zones(self):
        return list(self.iter_ready())

    @property
    def ready_ids(self):
        return [
            zone.zone_id
            for zone in self.iter_ready()
        ]

    @property
    def ready_types(self):

        types = []

        for zone in self.iter_ready():

            if zone.zone_type not in types:
                types.append(zone.zone_type)

        return types

    @property
    def any_ready(self):

        return any(
            zone.is_ready
            for zone in self._zones
        )

    @property
    def is_defining(self):
        return self._defining is not None

    @property
    def defining_zone(self):
        return self._defining

    @property
    def defining_type(self):

        if self._defining is None:
            return None

        return self._defining.zone_type

    @property
    def defining_id(self):

        if self._defining is None:
            return None

        return self._defining.zone_id

    @property
    def last_finished(self):
        return self._last_finished

    def describe(self):

        return [
            zone.describe()
            for zone in self.iter_ready()
        ]

    def set_frame_size(
        self,
        width,
        height,
    ):

        self._frame_width = width
        self._frame_height = height

        for zone in self._zones:

            zone.set_frame_size(
                width,
                height,
            )

    # ------------------------------------------------------------------
    # CREATE ZONE
    # ------------------------------------------------------------------

    def _new_zone(self, zone_type):

        cfg = self.cfg

        fill_alpha = (
            cfg.ZONE_FILL_ALPHA_SAFE
            if zone_type == cfg.ZONE_SAFE
            else cfg.ZONE_FILL_ALPHA
        )

        zone = SafetyZone(
            zone_type=zone_type,
            zone_id=self.ids.next_id(
                zone_type
            ),
            name=cfg.ZONE_LABELS[
                zone_type
            ],
            risk_level=cfg.ZONE_RISK[
                zone_type
            ],
            color=cfg.ZONE_COLORS[
                zone_type
            ],
            show_tag=getattr(
                cfg,
                "SHOW_ZONE_TAG",
                True,
            ),
            fill_alpha=fill_alpha,
            **self._common,
        )

        zone.set_frame_size(
            self._frame_width,
            self._frame_height,
        )

        self._zones.append(zone)

        return zone

    def start_definition(self, zone_type):

        if zone_type not in self.zone_types:

            self.last_message = (
                "Unknown zone type: {}".format(
                    zone_type
                )
            )

            return False

        self._discard_defining()

        zone = self._new_zone(
            zone_type
        )

        zone.start_definition()

        self._defining = zone

        self.last_message = (
            "Drawing {}: click points, ENTER to finish.".format(
                zone.zone_id
            )
        )

        return True

    def _discard_defining(self):

        zone = self._defining

        if zone is None:
            return None

        zone.clear()

        if zone in self._zones:
            self._zones.remove(zone)

        self._defining = None

        return zone

    def finish_definition(self):

        zone = self._defining

        if zone is None:
            return False, None

        if zone.finish_definition():

            self._defining = None
            self._last_finished = zone
            self.last_message = zone.last_message

            return True, zone.zone_type

        self.last_message = zone.last_message

        return False, zone.zone_type

    def cancel_definition(self):

        zone = self._discard_defining()

        if zone is None:
            return None

        self.last_message = (
            "Cancelled {}. Existing zones kept.".format(
                zone.zone_id
            )
        )

        return zone.zone_type

    def clear(self, zone_type=None):

        if zone_type is None:

            removed = len(self._zones)

            self._zones = []
            self._defining = None
            self._last_finished = None

            self.last_message = (
                "All zones cleared ({} removed).".format(
                    removed
                )
            )

            return

        doomed = self.zones_of_type(
            zone_type
        )

        if self._defining in doomed:
            self._defining = None

        if self._last_finished in doomed:
            self._last_finished = None

        self._zones = [
            zone
            for zone in self._zones
            if zone not in doomed
        ]

        self.last_message = (
            "{} cleared ({} removed).".format(
                self.cfg.ZONE_LABELS.get(
                    zone_type,
                    zone_type,
                ),
                len(doomed),
            )
        )

    # ------------------------------------------------------------------
    # PROGRAMMATIC ZONE CREATION
    # ------------------------------------------------------------------

    def set_zone_points(
        self,
        zone_type,
        points,
    ):

        if zone_type not in self.zone_types:

            return (
                False,
                "Unknown zone type: {}".format(
                    zone_type
                ),
            )

        self._discard_defining()

        zone = self._new_zone(
            zone_type
        )

        zone.start_definition()

        zone.points = [
            (int(x), int(y))
            for x, y in points
        ]

        if zone.finish_definition():

            self._last_finished = zone

            return True, ""

        reason = zone.last_message

        self._zones.remove(zone)

        return False, reason

    # ------------------------------------------------------------------
    # MOUSE
    # ------------------------------------------------------------------

    def handle_mouse(
        self,
        event,
        x,
        y,
        flags,
        param=None,
    ):

        zone = self._defining

        if zone is None:
            return

        zone.handle_mouse(
            event,
            x,
            y,
            flags,
            param,
        )

        self.last_message = (
            zone.last_message
        )

    # ------------------------------------------------------------------
    # CLASSIFICATION
    # ------------------------------------------------------------------

    def classify_zone(self, point):

        for zone in self.iter_ready():

            if zone.contains(point):
                return zone

        return None

    def classify(self, point):

        zone = self.classify_zone(point)

        if zone is None:
            return self.cfg.ZONE_OUTSIDE

        return zone.zone_type

    def resolve_confirmed(self, confirmed):

        for zone_type in self.priority:

            for zone in self.zones_of_type(
                zone_type
            ):

                if confirmed.get(
                    zone.zone_id
                ):

                    return (
                        zone_type,
                        zone.zone_id,
                    )

        return (
            self.cfg.ZONE_OUTSIDE,
            None,
        )

    def confirmed_ids_of_type(
        self,
        confirmed,
        zone_type,
    ):

        return [
            zone.zone_id
            for zone in self.zones_of_type(
                zone_type
            )
            if confirmed.get(
                zone.zone_id
            )
        ]

    def signed_distance(
        self,
        zone_type,
        point,
    ):

        distances = [
            zone.signed_distance(point)
            for zone in self.zones_of_type(
                zone_type
            )
            if zone.is_ready
        ]

        return (
            max(distances)
            if distances
            else float("-inf")
        )

    def distance_to_danger(self, point):

        signed = self.signed_distance(
            self.cfg.ZONE_DANGER,
            point,
        )

        if signed == float("-inf"):
            return None

        return (
            0.0
            if signed >= 0
            else float(-signed)
        )

    def contains(
        self,
        zone_type,
        point,
    ):

        return any(
            zone.contains(point)
            for zone in self.zones_of_type(
                zone_type
            )
        )

    # ------------------------------------------------------------------
    # DRAW
    # ------------------------------------------------------------------

    def draw(
        self,
        frame,
        violated_types=(),
        violated_ids=(),
    ):

        height, width = frame.shape[:2]

        self.set_frame_size(
            width,
            height,
        )

        types = set(
            violated_types or ()
        )

        ids = set(
            violated_ids or ()
        )

        gap = getattr(
            self.cfg,
            "ZONE_TAG_MIN_GAP_PX",
            4,
        )

        taken = []

        for zone in reversed(
            self.all_zones
        ):

            violated = (
                zone.zone_id in ids
                or zone.zone_type in types
            )

            zone.draw(
                frame,
                violated=violated,
                label_offset=self._label_offset(
                    zone,
                    taken,
                    gap,
                ),
            )

        return frame

    @staticmethod
    def _label_offset(
        zone,
        taken,
        gap,
    ):

        offset = (0, 0)

        if not zone.is_ready:
            return offset

        for _ in range(
            len(taken) + 1
        ):

            geometry = zone.label_geometry(
                offset
            )

            if geometry is None:
                return offset

            rect = geometry[2]

            clash = None

            for used in taken:

                if _rects_overlap(
                    used,
                    rect,
                ):

                    clash = used
                    break

            if clash is None:

                taken.append(rect)

                return offset

            offset = (
                offset[0],
                offset[1]
                + (clash[3] - rect[1])
                + gap,
            )

        geometry = zone.label_geometry(
            offset
        )

        if geometry is not None:
            taken.append(
                geometry[2]
            )

        return offset

    # ------------------------------------------------------------------
    # SUMMARY
    # ------------------------------------------------------------------

    def summary(self):

        parts = []

        for zone_type in self.zone_types:

            zones = self.zones_of_type(
                zone_type
            )

            initial = zone_type[0]

            ready = sum(
                1
                for zone in zones
                if zone.is_ready
            )

            if any(
                zone.is_defining
                for zone in zones
            ):

                parts.append(
                    "{}+".format(
                        initial.lower()
                    )
                )

            elif ready:

                parts.append(
                    "{}{}".format(
                        initial,
                        ready,
                    )
                )

            else:

                parts.append(
                    "{}-".format(
                        initial
                    )
                )

        return "Zones: " + " ".join(parts)