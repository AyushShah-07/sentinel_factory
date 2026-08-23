"""
src/frame_source.py
WHERE FRAMES COME FROM - Handles cameras, video files, and network streams
"""

import collections
import os
import sys
import threading
import time
import cv2

# Source type names used in config
WEBCAM = "webcam"
IP = "ip" 
CCTV = "cctv"
VIDEO = "video"

SOURCE_TYPES = (WEBCAM, IP, CCTV, VIDEO)
_NETWORK_TYPES = (IP, CCTV)  # Sources that use URLs

_HUMAN_NAMES = {  # Friendly names for display
    WEBCAM: "webcam",
    IP: "IP camera",
    CCTV: "CCTV stream",
    VIDEO: "video file",
}

_FAILURES_BEFORE_RECONNECT = 5  # Bad frames before trying to reconnect
_RECONNECT_BACKOFF = (0.5, 1.0, 2.0, 4.0)  # Wait times between reconnect attempts
_FPS_WINDOW = 60  # How many frames to average for FPS


class FrameSourceError(Exception):
    """Error when camera/video can't open - with helpful message for user"""
    pass


class LatestFrame:
    """Stores only the newest frame - older ones are dropped"""

    def __init__(self, depth=1):
        # Keep only 'depth' number of frames (default 1 = just the newest)
        self.depth = max(1, int(depth))
        self._frames = collections.deque(maxlen=self.depth)  # Storage
        self._lock = threading.Lock()  # Prevents thread conflicts

    def put(self, frame, stamp):
        """Store a frame. Returns how many old frames were dropped"""
        with self._lock:
            dropped = 1 if len(self._frames) == self.depth else 0
            self._frames.append((frame, stamp))  # Add new frame
            return dropped

    def get(self):
        """Get the newest frame and remove it from storage"""
        with self._lock:
            if not self._frames:
                return None, None
            return self._frames.popleft()  # Return and remove

    def clear(self):
        """Delete all stored frames"""
        with self._lock:
            self._frames.clear()

    def __len__(self):
        """Return how many frames are stored"""
        with self._lock:
            return len(self._frames)


