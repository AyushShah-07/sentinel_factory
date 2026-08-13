"""
src/zone.py
-----------
The "invisible" restricted area: an operator-drawn polygon over the camera view.

Responsibilities:
  * hold the polygon points
  * handle mouse interaction while the operator is drawing
  * validate the polygon
  * draw the polygon on a frame
  * answer "is this point inside the zone?"

  
The zone has exactly three states:

    IDLE     -- no zone yet (or it was cleared). Nothing is monitored.
    DEFINING -- the operator is clicking points. Detection keeps running, but
                no violations are reported until the polygon is finished.
    READY    -- a valid polygon exists and every person is checked against it.
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
    """A polygon-based restricted area defined with the mouse."""

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
        self.points = []          # committed polygon vertices, list of (x, y)
        self._polygon = None      # cached int32 numpy array for OpenCV
        self.hover_point = None   # live cursor position while drawing
        self.last_message = ""    # feedback shown to the operator on screen

        # Frame size, learned from the first frame we draw on. Used to clamp
        # mouse coordinates so a click on the window border cannot go negative.
        self._frame_width = 0
        self._frame_height = 0

    # -- state helpers ------------------------------------------------------

    @property
    def is_ready(self):
        """True when a valid polygon exists and monitoring should happen."""
        return self.state == STATE_READY and self._polygon is not None

    @property
    def is_defining(self):
        return self.state == STATE_DEFINING

    @property
    def polygon(self):
        """The polygon as an OpenCV int32 array, or None if not ready."""
        return self._polygon

    @property
    def point_count(self):
        return len(self.points)

    @property
    def area(self):
        return polygon_area(self._polygon)

    def set_frame_size(self, width, height):
        self._frame_width = width
        self._frame_height = height

    # -- operator actions ---------------------------------------------------

    def start_definition(self):
        """Enter drawing mode. Any previous zone is discarded."""
        self.state = STATE_DEFINING
        self.points = []
        self._polygon = None
        self.hover_point = None
        self.last_message = "Click to add points. ENTER = finish, C = cancel."

    def clear(self):
        """Remove the zone entirely and go back to idle."""
        self.state = STATE_IDLE
        self.points = []
        self._polygon = None
        self.hover_point = None
        self.last_message = "Restricted area cleared."

    def undo_last_point(self):
        """Remove the most recent point (right-click while drawing)."""
        if self.state == STATE_DEFINING and self.points:
            self.points.pop()
            self.last_message = "Removed last point ({} left).".format(len(self.points))

    def finish_definition(self):
        """
        Try to close the polygon (the 'Enter' key).

        Returns True on success. On failure we deliberately stay in DEFINING
        mode with a helpful message, rather than silently accepting a bad zone.
        """
        if self.state != STATE_DEFINING:
            return False

        ok, reason = is_valid_polygon(self.points, self.min_points, self.min_area)
        if not ok:
            self.last_message = "Invalid zone: {}".format(reason)
            return False

        self._polygon = to_polygon_array(self.points)
        self.state = STATE_READY
        self.hover_point = None
        self.last_message = "Restricted area set ({} points).".format(len(self.points))
        return True

    # -- mouse ---------------------------------------------------------------

    def handle_mouse(self, event, x, y, flags, param=None):
        """
        OpenCV mouse callback. Registered once by main.py.

        Mouse input is ignored unless we are in DEFINING mode, so stray clicks
        during normal monitoring can never move the safety perimeter.
        """
        if self.state != STATE_DEFINING:
            return

        point = clamp_point(x, y, self._frame_width or x + 1, self._frame_height or y + 1)

        if event == cv2.EVENT_MOUSEMOVE:
            self.hover_point = point

        elif event == cv2.EVENT_LBUTTONDOWN:
            # Ignore a duplicate click on the exact same pixel; it would create
            # a zero-length edge and confuse the area check.
            if self.points and self.points[-1] == point:
                return
            self.points.append(point)
            self.last_message = "Point {} added at {}.".format(len(self.points), point)

        elif event == cv2.EVENT_RBUTTONDOWN:
            self.undo_last_point()

    # -- geometry queries ----------------------------------------------------

    def contains(self, point):
        """Raw inside/outside test for a (x, y) point, no hysteresis."""
        if not self.is_ready:
            return False
        return point_in_polygon(point, self._polygon)

    def signed_distance(self, point):
        """
        Signed pixel distance from the point to the zone edge.
        Positive = inside, negative = outside. See utils/geometry.py.
        """
        if not self.is_ready:
            return float("-inf")
        return signed_distance_to_polygon(point, self._polygon)

    # -- person classification ----------------------------------------------

    def classify_person(self, person):
        """
        Determine the safety zone for a detected person.

        Uses the person's feet/bottom-center point.

        Updates the person dictionary directly:

            person["zone"]
            person["zone_id"]

        Example result:

            {
                ...
                "feet": (700, 650),
                "zone": "DANGER",
                "zone_id": "DANGER_1"
            }

        Returns:
            (zone_type, zone_id)
        """

        if person is None:
            return self.cfg.ZONE_OUTSIDE, None

        feet = person.get("feet")

        # No valid feet point
        if feet is None:
            person["zone"] = self.cfg.ZONE_OUTSIDE
            person["zone_id"] = None
            return self.cfg.ZONE_OUTSIDE, None

        try:
            x, y = feet
            point = (float(x), float(y))
        except (TypeError, ValueError):
            person["zone"] = self.cfg.ZONE_OUTSIDE
            person["zone_id"] = None
            return self.cfg.ZONE_OUTSIDE, None

        # ------------------------------------------------------------
        # Find the actual polygon containing the person's feet
        # ------------------------------------------------------------

        zone = self.classify_zone(point)

        if zone is None:

            person["zone"] = self.cfg.ZONE_OUTSIDE
            person["zone_id"] = None

            return self.cfg.ZONE_OUTSIDE, None

        # ------------------------------------------------------------
        # IMPORTANT:
        # Store BOTH the type and unique polygon ID
        # ------------------------------------------------------------

        person["zone"] = zone.zone_type
        person["zone_id"] = zone.zone_id

        return zone.zone_type, zone.zone_id

    # -- drawing -------------------------------------------------------------

    def draw(self, frame, violated=False, label_offset=(0, 0)):
        """
        Draw the zone onto `frame` (modified in place).

        `violated` switches the polygon to its red alert styling.
        `label_offset` nudges the text label, so a manager drawing several
        overlapping polygons can stop their labels landing on top of each other.
        """
        height, width = frame.shape[:2]
        self.set_frame_size(width, height)

        if self.state == STATE_IDLE:
            return frame

        color = self.alert_color if violated else self.color

        if self.state == STATE_DEFINING:
            self._draw_in_progress(frame)
            return frame

        # READY: translucent fill + solid outline.
        # We shade a copy and blend it, which is how you get transparency in
        # OpenCV -- there is no alpha channel on a normal BGR frame.
        overlay = frame.copy()
        cv2.fillPoly(overlay, [self._polygon], color)
        cv2.addWeighted(overlay, self.fill_alpha, frame, 1.0 - self.fill_alpha, 0, frame)

        cv2.polylines(
            frame, [self._polygon], True, color, self.thickness, lineType=cv2.LINE_AA
        )

        for point in self.points:
            cv2.circle(frame, point, self.point_radius - 2, color, -1, lineType=cv2.LINE_AA)

        self._draw_zone_label(frame, color, violated, label_offset)
        return frame

    def _draw_in_progress(self, frame):
        """Rubber-band preview shown while the operator is still clicking."""
        color = self.color

        if len(self.points) >= 2:
            pts = np.array(self.points, dtype=np.int32).reshape((-1, 1, 2))
            cv2.polylines(frame, [pts], False, color, self.thickness, lineType=cv2.LINE_AA)

        # Dashed-looking guide from the last point to the cursor, plus the
        # closing edge back to the first point, so the shape is predictable.
        if self.points and self.hover_point:
            cv2.line(frame, self.points[-1], self.hover_point, color, 1, lineType=cv2.LINE_AA)
            if len(self.points) >= 2:
                cv2.line(
                    frame, self.hover_point, self.points[0],
                    (150, 150, 150), 1, lineType=cv2.LINE_AA,
                )

        for index, point in enumerate(self.points):
            cv2.circle(frame, point, self.point_radius, color, -1, lineType=cv2.LINE_AA)
            cv2.circle(frame, point, self.point_radius, (255, 255, 255), 1, lineType=cv2.LINE_AA)
            cv2.putText(
                frame, str(index + 1),
                (point[0] + 9, point[1] - 9),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA,
            )

    LABEL_SCALE = 0.6
    LABEL_THICKNESS = 2
    LABEL_PAD = 8

    def label_geometry(self, offset=(0, 0)):
        """
        Where this zone's label lands: (text, origin, rect).

        `rect` is (x1, y1, x2, y2) of the plate behind the text. ZoneManager uses
        it to detect two zones whose labels would print on top of each other --
        nested SAFE / WARNING / DANGER polygons often share a centre.

        Returns None when there is nothing to label yet.
        """
        if not self.points:
            return None

        label = self._zone_label_text()
        center = polygon_centroid(self.points)
        (text_w, text_h), _ = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, self.LABEL_SCALE, self.LABEL_THICKNESS
        )
        origin = (center[0] - text_w // 2 + int(offset[0]),
                  center[1] + text_h // 2 + int(offset[1]))
        pad = self.LABEL_PAD
        rect = (origin[0] - pad, origin[1] - text_h - pad,
                origin[0] + text_w + pad, origin[1] + pad)
        return label, origin, rect

    def _draw_zone_label(self, frame, color, violated, offset=(0, 0)):
        """Write the zone's tag in the middle of the polygon."""
        geometry = self.label_geometry(offset)
        if geometry is None:
            return
        label, origin, rect = geometry

        # Solid plate behind the text keeps it readable over a busy factory
        # floor. When violated the plate itself turns red and the text stays
        # white -- red text on a dark plate would be much harder to read.
        plate_color = color if violated else (20, 20, 20)
        cv2.rectangle(frame, (rect[0], rect[1]), (rect[2], rect[3]), plate_color, -1)
        cv2.putText(
            frame, label, origin,
            cv2.FONT_HERSHEY_SIMPLEX, self.LABEL_SCALE,
            (255, 255, 255),
            self.LABEL_THICKNESS, cv2.LINE_AA,
        )

    def _zone_label_text(self):
        """The text drawn in the middle of the polygon. Overridden below."""
        return "RESTRICTED AREA"


