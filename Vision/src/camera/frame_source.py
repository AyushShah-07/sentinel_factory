"""
src/frame_source.py
-------------------
WHERE FRAMES COME FROM. This is the only file in the project that knows what a
camera is.

    Camera / IP / RTSP / video
              |
              v
        reader thread          one thread, nothing else
              |
              v
        LatestFrame            newest frame only, older ones dropped
              |
              v
     ProcessingScheduler       time-based, ~TARGET_PROCESSING_FPS
              |
              v
    detector -> tracker -> zones -> risk -> events      (unchanged)

Everything downstream receives a plain BGR numpy frame and has no idea whether
it came from a laptop webcam, a phone on the factory wifi, a CCTV camera on the
wall, or an mp4 recorded last week. That is the whole point of the file: switching
input is a config edit (config.SOURCE_TYPE), never a code edit, and there is no
"if webcam / elif phone / elif cctv" branching anywhere in main.py.

SUPPORTED SOURCES
    "webcam"  cv2.VideoCapture(CAMERA_INDEX)   local / USB camera
    "ip"      cv2.VideoCapture(SOURCE_URL)     phone or HTTP network camera
    "cctv"    cv2.VideoCapture(SOURCE_URL)     RTSP / IP camera
    "video"   cv2.VideoCapture(VIDEO_PATH)     recorded footage

"ip" and "cctv" are opened identically -- OpenCV just needs a URL. They are kept
as separate names because the event log records which kind of installation an
event came from, and because the error messages differ ("check the phone is on
the same wifi" is useless advice for a wall-mounted RTSP camera).

=============================================================================
WHY FRAMES ARE DROPPED ON PURPOSE
=============================================================================
A network camera sends frames whether or not we are ready for them. Read them
one-at-a-time from the main loop and they queue up in the network/OpenCV buffer:
YOLO then analyses what the camera saw a second ago, and the "live" view drifts
further behind the longer the app runs. That is the wrong trade for a safety
monitor -- we would rather know where the worker is NOW than eventually see every
frame they were in.

So one reader thread continuously drains the source and keeps only the NEWEST
frame. The main loop asks for that frame roughly TARGET_PROCESSING_FPS times a
second and gets whatever is freshest.

    source at 30 FPS, pipeline at 10 FPS
        -> ~10 frames/second processed
        -> ~20 frames/second dropped        <-- EXPECTED, NOT A BUG

We prefer NEW FRAME + LOW LATENCY over EVERY FRAME + HIGH LATENCY.

The one exception is a recorded file with VIDEO_REALTIME = False, which is read
sequentially with nothing dropped: a file cannot go stale, and offline analysis
of footage wants every frame. See uses_reader_thread().

WHAT THIS CAN AND CANNOT FIX
This removes the latency OUR pipeline adds -- buffered frames, and a slow
consumer holding up a fast producer. It cannot do anything about latency created
before the frame reaches us: wifi quality, the phone's camera app, encoding,
bitrate, resolution, network congestion and the stream protocol all add delay of
their own. No OpenCV arrangement can promise zero latency on a network stream.

ERRORS
Configuration mistakes (wrong index, unreachable phone, missing file, dead RTSP
stream) raise FrameSourceError with a message a human can act on. They are
NOT stack traces: a typo in a URL is an operator problem, not a crash.
Once running, a single bad frame is never fatal -- the reader retries, and
reconnects the stream if the failures continue.
"""

import collections
import os
import sys
import threading
import time

import cv2

# The four names accepted by config.SOURCE_TYPE.
WEBCAM = "webcam"
IP = "ip"
CCTV = "cctv"
VIDEO = "video"

SOURCE_TYPES = (WEBCAM, IP, CCTV, VIDEO)

# Sources that are just a URL handed to OpenCV.
_NETWORK_TYPES = (IP, CCTV)

_HUMAN_NAMES = {
    WEBCAM: "webcam",
    IP: "IP camera",
    CCTV: "CCTV stream",
    VIDEO: "video file",
}

# Consecutive failed reads before we stop retrying and rebuild the connection.
# A couple of bad frames is normal on wifi; five in a row means the stream died.
_FAILURES_BEFORE_RECONNECT = 5

# Backoff between reconnect attempts (seconds). The last value repeats.
_RECONNECT_BACKOFF = (0.5, 1.0, 2.0, 4.0)

