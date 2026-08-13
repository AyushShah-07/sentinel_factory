"""
src/tracker.py
--------------
Deliberately minimal person tracking + the anti-flicker (hysteresis) logic.

WHY THIS EXISTS
A person's feet point sitting right on the polygon edge would otherwise make
the status jump SAFE / RESTRICTED / SAFE / RESTRICTED many times per second,
which is useless to a human operator and would spam a real buzzer later.

Two cheap mechanisms fix that:

  1. MARGIN (dead-band): the point must be ZONE_MARGIN_PX *past* the edge, not
     merely across it. Between -margin and +margin nothing changes.
  2. CONFIRMATION FRAMES: the new state must persist for N consecutive frames
     before it is accepted. Entering is confirmed fast (safety first), leaving
     is confirmed slowly.

To apply per-person hysteresis we need to know which detection is which person
across frames, so we do the simplest possible tracking: match each detection to
the nearest existing track by feet-point distance (a greedy nearest-neighbour
match). This is NOT a robust tracker -- it can swap IDs when people cross --
but it is entirely sufficient for debouncing, adds no dependencies, and costs
microseconds. Set ENABLE_TRACKING = False in config.py to bypass it.

PHASE 2 ADDITIONS
The same two mechanisms now run against THREE zones instead of one, plus the
wrist. Nothing about the original logic changed -- it was extracted into
_debounce() and is now called four times per person instead of once.

Because tracking is what gives a person a stable identity, this is also the
natural home for the per-person danger-zone dwell timer. There is deliberately
no global timer: two workers standing in the danger zone have two independent
clocks.

IDENTITY
This file is also where a person gets their name. Right now that name is just
"P1" -- the track id with a prefix -- and every person carries an identity block
that a later face-recognition pass can fill in. See make_identity() below.
"""

import time

from utils.geometry import euclidean_distance, get_bottom_center

# Used only when no zones have been drawn yet. Matches config.ZONE_OUTSIDE;
# when zones do exist we read the names off the ZoneManager's own config, so
# the two can never drift apart.
ZONE_OUTSIDE = "OUTSIDE"

# ---------------------------------------------------------------------------
# IDENTITY
#
# A person is labelled P1, P2, P3 ... for the length of one monitoring session.
# The number is the tracker's own track id, which means:
#
#   * the label is STABLE for as long as the tracker believes it is the same
#     person -- P1 on frame 1 is P1 on frame 400,
#   * a genuinely new person always gets the next unused number. If P1 leaves
#     while P2 stays, the next arrival is P3; the tracker never recycles an id
#     inside a run, so a log line about P1 always means one specific person.
#
# THERE IS NO FACE RECOGNITION HERE, and the safety pipeline must never require
# it: an unrecognised worker has to be protected exactly as well as a recognised
# one. The identity block below is the seam where it will plug in later --
# a recognition pass fills employee_id / person_name / identity_status, and
# nothing else in the system has to change, because everything downstream
# (events, UI, backend) already reads these four fields.
# ---------------------------------------------------------------------------

PERSON_ID_PREFIX = "P"
UNIDENTIFIED = "UNIDENTIFIED"


def format_person_id(track_id):
    """Track id 1 -> 'P1'. The one place that string is built."""
    return "{}{}".format(PERSON_ID_PREFIX, track_id)


def make_identity(track_id, employee_id=None, person_name=None,
                  identity_status=UNIDENTIFIED):
    """
    The identity block carried by every person, as plain JSON values.

        {"person_id": "P1",           always present, session-scoped
         "employee_id": None,         filled in by future face recognition
         "person_name": None,         filled in by future face recognition
         "identity_status": "UNIDENTIFIED"}

    person_name stays None until a real recognition result exists. It is never a
    guess and never a placeholder name -- a wrong name on a safety event is worse
    than no name at all.
    """
    return {
        "person_id": format_person_id(track_id),
        "employee_id": employee_id,
        "person_name": person_name,
        "identity_status": identity_status,
    }