# ===========================================================================
# MULTI-ZONE SAFETY MONITORING
#
# RestrictedZone above is unchanged and still fully usable on its own. What
# follows adds an identity (unique tag / type / risk) to a polygon, and a manager
# that owns as many of them as the floor needs.
#
# Division of labour, unchanged since Phase 1:
#     zone.py        answers "WHERE is the person?"
#     risk_engine.py answers "HOW DANGEROUS is that?"
# ===========================================================================


class ZoneIdFactory:
    """
    THE ONE PLACE ZONE IDS COME FROM.

    Hands out SAFE_1, SAFE_2, WARNING_1, DANGER_1, DANGER_2 ... and guarantees
    that no id is ever issued twice inside a run, even after zones are deleted
    and redrawn.

    WHY NOT len(safe_zones) + 1
    Because that collides. Draw SAFE_1 and SAFE_2, delete SAFE_1, draw another:
    the list length says "2", so the new polygon is also called SAFE_2 -- and now
    two different pieces of floor share one id, in the live UI and in yesterday's
    event log. A monotonic per-type counter cannot do that.

    Counters are NEVER rewound, not even by 'clear all'. Reusing SAFE_1 for a
    different polygon in the same session would silently corrupt the audit trail,
    which is the one thing a safety log must not do.
    """

    def __init__(self):
        self._counters = {}   # zone_type -> how many have ever been issued
        self._issued = set()  # every id handed out, for a belt-and-braces check

    def next_id(self, zone_type):
        """The next free id for this type, e.g. 'DANGER_2'."""
        while True:
            count = self._counters.get(zone_type, 0) + 1
            self._counters[zone_type] = count
            zone_id = "{}_{}".format(zone_type, count)
            # Cannot normally collide; this makes it impossible.
            if zone_id not in self._issued:
                self._issued.add(zone_id)
                return zone_id

    @property
    def issued(self):
        """Every id issued so far. Used by the tests to prove uniqueness."""
        return set(self._issued)