# How many recent timestamps the FPS averages use.
_FPS_WINDOW = 60


class FrameSourceError(Exception):
    """
    Raised when the configured source cannot be opened, or dies mid-run.

    Carries a complete, human-readable explanation including how to fix it --
    main.py prints it as-is and exits, with no traceback.
    """


class LatestFrame:
    """
    A one-slot, thread-safe frame hand-off.

    The reader thread calls put(); the main loop calls get(). When put() finds the
    slot still occupied, the frame already sitting there has not been processed
    and is REPLACED -- that is the drop, and it is counted so the diagnostics can
    show it honestly.

    Deliberately not a Queue: a queue's job is to not lose anything, which is
    exactly the wrong behaviour here.
    """

    def __init__(self, depth=1):
        self.depth = max(1, int(depth))
        self._frames = collections.deque(maxlen=self.depth)
        self._lock = threading.Lock()

    def put(self, frame, stamp):
        """Store a frame. Returns how many unprocessed frames it displaced."""
        with self._lock:
            dropped = 1 if len(self._frames) == self.depth else 0
            self._frames.append((frame, stamp))
            return dropped

    def get(self):
        """
        Take the oldest frame still held (with depth 1, that is the newest frame
        the source produced). Returns (None, None) when nothing new has arrived.

        Taking it EMPTIES the slot, so the same frame is never processed twice and
        the pipeline naturally idles instead of re-running on stale pixels.
        """
        with self._lock:
            if not self._frames:
                return None, None
            return self._frames.popleft()

    def clear(self):
        with self._lock:
            self._frames.clear()

    def __len__(self):
        with self._lock:
            return len(self._frames)