class PersonTrack:
    """One tracked person and their confirmed in-zone state."""

    __slots__ = (
        "track_id", "person_id", "identity",
        "bbox", "confidence", "feet",
        "is_inside", "inside_streak", "outside_streak", "missing_frames",
        "zone",             # debounced body zone TYPE: SAFE / WARNING / DANGER / OUTSIDE
        "zone_id",          # debounced body zone TAG: "DANGER_1", or None
        "zone_inside",      # {zone_id: bool} confirmed membership per POLYGON
        "zone_streaks",     # {zone_id: [inside_streak, outside_streak]}
        "danger_since",     # wall-clock time this person entered DANGER, or None
        "danger_duration",  # seconds continuously inside DANGER
        "hand_in_danger",   # confirmed wrist-inside-danger violation
        "hand_zone",        # debounced HAND zone type, independent of the body
        "hand_zone_id",     # debounced HAND zone tag, or None
        "hand_inside",      # {zone_id: bool} confirmed wrist membership
        "hand_streaks",     # {zone_id: [inside_streak, outside_streak]}
        "wrists",           # last known wrist points [(x, y), ...]
        "wrist_flags",      # per-wrist raw "is this one in danger?" for drawing
    )

    def __init__(self, track_id, bbox, confidence, feet):
        self.track_id = track_id
        # "P1". Stable for the life of the track; see make_identity() above.
        self.person_id = format_person_id(track_id)
        self.identity = make_identity(track_id)
        self.bbox = bbox
        self.confidence = confidence
        self.feet = feet
        self.is_inside = False      # the debounced, confirmed state
        self.inside_streak = 0      # consecutive frames measured clearly inside
        self.outside_streak = 0     # consecutive frames measured clearly outside
        self.missing_frames = 0     # frames since this person was last seen

        self.zone = ZONE_OUTSIDE
        self.zone_id = None
        self.zone_inside = {}
        self.zone_streaks = {}
        self.danger_since = None
        self.danger_duration = 0.0
        self.hand_in_danger = False
        self.hand_zone = None       # None = no wrist information yet
        self.hand_zone_id = None
        self.hand_inside = {}
        self.hand_streaks = {}
        self.wrists = []
        self.wrist_flags = []


