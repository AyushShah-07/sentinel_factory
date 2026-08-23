"""
src/event_logger.py
THE ONLY FILE THAT WRITES TO DISK.

events.py decides WHAT is an event (when something happens)
event_logger.py decides WHERE it is stored (this file writes the JSON)

This separation means we can test events without touching the filesystem,
or swap this logger for an HTTP POST to a backend later.
"""

import json
import os
from datetime import datetime

# Folder inside logs/ that stores one file per run
SESSIONS_DIRNAME = "sessions"

# logs/events_2026-08-21.jsonl
DATE_FORMAT = "%Y-%m-%d"
EVENTS_FILENAME = "events_{date}.jsonl"


def json_safe(value):
    """
    Convert ANY value into JSON-friendly format.
    
    Why this exists:
    - numpy floats -> regular floats
    - numpy arrays -> lists
    - tuples/sets -> lists
    - objects -> str(value)
    
    This prevents crashes from numpy/YOLO objects making it into JSON.
    The backend is Java, so it gets plain JSON - nothing else.
    """
    # Already JSON-safe
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    # Handle numpy scalars (np.float32, np.int64, etc.)
    item = getattr(value, "item", None)
    if callable(item) and getattr(value, "shape", None) == ():
        try:
            return item()
        except (TypeError, ValueError):
            pass

    # Handle dictionaries
    if isinstance(value, dict):
        return {str(key): json_safe(inner) for key, inner in value.items()}

    # Handle lists, tuples, sets
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(inner) for inner in value]

    # Handle numpy arrays
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            return json_safe(tolist())
        except (TypeError, ValueError):
            pass

    # Last resort: convert to string
    return str(value)


class EventLogger:
    """
    Writes events to a daily JSONL file and keeps a session metadata file.
    
    Every method is failure-tolerant: if disk is full, we warn once
    and continue WITHOUT logging. Losing logs is bad, but stopping
    the safety monitor because of a full disk would be worse.
    """

    def __init__(self, log_root="logs", schema_version="1.0"):
        self.log_root = log_root
        self.schema_version = schema_version
        
        self.session_id = None
        self.events_path = None
        self.session_path = None
        self.event_count = 0
        
        self._handle = None  # Open file handle
        self._warned = False  # Warn once about disk errors

    # -- lifecycle -----------------------------------------------------------

    @property
    def is_open(self):
        """True if logger is ready to write events"""
        return self._handle is not None

    def open(self, session_id, metadata=None, now=None):
        """
        Start logging for one run.
        
        Creates:
        - logs/events_2026-08-21.jsonl (appended to daily)
        - logs/sessions/2026-08-11_12-30-45.json (session metadata)
        
        Returns events file path, or None if logging couldn't start.
        """
        now = now or datetime.now()
        self.session_id = session_id
        self.event_count = 0

        try:
            # Create sessions folder
            sessions_dir = os.path.join(self.log_root, SESSIONS_DIRNAME)
            os.makedirs(sessions_dir, exist_ok=True)

            # Daily events file (shared by all runs on this day)
            self.events_path = os.path.join(
                self.log_root, EVENTS_FILENAME.format(date=now.strftime(DATE_FORMAT)))
            
            # Session metadata file (one per run)
            self.session_path = os.path.join(sessions_dir, "{}.json".format(session_id))

            # Open events file in append mode
            self._handle = open(self.events_path, "a", encoding="utf-8")

            # Write session metadata
            session = {
                "session_id": session_id,
                "schema_version": self.schema_version,
                "started_at": now.isoformat(timespec="seconds")
            }
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
        Close the logger:
        1. Update session file with summary
        2. Close events file
        """
        # Update session file with summary
        if self.session_path is not None:
            try:
                with open(self.session_path, "r", encoding="utf-8") as handle:
                    session = json.load(handle)
            except (OSError, ValueError):
                session = {"session_id": self.session_id}
            session.update(summary or {})
            self._write_session(session)

        # Close events file
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None

    # -- writing -------------------------------------------------------------

    def write(self, record):
        """
        Write one event as a single JSON line.
        Flush immediately so no events are lost on crash.
        
        Returns True if written successfully.
        """
        if self._handle is None:
            return False
        
        try:
            # Write JSON + newline, flush immediately
            self._handle.write(json.dumps(json_safe(record)) + "\n")
            self._handle.flush()  # Important: don't buffer!
            self.event_count += 1
            return True
            
        except (OSError, TypeError, ValueError) as exc:
            if not self._warned:
                self._warned = True
                print("[ZONE X] Warning: could not write an event: {}".format(exc))
            return False

    def _write_session(self, session):
        """Write session metadata as pretty JSON"""
        try:
            with open(self.session_path, "w", encoding="utf-8") as handle:
                json.dump(json_safe(session), handle, indent=4)
                handle.write("\n")
        except OSError as exc:
            print("[ZONE X] Warning: could not write {}: {}".format(
                self.session_path, exc))