class SourceStats:
    """Tracks performance numbers: FPS, dropped frames, delays"""

    def __init__(self, window=_FPS_WINDOW):
        self._lock = threading.Lock()
        # Source info
        self.source_type = ""
        self.width = 0
        self.height = 0
        self.reported_fps = 0.0
        
        # Counters
        self.received = 0      # Frames read from source
        self.processed = 0     # Frames given to AI
        self.dropped = 0       # Frames replaced before processing
        self.read_failures = 0 # Bad/empty reads
        self.reconnects = 0
        
        # Timing
        self.buffer_age = 0.0      # How long frame waited in buffer
        self.process_time = 0.0    # How long AI took on last frame
        self.last_received_at = None
        self._received_times = collections.deque(maxlen=window)
        self._processed_times = collections.deque(maxlen=window)

    def note_received(self, dropped=0, now=None):
        """Record that a frame arrived from the source"""
        now = time.monotonic() if now is None else now
        with self._lock:
            self.received += 1
            self.dropped += dropped
            self.last_received_at = now
            self._received_times.append(now)

    def note_read_failure(self):
        """Record a failed read attempt"""
        with self._lock:
            self.read_failures += 1

    def note_reconnect(self):
        """Record that we reconnected to the source"""
        with self._lock:
            self.reconnects += 1

    def note_geometry(self, source_type, width, height, reported_fps):
        """Store source resolution and claimed FPS"""
        with self._lock:
            self.source_type = source_type
            self.width = int(width or 0)
            self.height = int(height or 0)
            self.reported_fps = float(reported_fps or 0.0)

    def note_processed(self, buffer_age, now=None):
        """Record that a frame was sent to AI processing"""
        now = time.monotonic() if now is None else now
        with self._lock:
            self.processed += 1
            self.buffer_age = float(buffer_age)
            self._processed_times.append(now)

    def note_process_time(self, seconds):
        """Record how long AI took on last frame"""
        with self._lock:
            self.process_time = float(seconds)

    @staticmethod
    def _rate(times):
        """Calculate FPS from a list of timestamps"""
        if len(times) < 2:
            return 0.0
        span = times[-1] - times[0]
        return (len(times) - 1) / span if span > 0 else 0.0

    @property
    def source_fps(self):
        """Measured arrival rate (not what camera claims)"""
        with self._lock:
            return self._rate(self._received_times)

    @property
    def process_fps(self):
        """Measured AI processing rate"""
        with self._lock:
            return self._rate(self._processed_times)

    @property
    def drop_rate(self):
        """Fraction of frames dropped (0.0 to 1.0)"""
        with self._lock:
            return self.dropped / self.received if self.received else 0.0

    def snapshot(self):
        """Return all stats as a dictionary"""
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
        """One-line summary for console output"""
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
        """Short stats for on-screen display"""
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
    """Decides when to process next frame (time-based, not frame count)"""

    _TOLERANCE = 1e-6  # Small fudge factor for floating point

    def __init__(self, target_fps=None):
        self.target_fps = float(target_fps) if target_fps else None
        self.interval = 1.0 / self.target_fps if self.target_fps else 0.0
        self._next_due = None  # When next frame is due

    @property
    def enabled(self):
        """True if scheduler is active (target_fps is set)"""
        return self.interval > 0.0

    def due(self, now=None):
        """True when it's time to process another frame"""
        if not self.enabled or self._next_due is None:
            return True
        now = time.monotonic() if now is None else now
        return now >= (self._next_due - self._TOLERANCE)

    def mark(self, now=None):
        """Record that we just took a frame for processing"""
        now = time.monotonic() if now is None else now
        if not self.enabled:
            self._next_due = now
            return
        if self._next_due is None:
            self._next_due = now + self.interval
            return
        self._next_due += self.interval
        if self._next_due < now:
            # Behind schedule - resync instead of catching up
            self._next_due = now + self.interval

    def seconds_until_due(self, now=None):
        """How long until next frame slot (0.0 if due now)"""
        if not self.enabled or self._next_due is None:
            return 0.0
        now = time.monotonic() if now is None else now
        return max(0.0, self._next_due - now)


