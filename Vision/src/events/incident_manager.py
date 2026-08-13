"""
src/incident_manager.py
-----------------------
WARNING-ZONE INCIDENTS: a complete safety occurrence, not a state change.

    EVENT     "P1 entered WARNING_1"        -- a transition, one line in the log
    INCIDENT  "P1 was in WARNING_1 from 16:03:25 to 16:03:33 (8.0 s), photo here"
              -- a real-world occurrence, opened by one event and closed by another

events.py already detects the transitions. This file remembers what happens
BETWEEN them: when the visit started, which id ties the two events together, how
long it lasted, and what the camera saw at the moment of entry.

WHY THIS IS A SEPARATE FILE
Two reasons, both about keeping events.py honest:

  1. events.py holds no state beyond "what did I last report", and calculates
     nothing. An incident has a lifetime, an id and a duration.
  2. events.py writes no files -- event_logger.py does. Saving a JPEG is file I/O,
     and it belongs with the incident it is evidence for.

Everything here is keyed by the tracker's own track id and zone.py's own zone tag.
No second identity system, no second zone-id system.

ONE INCIDENT PER PERSON PER ZONE
P1 in WARNING_1 and P2 in WARNING_1 are two incidents. P1 in WARNING_1 and P1 in
WARNING_2 are also two incidents. Leaving and coming back is a NEW incident with a
new id and a new photo -- an id is never reused.
"""

import os
from datetime import datetime

import cv2

# The one event type a warning-zone visit produces. Defined here because the
# incident builds its own record; events.py re-exports it as part of the vocabulary.
WARNING_ZONE_INCIDENT = "WARNING_ZONE_INCIDENT"

# Image layout:  <incident_dir>/<YYYY-MM-DD>/<person>_<zone>_<HHMMSS>.jpg
# No colons anywhere: Windows filenames cannot contain them.
_DATE_FOLDER = "%Y-%m-%d"
_FILE_STAMP = "%H%M%S"

# Characters we refuse to put in a filename, whatever a zone gets called.
_UNSAFE = '<>:"/\\|?*'


class WarningIncident:
    """
    One visit to one warning zone by one person.

    Open while the person is inside; closed on the confirmed exit (or when the
    tracker gives up on them). Closing it is what writes the single
    WARNING_ZONE_INCIDENT line -- nothing is logged while the visit is in progress.
    """

    __slots__ = (
    "person_id",
    "track_id",
    "zone_id",
    "zone_type",
    "entry_time",
    "exit_time",
    "incident_image",
    "alarm_triggered",
    "alarm_triggered_at",
    "entry_frame",)

    def __init__(self, person_id, track_id, zone_id, zone_type, entry_time,frame=None):
        self.person_id = person_id
        self.track_id = track_id
        self.zone_id = zone_id
        self.zone_type = zone_type
        self.entry_time = entry_time          # datetime, when the entry was CONFIRMED
        self.exit_time = None                 # datetime, set once on close
        self.incident_image = None            # path stored in the event, or None
        # Set True only when the buzzer ACTUALLY sounded because of this visit --
        # not merely because the dwell threshold was passed.
        self.alarm_triggered = False

        self.alarm_triggered_at = None
        self.entry_frame = frame if frame is not None else None

    @property
    def is_open(self):
        return self.exit_time is None

    def elapsed(self, now=None):
        """
        Seconds inside the zone so far (or in total, once closed).

        Measured from the two timestamps that appear in the log, so anyone reading
        the JSONL can verify the duration for themselves.
        """
        end = self.exit_time or now or datetime.now()
        return max(0.0, (end - self.entry_time).total_seconds())

    def as_event(self, source_type=None):
        """
        The complete incident, as the ONE JSON object that gets logged.

        Exactly these nine fields, in this order -- no session id, no risk, no
        reasons. A warning-zone visit is one line, and this is that line.
        """
        return {
            "event_type": WARNING_ZONE_INCIDENT,
            "person_id": self.person_id,
            "zone_id": self.zone_id,
            "entry_time": _iso(self.entry_time),
            "exit_time": _iso(self.exit_time),
            "duration_sec": round(self.elapsed(), 3),
            "alarm_triggered": bool(self.alarm_triggered),
            "source_type": source_type,
            "incident_image": self.incident_image,
        }


