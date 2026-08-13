"""
src/event_logger.py
-------------------
THE ONLY FILE THAT WRITES TO DISK.

    events.py        decides WHAT is an event   (transition detection)
    event_logger.py  decides WHERE it is stored (this file)

Splitting the two means the transition rules can be tested with no filesystem at
all, and the storage can later be swapped for an HTTP POST to the Spring Boot
backend by replacing this one class -- nothing in events.py would change.

ON-DISK LAYOUT
    logs/
        events_2026-08-11.jsonl        every event of that day, one per line
        sessions/
            2026-08-11_12-30-45.json   one run: metadata + closing summary

WHY JSON LINES
One line = one complete event = one INDEPENDENTLY PARSEABLE record. A consumer
(today a human with a text editor, tomorrow a Spring Boot ingester) reads a line,
parses it, and is done -- there is no enclosing array to close, so a log that is
still being written, or one truncated by a hard kill, is still fully readable up
to its last complete line. Every line is flushed as it is written for exactly
that reason.

WHY ONE FILE PER DAY
Because that is how an operator looks for an incident: "what happened on the
11th". Runs stay separable because every event carries session_id.

WHAT MUST NEVER REACH THIS FILE
numpy scalars, numpy arrays, OpenCV objects, YOLO result objects, tuples of
numpy floats -- anything whose JSON form is a Python implementation detail. The
backend is Java; it gets strings, numbers, booleans, null, arrays and objects and
nothing else. json_safe() below enforces that at the boundary rather than trusting
every call site to remember.
"""

import json
import os
from datetime import datetime

# Directory inside LOG_ROOT holding one file per run.
SESSIONS_DIRNAME = "sessions"

# logs/events_<YYYY-MM-DD>.jsonl
DATE_FORMAT = "%Y-%m-%d"
EVENTS_FILENAME = "events_{date}.jsonl"


def json_safe(value):
    """
    Convert a value into something json.dumps can render as plain JSON.

        numpy scalar   -> int / float          (via .item())
        numpy array    -> list
        tuple / set    -> list
        dict           -> dict with safe values
        anything else  -> str(value)

    The str() fallback is deliberate: an event line that says "<Boxes object>" is
    ugly but harmless, while a crash inside the logger would take down a running
    safety monitor.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    # numpy scalars (np.float32, np.int64, ...) expose .item(); so do 0-d arrays.
    item = getattr(value, "item", None)
    if callable(item) and getattr(value, "shape", None) == ():
        try:
            return item()
        except (TypeError, ValueError):
            pass

    if isinstance(value, dict):
        return {str(key): json_safe(inner) for key, inner in value.items()}

    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(inner) for inner in value]

    # numpy arrays and anything else array-like.
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            return json_safe(tolist())
        except (TypeError, ValueError):
            pass

    return str(value)


class EventLogger:
    """
    Appends events to today's JSONL file and keeps one metadata file per run.

    Every method is failure-tolerant: if the disk is full or the directory is
    read-only we print a warning once and carry on in memory-only mode. Losing
    the log is bad; stopping the safety monitor because of it would be worse.
    """

    def __init__(self, log_root="logs", schema_version="1.0"):
        self.log_root = log_root
        self.schema_version = schema_version

        self.session_id = None
        self.events_path = None
        self.session_path = None
        self.event_count = 0

        self._handle = None
        self._warned = False

    # -- lifecycle -----------------------------------------------------------

    @property
    def is_open(self):
        return self._handle is not None

    def open(self, session_id, metadata=None, now=None):
        """
        Start logging for one run. Returns the events file path, or None when
        logging could not be started.
        """
        now = now or datetime.now()
        self.session_id = session_id
        self.event_count = 0

        try:
            sessions_dir = os.path.join(self.log_root, SESSIONS_DIRNAME)
            os.makedirs(sessions_dir, exist_ok=True)

            self.events_path = os.path.join(
                self.log_root, EVENTS_FILENAME.format(date=now.strftime(DATE_FORMAT)))
            self.session_path = os.path.join(sessions_dir, "{}.json".format(session_id))

            # Append: several runs on the same day share one events file.
            self._handle = open(self.events_path, "a", encoding="utf-8")

            session = {"session_id": session_id,
                       "schema_version": self.schema_version,
                       "started_at": now.isoformat(timespec="seconds")}
            session.update(metadata or {})
            self._write_session(session)
        except OSError as exc:
            print("[ZONE X] Warning: could not open the log directory '{}': {} "
                  "-- continuing without JSON logs.".format(self.log_root, exc))
            self.events_path = None
            self.session_path = None
            self._handle = None

        return self.events_path

    def close(self, summary=None):
        """
        Stamp the session file with the closing summary and close the events file.

        Events were flushed as they were written, so an unclean shutdown loses at
        most this summary -- never the event history.
        """
        if self.session_path is not None:
            try:
                with open(self.session_path, "r", encoding="utf-8") as handle:
                    session = json.load(handle)
            except (OSError, ValueError):
                session = {"session_id": self.session_id}
            session.update(summary or {})
            self._write_session(session)

        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None

    # -- writing -------------------------------------------------------------

    def write(self, record):
        """Append one event as one flushed line. Returns True if it was written."""
        if self._handle is None:
            return False
        try:
            self._handle.write(json.dumps(json_safe(record)) + "\n")
            # Flush every line: an unplugged camera or a hard kill must not lose
            # the events that led up to it.
            self._handle.flush()
            self.event_count += 1
            return True
        except (OSError, TypeError, ValueError) as exc:
            if not self._warned:
                self._warned = True
                print("[ZONE X] Warning: could not write an event: {}".format(exc))
            return False

    def _write_session(self, session):
        try:
            with open(self.session_path, "w", encoding="utf-8") as handle:
                json.dump(json_safe(session), handle, indent=4)
                handle.write("\n")
        except OSError as exc:
            print("[ZONE X] Warning: could not write {}: {}".format(
                self.session_path, exc))
