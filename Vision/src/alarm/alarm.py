"""
src/alarm.py
------------
THE NOISE. A repeating PC beep for as long as a worker is in a confirmed critical
condition, and silence otherwise.

    start_alarm()      begin beeping (does nothing if already beeping)
    stop_alarm()       go quiet
    is_alarm_active()  is it beeping right now?

DELIBERATELY ISOLATED
This module knows nothing about YOLO, tracking, zones, risk scores, events or
frames -- and none of them know about it. It is handed a decision that has already
been made and does exactly one thing with it. main.py is the only file that talks
to it, in one place (_update_alarm). Deleting this file would cost the project its
buzzer and nothing else.

WHO DECIDES
Not this file. main.py reads the CONFIRMED, debounced state that tracker.py
produces -- body zone DANGER, or a confirmed hand-in-danger reach-in -- so the
existing hysteresis (ENTER_CONFIRM_FRAMES / HAND_ENTER_CONFIRM_FRAMES) is what
gates the alarm. A single noisy detection can never sound it, and there is no
second danger-decision system anywhere in the project.

ONE THREAD, ONE ALARM
The beeping happens on a single background thread, because winsound.Beep blocks
for the length of the beep and blocking the main loop would freeze detection. That
thread is created when the alarm starts and stopped when it stops. Calling
start_alarm() again while it is already running does nothing at all, so calling it
every frame -- which main.py does not do, but which must be safe anyway -- cannot
pile up threads.

NO NEW DEPENDENCY
winsound is part of the Python standard library on Windows, which is what this
prototype runs on. On any other platform we fall back to the terminal bell so the
code still runs (quietly) instead of crashing; see _emit_beep.
"""

import sys
import threading
import time

# Windows only, and part of the standard library there. No pip install.
try:
    import winsound
except ImportError:      # macOS / Linux -- see _emit_beep
    winsound = None

# winsound.Beep refuses anything outside this range.
_MIN_FREQUENCY = 37
_MAX_FREQUENCY = 32767


class Alarm:
    """
    A repeating beep pattern on its own thread.

        BEEP BEEP BEEP BEEP  (pause)  BEEP BEEP BEEP BEEP  (pause) ...

    Every timing value comes from config.py. Thread-safe: start_alarm() and
    stop_alarm() may be called from anywhere, as often as you like.
    """

    def __init__(self, enabled=True, frequency=1000, beep_ms=180, gap_ms=90,
                 beeps_per_burst=4, pause_ms=600, repeat=True):
        self.enabled = bool(enabled)
        self.frequency = int(max(_MIN_FREQUENCY, min(int(frequency), _MAX_FREQUENCY)))
        self.beep_ms = max(20, int(beep_ms))
        self.gap_ms = max(0, int(gap_ms))
        self.beeps_per_burst = max(1, int(beeps_per_burst))
        self.pause_ms = max(0, int(pause_ms))
        self.repeat = bool(repeat)

        self._thread = None
        self._stop = threading.Event()
        self._active = False
        self._lock = threading.Lock()
        # Printed once if we are not on Windows, rather than on every beep.
        self._warned = False

    @classmethod
    def from_config(cls, cfg):
        """Build the alarm described by config.py."""
        return cls(
            enabled=getattr(cfg, "ENABLE_PC_ALARM", True),
            frequency=getattr(cfg, "ALARM_BEEP_FREQUENCY", 1000),
            beep_ms=getattr(cfg, "ALARM_BEEP_DURATION_MS", 180),
            gap_ms=getattr(cfg, "ALARM_BEEP_GAP_MS", 90),
            beeps_per_burst=getattr(cfg, "ALARM_BEEPS_PER_BURST", 4),
            pause_ms=getattr(cfg, "ALARM_PAUSE_DURATION_MS", 600),
            repeat=getattr(cfg, "ALARM_REPEAT", True),
        )

    # -- public interface ----------------------------------------------------

    def start_alarm(self):
        """
        Begin beeping. Returns True only if this call actually started it, so the
        caller can log the transition without keeping its own flag.

        Safe to call repeatedly: while the alarm is already sounding this is a
        no-op and NO second thread is created.
        """
        if not self.enabled:
            return False

        with self._lock:
            if self._active:
                return False
            self._stop.clear()
            self._active = True
            self._thread = threading.Thread(
                target=self._run,
                name="alarm",
                daemon=True,      # must never keep the process alive
            )
            self._thread.start()
            return True

    def stop_alarm(self):
        """
        Go quiet. Returns True only if this call actually stopped a running alarm.

        A beep already in progress finishes first -- winsound.Beep cannot be
        interrupted -- so silence arrives within one beep length (ALARM_BEEP_
        DURATION_MS, 180 ms by default).
        """
        with self._lock:
            if not self._active:
                return False
            self._active = False
            thread = self._thread
            self._thread = None
        self._stop.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        return True

    def is_alarm_active(self):
        """True while the alarm is sounding."""
        with self._lock:
            return self._active

    # -- internals -----------------------------------------------------------

    def _run(self):
        """
        The beep pattern. Runs until stop_alarm(), or until one burst has played
        when ALARM_REPEAT is False.

        Nothing in here may raise: an audio device that refuses to beep must not
        take down a running safety monitor.
        """
        try:
            while not self._stop.is_set():
                for _ in range(self.beeps_per_burst):
                    if self._stop.is_set():
                        break
                    self._emit_beep()
                    if self._stop.wait(self.gap_ms / 1000.0):
                        break

                if not self.repeat:
                    break
                if self._stop.wait(self.pause_ms / 1000.0):
                    break
        finally:
            # A non-repeating alarm ends on its own, so the flag has to be cleared
            # here too -- otherwise is_alarm_active() would lie forever after.
            with self._lock:
                self._active = False

    def _emit_beep(self):
        """
        One beep. Blocks for beep_ms, which is why this runs on its own thread.

        winsound.Beep drives the motherboard/PC speaker path on Windows and is
        loud and unmissable, which is the point. Off Windows there is no standard
        equivalent, so we ring the terminal bell and sleep for the same duration:
        the timing stays correct and the app still runs.
        """
        try:
            if winsound is not None:
                winsound.Beep(self.frequency, self.beep_ms)
                return
            if not self._warned:
                self._warned = True
                print("[ZONE X] No winsound on this platform: the alarm falls back "
                      "to the terminal bell.")
            sys.stdout.write("\a")
            sys.stdout.flush()
            time.sleep(self.beep_ms / 1000.0)
        except Exception as exc:            # audio device busy, no speaker, ...
            if not self._warned:
                self._warned = True
                print("[ZONE X] Warning: could not sound the alarm: {}".format(exc))
            time.sleep(self.beep_ms / 1000.0)