class PersonTracker:
    """
    Greedy nearest-neighbour tracker with built-in in-zone hysteresis.

    Call update() once per frame; it returns the list of currently visible
    people, each already carrying a stable id and a debounced `is_inside` flag.
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
        self.enabled = enabled
        self.max_distance = max_distance
        self.max_missing_frames = max_missing_frames
        self.zone_margin_px = zone_margin_px
        self.enter_confirm_frames = enter_confirm_frames
        self.exit_confirm_frames = exit_confirm_frames
        # Wrist thresholds are separate: a hand moves much faster than a body,
        # so it needs its own dead-band and its own confirmation counts.
        self.hand_margin_px = hand_margin_px
        self.hand_enter_confirm_frames = hand_enter_confirm_frames
        self.hand_exit_confirm_frames = hand_exit_confirm_frames

        self._tracks = []
        self._next_id = 1
        # Ids dropped by the LAST update() call, i.e. people who really left
        # rather than people YOLO happened to miss on one frame. events.py needs
        # this distinction; see live_ids below.
        self.lost_ids = []

    def reset(self):
        """Forget every track. Called when the zone changes, so old confirmed
        states from a previous polygon cannot leak into the new one."""
        self._tracks = []
        self._next_id = 1
        self.lost_ids = []

    @property
    def live_ids(self):
        """
        Every track the tracker still considers present, INCLUDING those missing
        from the current frame but not yet aged out (TRACK_MAX_MISSING_FRAMES).

        This is not the same as "the people update() just returned". A worker YOLO
        misses for two frames is still the same worker, and must not be reported
        as having left and come back -- which is exactly what the event log used
        to do, filling the file with PERSON_LOST / PERSON_DETECTED pairs.
        """
        return [track.track_id for track in self._tracks]

    def update(self, detections, zones, now=None):
        """
        detections: list of {"bbox": (x1,y1,x2,y2), "confidence": float}
        zones:      a ZoneManager (may be None, or have no finished polygons)
        now:        wall-clock seconds; injectable so tests can fake the clock

        Returns a list of dicts, one per visible person:
            {"id", "person_id", "identity",
             "bbox", "confidence", "feet",
             "zone", "zone_id", "danger_duration", "distance_to_danger",
             "hand_in_danger", "hand_zone", "hand_zone_id", "wrists",
             "is_inside", "distance"}

        "id" is the raw integer track id, used as the dictionary key everywhere in
        the pipeline. "person_id" is the display / log name for the same person
        ("P1"), and "identity" is the block a later face-recognition pass fills in.

        "zone" is the zone TYPE (SAFE / WARNING / DANGER / OUTSIDE) that the risk
        rules key off; "zone_id" is WHICH polygon it was ("DANGER_1").

        "is_inside" and "distance" are kept from Phase 1 and refer to the DANGER
        zone, so anything written against the old shape still works.
        """
        if now is None:
            now = time.time()

        # Pre-compute each detection's ground contact point once.
        prepared = []
        for det in detections:
            feet = get_bottom_center(det["bbox"])
            prepared.append({
                "bbox": det["bbox"],
                "confidence": det["confidence"],
                "feet": feet,
            })

        if not self.enabled:
            # No identity, so nothing can be "lost" in a meaningful sense.
            self.lost_ids = []
            return self._stateless_results(prepared, zones)

        matched_track_ids = self._match(prepared)

        # Age out tracks that were not matched this frame.
        for track in self._tracks:
            if track.track_id not in matched_track_ids:
                track.missing_frames += 1

        alive = [t for t in self._tracks if t.missing_frames <= self.max_missing_frames]
        # Report who was actually dropped, so the event log can say PERSON_LOST
        # once, when the person is really gone.
        self.lost_ids = [t.track_id for t in self._tracks if t not in alive]
        self._tracks = alive

        # Apply the zone tests + hysteresis to every visible track.
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
                "zone": track.zone,
                "zone_id": track.zone_id,
                "danger_duration": track.danger_duration,
                "distance_to_danger": self._danger_distance(track.feet, zones),
                "hand_in_danger": track.hand_in_danger,
                "hand_zone": track.hand_zone,
                "hand_zone_id": track.hand_zone_id,
                "wrists": list(track.wrists),
                "wrist_flags": list(track.wrist_flags),
                # -- Phase 1 keys, still meaning "the DANGER zone" --
                "is_inside": track.is_inside,
                "distance": self._zone_distance(track.feet, zones),
            })

        results.sort(key=lambda r: r["id"])
        return results

    def update_hands(self, people, wrist_map, zones):
        """
        SECOND pass: fold wrist observations into each person's hand state.

        Call this ONLY on frames where the pose model actually ran. That is what
        makes POSE_EVERY_N_FRAMES safe: on the frames pose is skipped we do not
        touch the hand state at all, rather than misreading "no data" as "the
        hand left the danger zone". The confirmation counters therefore count
        POSE frames, which is exactly what the thresholds in config.py mean.

            people:    the list returned by update(), patched in place
            wrist_map: {person_id: [(x, y), ...]} wrist points for that person
            zones:     the ZoneManager

        A person with no wrists this pose frame IS an observation -- it counts
        towards clearing an existing violation, just more slowly than entering.
        """
        danger_type = zones.cfg.ZONE_DANGER if zones is not None else None
        active = zones is not None and zones.any_ready
        # Is there any finished danger polygon at all? (There may be several.)
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
                # Tracking disabled: no identity, so no persistence is possible.
                # Fall back to the raw per-frame test (documented in config.py).
                hand_zone, hand_zone_id = self._raw_hand_zone(wrists, zones)

            confirmed = hand_zone == danger_type

            # Which INDIVIDUAL wrist is in a danger zone, raw and undebounced.
            # Used only for drawing: highlighting both wrists when only one is
            # in the machine would tell the operator the wrong thing.
            flags = [bool(zones.contains(danger_type, w)) for w in wrists] \
                if has_danger else [False] * len(wrists)

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
        Run the hysteresis for the WRIST against every zone, exactly as
        _update_zone_states does for the feet, and resolve one hand zone.

        The hand zone is deliberately INDEPENDENT of the body zone. A worker
        standing in SAFE while reaching into DANGER must keep body=SAFE and
        hand=DANGER; the rule engine is what combines them.

        Uses the MOST-INSIDE wrist per zone: if either hand is in a danger
        zone that is a reach-in, so we take the maximum signed distance.

        State is keyed by zone_id (the individual polygon), not by zone type, so
        two danger zones around two machines get two independent sets of counters.

        Returns (hand_zone_type, hand_zone_id), both None when there is no wrist
        information at all.
        """
        ready = []
        for zone in zones.iter_ready():
            zone_id = zone.zone_id
            ready.append(zone_id)
            if wrists:
                distance = max(zone.signed_distance(w) for w in wrists)
            else:
                # No wrist observed on this pose frame. That IS an observation:
                # it counts towards clearing, just more slowly than entering.
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

        # Forget zones that were cleared or redrawn.
        for zone_id in list(track.hand_inside):
            if zone_id not in ready:
                track.hand_inside.pop(zone_id, None)
                track.hand_streaks.pop(zone_id, None)

        if wrists or any(track.hand_inside.values()):
            return zones.resolve_confirmed(track.hand_inside)
        return None, None   # no wrist information at all

    @staticmethod
    def _raw_hand_zone(wrists, zones):
        """
        Undebounced hand zone, used only when tracking is disabled.
        Returns (zone_type, zone_id).
        """
        if not wrists or zones is None:
            return None, None
        # Highest-priority zone reached by ANY wrist.
        for zone in zones.iter_ready():
            if any(zone.contains(w) for w in wrists):
                return zone.zone_type, zone.zone_id
        return zones.cfg.ZONE_OUTSIDE, None

    # -- internals -----------------------------------------------------------

    def _stateless_results(self, prepared, zones):
        """Tracking disabled: plain per-frame test, numbered in detection order."""
        results = []
        for index, item in enumerate(prepared, start=1):
            distance = self._zone_distance(item["feet"], zones)
            zone = zones.classify_zone(item["feet"]) if zones is not None else None
            zone_name = zone.zone_type if zone is not None else ZONE_OUTSIDE
            danger_type = zones.cfg.ZONE_DANGER if zones is not None else None
            results.append({
                "id": index,
                # Numbered in detection order: with tracking off these labels are
                # NOT stable between frames, which is why config.ENABLE_TRACKING
                # should stay True in production.
                "person_id": format_person_id(index),
                "identity": make_identity(index),
                "bbox": item["bbox"],
                "confidence": item["confidence"],
                "feet": item["feet"],
                "zone": zone_name,
                "zone_id": zone.zone_id if zone is not None else None,
                # Per-person dwell timing needs stable IDs, which is exactly
                # what tracking provides. Without it the timer cannot exist.
                "danger_duration": 0.0,
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
        """Signed distance to the DANGER zone edge (Phase 1's "distance" key)."""
        if zones is None:
            return float("-inf")
        return zones.signed_distance(zones.cfg.ZONE_DANGER, feet)

    @staticmethod
    def _danger_distance(feet, zones):
        """Unsigned px to the DANGER edge, 0.0 inside, None if not drawn."""
        if zones is None:
            return None
        return zones.distance_to_danger(feet)


    def _match(self, prepared):
        """
        Greedy nearest-neighbour association.

        For every detection we find the closest unclaimed track within
        max_distance. Detections with no match become new tracks. We attach the
        chosen track back onto the detection dict under the "track" key.
        """
        available = list(self._tracks)
        matched_ids = set()

        # Sort by distance-to-best-candidate so obvious pairs are locked in
        # first and an ambiguous detection cannot steal a confident match.
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

        candidates.sort(key=lambda c: c[0])

        for _, item, preferred in candidates:
            chosen = None
            if preferred is not None and preferred in available:
                chosen = preferred
            else:
                # The preferred track was taken by a closer detection; look again.
                best_distance = None
                for track in available:
                    distance = euclidean_distance(item["feet"], track.feet)
                    if distance <= self.max_distance and (
                        best_distance is None or distance < best_distance
                    ):
                        chosen = track
                        best_distance = distance

            if chosen is None:
                chosen = PersonTrack(
                    self._next_id, item["bbox"], item["confidence"], item["feet"]
                )
                self._next_id += 1
                self._tracks.append(chosen)
            else:
                available.remove(chosen)
                chosen.bbox = item["bbox"]
                chosen.confidence = item["confidence"]
                chosen.feet = item["feet"]

            chosen.missing_frames = 0
            matched_ids.add(chosen.track_id)
            item["track"] = chosen

        return matched_ids

    @staticmethod
    def _debounce(state, streaks, distance, margin, enter_frames, exit_frames):
        """
        The hysteresis state machine for ONE boundary. Phase 1 logic verbatim,
        just parameterised so the feet (x3 zones) and the wrist can share it.

        distance > +margin  -> clearly inside  -> count towards entering
        distance < -margin  -> clearly outside -> count towards leaving
        in between          -> ambiguous       -> hold the current state

        `streaks` is a mutable [inside_streak, outside_streak] pair, updated in
        place. Returns the new confirmed state.
        """
        if distance > margin:
            streaks[0] += 1
            streaks[1] = 0
        elif distance < -margin:
            streaks[1] += 1
            streaks[0] = 0
        else:
            # Sitting in the dead-band right on the boundary: change nothing.
            return state

        if not state and streaks[0] >= enter_frames:
            return True
        if state and streaks[1] >= exit_frames:
            return False
        return state

    def _update_zone_states(self, track, zones, now):
        """
        Run the hysteresis independently against every finished POLYGON, then
        resolve the results into one body zone using the manager's priority.

        Each polygon gets its OWN streak counters, keyed by zone_id, so a worker
        crossing from WARNING into DANGER is confirmed as entering danger on
        exactly the same schedule as Phase 1 confirmed entering the restricted
        area -- and two danger zones around two machines never share counters.
        """
        if zones is None or not zones.any_ready:
            track.zone_inside.clear()
            track.zone_streaks.clear()
            track.zone = ZONE_OUTSIDE
            track.zone_id = None
            track.is_inside = False
            track.inside_streak = 0
            track.outside_streak = 0
            self._update_danger_timer(track, now, False)
            return

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

        # Forget state for zones that were cleared or redrawn -- a confirmed
        # membership from a deleted polygon must never leak into the new one.
        for zone_id in list(track.zone_inside):
            if zone_id not in ready:
                track.zone_inside.pop(zone_id, None)
                track.zone_streaks.pop(zone_id, None)

        track.zone, track.zone_id = zones.resolve_confirmed(track.zone_inside)

        # Keep the Phase 1 fields alive, now meaning "any DANGER zone".
        danger_type = zones.cfg.ZONE_DANGER
        danger_ids = zones.confirmed_ids_of_type(track.zone_inside, danger_type)
        track.is_inside = bool(danger_ids)
        danger_streaks = track.zone_streaks.get(
            danger_ids[0] if danger_ids else None, [0, 0])
        track.inside_streak, track.outside_streak = danger_streaks

        self._update_danger_timer(track, now, track.zone == danger_type)

    @staticmethod
    def _update_danger_timer(track, now, in_danger):
        """
        Per-person danger dwell clock. Driven by the DEBOUNCED zone, so a single
        noisy frame on the boundary cannot restart or reset the timer.

        Entering starts the clock, staying accumulates, leaving resets it to
        zero. There is intentionally no global timer anywhere in the system.
        """
        if in_danger:
            if track.danger_since is None:
                track.danger_since = now
            track.danger_duration = max(0.0, now - track.danger_since)
        else:
            track.danger_since = None
            track.danger_duration = 0.0