class FrameSource:
    """
    Main class that handles all video sources (webcam, IP, CCTV, video file)
    """

    def __init__(self, source_type=WEBCAM, camera_index=0, source_url="",
                 video_path="", width=None, height=None, mirror=False,
                 open_timeout=5.0, realtime=True, buffer_size=1, reconnect=True):
        # Source settings from config
        self.source_type = str(source_type or "").strip().lower()
        self.camera_index = camera_index
        self.source_url = (source_url or "").strip()
        self.video_path = (video_path or "").strip()
        self.width = width
        self.height = height
        self.mirror = bool(mirror)  # Flip image horizontally
        self.open_timeout = float(open_timeout)  # Max seconds to wait
        self.realtime = bool(realtime)  # For video files - play at normal speed
        self.reconnect = bool(reconnect)  # Try to reconnect if stream dies
        
        self._capture = None  # OpenCV VideoCapture object
        self._backend_name = ""
        self._pending_frame = None  # First frame read at open time
        self._frame_interval = 0.0  # Seconds per frame (for paced playback)
        self._next_frame_time = None
        
        self.ended = False  # True for video files that finished playing
        
        self.frame_width = 0
        self.frame_height = 0
        self.fps = 0.0
        
        # Reader thread for live sources
        self.stats = SourceStats()
        self._buffer = LatestFrame(buffer_size)
        self._thread = None
        self._stop = threading.Event()
        self._reader_message = ""

    @classmethod
    def from_config(cls, cfg):
        """Create FrameSource using settings from config.py"""
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

    @property
    def is_file(self):
        """True if source is a video file"""
        return self.source_type == VIDEO

    @property
    def uses_reader_thread(self):
        """
        True if source needs a background thread to drain frames
        (live sources YES, video files only if VIDEO_REALTIME is True)
        """
        if self.source_type in _NETWORK_TYPES or self.source_type == WEBCAM:
            return True
        return self.is_file and self.realtime

    @property
    def description(self):
        """Short description for logs"""
        if self.source_type == WEBCAM:
            return "webcam index {}".format(self.camera_index)
        if self.source_type in _NETWORK_TYPES:
            return "{} {}".format(_HUMAN_NAMES[self.source_type], self.source_url)
        if self.source_type == VIDEO:
            return "video file {}".format(self.video_path)
        return "unknown source '{}'".format(self.source_type)

    def describe(self):
        """Returns source info for event logs (no URL because it might have passwords)"""
        return {"source_type": self.source_type}

    @property
    def stalled_for(self):
        """Seconds since last frame arrived (0.0 before first frame)"""
        last = self.stats.last_received_at
        if last is None:
            return 0.0
        return max(0.0, time.monotonic() - last)

    @property
    def reader_message(self):
        """Last error message from reader thread"""
        return self._reader_message

    def open(self):
        """Open the source and verify it works. Returns self for chaining."""
        self._validate()  # Check config settings first
        
        # Open the right type of source
        if self.source_type == WEBCAM:
            self._capture = self._open_webcam()
        elif self.source_type in _NETWORK_TYPES:
            self._capture = self._open_network()
        else:
            self._capture = self._open_video()
        
        self._read_properties()  # Get resolution and FPS
        self.ended = False
        self._next_frame_time = None
        self.stats.note_geometry(self.source_type, self.frame_width,
                                 self.frame_height, self.fps)
        
        # Start background reader for live sources
        if self.uses_reader_thread:
            self._start_reader()
        
        return self

    def is_opened(self):
        """True if source is still open and working"""
        return self._capture is not None and self._capture.isOpened()

    def read(self):
        """
        Get the newest frame. Returns (True, frame) or (False, None).
        For live sources, False means "no new frame yet" (not an error).
        """
        if self.uses_reader_thread:
            # Get frame from background thread
            frame, stamp = self._buffer.get()
            if frame is None:
                return False, None
            self.stats.note_processed(max(0.0, time.monotonic() - stamp))
            return True, self._post_process(frame)
        
        # For video files without reader thread
        return self._read_sequential()

    def _read_sequential(self):
        """Simple sequential reading for video files (no dropping)"""
        if self._capture is None:
            return False, None
        
        # Return the probe frame from open() on first call
        if self._pending_frame is not None:
            frame = self._pending_frame
            self._pending_frame = None
            return True, self._post_process(frame)
        
        self._wait_for_next_frame()  # Pace to video speed
        
        ok, frame = self._capture.read()
        if not ok or frame is None or getattr(frame, "size", 0) == 0:
            if self.is_file:
                self.ended = True  # Video finished
            return False, None
        
        self.stats.note_received()
        self.stats.note_processed(0.0)
        return True, self._post_process(frame)

    def release(self):
        """Close the source and stop any background threads"""
        self._stop_reader()
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        self._pending_frame = None
        self._buffer.clear()

    def _start_reader(self):
        """Start background thread that continuously reads frames"""
        self._stop.clear()
        # Put the probe frame from open() into buffer
        if self._pending_frame is not None:
            self._buffer.put(self._pending_frame, time.monotonic())
            self.stats.note_received()
            self._pending_frame = None
        
        self._thread = threading.Thread(
            target=self._reader_loop,
            name="frame-reader",
            daemon=True,
        )
        self._thread.start()

    def _stop_reader(self):
        """Stop the background reader thread"""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def _reader_loop(self):
        """
        Background thread that continuously reads frames and keeps only newest.
        This prevents frames from piling up in the OpenCV buffer.
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
                ok, frame = False, None
                self._reader_message = "decode error: {}".format(exc)
            
            if not ok or frame is None or getattr(frame, "size", 0) == 0:
                if self.is_file:
                    self.ended = True  # Video finished
                    break
                
                failures += 1
                self.stats.note_read_failure()
                
                # Try to reconnect after too many failures
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
                    # Brief pause to not spin CPU on dead stream
                    if self._stop.wait(0.02):
                        break
                continue
            
            failures = 0
            attempt = 0
            
            # Pace video file to normal speed
            self._wait_for_next_frame(interruptible=True)
            
            # Store newest frame, drop oldest if buffer full
            dropped = self._buffer.put(frame, time.monotonic())
            self.stats.note_received(dropped)

    def _reopen(self):
        """Try to reconnect after stream died. Returns True if successful."""
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
        
        # Put probe frame into buffer
        if self._pending_frame is not None:
            self._buffer.put(self._pending_frame, time.monotonic())
            self.stats.note_received()
            self._pending_frame = None
        
        print("[ZONE X] Reconnected to {}.".format(self.description))
        return True

    def _validate(self):
        """Check config for errors before trying to open source"""
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

    def _open_webcam(self):
        """Open a local USB webcam"""
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
                last_error = "the camera opened but returned no frames"
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
        """Open an IP camera or phone stream"""
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
        """Helpful error message for network sources"""
        if self.source_type == CCTV:
            hints = ("    * check the RTSP path\n"
                     "    * check username/password in URL\n"
                     "    * confirm camera answers: ping its IP\n"
                     "    * some cameras only allow one client at a time")
        else:
            hints = ("    * phone and computer on SAME wifi\n"
                     "    * open URL in browser first to test\n"
                     "    * check port and /video suffix\n"
                     "    * phone app stops when screen locks")
        return ("{headline}\n"
                "  URL: {url}\n"
                "  Try:\n{hints}".format(
                    headline=headline, url=self.source_url, hints=hints))

    def _open_video(self):
        """Open a video file"""
        print("[ZONE X] Opening video file: {}".format(self.video_path))
        
        capture = cv2.VideoCapture(self.video_path)
        
        if not capture.isOpened():
            capture.release()
            raise FrameSourceError(
                "Could not open the video file.\n"
                "  VIDEO_PATH: {}\n"
                "  Try:\n"
                "    * re-encode it as H.264 mp4\n"
                "    * check file is not 0 bytes"
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
        """Wait up to open_timeout seconds for camera/stream to open"""
        deadline = time.time() + self.open_timeout
        while not capture.isOpened() and time.time() < deadline:
            time.sleep(0.05)
        return capture.isOpened()

    def _apply_resolution(self, capture):
        """Request specific resolution from camera (camera may ignore)"""
        if self.width:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        if self.height:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

    def _minimise_buffering(self, capture):
        """Ask driver to hold minimal frames (ignored by many backends)"""
        try:
            capture.set(cv2.CAP_PROP_BUFFERSIZE, max(1, self._buffer.depth))
        except cv2.error:
            pass

    def _read_properties(self):
        """Get actual resolution and FPS from the source"""
        capture = self._capture
        probe = self._pending_frame
        
        self.frame_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        self.frame_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if probe is not None:
            # Probe frame is the truth, properties are just hints
            self.frame_height, self.frame_width = probe.shape[:2]
        
        fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
        self.fps = float(fps) if 0.0 < fps < 240.0 else 0.0
        
        # For video files playing in realtime, pace to original speed
        self._frame_interval = 0.0
        if self.is_file and self.realtime and self.fps > 0.0:
            self._frame_interval = 1.0 / self.fps
        
        print("[ZONE X] Source ready: {} ({}x{}{}) via {}".format(
            self.description, self.frame_width, self.frame_height,
            ", {:.0f} FPS".format(self.fps) if self.fps else "",
            self._backend_name))

    def _wait_for_next_frame(self, interruptible=False):
        """
        Wait to pace video file to normal speed.
        Without this, video would play as fast as CPU allows.
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
                self._stop.wait(remaining)  # Can be interrupted by stop
            else:
                time.sleep(remaining)
            self._next_frame_time += self._frame_interval
        else:
            # Behind schedule - don't catch up, just resync
            self._next_frame_time = now + self._frame_interval

    def _post_process(self, frame):
        """Apply mirroring to every frame before sending to pipeline"""
        if self.mirror:
            frame = cv2.flip(frame, 1)
        return frame