import json
import os
import time
from datetime import datetime

import cv2

from src.events.helpers import (
    now,
    person_id,
    zone_type,
    hand_is_in_danger,
    danger_source,
)


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

        person_id_value = person_id(
            person
        )

        if person_id_value is None:
            return None

        # Already detected
        if (
            person_id_value
            in self._detected_persons
        ):
            return None

        timestamp = now()

        record = {
            "timestamp": timestamp,
            "event": PERSON_DETECTED,
            "person_id": person_id_value,
        }

        # --------------------------------------------------------
        # LOCAL JSON
        # --------------------------------------------------------

        self._append_json(
            self.persons_path,
            record,
        )

        self._detected_persons.add(
            person_id_value
        )

        # --------------------------------------------------------
        # MEMORY
        # --------------------------------------------------------

        self.events.append(
            record
        )

        self.total_emitted += 1

        if len(self.events) > self.max_events:

            self.events = self.events[
                -self.max_events:
            ]

        # --------------------------------------------------------
        # BACKEND
        # --------------------------------------------------------

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

        # --------------------------------------------------------
        # CONSOLE
        # --------------------------------------------------------

        print(
            f"[Event] {timestamp} "
            f"PERSON_DETECTED "
            f"{person_id_value}"
        )

        return record

    # ========================================================
    # SAVE INCIDENT IMAGE
    # ========================================================

    def _save_frame(
        self,
        frame,
        person_id_value,
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

            directory = self.danger_image_dir

        elif incident_type == "warning":

            directory = self.warning_image_dir

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
            f"{person_id_value}_{timestamp_text}.jpg"
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
        person_id_value,
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
            str(person_id_value)
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

        person_id_value = person_id(person)

        if person_id_value is None:
            return None

        # ----------------------------------------------------
        # ASSIGNED PERSON FILTER
        # ----------------------------------------------------

        if not self._is_assigned_person(
            person_id_value
        ):

            print(
                f"[DANGER] {person_id_value} is not assigned. "
                "Incident ignored."
            )

            return None

        # ----------------------------------------------------
        # DUPLICATE PROTECTION
        # ----------------------------------------------------

        if person_id_value in self._danger_active:
            return None

        # ----------------------------------------------------
        # DETERMINE SOURCE
        # ----------------------------------------------------

        if source is None:
            source = danger_source(person)

        if source is None:
            return None

        # ----------------------------------------------------
        # ZONE DATA
        # ----------------------------------------------------

        body_zone = zone_type(person)

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
            f"[DANGER] {person_id_value} "
            f"INCIDENT SOURCE={source} "
            f"BODY={body_zone}/{body_zone_id} "
            f"HAND={hand_zone}/{hand_zone_id}"
        )

        # ----------------------------------------------------
        # CAPTURE IMMEDIATELY
        # ----------------------------------------------------

        timestamp = now()

        picture = self._save_frame(
            frame,
            person_id_value,
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
                f"[DANGER] {person_id_value}: "
                "image capture FAILED"
            )

            return None

        # ----------------------------------------------------
        # JSON RECORD
        # ----------------------------------------------------

        record = {
            "timestamp": timestamp,
            "person_id": person_id_value,
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
            person_id_value
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
            f"{person_id_value} | "
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

        A warning incident occurs when either:

        1. The person's BODY is inside a WARNING zone
        OR
        2. The person's HAND is inside a WARNING zone

        The person must remain continuously in the same
        warning zone for warning_dwell_threshold seconds.

        Changing WARNING_1 -> WARNING_2 resets the timer.

        Leaving all warning zones resets the timer.

        Only one incident is generated per continuous stay.
        """

        # ====================================================
        # PERSON ID
        # ====================================================

        pid = person_id(person)

        if pid is None:
            return None

        # ====================================================
        # BODY WARNING DATA
        # ====================================================

        body_zone = zone_type(person)

        body_zone_id = person.get("zone_id")

        if body_zone_id is not None:
            body_zone_id = str(body_zone_id).strip()

        # ====================================================
        # HAND WARNING DATA
        # ====================================================

        hand_zone = person.get("hand_zone")

        hand_zone_id = person.get("hand_zone_id")

        if hand_zone_id is not None:
            hand_zone_id = str(hand_zone_id).strip()

        # ====================================================
        # DETERMINE WARNING ZONE
        #
        # BODY HAS PRIORITY
        # ====================================================

        warning_zone = None
        warning_zone_id = None
        warning_source = None

        # ----------------------------------------------------
        # BODY IS IN WARNING
        # ----------------------------------------------------

        if body_zone == "WARNING":

            warning_zone = "WARNING"
            warning_zone_id = body_zone_id
            warning_source = "BODY"

        # ----------------------------------------------------
        # OTHERWISE CHECK HAND
        # ----------------------------------------------------

        elif hand_zone == "WARNING":

            warning_zone = "WARNING"
            warning_zone_id = hand_zone_id
            warning_source = "HAND"

        # ====================================================
        # DEBUG
        # ====================================================

        print(
            f"[WARNING DEBUG] "
            f"PID={pid} "
            f"body_zone={repr(body_zone)} "
            f"body_zone_id={repr(body_zone_id)} "
            f"hand_zone={repr(hand_zone)} "
            f"hand_zone_id={repr(hand_zone_id)} "
            f"active_warning={repr(warning_zone_id)} "
            f"source={repr(warning_source)} "
            f"threshold={self.warning_dwell_threshold}"
        )

        # ====================================================
        # NOT IN WARNING
        # ====================================================

        if warning_zone != "WARNING":

            state = self._warning_state.get(pid)

            if state is not None:

                elapsed = (
                    time.monotonic()
                    - state["entered_at"]
                )

                print(
                    f"[WARNING] {pid} "
                    f"LEFT WARNING "
                    f"zone={state.get('zone_id')} "
                    f"source={state.get('source')} "
                    f"after {elapsed:.2f}s"
                )

            self._warning_state.pop(
                pid,
                None,
            )

            return None

        # ====================================================
        # NEW WARNING ENTRY
        # ====================================================

        state = self._warning_state.get(pid)

        if state is None:

            self._warning_state[pid] = {
                "zone_id": warning_zone_id,
                "entered_at": time.monotonic(),
                "triggered": False,
                "source": warning_source,
            }

            print(
                f"[WARNING] {pid} "
                f"ENTERED WARNING "
                f"zone={warning_zone_id} "
                f"source={warning_source}"
            )

            return None

        # ====================================================
        # WARNING ZONE CHANGED
        # ====================================================

        if state.get("zone_id") != warning_zone_id:

            print(
                f"[WARNING] {pid} "
                f"WARNING ZONE CHANGED "
                f"{state.get('zone_id')} "
                f"-> {warning_zone_id}"
            )

            self._warning_state[pid] = {
                "zone_id": warning_zone_id,
                "entered_at": time.monotonic(),
                "triggered": False,
                "source": warning_source,
            }

            return None

        # ====================================================
        # SOURCE CHANGED
        #
        # BODY -> HAND or HAND -> BODY
        #
        # Keep timer because person is still in the same
        # WARNING zone.
        # ====================================================

        if state.get("source") != warning_source:

            print(
                f"[WARNING] {pid} "
                f"WARNING SOURCE CHANGED "
                f"{state.get('source')} "
                f"-> {warning_source}"
            )

            state["source"] = warning_source

        # ====================================================
        # ALREADY TRIGGERED
        # ====================================================

        if state.get("triggered", False):
            return None

        # ====================================================
        # CALCULATE DWELL TIME
        # ====================================================

        elapsed = (
            time.monotonic()
            - state["entered_at"]
        )

        print(
            f"[WARNING TIMER] "
            f"{pid} "
            f"zone={warning_zone_id} "
            f"source={warning_source} "
            f"elapsed={elapsed:.2f}s/"
            f"{self.warning_dwell_threshold:.2f}s"
        )

        # ====================================================
        # THRESHOLD NOT REACHED
        # ====================================================

        if elapsed < self.warning_dwell_threshold:
            return None

        # ====================================================
        # WARNING INCIDENT TRIGGERED
        # ====================================================

        timestamp = now()

        print(
            f"[WARNING] {pid} "
            f"stayed {elapsed:.2f}s "
            f"in WARNING "
            f"zone={warning_zone_id} "
            f"source={warning_source}"
        )

        # ====================================================
        # SAVE INCIDENT IMAGE
        # ====================================================

        picture = self._save_frame(
            frame,
            pid,
            "warning",
        )

        if (
            self.save_incident_images
            and picture is None
        ):

            print(
                f"[WARNING] {pid}: "
                "image capture FAILED"
            )

            return None

        # ====================================================
        # CREATE EVENT
        # ====================================================

        record = {
            "timestamp": timestamp,
            "person_id": pid,

            "incident_name":
                WARNING_INCIDENT_NAME,

            "incident_type":
                "WARNING",

            "zone":
                "WARNING",

            "zone_id":
                warning_zone_id,

            "source":
                warning_source,

            "dwell_seconds":
                round(
                    elapsed,
                    2,
                ),

            "picture":
                picture,
        }

        # ====================================================
        # SAVE JSON
        # ====================================================

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

        # ====================================================
        # LOG
        # ====================================================

        print(
            f"[INCIDENT SAVED] "
            f"{WARNING_INCIDENT_NAME} | "
            f"{pid} | "
            f"zone={warning_zone_id} | "
            f"source={warning_source} | "
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

            person_id_value = person_id(person)

            if person_id_value is None:
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
                person_id_value,
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
                person_id_value
            )

            # =================================================
            # PERSON DETECTED
            # =================================================

            event = self._person_detected(
                person
            )

            if event is not None:
                emitted.append(event)

            # =================================================
            # CURRENT BODY ZONE
            # =================================================

            current_zone = zone_type(person)

            current_zone_id = person.get(
                "zone_id"
            )

            # =================================================
            # CURRENT HAND DANGER
            # =================================================

            hand_danger = hand_is_in_danger(
                person
            )

            # =================================================
            # PREVIOUS STATE
            # =================================================

            previous = self._previous.get(
                person_id_value
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

                    source = danger_source(
                        person
                    )

                    print(
                        f"[DANGER] {person_id_value}: "
                        f"ENTERED DANGER "
                        f"source={source}"
                    )

                    event = self._danger_entry(
                        person,
                        frame,
                        source,
                    )

                    if event is not None:
                        emitted.append(event)

                else:

                    # Still in danger.
                    # No duplicate incident.

                    if (
                        person_id_value
                        not in self._danger_active
                    ):

                        source = danger_source(
                            person
                        )

                        self._danger_active[
                            person_id_value
                        ] = (
                            source
                            or "UNKNOWN"
                        )

            else:

                # ------------------------------------------------
                # PERSON LEFT DANGER
                # ------------------------------------------------

                if danger_before:

                    print(
                        f"[DANGER] {person_id_value} "
                        "LEFT DANGER"
                    )

                self._danger_active.pop(
                    person_id_value,
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
                emitted.append(event)

            # =================================================
            # SAVE CURRENT STATE
            # =================================================

            self._previous[
                person_id_value
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

        for person_id_value in lost_ids:

            previous = self._previous.get(
                person_id_value
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

                if previous_zone == "WARNING":

                    print(
                        f"[WARNING] {person_id_value} "
                        "LOST while in WARNING"
                    )

                if (
                    previous_zone == "DANGER"
                    or previous_hand
                ):

                    print(
                        f"[DANGER] {person_id_value} "
                        "LOST while in DANGER"
                    )

            # ------------------------------------------------
            # Losing a person resets the warning timer.
            # ------------------------------------------------

            self._previous.pop(
                person_id_value,
                None,
            )

            self._warning_state.pop(
                person_id_value,
                None,
            )

            self._danger_active.pop(
                person_id_value,
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