class SafetyZone(RestrictedZone):
    """
    A RestrictedZone that knows what kind of zone it is and which zone it is.

    Adds an identity and inherits every bit of the mouse handling, validation,
    hysteresis-friendly signed distance and drawing code above.

        zone_id     "DANGER_1"   unique across ALL zones and ALL types
        zone_type   "DANGER"     what the risk rules key off
        name        "DANGER ZONE" human wording for banners
    """

    def __init__(self, zone_type, zone_id, name, risk_level, color,
                 show_tag=True, **kwargs):
        super().__init__(color=color, **kwargs)
        self.zone_id = zone_id          # "DANGER_1" -- the tag, unique
        self.zone_type = zone_type      # "SAFE" / "WARNING" / "DANGER"
        self.name = name                # "DANGER ZONE"
        self.risk_level = risk_level    # base risk for standing here
        self.show_tag = show_tag        # draw the tag rather than the long name

    @property
    def tag(self):
        """Alias for zone_id -- 'tag' is what the operator sees on screen."""
        return self.zone_id

    def finish_definition(self):
        """Same validation as the base class, with a tag-aware message."""
        ok = super().finish_definition()
        if ok:
            self.last_message = "{} set ({} points).".format(
                self.zone_id, len(self.points))
        return ok

    def describe(self):
        """
        The zone's identity as plain JSON values, for events and for any future
        'save the zone layout to disk' feature. No numpy, no OpenCV objects.
        """
        return {
            "zone_id": self.zone_id,
            "zone_type": self.zone_type,
            "zone_name": self.name,
            "points": [[int(x), int(y)] for x, y in self.points],
        }

    def _zone_label_text(self):
        return self.zone_id if (self.show_tag and self.zone_id) else self.name


