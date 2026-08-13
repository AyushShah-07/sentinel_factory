import json
import os
import time
from datetime import datetime

import cv2


from src.integration.connection import BackendClient

# ============================================================
# EVENT NAMES
# ============================================================

PERSON_DETECTED = "PERSON_DETECTED"

DANGER_INCIDENT_NAME = "Person enters the danger zone"

HAND_DANGER_INCIDENT_NAME = (
    "Person reaches into the danger zone"
)

WARNING_INCIDENT_NAME = (
    "Person stays in the warning zone for more than 10s"
)


# ============================================================
# TIME
# ============================================================

def _now():
    return datetime.now().isoformat(
        timespec="milliseconds"
    )


# ============================================================
# PERSON ID
# ============================================================

def _person_id(person):
    """
    Convert tracker information into:

        tracker 1 -> P1
        tracker 2 -> P2
        tracker 3 -> P3
    """

    if person is None:
        return None

    # Already assigned person ID
    person_id = person.get("person_id")

    if person_id:
        return str(person_id)

    # Identity information
    identity = person.get("identity") or {}

    identity_id = identity.get("person_id")

    if identity_id:
        return str(identity_id)

    # Tracker ID
    raw_id = person.get("id")

    if raw_id is None:
        return None

    return f"P{raw_id}"


# ============================================================
# ZONE NORMALIZATION
# ============================================================

def _zone_value(person):
    """
    Get raw zone value.

    Examples:

        DANGER
        DANGER_1
        WARNING
        WARNING_1
        SAFE
        OUTSIDE
    """

    if not person:
        return None

    zone = person.get("zone")

    if zone is None:
        return None

    return str(zone).strip().upper()


def _zone_type(person):
    """
    Normalize zone names.

        DANGER       -> DANGER
        DANGER_1     -> DANGER
        DANGER_2     -> DANGER

        WARNING      -> WARNING
        WARNING_1    -> WARNING
        WARNING_2    -> WARNING

        SAFE         -> SAFE
        SAFE_1       -> SAFE

        OUTSIDE      -> OUTSIDE
    """

    zone = _zone_value(person)

    if not zone:
        return None

    # Exact zones
    if zone in {
        "DANGER",
        "WARNING",
        "SAFE",
        "OUTSIDE",
    }:
        return zone

    # Zone IDs
    if zone.startswith("DANGER_"):
        return "DANGER"

    if zone.startswith("WARNING_"):
        return "WARNING"

    if zone.startswith("SAFE_"):
        return "SAFE"

    if zone.startswith("OUTSIDE_"):
        return "OUTSIDE"

    return zone


# ============================================================
# HAND / WRIST DANGER
# ============================================================

def _hand_is_in_danger(person):
    """
    The pose/zone pipeline already provides:

        hand_in_danger = True / False

    This allows:

        body  -> OUTSIDE
        hand  -> DANGER

    to still create a danger incident.
    """

    if not person:
        return False

    return bool(
        person.get(
            "hand_in_danger",
            False,
        )
    )


def _danger_source(person):
    """
    Determine why the danger incident occurred.

    Returns:

        BODY
        HAND
        BODY_AND_HAND
        None
    """

    body_danger = (
        _zone_type(person) == "DANGER"
    )

    hand_danger = _hand_is_in_danger(
        person
    )

    if body_danger and hand_danger:
        return "BODY_AND_HAND"

    if body_danger:
        return "BODY"

    if hand_danger:
        return "HAND"

    return None


# ============================================================
# EVENT LOG
# ============================================================

