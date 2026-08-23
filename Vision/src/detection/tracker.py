"""
src/tracker.py
Tracks people across frames and prevents flickering when people are near zone edges
"""

import time
from utils.geometry import euclidean_distance, get_bottom_center

ZONE_OUTSIDE = "OUTSIDE"  # Default zone when no zones are drawn

# ---------------------------------------------------------------------------
# IDENTITY - Giving each person a stable name like "P1", "P2", etc.
# ---------------------------------------------------------------------------

PERSON_ID_PREFIX = "P"
UNIDENTIFIED = "UNIDENTIFIED"


def format_person_id(track_id):
    """Convert track ID 1 -> 'P1'"""
    return "{}{}".format(PERSON_ID_PREFIX, track_id)


def make_identity(track_id, employee_id=None, person_name=None, identity_status=UNIDENTIFIED):
    """
    Creates identity block for a person.
    Later face recognition will fill in employee_id and person_name.
    """
    return {
        "person_id": format_person_id(track_id),
        "employee_id": employee_id,
        "person_name": person_name,
        "identity_status": identity_status,
    }


def calculate_body_points(bbox):
    """
    Calculates key body points from a bounding box:
    feet, shoulders, head, chest, hips
    """
    x1, y1, x2, y2 = bbox
    width = x2 - x1
    height = y2 - y1
    body_points = {
        "feet_center": ((x1 + x2) // 2, y2),
        "left_foot": (int(x1 + width * 0.25), int(y1 + height * 0.95)),
        "right_foot": (int(x1 + width * 0.75), int(y1 + height * 0.95)),
        "left_shoulder": (int(x1 + width * 0.25), int(y1 + height * 0.25)),
        "right_shoulder": (int(x1 + width * 0.75), int(y1 + height * 0.25)),
        "head": (int(x1 + width * 0.5), int(y1 + height * 0.05)),
        "chest": (int(x1 + width * 0.5), int(y1 + height * 0.35)),
        "hips": (int(x1 + width * 0.5), int(y1 + height * 0.70)),
    }
    return body_points


class PersonTrack:
    """Stores everything about one tracked person"""
    
    __slots__ = (
        "track_id", "person_id", "identity",
        "bbox", "confidence", "feet",
        "is_inside", "inside_streak", "outside_streak", "missing_frames",
        "zone",             # Debounced body zone: SAFE / WARNING / DANGER / OUTSIDE
        "zone_id",          # Which polygon: "DANGER_1", or None
        "zone_inside",      # Confirmed membership per polygon
        "zone_streaks",     # Streak counters per polygon
        "danger_since",     # When person entered DANGER zone
        "danger_duration",  # Seconds continuously in DANGER
        "warning_since",    # When person entered WARNING zone
        "warning_duration", # Seconds continuously in WARNING
        "hand_in_danger",   # True if wrist is in danger zone (confirmed)
        "hand_zone",        # Debounced hand zone
        "hand_zone_id",     # Which hand zone polygon
        "hand_inside",      # Confirmed wrist membership per polygon
        "hand_streaks",     # Streak counters for hands
        "wrists",           # Wrist points [(x, y), ...]
        "wrist_flags",      # Which wrists are in danger (for drawing)
        "body_points",      # All body points
        "shoulders",        # Left and right shoulder
        "head",             # Head point
    )

    def __init__(self, track_id, bbox, confidence, feet, body_points=None):
        # Basic info
        self.track_id = track_id
        self.person_id = format_person_id(track_id)
        self.identity = make_identity(track_id)
        self.bbox = bbox
        self.confidence = confidence
        self.feet = feet
        
        # Zone state
        self.is_inside = False          # Debounced confirmed state
        self.inside_streak = 0          # Frames clearly inside
        self.outside_streak = 0         # Frames clearly outside
        self.missing_frames = 0         # Frames since last seen
        
        self.zone = ZONE_OUTSIDE
        self.zone_id = None
        self.zone_inside = {}           # {zone_id: True/False}
        self.zone_streaks = {}          # {zone_id: [in_streak, out_streak]}
        
        # Danger timer
        self.danger_since = None
        self.danger_duration = 0.0
        
        # Warning timer (NEW)
        self.warning_since = None
        self.warning_duration = 0.0
        
        # Hand state
        self.hand_in_danger = False
        self.hand_zone = None
        self.hand_zone_id = None
        self.hand_inside = {}
        self.hand_streaks = {}
        self.wrists = []
        self.wrist_flags = []
        
        # Body points
        if body_points is not None:
            self.body_points = body_points
        else:
            self.body_points = calculate_body_points(bbox)
        self.shoulders = [
            self.body_points.get("left_shoulder", (0, 0)),
            self.body_points.get("right_shoulder", (0, 0))
        ]
        self.head = self.body_points.get("head", (0, 0))


class PersonTracker:
    """
    Tracks people across frames using simple nearest-neighbour matching.
    Prevents flickering with hysteresis (confirmation frames + margin).
    """

    def __init__(
        self,
        enabled=True,
        max_distance=120.0,
        max_missing_frames=15,
        zone_margin_px=6.0,
        enter_confirm_frames=2,
        exit_confirm_frames=5,
        hand_margin_px=4.0,
        hand_enter_confirm_frames=3,
        hand_exit_confirm_frames=5,
    ):
        # Settings
        self.enabled = enabled
        self.max_distance = max_distance          # Max feet distance to match same person
        self.max_missing_frames = max_missing_frames  # Frames to wait before forgetting
        self.zone_margin_px = zone_margin_px      # Dead zone around edges
        self.enter_confirm_frames = enter_confirm_frames  # Frames to confirm entering
        self.exit_confirm_frames = exit_confirm_frames    # Frames to confirm leaving
        
        # Hand has different thresholds (moves faster)
        self.hand_margin_px = hand_margin_px
        self.hand_enter_confirm_frames = hand_enter_confirm_frames
        self.hand_exit_confirm_frames = hand_exit_confirm_frames
        
        # State
        self._tracks = []           # All active tracks
        self._next_id = 1           # Next track ID to assign
        self.lost_ids = []          # IDs that just left this frame

    def reset(self):
        """Forget all tracks - called when zones change"""
        self._tracks = []
        self._next_id = 1
        self.lost_ids = []

    @property
    def live_ids(self):
        """All tracks still considered alive (including those temporarily missing)"""
        return [track.track_id for track in self._tracks]

    def update(self, detections, zones, now=None):
        """
        Main update function - call every frame.
        
        detections: List from detector.py: [{"bbox": (x1,y1,x2,y2), "confidence": 0.87}]
        zones: ZoneManager with all drawn zones
        
        Returns list of people with zone states and timers.
        """
        if now is None:
            now = time.time()

        # Prepare detections: calculate feet and body points
        prepared = []
        for det in detections:
            feet = get_bottom_center(det["bbox"])
            prepared.append({
                "bbox": det["bbox"],
                "confidence": det["confidence"],
                "feet": feet,
                "body_points": calculate_body_points(det["bbox"]),
            })

        # If tracking disabled, just return detections without identity
        if not self.enabled:
            self.lost_ids = []
            return self._stateless_results(prepared, zones)

        # Match detections to existing tracks
        matched_track_ids = self._match(prepared)

        # Age out tracks not seen recently
        for track in self._tracks:
            if track.track_id not in matched_track_ids:
                track.missing_frames += 1

        # Keep only tracks that haven't been missing too long
        alive = [t for t in self._tracks if t.missing_frames <= self.max_missing_frames]
        self.lost_ids = [t.track_id for t in self._tracks if t not in alive]
        self._tracks = alive

        # Apply zone tests + hysteresis to every visible person
        results = []
        for item in prepared:
            track = item["track"]
            self._update_zone_states(track, zones, now)
            results.append({
                "id": track.track_id,
                "person_id": track.person_id,
                "identity": dict(track.identity),
                "bbox": track.bbox,
                "confidence": track.confidence,
                "feet": track.feet,
                "body_points": track.body_points,
                "shoulders": track.shoulders,
                "head": track.head,
                "zone": track.zone,
                "zone_id": track.zone_id,
                "danger_duration": track.danger_duration,
                "warning_duration": track.warning_duration,
                "distance_to_danger": self._danger_distance(track.feet, zones),
                "hand_in_danger": track.hand_in_danger,
                "hand_zone": track.hand_zone,
                "hand_zone_id": track.hand_zone_id,
                "wrists": list(track.wrists),
                "wrist_flags": list(track.wrist_flags),
                # Phase 1 keys - still mean DANGER zone
                "is_inside": track.is_inside,
                "distance": self._zone_distance(track.feet, zones),
            })

        results.sort(key=lambda r: r["id"])
        return results

    def update_hands(self, people, wrist_map, zones):
        """
        Second pass: update hand/wrist states.
        Call only on frames where pose model ran.
        """
        danger_type = zones.cfg.ZONE_DANGER if zones is not None else None
        active = zones is not None and zones.any_ready
        has_danger = active and bool(zones.zones_of_type(danger_type))

        tracks = {t.track_id: t for t in self._tracks}

        for person in people:
            wrists = list(wrist_map.get(person["id"], []))
            track = tracks.get(person["id"])

            if not active:
                hand_zone, hand_zone_id = None, None
                if track is not None:
                    track.hand_inside.clear()
                    track.hand_streaks.clear()
            elif self.enabled and track is not None:
                hand_zone, hand_zone_id = self._update_hand_zones(track, wrists, zones)
            else:
                # Tracking disabled - no persistence, use raw per-frame
                hand_zone, hand_zone_id = self._raw_hand_zone(wrists, zones)

            confirmed = hand_zone == danger_type

            # Which individual wrists are in danger (for drawing)
            flags = [bool(zones.contains(danger_type, w)) for w in wrists] \
                if has_danger else [False] * len(wrists)

            # Save hand state
            if track is not None:
                track.wrists = wrists
                track.wrist_flags = flags
                track.hand_in_danger = confirmed
                track.hand_zone = hand_zone
                track.hand_zone_id = hand_zone_id

            person["wrists"] = wrists
            person["wrist_flags"] = flags
            person["hand_in_danger"] = confirmed
            person["hand_zone"] = hand_zone
            person["hand_zone_id"] = hand_zone_id

        return people

    def _update_hand_zones(self, track, wrists, zones):
        """
        Run hysteresis for wrists against every zone.
        Uses the wrist that is most inside each zone.
        """
        ready = []
        for zone in zones.iter_ready():
            zone_id = zone.zone_id
            ready.append(zone_id)
            if wrists:
                # Take wrist that is most inside
                distance = max(zone.signed_distance(w) for w in wrists)
            else:
                # No wrist observed - counts toward clearing
                distance = float("-inf")

            streaks = track.hand_streaks.setdefault(zone_id, [0, 0])
            track.hand_inside[zone_id] = self._debounce(
                track.hand_inside.get(zone_id, False),
                streaks,
                distance,
                self.hand_margin_px,
                self.hand_enter_confirm_frames,
                self.hand_exit_confirm_frames,
            )

        # Forget removed zones
        for zone_id in list(track.hand_inside):
            if zone_id not in ready:
                track.hand_inside.pop(zone_id, None)
                track.hand_streaks.pop(zone_id, None)

        if wrists or any(track.hand_inside.values()):
            return zones.resolve_confirmed(track.hand_inside)
        return None, None  # No wrist information

    @staticmethod
    def _raw_hand_zone(wrists, zones):
        """Undebounced hand zone - used when tracking is off"""
        if not wrists or zones is None:
            return None, None
        for zone in zones.iter_ready():
            if any(zone.contains(w) for w in wrists):
                return zone.zone_type, zone.zone_id
        return zones.cfg.ZONE_OUTSIDE, None

    # -- internals -----------------------------------------------------------

    def _stateless_results(self, prepared, zones):
        """When tracking is disabled - just number detections in order"""
        results = []
        for index, item in enumerate(prepared, start=1):
            distance = self._zone_distance(item["feet"], zones)
            zone = zones.classify_zone(item["feet"]) if zones is not None else None
            zone_name = zone.zone_type if zone is not None else ZONE_OUTSIDE
            danger_type = zones.cfg.ZONE_DANGER if zones is not None else None
            results.append({
                "id": index,
                "person_id": format_person_id(index),
                "identity": make_identity(index),
                "bbox": item["bbox"],
                "confidence": item["confidence"],
                "feet": item["feet"],
                "body_points": item["body_points"],
                "shoulders": [item["body_points"]["left_shoulder"], item["body_points"]["right_shoulder"]],
                "head": item["body_points"]["head"],
                "zone": zone_name,
                "zone_id": zone.zone_id if zone is not None else None,
                "danger_duration": 0.0,
                "warning_duration": 0.0,
                "distance_to_danger": self._danger_distance(item["feet"], zones),
                "hand_in_danger": False,
                "hand_zone": None,
                "hand_zone_id": None,
                "wrists": [],
                "wrist_flags": [],
                "is_inside": zone_name == danger_type,
                "distance": distance,
            })
        return results

    @staticmethod
    def _zone_distance(feet, zones):
        """Distance to DANGER zone (Phase 1's distance key)"""
        if zones is None:
            return float("-inf")
        return zones.signed_distance(zones.cfg.ZONE_DANGER, feet)

    @staticmethod
    def _danger_distance(feet, zones):
        """Distance to DANGER edge in pixels (0 inside, None if no danger zone)"""
        if zones is None:
            return None
        return zones.distance_to_danger(feet)

    def _match(self, prepared):
        """
        Match detections to existing tracks using nearest-neighbour.
        Greedy algorithm - closest match gets assigned first.
        """
        available = list(self._tracks)
        matched_ids = set()

        # Find best match for each detection
        candidates = []
        for item in prepared:
            best_track = None
            best_distance = None
            for track in available:
                distance = euclidean_distance(item["feet"], track.feet)
                if distance <= self.max_distance and (
                    best_distance is None or distance < best_distance
                ):
                    best_track = track
                    best_distance = distance
            candidates.append((best_distance if best_distance is not None else 1e12, item, best_track))

        # Sort by distance - closest first
        candidates.sort(key=lambda c: c[0])

        # Assign matches
        for _, item, preferred in candidates:
            chosen = None
            if preferred is not None and preferred in available:
                chosen = preferred
            else:
                # Preferred was taken, find next best
                best_distance = None
                for track in available:
                    distance = euclidean_distance(item["feet"], track.feet)
                    if distance <= self.max_distance and (
                        best_distance is None or distance < best_distance
                    ):
                        chosen = track
                        best_distance = distance

            if chosen is None:
                # New person - create new track
                chosen = PersonTrack(
                    self._next_id, item["bbox"], item["confidence"], item["feet"]
                )
                self._next_id += 1
                self._tracks.append(chosen)
            else:
                # Update existing track
                available.remove(chosen)
                chosen.bbox = item["bbox"]
                chosen.confidence = item["confidence"]
                chosen.feet = item["feet"]
                chosen.body_points = calculate_body_points(item["bbox"])
                chosen.shoulders = [
                    chosen.body_points["left_shoulder"],
                    chosen.body_points["right_shoulder"]
                ]
                chosen.head = chosen.body_points["head"]

            chosen.missing_frames = 0
            matched_ids.add(chosen.track_id)
            item["track"] = chosen

        return matched_ids

    @staticmethod
    def _debounce(state, streaks, distance, margin, enter_frames, exit_frames):
        """
        Hysteresis state machine for one zone edge.
        
        distance > margin  -> clearly inside  -> count toward entering
        distance < -margin -> clearly outside -> count toward leaving
        in between         -> ambiguous       -> hold current state
        
        Returns new confirmed state.
        """
        if distance > margin:
            streaks[0] += 1  # Inside streak
            streaks[1] = 0
        elif distance < -margin:
            streaks[1] += 1  # Outside streak
            streaks[0] = 0
        else:
            # In the dead-band - change nothing
            return state

        if not state and streaks[0] >= enter_frames:
            return True   # Confirmed entering
        if state and streaks[1] >= exit_frames:
            return False  # Confirmed leaving
        return state

    def _update_zone_states(self, track, zones, now):
        """
        Run hysteresis against every polygon zone.
        Each zone has its own streak counters.
        """
        if zones is None or not zones.any_ready:
            # No zones - everything is OUTSIDE
            track.zone_inside.clear()
            track.zone_streaks.clear()
            track.zone = ZONE_OUTSIDE
            track.zone_id = None
            track.is_inside = False
            track.inside_streak = 0
            track.outside_streak = 0
            self._update_danger_timer(track, now, False)
            self._update_warning_timer(track, now, False)
            return

        # Check each zone
        ready = []
        for zone in zones.iter_ready():
            zone_id = zone.zone_id
            ready.append(zone_id)
            streaks = track.zone_streaks.setdefault(zone_id, [0, 0])
            track.zone_inside[zone_id] = self._debounce(
                track.zone_inside.get(zone_id, False),
                streaks,
                zone.signed_distance(track.feet),
                self.zone_margin_px,
                self.enter_confirm_frames,
                self.exit_confirm_frames,
            )

        # Forget removed zones
        for zone_id in list(track.zone_inside):
            if zone_id not in ready:
                track.zone_inside.pop(zone_id, None)
                track.zone_streaks.pop(zone_id, None)

        # Resolve which zone wins (DANGER > WARNING > SAFE)
        track.zone, track.zone_id = zones.resolve_confirmed(track.zone_inside)

        # Phase 1 compatibility - check DANGER zone
        danger_type = zones.cfg.ZONE_DANGER
        danger_ids = zones.confirmed_ids_of_type(track.zone_inside, danger_type)
        track.is_inside = bool(danger_ids)
        danger_streaks = track.zone_streaks.get(
            danger_ids[0] if danger_ids else None, [0, 0])
        track.inside_streak, track.outside_streak = danger_streaks

        # Update timers
        self._update_danger_timer(track, now, track.zone == danger_type)
        warning_type = zones.cfg.ZONE_WARNING
        self._update_warning_timer(track, now, track.zone == warning_type)

    @staticmethod
    def _update_danger_timer(track, now, in_danger):
        """
        Track how long person has been in DANGER zone.
        Starts when entering, resets when leaving.
        """
        if in_danger:
            if track.danger_since is None:
                track.danger_since = now
            track.danger_duration = max(0.0, now - track.danger_since)
        else:
            track.danger_since = None
            track.danger_duration = 0.0

    @staticmethod
    def _update_warning_timer(track, now, in_warning):
        """
        Track how long person has been in WARNING zone.
        Starts when entering, resets when leaving.
        """
        if in_warning:
            if track.warning_since is None:
                track.warning_since = now
            track.warning_duration = max(0.0, now - track.warning_since)
        else:
            track.warning_since = None
            track.warning_duration = 0.0