def _rects_overlap(a, b):
    """Do two (x1, y1, x2, y2) rectangles share any pixels?"""
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


class ZoneManager:
    """
    Owns every safety polygon on the floor and answers positional questions.

    The operator may draw ANY NUMBER of zones of each type -- a DANGER polygon
    around each machine, a WARNING ring around each of those, SAFE walkways --
    and every polygon gets a unique tag from ZoneIdFactory:

        SAFE_1  SAFE_2  WARNING_1  DANGER_1  DANGER_2

    Pressing S / W / D therefore ADDS a zone; it does not replace the previous
    one of that type. Nothing the operator has already drawn is ever touched by
    starting, finishing or abandoning a new polygon.

    Type-level questions still work exactly as before, and now resolve across
    every zone of that type:

        contains(ZONE_DANGER, point)      inside ANY danger polygon
        signed_distance(ZONE_DANGER, pt)  the danger polygon the point is most
                                          deeply inside
        distance_to_danger(point)         the NEAREST danger polygon

    Only ONE zone can be in DEFINING mode at a time, so the operator can never
    accidentally add points to two polygons at once. Mouse events are routed to
    whichever zone that is; every other zone ignores input.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.priority = tuple(cfg.ZONE_PRIORITY)
        self.last_message = ""

        # THE single source of zone ids. Never bypass it.
        self.ids = ZoneIdFactory()

        self._zones = []            # every zone, in the order it was created
        self._defining = None       # the one zone currently being clicked out
        self._last_finished = None  # the zone most recently completed
        self._frame_width = 0
        self._frame_height = 0

        # Shared polygon-editing settings, identical to the Phase 1 zone.
        self._common = dict(
            min_points=cfg.MIN_POLYGON_POINTS,
            min_area=cfg.MIN_POLYGON_AREA,
            point_radius=cfg.POLYGON_POINT_RADIUS,
            thickness=cfg.POLYGON_THICKNESS,
            alert_color=cfg.COLOR_ZONE_ALERT,
        )

    # -- access -------------------------------------------------------------

    @property
    def zone_types(self):
        """The three configured zone types, in SAFE / WARNING / DANGER order."""
        cfg = self.cfg
        return (cfg.ZONE_SAFE, cfg.ZONE_WARNING, cfg.ZONE_DANGER)

    @property
    def zones(self):
        """Every zone, in the order the operator created them."""
        return list(self._zones)

    @property
    def all_zones(self):
        """Every zone in priority order (DANGER first by default)."""
        ordered = []
        for zone_type in self.priority:
            ordered.extend(self.zones_of_type(zone_type))
        return ordered

    def zones_of_type(self, zone_type):
        """Every zone of one type, in creation order."""
        return [zone for zone in self._zones if zone.zone_type == zone_type]

    def zone(self, zone_type):
        """
        A single representative zone of this type, or None.

        Prefers the newest FINISHED polygon, so a half-drawn addition never hides
        the zone that is actually being monitored.
        """
        candidates = self.zones_of_type(zone_type)
        ready = [zone for zone in candidates if zone.is_ready]
        if ready:
            return ready[-1]
        return candidates[-1] if candidates else None

    def zone_by_id(self, zone_id):
        """The zone carrying this tag, e.g. 'DANGER_2'. None if it is gone."""
        for zone in self._zones:
            if zone.zone_id == zone_id:
                return zone
        return None

    def type_of(self, zone_id):
        """'DANGER_2' -> 'DANGER'. None when the zone no longer exists."""
        zone = self.zone_by_id(zone_id)
        return zone.zone_type if zone is not None else None

    def iter_ready(self):
        """
        Yield every finished zone: highest-priority TYPE first, and within a type
        in creation order. This is the order that decides which zone governs when
        polygons overlap, so it is deliberately deterministic.
        """
        for zone_type in self.priority:
            for zone in self.zones_of_type(zone_type):
                if zone.is_ready:
                    yield zone

    @property
    def ready_zones(self):
        return list(self.iter_ready())

    @property
    def ready_ids(self):
        """Tags of every finished zone, e.g. ['DANGER_1', 'WARNING_1']."""
        return [zone.zone_id for zone in self.iter_ready()]

    @property
    def ready_types(self):
        """Distinct zone TYPES that have at least one finished polygon."""
        types = []
        for zone in self.iter_ready():
            if zone.zone_type not in types:
                types.append(zone.zone_type)
        return types

    @property
    def any_ready(self):
        return any(zone.is_ready for zone in self._zones)

    @property
    def is_defining(self):
        return self._defining is not None

    @property
    def defining_zone(self):
        return self._defining

    @property
    def defining_type(self):
        return self._defining.zone_type if self._defining is not None else None

    @property
    def defining_id(self):
        return self._defining.zone_id if self._defining is not None else None

    @property
    def last_finished(self):
        """The zone completed most recently, for the operator message in main.py."""
        return self._last_finished

    def describe(self):
        """
        Every finished zone as plain JSON values, for the session metadata file:
            [{"zone_id": "DANGER_1", "zone_type": "DANGER", "points": [...]}, ...]
        """
        return [zone.describe() for zone in self.iter_ready()]

    def set_frame_size(self, width, height):
        self._frame_width = width
        self._frame_height = height
        for zone in self._zones:
            zone.set_frame_size(width, height)

    # -- operator actions ---------------------------------------------------

    def _new_zone(self, zone_type):
        """Create a zone of `zone_type` with the next unique tag, and keep it."""
        cfg = self.cfg
        # The SAFE zone is shaded faintly -- it is information, not a warning.
        fill_alpha = (cfg.ZONE_FILL_ALPHA_SAFE
                      if zone_type == cfg.ZONE_SAFE else cfg.ZONE_FILL_ALPHA)
        zone = SafetyZone(
            zone_type=zone_type,
            zone_id=self.ids.next_id(zone_type),
            name=cfg.ZONE_LABELS[zone_type],
            risk_level=cfg.ZONE_RISK[zone_type],
            color=cfg.ZONE_COLORS[zone_type],
            show_tag=getattr(cfg, "SHOW_ZONE_TAG", True),
            fill_alpha=fill_alpha,
            **self._common
        )
        zone.set_frame_size(self._frame_width, self._frame_height)
        self._zones.append(zone)
        return zone

    def start_definition(self, zone_type):
        """
        Begin drawing a NEW zone of this type. Every existing zone is left alone.

        If a half-drawn polygon is abandoned -- the operator presses 'w', then
        changes their mind and presses 'd' -- the abandoned polygon is simply
        discarded. Because a new polygon is ADDED rather than overwriting the
        previous one, a working WARNING zone can no longer be lost that way.
        """
        if zone_type not in self.zone_types:
            self.last_message = "Unknown zone type: {}".format(zone_type)
            return False

        self._discard_defining()

        zone = self._new_zone(zone_type)
        zone.start_definition()
        self._defining = zone
        self.last_message = "Drawing {}: click points, ENTER to finish.".format(
            zone.zone_id)
        return True

    def _discard_defining(self):
        """
        Throw away the polygon being drawn and leave DEFINING mode.

        Its tag is retired with it and never reissued, so a log line mentioning
        DANGER_2 can only ever mean the polygon that really was DANGER_2.

        Returns the discarded zone, or None.
        """
        zone = self._defining
        if zone is None:
            return None
        zone.clear()
        if zone in self._zones:
            self._zones.remove(zone)
        self._defining = None
        return zone

    def finish_definition(self):
        """
        Close the polygon currently being drawn (the 'Enter' key).

        Returns (ok, zone_type). On failure we stay in DEFINING mode, exactly
        like the Phase 1 behaviour, so a bad polygon is never silently accepted.
        """
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
        """
        Abandon the polygon being drawn. Zones already finished are untouched.

        Returns the zone type that was cancelled, or None.
        """
        zone = self._discard_defining()
        if zone is None:
            return None
        self.last_message = "Cancelled {}. Existing zones kept.".format(zone.zone_id)
        return zone.zone_type

    def clear(self, zone_type=None):
        """Remove every zone of one type, or every zone when zone_type is None."""
        if zone_type is None:
            removed = len(self._zones)
            self._zones = []
            self._defining = None
            self._last_finished = None
            self.last_message = "All zones cleared ({} removed).".format(removed)
            return

        doomed = self.zones_of_type(zone_type)
        if self._defining in doomed:
            self._defining = None
        if self._last_finished in doomed:
            self._last_finished = None
        self._zones = [zone for zone in self._zones if zone not in doomed]
        self.last_message = "{} cleared ({} removed).".format(
            self.cfg.ZONE_LABELS.get(zone_type, zone_type), len(doomed))

    def set_zone_points(self, zone_type, points):
        """
        Define a zone programmatically, bypassing the mouse. Adds a new zone with
        its own unique tag, exactly like drawing one by hand.

        Used by the test suite and by any future "load saved zones from disk"
        feature. Returns (ok, reason) just like is_valid_polygon.
        """
        if zone_type not in self.zone_types:
            return False, "Unknown zone type: {}".format(zone_type)

        self._discard_defining()

        zone = self._new_zone(zone_type)
        zone.start_definition()
        zone.points = [(int(x), int(y)) for x, y in points]

        if zone.finish_definition():
            self._last_finished = zone
            return True, ""

        reason = zone.last_message
        self._zones.remove(zone)
        return False, reason

    # -- mouse ---------------------------------------------------------------

    def handle_mouse(self, event, x, y, flags, param=None):
        """
        Single OpenCV mouse callback for every zone, registered by main.py.

        Routed to the zone being defined; when nothing is being defined every
        click is ignored, so a stray click can never move a live perimeter.
        """
        zone = self._defining
        if zone is None:
            return
        zone.handle_mouse(event, x, y, flags, param)
        self.last_message = zone.last_message

    # -- geometry queries ----------------------------------------------------

    def classify_zone(self, point):
        """
        Which ZONE is this point in? Raw test, NO hysteresis. None if in none.

        Resolved by iter_ready(), i.e. by cfg.ZONE_PRIORITY, so with nested
        polygons DANGER wins over WARNING wins over SAFE.
        """
        for zone in self.iter_ready():
            if zone.contains(point):
                return zone
        return None

    def classify(self, point):
        """
        Which zone TYPE is this point in? Raw test, NO hysteresis.

        Returns ZONE_OUTSIDE when the point is in none of them (which is also
        what you get before any zone is drawn).

        The debounced equivalent lives in tracker.py, which needs per-person
        state to do its job; this is the stateless version.
        """
        zone = self.classify_zone(point)
        return zone.zone_type if zone is not None else self.cfg.ZONE_OUTSIDE

    def resolve_confirmed(self, confirmed):
        """
        Resolve a {zone_id: bool} map of already-debounced memberships into the
        single zone that governs, as (zone_type, zone_id).

        Same priority order as classify(): DANGER beats WARNING beats SAFE, and
        within one type the zone drawn first wins. Returns (ZONE_OUTSIDE, None)
        when the person is confirmed in nothing.
        """
        for zone_type in self.priority:
            for zone in self.zones_of_type(zone_type):
                if confirmed.get(zone.zone_id):
                    return zone_type, zone.zone_id
        return self.cfg.ZONE_OUTSIDE, None

    def confirmed_ids_of_type(self, confirmed, zone_type):
        """The tags of every zone of one type this {zone_id: bool} map is inside."""
        return [zone.zone_id for zone in self.zones_of_type(zone_type)
                if confirmed.get(zone.zone_id)]

    def signed_distance(self, zone_type, point):
        """
        Signed px distance to this zone TYPE (positive inside), or -inf when no
        zone of that type has been drawn yet.

        With several zones of one type we take the MAXIMUM, i.e. the polygon the
        point is most deeply inside (or, when outside them all, the nearest one).
        That is what keeps the hysteresis dead-band behaving as documented.
        """
        distances = [zone.signed_distance(point)
                     for zone in self.zones_of_type(zone_type) if zone.is_ready]
        return max(distances) if distances else float("-inf")

    def distance_to_danger(self, point):
        """
        Unsigned pixel distance from `point` to the edge of the NEAREST danger
        polygon. 0.0 when the point is already inside one, None when no DANGER
        zone has been drawn.

        THIS IS PIXELS, NOT METRES. cv2.pointPolygonTest already returns the
        distance to the nearest edge, so no extra geometry is needed -- we just
        drop the sign and floor it at zero.
        """
        signed = self.signed_distance(self.cfg.ZONE_DANGER, point)
        if signed == float("-inf"):
            return None
        return 0.0 if signed >= 0 else float(-signed)

    def contains(self, zone_type, point):
        """Raw inside/outside test against ANY zone of one type."""
        return any(zone.contains(point) for zone in self.zones_of_type(zone_type))

    # -- drawing -------------------------------------------------------------

    def draw(self, frame, violated_types=(), violated_ids=()):
        """
        Draw every zone onto `frame`.

        A zone is drawn in its alert colour when its own tag is in `violated_ids`,
        or when its whole type is in `violated_types` (the coarser call style,
        still supported).

        Painted in reverse priority order -- SAFE first, DANGER last -- so the
        most important polygon is never buried under another one's fill.
        """
        height, width = frame.shape[:2]
        self.set_frame_size(width, height)

        types = set(violated_types or ())
        ids = set(violated_ids or ())
        gap = getattr(self.cfg, "ZONE_TAG_MIN_GAP_PX", 4)
        taken = []   # label rectangles already used on this frame

        for zone in reversed(self.all_zones):
            violated = zone.zone_id in ids or zone.zone_type in types
            zone.draw(frame, violated=violated,
                      label_offset=self._label_offset(zone, taken, gap))
        return frame

    @staticmethod
    def _label_offset(zone, taken, gap):
        """
        Find a vertical offset at which this zone's tag does not sit on top of a
        tag already drawn, and reserve the space it uses.

        Nested zones frequently share a centroid, which would otherwise print
        DANGER_1 straight over WARNING_1. Each clashing tag is pushed below the
        one it hit.
        """
        offset = (0, 0)
        if not zone.is_ready:
            return offset

        for _ in range(len(taken) + 1):
            geometry = zone.label_geometry(offset)
            if geometry is None:
                return offset
            rect = geometry[2]
            clash = None
            for used in taken:
                if _rects_overlap(used, rect):
                    clash = used
                    break
            if clash is None:
                taken.append(rect)
                return offset
            # Drop below whatever we collided with and measure again.
            offset = (offset[0], offset[1] + (clash[3] - rect[1]) + gap)

        # Ran out of attempts (many stacked zones): keep the last position and
        # still reserve it, so the next zone is pushed clear of this one.
        geometry = zone.label_geometry(offset)
        if geometry is not None:
            taken.append(geometry[2])
        return offset

    def summary(self):
        """
        Short HUD string showing how many zones of each type exist:

            'Zones: S2 W1 D-'    two safe zones, one warning, no danger zone
            'Zones: S1 W1 d+'    a danger zone is being drawn right now
        """
        parts = []
        for zone_type in self.zone_types:
            zones = self.zones_of_type(zone_type)
            initial = zone_type[0]
            ready = sum(1 for zone in zones if zone.is_ready)
            if any(zone.is_defining for zone in zones):
                parts.append("{}+".format(initial.lower()))
            elif ready:
                parts.append("{}{}".format(initial, ready))
            else:
                parts.append("{}-".format(initial))
        return "Zones: " + " ".join(parts)