class SourceStats:
    """
    Lightweight diagnostics for the frame pipeline.

    DIAGNOSTICS, NOT SAFETY EVENTS. None of this is ever written to the JSON event
    log -- it describes our own plumbing, not what happened on the factory floor.

    All counters are plain ints and floats, updated under a lock because `received`
    comes from the reader thread while everything else is read from the main loop.
    """

    def __init__(self, window=_FPS_WINDOW):
        self._lock = threading.Lock()

        self.source_type = ""
        self.width = 0
        self.height = 0
        self.reported_fps = 0.0      # what the device claims, often 0 or wrong

        self.received = 0            # frames the reader pulled off the source
        self.processed = 0           # frames handed to the AI pipeline
        self.dropped = 0             # frames replaced before being processed
        self.read_failures = 0       # bad/empty reads (normal in small numbers)
        self.reconnects = 0

        # Seconds a frame waited in our buffer before being picked up. This is the
        # part of the latency WE control; it says nothing about camera/network delay.
        self.buffer_age = 0.0
        # Seconds the whole pipeline took on the last frame (YOLO + pose + draw).
        self.process_time = 0.0

        self.last_received_at = None   # monotonic
        self._received_times = collections.deque(maxlen=window)
        self._processed_times = collections.deque(maxlen=window)

    # -- reader thread side --------------------------------------------------

    def note_received(self, dropped=0, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            self.received += 1
            self.dropped += dropped
            self.last_received_at = now
            self._received_times.append(now)

    def note_read_failure(self):
        with self._lock:
            self.read_failures += 1

    def note_reconnect(self):
        with self._lock:
            self.reconnects += 1

    def note_geometry(self, source_type, width, height, reported_fps):
        with self._lock:
            self.source_type = source_type
            self.width = int(width or 0)
            self.height = int(height or 0)
            self.reported_fps = float(reported_fps or 0.0)

    # -- main loop side ------------------------------------------------------

    def note_processed(self, buffer_age, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            self.processed += 1
            self.buffer_age = float(buffer_age)
            self._processed_times.append(now)

    def note_process_time(self, seconds):
        with self._lock:
            self.process_time = float(seconds)

    # -- derived numbers -----------------------------------------------------

    @staticmethod
    def _rate(times):
        """Frames per second implied by a series of timestamps."""
        if len(times) < 2:
            return 0.0
        span = times[-1] - times[0]
        return (len(times) - 1) / span if span > 0 else 0.0

    @property
    def source_fps(self):
        """MEASURED arrival rate, not what the device claims it can do."""
        with self._lock:
            return self._rate(self._received_times)

    @property
    def process_fps(self):
        with self._lock:
            return self._rate(self._processed_times)

    @property
    def drop_rate(self):
        """Fraction of received frames that were dropped, 0.0-1.0."""
        with self._lock:
            return self.dropped / self.received if self.received else 0.0

    def snapshot(self):
        """Everything as plain JSON-safe values, for the HUD or a debug dump."""
        with self._lock:
            received, processed, dropped = self.received, self.processed, self.dropped
            failures, reconnects = self.read_failures, self.reconnects
            source_type = self.source_type
            width, height = self.width, self.height
            buffer_age, process_time = self.buffer_age, self.process_time
            source_fps = self._rate(self._received_times)
            process_fps = self._rate(self._processed_times)
        return {
            "source_type": source_type,
            "resolution": "{}x{}".format(width, height),
            "source_fps": round(source_fps, 1),
            "process_fps": round(process_fps, 1),
            "frames_received": received,
            "frames_processed": processed,
            "frames_dropped": dropped,
            "read_failures": failures,
            "reconnects": reconnects,
            "buffer_age_ms": round(buffer_age * 1000.0, 1),
            "process_time_ms": round(process_time * 1000.0, 1),
        }

    def console_line(self):
        """One-line summary for the console, in the order that reads best."""
        s = self.snapshot()
        return ("[Stats] SOURCE: {source_type} {resolution}  "
                "SOURCE FPS: {source_fps}  PROCESS FPS: {process_fps}  "
                "RECEIVED: {frames_received}  PROCESSED: {frames_processed}  "
                "DROPPED: {frames_dropped}  "
                "BUFFER AGE: {buffer_age_ms} ms  PIPELINE: {process_time_ms} ms"
                .format(**s)
                + ("  RECONNECTS: {}".format(s["reconnects"])
                   if s["reconnects"] else ""))

    def hud_lines(self):
        """Short label/value pairs for the on-screen panel."""
        s = self.snapshot()
        return [
            ("SOURCE", "{} {}".format(s["source_type"].upper(), s["resolution"])),
            ("SOURCE FPS", "{:.1f}".format(s["source_fps"])),
            ("PROCESS FPS", "{:.1f}".format(s["process_fps"])),
            ("RECEIVED", str(s["frames_received"])),
            ("PROCESSED", str(s["frames_processed"])),
            ("DROPPED", str(s["frames_dropped"])),
            ("BUFFER AGE", "{:.0f} ms".format(s["buffer_age_ms"])),
            ("PIPELINE", "{:.0f} ms".format(s["process_time_ms"])),
        ]


class ProcessingScheduler:
    """
    Decides WHEN the pipeline should take another frame.

    TIME-based, on a monotonic clock -- never "every Nth frame". Frame counting
    would mean a different real-world rate on every source (a 15 FPS phone and a
    60 FPS webcam would behave completely differently), and monotonic time cannot
    jump backwards when the system clock is adjusted.

        interval = 1 / target_fps
        due when  now >= next_due

    The next slot is measured from the PREVIOUS SLOT, not from the moment the last
    frame happened to arrive. That distinction matters on a source whose frame rate
    is not a neat multiple of the target: with a 15 FPS camera and a 10 FPS target,
    restarting the clock on each arrival would quantise to every second frame
    (7.5 FPS), while advancing the slot keeps two frames in three (a real 10 FPS).

    If the pipeline falls more than one interval behind -- a slow machine, or the
    app was paused -- the schedule resyncs rather than firing a burst of catch-up
    frames, which would defeat the whole point of preferring fresh frames.

    target_fps=None disables the gate entirely: due() is always True. That is what
    a sequentially-read video file uses, where every frame is wanted.
    """

    # Guards the boundary case: 100.10 - 100.0 is not exactly 0.1 in floating
    # point, and without this a frame due right now can be deferred a whole pass.
    _TOLERANCE = 1e-6

    def __init__(self, target_fps=None):
        self.target_fps = float(target_fps) if target_fps else None
        self.interval = 1.0 / self.target_fps if self.target_fps else 0.0
        self._next_due = None

    @property
    def enabled(self):
        return self.interval > 0.0

    def due(self, now=None):
        """True when enough time has passed to process another frame."""
        if not self.enabled or self._next_due is None:
            return True
        now = time.monotonic() if now is None else now
        return now >= (self._next_due - self._TOLERANCE)

    def mark(self, now=None):
        """Record that a frame was just taken for processing."""
        now = time.monotonic() if now is None else now
        if not self.enabled:
            self._next_due = now
            return
        if self._next_due is None:
            self._next_due = now + self.interval
            return
        self._next_due += self.interval
        if self._next_due < now:
            # More than a full interval behind: resync instead of catching up.
            self._next_due = now + self.interval

    def seconds_until_due(self, now=None):
        """How long until the next slot. 0.0 when a frame is due right now."""
        if not self.enabled or self._next_due is None:
            return 0.0
        now = time.monotonic() if now is None else now
        return max(0.0, self._next_due - now)


class FrameSource:
    """
    One video input, whatever kind it is.

        source = FrameSource.from_config(config).open()
        while True:
            ok, frame = source.read()      # newest frame, or (False, None)
            if not ok:
                continue                   # nothing NEW yet -- not an error
        source.release()

    Four methods, deliberately: open(), read(), is_opened(), release().
    """

    def __init__(
        self,
        source_type=WEBCAM,
        camera_index=0,
        source_url="",
        video_path="",
        width=None,
        height=None,
        mirror=False,
        open_timeout=5.0,
        realtime=True,
        buffer_size=1,
        reconnect=True,
    ):
        self.source_type = str(source_type or "").strip().lower()
        self.camera_index = camera_index
        self.source_url = (source_url or "").strip()
        self.video_path = (video_path or "").strip()
        self.width = width
        self.height = height
        self.mirror = bool(mirror)
        self.open_timeout = float(open_timeout)
        # Only meaningful for a video file; a live camera paces itself.
        self.realtime = bool(realtime)
        self.reconnect = bool(reconnect)

        self._capture = None
        self._backend_name = ""
        # The frame read while probing the source at open() time. Handing it back
        # on the first read() means a video file never silently loses frame 1.
        self._pending_frame = None
        self._frame_interval = 0.0   # seconds per frame, for paced playback
        self._next_frame_time = None

        # True once a video FILE has run out. main.py treats this as a clean
        # end-of-run, not as a lost camera.
        self.ended = False

        # Filled in by open(), for the HUD / session metadata.
        self.frame_width = 0
        self.frame_height = 0
        self.fps = 0.0

        # -- latest-frame plumbing --
        self.stats = SourceStats()
        self._buffer = LatestFrame(buffer_size)
        self._thread = None
        self._stop = threading.Event()
        # Last problem the reader hit, so the error main.py prints can explain
        # what was actually going wrong.
        self._reader_message = ""

    # -- construction --------------------------------------------------------

    @classmethod
    def from_config(cls, cfg):
        """Build the source described by config.py. The only place that mapping lives."""
        return cls(
            source_type=getattr(cfg, "SOURCE_TYPE", WEBCAM),
            camera_index=getattr(cfg, "CAMERA_INDEX", 0),
            source_url=getattr(cfg, "SOURCE_URL", ""),
            video_path=getattr(cfg, "VIDEO_PATH", ""),
            width=getattr(cfg, "FRAME_WIDTH", None),
            height=getattr(cfg, "FRAME_HEIGHT", None),
            mirror=getattr(cfg, "MIRROR_PREVIEW", False),
            open_timeout=getattr(cfg, "CAMERA_OPEN_TIMEOUT", 5.0),
            realtime=getattr(cfg, "VIDEO_REALTIME", True),
            buffer_size=getattr(cfg, "FRAME_READER_BUFFER_SIZE", 1),
            reconnect=getattr(cfg, "FRAME_SOURCE_RECONNECT", True),
        )

    # -- description ---------------------------------------------------------

    @property
    def is_file(self):
        """True for a recorded video, which is the only source that can 'end'."""
        return self.source_type == VIDEO

    @property
    def uses_reader_thread(self):
        """
        Whether this source is read on its own thread with only the newest frame
        kept.

        Live sources: YES. Frames go stale the instant they arrive, and the
        producer cannot be asked to slow down.

        A recorded file: only when VIDEO_REALTIME is on, i.e. when the file is
        standing in for a live camera and should behave like one. With
        VIDEO_REALTIME off the file is read sequentially and NOTHING is dropped --
        that mode exists for offline analysis, where every frame matters and wall
        -clock time does not.
        """
        if self.source_type in _NETWORK_TYPES or self.source_type == WEBCAM:
            return True
        return self.is_file and self.realtime

    @property
    def description(self):
        """Short human description, e.g. 'CCTV stream rtsp://...' -- for logs."""
        if self.source_type == WEBCAM:
            return "webcam index {}".format(self.camera_index)
        if self.source_type in _NETWORK_TYPES:
            return "{} {}".format(_HUMAN_NAMES[self.source_type], self.source_url)
        if self.source_type == VIDEO:
            return "video file {}".format(self.video_path)
        return "unknown source '{}'".format(self.source_type)

    def describe(self):
        """
        The JSON-safe source block attached to events:
            {"source_type": "cctv"}
        Only the TYPE is exported. A URL can carry camera credentials, and those
        must never end up in an event log that gets shipped to a backend.
        """
        return {"source_type": self.source_type}

    @property
    def stalled_for(self):
        """
        Seconds since the reader last got a frame -- 0.0 before the first one.

        main.py uses this instead of counting empty reads: with a reader thread,
        read() returning nothing usually just means "no NEW frame yet", which is
        completely normal when the pipeline polls faster than the camera produces.
        A source that is really gone shows up as this number growing.
        """
        last = self.stats.last_received_at
        if last is None:
            return 0.0
        return max(0.0, time.monotonic() - last)

    @property
    def reader_message(self):
        """The last problem the reader thread hit, for error reporting."""
        return self._reader_message

    # -- lifecycle -----------------------------------------------------------

    def open(self):
        """
        Open the configured source, verify it really produces frames, and return
        self so it can be chained.

        Raises FrameSourceError with an actionable message on any failure.
        """
        self._validate()

        if self.source_type == WEBCAM:
            self._capture = self._open_webcam()
        elif self.source_type in _NETWORK_TYPES:
            self._capture = self._open_network()
        else:
            self._capture = self._open_video()

        self._read_properties()
        self.ended = False
        self._next_frame_time = None
        self.stats.note_geometry(self.source_type, self.frame_width,
                                 self.frame_height, self.fps)

        if self.uses_reader_thread:
            self._start_reader()

        return self

    def is_opened(self):
        """True while frames can still be read from this source."""
        return self._capture is not None and self._capture.isOpened()

    def read(self):
        """
        Hand the pipeline the freshest frame available.

        Returns (True, frame) or (False, None).

        A False from a LIVE source means "no new frame has arrived since the last
        call" -- normal, and not an error. Use .stalled_for to tell that apart from
        a source that has actually died. For a video file it also sets .ended when
        the recording finishes.

        Mirroring (config.MIRROR_PREVIEW) is applied HERE rather than in the reader,
        so frames that get dropped never cost anything to flip, and detections,
        mouse clicks and the drawn zones still share one coordinate system.
        """
        if self.uses_reader_thread:
            frame, stamp = self._buffer.get()
            if frame is None:
                return False, None
            self.stats.note_processed(max(0.0, time.monotonic() - stamp))
            return True, self._post_process(frame)

        return self._read_sequential()

    def _read_sequential(self):
        """
        The simple path: read the next frame in order, nothing dropped.

        Used for a video file with VIDEO_REALTIME = False. Kept exactly as it was
        so offline analysis of a recording is unchanged and fully deterministic.
        """
        if self._capture is None:
            return False, None

        # The probe frame from open(), handed over on the first call.
        if self._pending_frame is not None:
            frame = self._pending_frame
            self._pending_frame = None
            return True, self._post_process(frame)

        self._wait_for_next_frame()

        ok, frame = self._capture.read()
        if not ok or frame is None or getattr(frame, "size", 0) == 0:
            if self.is_file:
                # A file that stops returning frames has simply finished.
                self.ended = True
            return False, None

        self.stats.note_received()
        self.stats.note_processed(0.0)
        return True, self._post_process(frame)

    def release(self):
        """Hand the device back. Safe to call twice, and safe if open() failed."""
        self._stop_reader()
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        self._pending_frame = None
        self._buffer.clear()

    # -- the reader thread ---------------------------------------------------

    def _start_reader(self):
        """Start the ONE background thread that drains the source."""
        self._stop.clear()
        # The frame open() already probed is the first thing the pipeline sees.
        if self._pending_frame is not None:
            self._buffer.put(self._pending_frame, time.monotonic())
            self.stats.note_received()
            self._pending_frame = None

        self._thread = threading.Thread(
            target=self._reader_loop,
            name="frame-reader",
            daemon=True,     # never keep the process alive on its own
        )
        self._thread.start()

    def _stop_reader(self):
        """Ask the reader to finish and wait briefly for it."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            # capture.read() can block for a moment on a dead network stream;
            # the thread is a daemon, so a slow exit cannot hang the app.
            thread.join(timeout=2.0)
        self._thread = None

    def _reader_loop(self):
        """
        Continuously pull frames and keep only the newest.

        This is the whole point of the file: the loop never waits for the AI
        pipeline, so nothing accumulates in the network/OpenCV buffer. Frames the
        pipeline was too busy to take are REPLACED, which is counted as a drop and
        is entirely expected.

        Nothing in here may raise: a bad frame, a dropped stream or a sleeping
        phone must never take down a running safety monitor.
        """
        failures = 0
        attempt = 0

        while not self._stop.is_set():
            capture = self._capture
            if capture is None:
                break

            try:
                ok, frame = capture.read()
            except cv2.error as exc:
                # Decoder blew up on a corrupt packet. Treat it as a failed read.
                ok, frame = False, None
                self._reader_message = "decode error: {}".format(exc)

            if not ok or frame is None or getattr(frame, "size", 0) == 0:
                if self.is_file:
                    # A recording that stops producing frames has finished.
                    self.ended = True
                    break

                failures += 1
                self.stats.note_read_failure()

                if self.reconnect and failures >= _FAILURES_BEFORE_RECONNECT:
                    delay = _RECONNECT_BACKOFF[min(attempt, len(_RECONNECT_BACKOFF) - 1)]
                    attempt += 1
                    print("[ZONE X] {} stopped delivering frames; "
                          "reconnecting in {:.1f}s (attempt {}).".format(
                              _HUMAN_NAMES.get(self.source_type, "source"),
                              delay, attempt))
                    if self._stop.wait(delay):
                        break
                    if self._reopen():
                        failures = 0
                        attempt = 0
                else:
                    # Brief pause so a broken stream cannot spin a whole core.
                    if self._stop.wait(0.02):
                        break
                continue

            failures = 0
            attempt = 0

            # Pace a recorded file to its own frame rate, so VIDEO_REALTIME still
            # means "plays like the camera it stands in for".
            self._wait_for_next_frame(interruptible=True)

            dropped = self._buffer.put(frame, time.monotonic())
            self.stats.note_received(dropped)

    def _reopen(self):
        """
        Rebuild the capture after a stream dropped. Returns True on success.

        Never raises -- a failed reconnect just means we try again on the next
        pass, and main.py gives up only if SOURCE_STALL_TIMEOUT elapses with no
        frames at all.
        """
        try:
            if self._capture is not None:
                self._capture.release()
        except cv2.error:
            pass
        self._capture = None

        try:
            if self.source_type == WEBCAM:
                capture = self._open_webcam()
            elif self.source_type in _NETWORK_TYPES:
                capture = self._open_network()
            else:
                capture = self._open_video()
        except FrameSourceError as exc:
            self._reader_message = str(exc).splitlines()[0]
            return False

        self._capture = capture
        self._next_frame_time = None
        self.stats.note_reconnect()

        # The probe frame from _open_* is already the newest thing available.
        if self._pending_frame is not None:
            self._buffer.put(self._pending_frame, time.monotonic())
            self.stats.note_received()
            self._pending_frame = None

        print("[ZONE X] Reconnected to {}.".format(self.description))
        return True

    # -- internals: validation ------------------------------------------------

    def _validate(self):
        """Catch config mistakes before touching OpenCV, with a clear message."""
        if self.source_type not in SOURCE_TYPES:
            raise FrameSourceError(
                "Unknown SOURCE_TYPE '{}' in config.py.\n"
                "  Valid values: {}".format(
                    self.source_type, ", ".join('"{}"'.format(t) for t in SOURCE_TYPES))
            )

        if self.source_type == WEBCAM and not isinstance(self.camera_index, int):
            raise FrameSourceError(
                "CAMERA_INDEX must be a whole number such as 0 or 1, "
                "but it is {!r}.\n"
                "  For a phone or network camera use:\n"
                '      SOURCE_TYPE = "ip"\n'
                '      SOURCE_URL  = "http://<phone-ip>:8080/video"'
                .format(self.camera_index)
            )

        if self.source_type in _NETWORK_TYPES and not self.source_url:
            raise FrameSourceError(
                'SOURCE_TYPE is "{}" but SOURCE_URL is empty in config.py.\n'
                "  Set it to the stream address, for example:\n"
                "      {}".format(
                    self.source_type,
                    'SOURCE_URL = "rtsp://user:pass@192.168.1.50:554/stream1"'
                    if self.source_type == CCTV
                    else 'SOURCE_URL = "http://192.168.1.42:8080/video"')
            )

        if self.source_type == VIDEO:
            if not self.video_path:
                raise FrameSourceError(
                    'SOURCE_TYPE is "video" but VIDEO_PATH is empty in config.py.\n'
                    '  Set it to a recording, for example:\n'
                    '      VIDEO_PATH = "videos/factory_test.mp4"'
                )
            if not os.path.isfile(self.video_path):
                raise FrameSourceError(
                    "Video file not found.\n"
                    "  VIDEO_PATH: {}\n"
                    "  Looked in:  {}\n"
                    "  Put the recording there, or fix VIDEO_PATH in config.py."
                    .format(self.video_path, os.path.abspath(self.video_path))
                )

    # -- internals: opening ---------------------------------------------------

    def _open_webcam(self):
        """
        Open a local camera, trying the Windows backends that actually work.

        DirectShow first: the default MSMF backend on Windows often takes several
        seconds to open a webcam, or fails outright while another app holds it.
        """
        backends = []
        if sys.platform.startswith("win"):
            backends = [("DSHOW", cv2.CAP_DSHOW), ("MSMF", cv2.CAP_MSMF)]
        backends.append(("DEFAULT", cv2.CAP_ANY))

        last_error = "no backend accepted the camera"

        for name, backend in backends:
            capture = cv2.VideoCapture(self.camera_index, backend)
            if not self._await_open(capture):
                capture.release()
                last_error = "index {} could not be opened".format(self.camera_index)
                continue

            self._apply_resolution(capture)
            self._minimise_buffering(capture)

            ok, frame = capture.read()
            if not ok or frame is None or frame.size == 0:
                capture.release()
                last_error = ("the camera opened but returned no frames "
                              "(in use by another app?)")
                continue

            self._pending_frame = frame
            self._backend_name = name
            return capture

        raise FrameSourceError(
            "Could not open the webcam (index {index}).\n"
            "  Reason: {reason}\n"
            "  Try:\n"
            "    * close Zoom / Teams / the Camera app\n"
            "    * set a different CAMERA_INDEX in config.py (1, 2, ...)\n"
            "    * check Windows camera permissions\n"
            "    * or switch to another source entirely:\n"
            '        SOURCE_TYPE = "video"   VIDEO_PATH = "videos/factory_test.mp4"'
            .format(index=self.camera_index, reason=last_error)
        )

    def _open_network(self):
        """Open an HTTP (phone) or RTSP (CCTV) stream."""
        is_cctv = self.source_type == CCTV
        print("[ZONE X] Opening {}: {}".format(
            _HUMAN_NAMES[self.source_type], self.source_url))

        capture = cv2.VideoCapture(self.source_url)

        if not self._await_open(capture):
            capture.release()
            raise FrameSourceError(self._network_error(
                "Could not open CCTV stream." if is_cctv
                else "Could not connect to IP camera."))

        self._apply_resolution(capture)
        self._minimise_buffering(capture)

        ok, frame = capture.read()
        if not ok or frame is None or frame.size == 0:
            capture.release()
            raise FrameSourceError(self._network_error(
                "The {} connected but produced no frames.".format(
                    _HUMAN_NAMES[self.source_type])))

        self._pending_frame = frame
        self._backend_name = "URL"
        return capture

    def _network_error(self, headline):
        """Shared advice block for the two network source types."""
        if self.source_type == CCTV:
            hints = ("    * check the RTSP path -- most cameras need a full stream\n"
                     "      path such as /Streaming/Channels/101 or /stream1\n"
                     "    * check the username / password in the URL\n"
                     "    * confirm the camera answers: ping its IP address\n"
                     "    * some cameras only allow one client at a time")
        else:
            hints = ("    * confirm the phone and this computer are on the SAME wifi\n"
                     "    * open the URL in a browser on this computer first\n"
                     "    * check the port and the /video suffix\n"
                     "    * some phone apps stop streaming when the screen locks")
        return ("{headline}\n"
                "  URL: {url}\n"
                "  Try:\n{hints}".format(
                    headline=headline, url=self.source_url, hints=hints))

    def _open_video(self):
        """Open a recorded file. Existence was already checked by _validate()."""
        print("[ZONE X] Opening video file: {}".format(self.video_path))

        capture = cv2.VideoCapture(self.video_path)

        if not capture.isOpened():
            capture.release()
            raise FrameSourceError(
                "Could not open the video file.\n"
                "  VIDEO_PATH: {}\n"
                "  The file exists but OpenCV cannot decode it. Try:\n"
                "    * re-encode it as H.264 mp4\n"
                "    * check the file is not still being written / is not 0 bytes"
                .format(self.video_path)
            )

        ok, frame = capture.read()
        if not ok or frame is None or frame.size == 0:
            capture.release()
            raise FrameSourceError(
                "The video file opened but contains no readable frames.\n"
                "  VIDEO_PATH: {}".format(self.video_path)
            )

        self._pending_frame = frame
        self._backend_name = "FILE"
        return capture

    def _await_open(self, capture):
        """Give a device or stream up to open_timeout seconds to come up."""
        deadline = time.time() + self.open_timeout
        while not capture.isOpened() and time.time() < deadline:
            time.sleep(0.05)
        return capture.isOpened()

    def _apply_resolution(self, capture):
        """Ask for the configured capture size. Cameras are free to ignore it."""
        if self.width:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        if self.height:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

    def _minimise_buffering(self, capture):
        """
        Ask the driver to hold as few frames as possible.

        Many backends (especially MJPEG-over-HTTP) simply ignore this, which is why
        the reader thread exists -- that is what actually keeps us on the newest
        frame. This is a cheap extra, not the mechanism.
        """
        try:
            capture.set(cv2.CAP_PROP_BUFFERSIZE, max(1, self._buffer.depth))
        except cv2.error:
            pass

    def _read_properties(self):
        """Record the size / rate we actually got, and set up paced playback."""
        capture = self._capture
        probe = self._pending_frame

        self.frame_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        self.frame_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if probe is not None:
            # The probe frame is the truth; the properties are only a hint.
            self.frame_height, self.frame_width = probe.shape[:2]

        fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
        # Webcams frequently report 0 or nonsense here.
        self.fps = float(fps) if 0.0 < fps < 240.0 else 0.0

        self._frame_interval = 0.0
        if self.is_file and self.realtime and self.fps > 0.0:
            self._frame_interval = 1.0 / self.fps

        print("[ZONE X] Source ready: {} ({}x{}{}) via {}".format(
            self.description, self.frame_width, self.frame_height,
            ", {:.0f} FPS".format(self.fps) if self.fps else "",
            self._backend_name))

    def _wait_for_next_frame(self, interruptible=False):
        """
        Pace a video file to its recorded frame rate.

        Without this a file decodes as fast as the CPU allows, so a 5-second
        danger dwell -- which is measured on the wall clock -- would need many
        more video frames than it does on a live camera, and the recording would
        escalate on a schedule the real floor never sees. Live sources are paced
        by the hardware, so this does nothing for them.

        `interruptible` makes the wait respond to release() immediately, which the
        reader thread needs so shutdown is not delayed by a slow frame rate.
        """
        if self._frame_interval <= 0.0:
            return

        now = time.perf_counter()
        if self._next_frame_time is None:
            self._next_frame_time = now + self._frame_interval
            return

        remaining = self._next_frame_time - now
        if remaining > 0:
            if interruptible:
                self._stop.wait(remaining)
            else:
                time.sleep(remaining)
            self._next_frame_time += self._frame_interval
        else:
            # Behind schedule (slow inference): do not try to catch up, just
            # resync, otherwise the delay compounds into a stutter.
            self._next_frame_time = now + self._frame_interval

    def _post_process(self, frame):
        """The one transform applied to every frame, whatever the source."""
        if self.mirror:
            frame = cv2.flip(frame, 1)
        return frame