class EventLog:

    def __init__(
        self,
        max_events=200,
        danger_time_threshold=10.0,
        critical_threshold=90,
        on_event=None,
        source_type=None,
        schema_version="1.0",
        log_risk_changes=False,
        log_root="logs",
        warning_dwell_threshold=10.0,
        incident_dir="logs/incidents",
        save_incident_images=True,
    ):

        self.max_events = max_events

        self.danger_time_threshold = (
            danger_time_threshold
        )

        self.critical_threshold = (
            critical_threshold
        )

        self.warning_dwell_threshold = float(
            warning_dwell_threshold
        )

        self.on_event = on_event
        self.source_type = source_type
        self.schema_version = schema_version
        self.log_risk_changes = log_risk_changes

        self.save_incident_images = bool(
            save_incident_images
        )

        # ----------------------------------------------------
        # LOG ROOT
        # ----------------------------------------------------

        self.log_root = log_root

        os.makedirs(
            self.log_root,
            exist_ok=True,
        )

        # ----------------------------------------------------
        # IMAGE DIRECTORIES
        # ----------------------------------------------------

        self.danger_image_dir = os.path.join(
            self.log_root,
            "incidents",
            "danger",
        )

        self.warning_image_dir = os.path.join(
            self.log_root,
            "incidents",
            "warning",
        )

        os.makedirs(
            self.danger_image_dir,
            exist_ok=True,
        )

        os.makedirs(
            self.warning_image_dir,
            exist_ok=True,
        )

        # ----------------------------------------------------
        # JSON FILES
        # ----------------------------------------------------

        self.persons_path = os.path.join(
            self.log_root,
            "persons.json",
        )

        self.danger_path = os.path.join(
            self.log_root,
            "danger_incidents.json",
        )

        self.warning_path = os.path.join(
            self.log_root,
            "warning_incidents.json",
        )

        self._ensure_json_file(
            self.persons_path
        )

        self._ensure_json_file(
            self.danger_path
        )

        self._ensure_json_file(
            self.warning_path
        )

        # ----------------------------------------------------
        # PREVIOUS PERSON STATE
        # ----------------------------------------------------

        self._previous = {}

        # ----------------------------------------------------
        # PERSONS ALREADY DETECTED
        # ----------------------------------------------------

        self._detected_persons = set()

        # ----------------------------------------------------
        # WARNING STATE
        #
        # {
        #     "P1": {
        #         "zone_id": "WARNING_1",
        #         "entered_at": 123456.7,
        #         "triggered": False
        #     }
        # }
        # ----------------------------------------------------

        self._warning_state = {}

        # ----------------------------------------------------
        # ACTIVE DANGER STATE
        #
        # {
        #     "P1": "HAND",
        #     "P2": "BODY"
        # }
        # ----------------------------------------------------

        self._danger_active = {}

        # ----------------------------------------------------
        # COMPATIBILITY
        # ----------------------------------------------------

        self.events = []

        self.total_emitted = 0

        self._alarm_active = False

        self.session_id = None

        # ----------------------------------------------------
        # SPRING BOOT BACKEND
        # ----------------------------------------------------

        self.backend_client = BackendClient(
            base_url="http://localhost:8080",
        )

        # Must match the cameraId used by your backend/Yolo API.
        self.camera_id = "CAM-01"

    # ========================================================
    # JSON
    # ========================================================

    @staticmethod
    def _ensure_json_file(path):

        directory = os.path.dirname(path)

        if directory:
            os.makedirs(
                directory,
                exist_ok=True,
            )

        if not os.path.exists(path):

            with open(
                path,
                "w",
                encoding="utf-8",
            ) as f:

                json.dump(
                    [],
                    f,
                    indent=2,
                )

    @staticmethod
    def _append_json(path, record):

        try:

            with open(
                path,
                "r",
                encoding="utf-8",
            ) as f:

                data = json.load(f)

                if not isinstance(
                    data,
                    list,
                ):
                    data = []

        except (
            FileNotFoundError,
            json.JSONDecodeError,
        ):

            data = []

        data.append(record)

        temp_path = path + ".tmp"

        with open(
            temp_path,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                data,
                f,
                indent=2,
            )

        os.replace(
            temp_path,
            path,
        )

    # ========================================================
    # SESSION COMPATIBILITY
    # ========================================================

    def open_session(
        self,
        log_root=None,
        metadata=None,
    ):
        """
        Compatibility method.

        No session.json is created.
        """

        if log_root:

            self.log_root = log_root

        self.session_id = (
            datetime.now().strftime(
                "%Y-%m-%d_%H-%M-%S"
            )
        )

        self._previous.clear()

        self._warning_state.clear()

        self._danger_active.clear()

        return None

    def close_session(self):

        return None

    @property
    def events_path(self):

        return None

    @property
    def session_path(self):

        return None

    @property
    def is_open(self):

        return True

    # ========================================================
    # FIRST PERSON DETECTION
    # ========================================================

    def _person_detected(self, person):

        person_id = _person_id(
            person
        )

        if person_id is None:
            return None

        # Already detected
        if person_id in self._detected_persons:
            return None

        timestamp = _now()

        record = {
            "timestamp": timestamp,
            "event": PERSON_DETECTED,
            "person_id": person_id,
        }

        self._append_json(
            self.persons_path,
            record,
        )

        self._detected_persons.add(
            person_id
        )

        print(
            f"[Event] {timestamp} "
            f"PERSON_DETECTED {person_id}"
        )

        return record

    # ========================================================
    # SAVE INCIDENT IMAGE
    # ========================================================

    def _save_frame(
        self,
        frame,
        person_id,
        incident_type,
    ):

        if not self.save_incident_images:

            return None

        # ----------------------------------------------------
        # FRAME CHECK
        # ----------------------------------------------------

        if frame is None:

            print(
                "[Event] ERROR: frame is None."
            )

            return None

        if not hasattr(
            frame,
            "shape",
        ):

            print(
                "[Event] ERROR: Invalid frame."
            )

            return None

        if frame.size == 0:

            print(
                "[Event] ERROR: Empty frame."
            )

            return None

        # ----------------------------------------------------
        # TIMESTAMP
        # ----------------------------------------------------

        timestamp = datetime.now()

        timestamp_text = timestamp.strftime(
            "%Y-%m-%d_%H-%M-%S-%f"
        )

        # ----------------------------------------------------
        # DIRECTORY
        # ----------------------------------------------------

        if incident_type == "danger":

            directory = (
                self.danger_image_dir
            )

        elif incident_type == "warning":

            directory = (
                self.warning_image_dir
            )

        else:

            print(
                "[Event] ERROR: Unknown incident "
                f"type: {incident_type}"
            )

            return None

        os.makedirs(
            directory,
            exist_ok=True,
        )

        # ----------------------------------------------------
        # FILE
        # ----------------------------------------------------

        filename = (
            f"{person_id}_{timestamp_text}.jpg"
        )

        path = os.path.abspath(
            os.path.join(
                directory,
                filename,
            )
        )

        # ----------------------------------------------------
        # WRITE
        # ----------------------------------------------------

        success = cv2.imwrite(
            path,
            frame,
        )

        if not success:

            print(
                "[Event] ERROR: cv2.imwrite failed:"
            )

            print(
                f"         {path}"
            )

            return None

        # ----------------------------------------------------
        # VERIFY
        # ----------------------------------------------------

        if not os.path.exists(path):

            print(
                "[Event] ERROR: Image was not created:"
            )

            print(
                f"         {path}"
            )

            return None

        print(
            f"[Event] IMAGE SAVED: {path}"
        )

        return path

    # ========================================================
    # ASSIGNED PERSON
    # ========================================================

    def _is_assigned_person(
        self,
        person_id,
    ):
        """
        Optional filtering.

        If ASSIGNED_PERSON_IDS does not exist:
            everyone is allowed.

        If it exists and contains values:
            only those people generate danger incidents.
        """

        try:

            import config

            assigned = getattr(
                config,
                "ASSIGNED_PERSON_IDS",
                None,
            )

        except Exception:

            assigned = None

        # ----------------------------------------------------
        # No configuration:
        # everyone allowed.
        # ----------------------------------------------------

        if assigned is None:
            return True

        assigned = {
            str(value).strip()
            for value in assigned
        }

        # Empty set means no filtering.
        if not assigned:
            return True

        return (
            str(person_id)
            in assigned
        )

    # ========================================================
    # DANGER INCIDENT
    # ========================================================

    def _danger_entry(
        self,
        person,
        frame,
        source=None,
    ):

        person_id = _person_id(
            person
        )

        if person_id is None:
            return None

        # ----------------------------------------------------
        # ASSIGNED PERSON FILTER
        # ----------------------------------------------------

        if not self._is_assigned_person(
            person_id
        ):

            print(
                f"[DANGER] {person_id} is not assigned. "
                "Incident ignored."
            )

            return None

        # ----------------------------------------------------
        # DUPLICATE PROTECTION
        # ----------------------------------------------------

        if person_id in self._danger_active:

            return None

        # ----------------------------------------------------
        # DETERMINE SOURCE
        # ----------------------------------------------------

        if source is None:

            source = _danger_source(
                person
            )

        if source is None:
            return None

        # ----------------------------------------------------
        # ZONE DATA
        # ----------------------------------------------------

        body_zone = _zone_type(
            person
        )

        body_zone_id = person.get(
            "zone_id"
        )

        hand_zone = person.get(
            "hand_zone"
        )

        hand_zone_id = person.get(
            "hand_zone_id"
        )

        # ----------------------------------------------------
        # INCIDENT NAME
        # ----------------------------------------------------

        if source == "HAND":

            incident_name = (
                HAND_DANGER_INCIDENT_NAME
            )

        elif source == "BODY_AND_HAND":

            incident_name = (
                "Person enters/reaches into "
                "the danger zone"
            )

        else:

            incident_name = (
                DANGER_INCIDENT_NAME
            )

        print(
            f"[DANGER] {person_id} "
            f"INCIDENT SOURCE={source} "
            f"BODY={body_zone}/{body_zone_id} "
            f"HAND={hand_zone}/{hand_zone_id}"
        )

        # ----------------------------------------------------
        # CAPTURE IMMEDIATELY
        # ----------------------------------------------------

        timestamp = _now()

        picture = self._save_frame(
            frame,
            person_id,
            "danger",
        )

        # ----------------------------------------------------
        # IMAGE FAILURE
        # ----------------------------------------------------

        if (
            self.save_incident_images
            and picture is None
        ):

            print(
                f"[DANGER] {person_id}: "
                "image capture FAILED"
            )

            return None

        # ----------------------------------------------------
        # JSON RECORD
        # ----------------------------------------------------

        record = {
            "timestamp": timestamp,
            "person_id": person_id,
            "incident_name": incident_name,
            "incident_type": "DANGER",
            "source": source,

            "body_zone": body_zone,
            "body_zone_id": body_zone_id,

            "hand_zone": hand_zone,
            "hand_zone_id": hand_zone_id,

            "picture": picture,
        }

        self._append_json(
            self.danger_path,
            record,
        )

        # ----------------------------------------------------
        # MEMORY
        # ----------------------------------------------------

        self._danger_active[
            person_id
        ] = source

        self.events.append(
            record
        )

        self.total_emitted += 1

        if len(self.events) > self.max_events:

            self.events = self.events[
                -self.max_events:
            ]

        # ----------------------------------------------------
        # CALLBACK
        # ----------------------------------------------------

        if self.on_event:

            try:

                self.on_event(
                    record
                )

            except Exception as exc:

                print(
                    "[Event] on_event error:",
                    exc,
                )

        print(
            f"[INCIDENT SAVED] "
            f"{incident_name} | "
            f"{person_id} | "
            f"{source}"
        )

        return record

    # ========================================================
    # WARNING INCIDENT
    # ========================================================

    def _warning_update(
        self,
        person,
        frame,
    ):
        """
        WARNING DWELL LOGIC

        A warning incident occurs only when:

            person.zone == WARNING

        continuously for:

            warning_dwell_threshold seconds

        Default:

            10 seconds

        Important:

        - Hand zone is NOT used here.
        - Danger logic is NOT used here.
        - WARNING_1 and WARNING_2 are treated as
          different warning zones.
        - Leaving WARNING resets the timer.
        - Changing WARNING zone resets the timer.
        - One incident is generated per continuous
          warning-zone stay.
        """

        person_id = _person_id(
            person
        )

        if person_id is None:
            return None

        # ----------------------------------------------------
        # CURRENT BODY ZONE
        # ----------------------------------------------------

        zone = _zone_type(
            person
        )

        zone_id = person.get(
            "zone_id"
        )

        if zone_id is not None:

            zone_id = str(
                zone_id
            ).strip()

        # ====================================================
        # PERSON NOT IN WARNING
        # ====================================================

        if zone != "WARNING":

            if person_id in self._warning_state:

                state = self._warning_state.get(
                    person_id
                )

                if state:

                    elapsed = (
                        time.monotonic()
                        - state["entered_at"]
                    )

                    print(
                        f"[WARNING] {person_id} "
                        f"LEFT WARNING "
                        f"zone={state.get('zone_id')} "
                        f"after {elapsed:.2f}s"
                    )

            # ------------------------------------------------
            # RESET WARNING TIMER
            # ------------------------------------------------

            self._warning_state.pop(
                person_id,
                None,
            )

            return None

        # ====================================================
        # NEW WARNING ENTRY
        # ====================================================

        state = self._warning_state.get(
            person_id
        )

        if state is None:

            self._warning_state[
                person_id
            ] = {

                "zone_id": zone_id,

                "entered_at":
                    time.monotonic(),

                "triggered": False,
            }

            print(
                f"[WARNING] {person_id} "
                f"ENTERED WARNING "
                f"zone={zone_id}"
            )

            return None

        # ====================================================
        # WARNING ZONE CHANGED
        # ====================================================

        if (
            state.get("zone_id")
            != zone_id
        ):

            print(
                f"[WARNING] {person_id} "
                f"WARNING ZONE CHANGED "
                f"{state.get('zone_id')} "
                f"-> {zone_id}"
            )

            # Restart timer.
            self._warning_state[
                person_id
            ] = {

                "zone_id": zone_id,

                "entered_at":
                    time.monotonic(),

                "triggered": False,
            }

            return None

        # ====================================================
        # ALREADY TRIGGERED
        # ====================================================

        if state.get(
            "triggered",
            False,
        ):

            return None

        # ====================================================
        # DWELL TIME
        # ====================================================

        elapsed = (
            time.monotonic()
            - state["entered_at"]
        )

        # ====================================================
        # NOT YET 10 SECONDS
        # ====================================================

        if (
            elapsed
            < self.warning_dwell_threshold
        ):

            return None

        # ====================================================
        # WARNING INCIDENT
        # ====================================================

        timestamp = _now()

        print(
            f"[WARNING] {person_id} "
            f"stayed {elapsed:.2f}s "
            f"in WARNING "
            f"zone={zone_id}"
        )

        # ----------------------------------------------------
        # CAPTURE FRAME
        # ----------------------------------------------------

        picture = self._save_frame(
            frame,
            person_id,
            "warning",
        )

        # ----------------------------------------------------
        # IMAGE FAILURE
        # ----------------------------------------------------

        if (
            self.save_incident_images
            and picture is None
        ):

            print(
                f"[WARNING] {person_id}: "
                "image capture FAILED"
            )

            return None

        # ====================================================
        # JSON RECORD
        # ====================================================

        record = {

            "timestamp": timestamp,

            "person_id": person_id,

            "incident_name":
                WARNING_INCIDENT_NAME,

            "incident_type":
                "WARNING",

            "zone":
                zone,

            "zone_id":
                zone_id,

            "dwell_seconds":
                round(
                    elapsed,
                    2,
                ),

            "picture":
                picture,
        }

        self._append_json(
            self.warning_path,
            record,
        )

        # ====================================================
        # MARK TRIGGERED
        # ====================================================

        state["triggered"] = True

        # ====================================================
        # MEMORY
        # ====================================================

        self.events.append(
            record
        )

        self.total_emitted += 1

        if len(self.events) > self.max_events:

            self.events = self.events[
                -self.max_events:
            ]

        # ====================================================
        # CALLBACK
        # ====================================================

        if self.on_event:

            try:

                self.on_event(
                    record
                )

            except Exception as exc:

                print(
                    "[Event] on_event error:",
                    exc,
                )

        print(
            f"[INCIDENT SAVED] "
            f"{WARNING_INCIDENT_NAME} | "
            f"{person_id} | "
            f"zone={zone_id} | "
            f"dwell={elapsed:.2f}s"
        )

        return record

    # ========================================================
    # MAIN UPDATE
    # ========================================================

    def update(
        self,
        people,
        risks=None,
        machine_on=False,
        cfg=None,
        live_ids=None,
        frame=None,
    ):

        emitted = []

        # ----------------------------------------------------
        # PEOPLE SAFETY
        # ----------------------------------------------------

        if people is None:

            people = []

        # ----------------------------------------------------
        # CURRENT PEOPLE
        # ----------------------------------------------------

        current_ids = set()

        # ====================================================
        # PROCESS EACH PERSON
        # ====================================================

        for person in people:

            if not person:
                continue

            person_id = _person_id(
                person
            )

            if person_id is None:
                continue

            # ------------------------------------------------
            # DEBUG
            # ------------------------------------------------

            print(
                "[DEBUG PERSON]",
                person,
            )

            print(
                "[DEBUG ZONE]",
                person_id,
                "zone=",
                person.get("zone"),
                "zone_id=",
                person.get("zone_id"),
                "hand_zone=",
                person.get("hand_zone"),
                "hand_zone_id=",
                person.get("hand_zone_id"),
                "hand_in_danger=",
                person.get("hand_in_danger"),
            )

            current_ids.add(
                person_id
            )

            # =================================================
            # PERSON DETECTED
            # =================================================

            event = self._person_detected(
                person
            )

            if event is not None:

                emitted.append(
                    event
                )

                # Send PERSON_DETECTED to Spring Boot
                self.backend_client.send_detection(
                    event,
                    self.camera_id,
                )

            # =================================================
            # CURRENT BODY ZONE
            # =================================================

            current_zone = _zone_type(
                person
            )

            current_zone_id = person.get(
                "zone_id"
            )

            # =================================================
            # CURRENT HAND DANGER
            # =================================================

            hand_danger = (
                _hand_is_in_danger(
                    person
                )
            )

            # =================================================
            # PREVIOUS STATE
            # =================================================

            previous = self._previous.get(
                person_id
            )

            previous_zone = None

            previous_zone_id = None

            previous_hand_danger = False

            if previous is not None:

                previous_zone = previous.get(
                    "zone"
                )

                previous_zone_id = previous.get(
                    "zone_id"
                )

                previous_hand_danger = bool(
                    previous.get(
                        "hand_in_danger",
                        False,
                    )
                )

            # =================================================
            # DANGER
            #
            # BODY OR HAND
            # =================================================

            danger_now = (
                current_zone == "DANGER"
                or hand_danger
            )

            danger_before = (
                previous_zone == "DANGER"
                or previous_hand_danger
            )

            # -------------------------------------------------
            # JUST ENTERED DANGER
            # -------------------------------------------------

            if danger_now:

                if not danger_before:

                    source = _danger_source(
                        person
                    )

                    print(
                        f"[DANGER] {person_id}: "
                        f"ENTERED DANGER "
                        f"source={source}"
                    )

                    event = (
                        self._danger_entry(
                            person,
                            frame,
                            source,
                        )
                    )

                    if event is not None:

                        emitted.append(
                            event
                        )

                        # Send DANGER incident to Spring Boot
                        self.backend_client.send_incident(
                            event,
                            self.camera_id,
                        )

                else:

                    # -----------------------------------------
                    # STILL IN DANGER
                    #
                    # No duplicate incident.
                    # -----------------------------------------

                    if (
                        person_id
                        not in self._danger_active
                    ):

                        source = (
                            _danger_source(
                                person
                            )
                        )

                        self._danger_active[
                            person_id
                        ] = (
                            source
                            or "UNKNOWN"
                        )

            else:

                # ---------------------------------------------
                # PERSON LEFT DANGER
                # ---------------------------------------------

                if danger_before:

                    print(
                        f"[DANGER] {person_id} "
                        "LEFT DANGER"
                    )

                self._danger_active.pop(
                    person_id,
                    None,
                )

            # =================================================
            # WARNING
            #
            # BODY ZONE ONLY
            # =================================================

            event = self._warning_update(
                person,
                frame,
            )

            if event is not None:

                emitted.append(
                    event
                )

                # Send WARNING incident to Spring Boot
                self.backend_client.send_incident(
                    event,
                    self.camera_id,
                )

            # =================================================
            # SAVE CURRENT STATE
            # =================================================

            self._previous[
                person_id
            ] = {

                "person":
                    person,

                "zone":
                    current_zone,

                "zone_id":
                    current_zone_id,

                "hand_in_danger":
                    hand_danger,

                "hand_zone":
                    person.get(
                        "hand_zone"
                    ),

                "hand_zone_id":
                    person.get(
                        "hand_zone_id"
                    ),
            }

        # ====================================================
        # PEOPLE NO LONGER VISIBLE
        # ====================================================

        if live_ids is not None:

            live_person_ids = {
                f"P{track_id}"
                for track_id in live_ids
            }

            lost_ids = (
                set(self._previous)
                - live_person_ids
            )

        else:

            lost_ids = (
                set(self._previous)
                - current_ids
            )

        # ====================================================
        # CLEAN LOST PEOPLE
        # ====================================================

        for person_id in lost_ids:

            previous = self._previous.get(
                person_id
            )

            if previous:

                previous_zone = (
                    previous.get(
                        "zone"
                    )
                )

                previous_hand = bool(
                    previous.get(
                        "hand_in_danger",
                        False,
                    )
                )

                if (
                    previous_zone
                    == "WARNING"
                ):

                    print(
                        f"[WARNING] {person_id} "
                        "LOST while in WARNING"
                    )

                if (
                    previous_zone
                    == "DANGER"
                    or previous_hand
                ):

                    print(
                        f"[DANGER] {person_id} "
                        "LOST while in DANGER"
                    )

            # ------------------------------------------------
            # IMPORTANT
            #
            # Losing a person resets the warning timer.
            # If they appear again, it starts from 0.
            # ------------------------------------------------

            self._previous.pop(
                person_id,
                None,
            )

            self._warning_state.pop(
                person_id,
                None,
            )

            self._danger_active.pop(
                person_id,
                None,
            )

        return emitted

    # ========================================================
    # WARNING DWELL COMPATIBILITY
    # ========================================================

    def warning_dwell_violations(
        self,
        frame=None,
    ):
        """
        Compatibility method.

        Warning incidents are generated directly
        from update().
        """

        return []

    # ========================================================
    # ALARM COMPATIBILITY
    # ========================================================

    def update_alarm(
        self,
        active,
        offenders=(),
        risks=None,
        machine_on=None,
        cfg=None,
    ):

        self._alarm_active = bool(
            active
        )

        return []

    # ========================================================
    # RECENT EVENTS
    # ========================================================

    def recent(
        self,
        count=10,
    ):

        return self.events[-count:]

    # ========================================================
    # RESET
    # ========================================================

    def reset(self):

        self._previous.clear()

        self._warning_state.clear()

        self._danger_active.clear()

        self._detected_persons.clear()

        self.events.clear()

        self.total_emitted = 0

        self._alarm_active = False

        return []