class IncidentManager:
    """
    Opens, tracks and closes warning-zone incidents, and saves the entry photo.

    Held by EventLog, which calls open_incident() / close_incident() at the exact
    transitions it already detects. main.py only ever asks it one question:
    "is anyone past the warning dwell threshold?"
    """

    def __init__(self, incident_dir="logs/incidents", save_images=True,
                 dwell_threshold=5.0, relative_to="logs"):
        self.incident_dir = incident_dir
        self.save_images = bool(save_images)
        self.dwell_threshold = float(dwell_threshold)
        # Image paths are stored relative to this folder, so the event carries
        # "incidents/2026-08-11/P1_WARNING_1_153210.jpg" rather than a full path.
        self.relative_to = relative_to

        # {(track_id, zone_id): WarningIncident} -- only OPEN incidents.
        self._open = {}
        self._warned = False      # so an unwritable folder complains once, not always

    # -- lifecycle -----------------------------------------------------------

    def open_incident(self, person, zone_id, zone_type, frame=None, now=None):
        """
        Start an incident for this person and zone, saving the entry frame once.

        Returns the WarningIncident. If one is somehow already open for this pair it
        is returned unchanged, so a repeated call cannot create a second incident or
        a second photo.
        """
        track_id = person.get("id")
        key = (track_id, zone_id)
        existing = self._open.get(key)
        if existing is not None:
            return existing

        entry_time = now or datetime.now()
        person_id = person.get("person_id") or "P{}".format(track_id)

        incident = WarningIncident(
            person_id=person_id,
            track_id=track_id,
            zone_id=zone_id,
            zone_type=zone_type,
            entry_time=entry_time,
            frame=frame,
        )

        # THE ONLY PLACE A FRAME IS EVER SAVED. One image per incident, taken at
        # the moment the entry became confirmed -- never per frame, never on a timer.
        if self.save_images and frame is not None:
            incident.incident_image = self._save_evidence(incident, frame)
            incident.entry_frame = None

        self._open[key] = incident
        return incident

    def close_incident(self, track_id, zone_id, now=None):
        """
        Close the incident for this person and zone and return it, or None if there
        was no open incident (which is normal -- most zones are not warning zones).
        """
        incident = self._open.pop((track_id, zone_id), None)
        if incident is None:
            return None
        incident.exit_time = now or datetime.now()
        return incident

    def close_all_for(self, track_id, now=None):
        """
        Close every open incident belonging to one person.

        Used when the tracker finally gives up on them: a person cannot still be
        standing in a warning zone once their track is gone.
        """
        keys = [key for key in self._open if key[0] == track_id]
        return [self.close_incident(key[0], key[1], now=now) for key in keys]

    def get(self, track_id, zone_id):
        """The open incident for this person and zone, or None."""
        return self._open.get((track_id, zone_id))

    @property
    def open_incidents(self):
        """Every incident currently open, in the order they were opened."""
        return list(self._open.values())

    def dwell_violations(self, now=None):
        """
        Return WARNING incidents that have reached the dwell threshold.

        The entry frame is saved exactly once when the incident first crosses
        the threshold.

        Example:
            WARNING threshold = 10 seconds

            0s  -> incident opened
            5s  -> nothing
            9s  -> nothing
            10s -> save ONE photo
            11s -> same incident, no second photo
            20s -> same incident, no second photo
        """

        now = now or datetime.now()

        violations = []

        for incident in self._open.values():

            elapsed = incident.elapsed(now)

            if elapsed < self.dwell_threshold:
                continue

            # ----------------------------------------------------------
            # Threshold crossed for the first time.
            # Save exactly ONE photo for this incident.
            # ----------------------------------------------------------

            if incident.incident_image is None:

                if (
                    incident.entry_frame is not None
                    and self.save_images
                ):
                    incident.incident_image = self._save_evidence(
                        incident,
                        incident.entry_frame,
                    )

                # Release the frame after the first save attempt.
                # We don't need to keep a full camera frame in memory.
                incident.entry_frame = None

            violations.append(incident)

        return violations

    def take_open(self):
        """
        Hand over every open incident and forget them, WITHOUT setting an exit time.

        Called when the zones are redrawn: those polygons no longer exist, so the
        caller closes and logs the visits that were in progress rather than letting
        them vanish from the audit trail.
        """
        incidents = list(self._open.values())
        self._open.clear()
        return incidents

    def reset(self):
        """Drop every open incident. See take_open() for the logging path."""
        self._open.clear()

    # -- internals -----------------------------------------------------------

    def _save_evidence(self, incident, frame):
        """
        Write the entry frame and return the path to store in the event:

            incidents/2026-08-11/P1_WARNING_1_153210.jpg

        Relative (to config.LOG_ROOT) and posix-style on purpose: the backend will
        map these onto uploaded evidence, and an absolute C:\\... path would be
        meaningless there. One folder per day keeps a long-running site browsable.

        The frame handed in is the camera frame BEFORE any overlay is drawn, so the
        image is evidence rather than a screenshot of our own HUD.

        Never raises: failing to save a photo must not stop a safety monitor.
        """
        folder = os.path.join(self.incident_dir,
                              incident.entry_time.strftime(_DATE_FOLDER))
        filename = "{}_{}_{}.jpg".format(
            _safe(incident.person_id),
            _safe(incident.zone_id),
            incident.entry_time.strftime(_FILE_STAMP))

        try:
            os.makedirs(folder, exist_ok=True)
            path = self._unique_path(folder, filename)
            if not cv2.imwrite(path, frame):
                raise OSError("cv2.imwrite refused to write the file")
        except (OSError, cv2.error) as exc:
            if not self._warned:
                self._warned = True
                print("[ZONE X] Warning: could not save the incident image "
                      "in '{}': {}".format(folder, exc))
            return None

        return _relative_to(path, self.relative_to)

    @staticmethod
    def _unique_path(folder, filename):
        """
        Avoid overwriting an image if a name is already taken.

        Two visits to the same zone in the same second are possible -- the exit
        hysteresis takes under a second -- and the filename is only accurate to the
        second, so the second one becomes ..._153210_2.jpg.
        """
        path = os.path.join(folder, filename)
        if not os.path.exists(path):
            return path
        stem, extension = os.path.splitext(filename)
        attempt = 2
        while True:
            candidate = os.path.join(folder,
                                     "{}_{}{}".format(stem, attempt, extension))
            if not os.path.exists(candidate):
                return candidate
            attempt += 1


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _relative_to(path, base):
    """
    Turn a saved image path into what the event log should carry:

        incidents/2026-08-11/P1_WARNING_1_153210.jpg

    Relative to `base` (the log root) and with forward slashes, because the backend
    will map these onto uploaded evidence and an absolute C:\\... path means nothing
    there. A path that cannot be expressed relative to the base is returned as-is
    (with forward slashes) rather than as a misleading '..' chain.
    """
    absolute = os.path.abspath(path)
    try:
        relative = os.path.relpath(absolute, os.path.abspath(base))
    except ValueError:
        # Different drive on Windows.
        return absolute.replace(os.sep, "/")
    if relative.startswith(os.pardir):
        return absolute.replace(os.sep, "/")
    return relative.replace(os.sep, "/")


def _iso(moment):
    """A datetime as the log's timestamp format, or None."""
    if moment is None:
        return None
    return moment.isoformat(timespec="milliseconds")


def _safe(text):
    """Make a fragment safe for a Windows filename."""
    cleaned = "".join("_" if character in _UNSAFE else character
                      for character in str(text))
    return cleaned.strip().rstrip(".") or "unknown"
