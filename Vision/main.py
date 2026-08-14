"""
ZONE X - Smart Safety Monitoring System
Phase 1: Local Computer Vision Prototype

main.py
-------

Application entry point and pipeline orchestrator.

Pipeline:

    FrameSource
        ↓
    PersonDetector
        ↓
    PersonTracker
        ↓
    ZoneManager
        ↓
    Pose / WristDetector
        ↓
    RiskEngine
        ↓
    EventLog
        ↓
    Alarm
        ↓
    UI Rendering
        ↓
    ┌──────────────────────────────┐
    │                              │
    ↓                              ↓
OpenCV Window                 FastAPI Dashboard
                                   ↓
                            FrameBroadcaster

Event pipeline:

    EventLog
        ↓
    Local JSON
        ↓
    Incident Image
        ↓
    BackendClient
        ↓
    Spring Boot Backend

IMPORTANT:

- main.py contains orchestration only.
- Camera/input handling remains inside FrameSource.
- Detection remains inside PersonDetector.
- Tracking remains inside PersonTracker.
- Zone handling remains inside ZoneManager.
- Risk decisions remain inside RiskEngine.
- Event creation remains inside EventLog.
- Alarm handling remains inside Alarm.
- Backend communication remains inside BackendClient.
- Web streaming remains inside FrameBroadcaster/WebServer.

Run:

    python main.py


Keyboard:

    s       add SAFE zone
    w       add WARNING zone
    d       add DANGER zone
    r       add DANGER zone
    Enter   finish polygon
    c       clear zones
    m       machine ON/OFF
    p       pause/resume
    q       quit
    Esc     quit
"""

import collections
import os
import sys
import time

import cv2

import config

from src.visualization import ui

from src.alarm.alarm import Alarm

from src.detection.detector import (
    ModelLoadError,
    PersonDetector,
)

from src.detection.tracker import (
    PersonTracker,
)

from src.events.events import (
    EventLog,
)

from src.camera.frame_source import (
    FrameSource,
    FrameSourceError,
    ProcessingScheduler,
)

from src.camera.frame_broadcaster import (
    FrameBroadcaster,
)

from src.fastapi.web_server import (
    WebServer,
)

from src.safety.risk_engine import (
    RiskEngine,
)

from src.zones.zone import (
    ZoneManager,
)

from src.integration.connection import (
    BackendClient,
)


# ============================================================================
# KEY CODES
# ============================================================================

# OpenCV reports Enter as 13 on Windows and 10 on some Linux builds.
KEY_ENTER = (13, 10)

# Escape key.
KEY_ESC = 27


# ============================================================================
# FPS METER
# ============================================================================

class FpsMeter:
    """
    Rolling-average FPS meter.

    A rolling average is used instead of calculating FPS from a single
    frame because a single-frame calculation can jump significantly.
    """

    def __init__(self, window=30):
        self._durations = collections.deque(
            maxlen=max(1, int(window))
        )

        self._last_time = None

    def tick(self):
        """
        Register one processed frame.
        """

        now = time.perf_counter()

        if self._last_time is not None:
            self._durations.append(
                now - self._last_time
            )

        self._last_time = now

    @property
    def fps(self):
        """
        Return rolling-average FPS.
        """

        if not self._durations:
            return 0.0

        average = (
            sum(self._durations)
            / len(self._durations)
        )

        if average <= 0:
            return 0.0

        return 1.0 / average


# ============================================================================
# INCIDENT OFFENDER
# ============================================================================

def _incident_offender(incident):
    """
    Create a minimal person-like object from an open warning incident.

    This is used when a worker responsible for a dwell violation is no longer
    visible in the current frame.

    Only fields required by the alarm/event pipeline are included.
    """

    return {
        "id": incident.track_id,

        "person_id": incident.person_id,

        "zone": incident.zone_type,

        "zone_id": incident.zone_id,

        "hand_zone": None,

        "hand_zone_id": None,

        "hand_in_danger": False,

        "danger_duration": 0.0,

        "distance_to_danger": None,
    }


# ============================================================================
# APPLICATION
# ============================================================================

class ZoneXApp:
    """
    Main Zone X application.

    This class is intentionally responsible for orchestration only.

    Main processing pipeline:

        frame
          ↓
        detection
          ↓
        tracking
          ↓
        zone evaluation
          ↓
        pose / hand detection
          ↓
        risk evaluation
          ↓
        event logging
          ↓
        alarm
          ↓
        rendering
          ↓
        OpenCV + Web Dashboard
    """

    # ------------------------------------------------------------------------
    # INIT
    # ------------------------------------------------------------------------

    def __init__(self, cfg):

        self.cfg = cfg

        # --------------------------------------------------------------------
        # Frame source
        # --------------------------------------------------------------------

        self.source = None

        # --------------------------------------------------------------------
        # Web dashboard
        # --------------------------------------------------------------------

        self.frame_broadcaster = FrameBroadcaster(
            jpeg_quality=getattr(
                cfg,
                "WEB_STREAM_JPEG_QUALITY",
                80,
            )
        )

        self.web_server = None

        # --------------------------------------------------------------------
        # Processing scheduler
        # --------------------------------------------------------------------

        self.schedule = ProcessingScheduler(None)

        # --------------------------------------------------------------------
        # Application state
        # --------------------------------------------------------------------

        self.running = False

        self.paused = False

        self.frame_index = 0

        # --------------------------------------------------------------------
        # Latest frame/results
        # --------------------------------------------------------------------

        self.last_frame = None

        self.people = []

        self.risks = {}

        self.worst_id = None

        self.worst = None

        # --------------------------------------------------------------------
        # Alert timing
        # --------------------------------------------------------------------

        self.last_violation_time = 0.0

        self.last_critical_time = 0.0

        # --------------------------------------------------------------------
        # Simulated machine state
        # --------------------------------------------------------------------

        self.machine_on = bool(
            cfg.MACHINE_ON
        )

        # --------------------------------------------------------------------
        # FPS
        # --------------------------------------------------------------------

        self.fps_meter = FpsMeter(
            cfg.FPS_SMOOTHING_WINDOW
        )

        # --------------------------------------------------------------------
        # Console statistics
        # --------------------------------------------------------------------

        self._last_stats_print = 0.0

        # --------------------------------------------------------------------
        # Zones
        # --------------------------------------------------------------------

        self.zones = ZoneManager(cfg)

        # --------------------------------------------------------------------
        # Tracker
        # --------------------------------------------------------------------

        self.tracker = PersonTracker(
            enabled=cfg.ENABLE_TRACKING,

            max_distance=cfg.TRACK_MAX_DISTANCE,

            max_missing_frames=(
                cfg.TRACK_MAX_MISSING_FRAMES
            ),

            zone_margin_px=cfg.ZONE_MARGIN_PX,

            enter_confirm_frames=(
                cfg.ENTER_CONFIRM_FRAMES
            ),

            exit_confirm_frames=(
                cfg.EXIT_CONFIRM_FRAMES
            ),

            hand_margin_px=cfg.HAND_MARGIN_PX,

            hand_enter_confirm_frames=(
                cfg.HAND_ENTER_CONFIRM_FRAMES
            ),

            hand_exit_confirm_frames=(
                cfg.HAND_EXIT_CONFIRM_FRAMES
            ),
        )

        # --------------------------------------------------------------------
        # Risk engine
        # --------------------------------------------------------------------

        self.risk_engine = RiskEngine(cfg)

        # --------------------------------------------------------------------
        # Event log
        # --------------------------------------------------------------------

        self.events = EventLog(
            max_events=cfg.MAX_STORED_EVENTS,

            danger_time_threshold=(
                cfg.DANGER_TIME_THRESHOLD_SEC
            ),

            critical_threshold=(
                cfg.RISK_CRITICAL_THRESHOLD
            ),

            # EventLog creates the event.
            #
            # _handle_event() only forwards it to the backend.
            on_event=self._handle_event,

            source_type=cfg.SOURCE_TYPE,

            schema_version=cfg.EVENT_SCHEMA_VERSION,

            log_risk_changes=cfg.LOG_RISK_CHANGES,

            log_root=cfg.LOG_ROOT,

            warning_dwell_threshold=(
                cfg.WARNING_DWELL_THRESHOLD_SEC
            ),

            incident_dir=cfg.INCIDENT_DIR,

            save_incident_images=(
                cfg.SAVE_INCIDENT_IMAGES
            ),
        )

        # --------------------------------------------------------------------
        # Spring Boot backend
        # --------------------------------------------------------------------

        self.backend = BackendClient(
            base_url=getattr(
                cfg,
                "BACKEND_URL",
                "http://localhost:8080",
            )
        )

        # --------------------------------------------------------------------
        # PC alarm
        # --------------------------------------------------------------------

        self.alarm = Alarm.from_config(cfg)

        # --------------------------------------------------------------------
        # Models
        # --------------------------------------------------------------------

        self.detector = None

        self.pose = None

    # ========================================================================
    # BACKEND EVENT INTEGRATION
    # ========================================================================

    def _handle_event(self, record):
        """
        Receive an already-created EventLog record.

        EventLog remains responsible for:

        - detecting events
        - creating the event record
        - writing JSON
        - saving evidence images

        This method only:

        - prints the event
        - forwards the event to Spring Boot

        Backend failure never stops the CV safety pipeline.
        """

        # --------------------------------------------------------------------
        # Console logging
        # --------------------------------------------------------------------

        if self.cfg.LOG_EVENTS_TO_CONSOLE:

            self._print_event(
                record
            )

        # --------------------------------------------------------------------
        # Backend integration
        # --------------------------------------------------------------------

        if self.backend is None:
            return

        try:

            self.backend.send_incident(
                record
            )

            print(
                "[BACKEND] Event sent successfully: {}".format(
                    record.get(
                        "event_type",
                        "UNKNOWN",
                    )
                )
            )

        except Exception as exc:

            print(
                "[BACKEND] Failed to send event: {}".format(
                    exc
                ),
                file=sys.stderr,
            )

    # ========================================================================
    # SETUP
    # ========================================================================

    def setup(self):
        """
        Initialize models, frame source, web server, logging and UI.
        """

        # --------------------------------------------------------------------
        # Person detector
        # --------------------------------------------------------------------

        self.detector = PersonDetector(
            model_path=self.cfg.MODEL_PATH,

            download_name=(
                self.cfg.MODEL_DOWNLOAD_NAME
            ),

            confidence_threshold=(
                self.cfg.CONFIDENCE_THRESHOLD
            ),

            iou_threshold=(
                self.cfg.IOU_THRESHOLD
            ),

            person_class_id=(
                self.cfg.PERSON_CLASS_ID
            ),

            imgsz=(
                self.cfg.INFERENCE_IMGSZ
            ),

            device=(
                self.cfg.DEVICE
            ),

            verbose=(
                self.cfg.VERBOSE_INFERENCE
            ),
        )

        # --------------------------------------------------------------------
        # Pose model
        # --------------------------------------------------------------------

        self._setup_pose()

        # --------------------------------------------------------------------
        # Frame source
        # --------------------------------------------------------------------

        self.source = (
            FrameSource
            .from_config(self.cfg)
            .open()
        )

        # --------------------------------------------------------------------
        # Processing scheduler
        # --------------------------------------------------------------------

        self.schedule = ProcessingScheduler(
            self.cfg.TARGET_PROCESSING_FPS
            if self.source.uses_reader_thread
            else None
        )

        # --------------------------------------------------------------------
        # Web dashboard
        # --------------------------------------------------------------------

        self._setup_web_server()

        # --------------------------------------------------------------------
        # Logging session
        # --------------------------------------------------------------------

        self._open_log_session()

        # --------------------------------------------------------------------
        # OpenCV window
        # --------------------------------------------------------------------

        cv2.namedWindow(
            self.cfg.WINDOW_NAME,
            cv2.WINDOW_NORMAL,
        )

        cv2.resizeWindow(
            self.cfg.WINDOW_NAME,
            self.cfg.WINDOW_WIDTH,
            self.cfg.WINDOW_HEIGHT,
        )

        cv2.setMouseCallback(
            self.cfg.WINDOW_NAME,
            self.zones.handle_mouse,
        )

        # --------------------------------------------------------------------
        # Startup messages
        # --------------------------------------------------------------------

        if self.schedule.enabled:

            print(
                "[ZONE X] Processing target: {:.0f} FPS "
                "(newest frame only; older frames are dropped by design)."
                .format(
                    self.schedule.target_fps
                )
            )

        else:

            print(
                "[ZONE X] Processing every frame in order "
                "(VIDEO_REALTIME = False)."
            )

        print_controls()

    # ========================================================================
    # WEB SERVER
    # ========================================================================

    def _setup_web_server(self):
        """
        Start the FastAPI dashboard server.

        The WebServer receives frames from FrameBroadcaster.

        main.py never handles HTTP directly.
        """

        enabled = getattr(
            self.cfg,
            "WEB_SERVER_ENABLED",
            True,
        )

        if not enabled:

            print(
                "[ZONE X] Web dashboard disabled."
            )

            return

        host = getattr(
            self.cfg,
            "WEB_SERVER_HOST",
            "0.0.0.0",
        )

        port = int(
            getattr(
                self.cfg,
                "WEB_SERVER_PORT",
                8000,
            )
        )

        self.web_server = WebServer(
            broadcaster=self.frame_broadcaster,

            host=host,

            port=port,
        )

        self.web_server.start()

        print(
            "[ZONE X] Web dashboard started: "
            "http://localhost:{}".format(
                port
            )
        )

    # ========================================================================
    # POSE
    # ========================================================================

    def _setup_pose(self):
        """
        Load pose model for wrist/reach-in detection.
        """

        if not self.cfg.ENABLE_POSE:

            print(
                "[ZONE X] Pose disabled "
                "(ENABLE_POSE = False)."
            )

            print(
                "[ZONE X] Hand / reach-in detection disabled."
            )

            return

        from src.pose.pose_detector import (
            WristDetector,
        )

        try:

            self.pose = WristDetector(
                model_path=(
                    self.cfg.POSE_MODEL_PATH
                ),

                download_name=(
                    self.cfg.POSE_MODEL_DOWNLOAD_NAME
                ),

                confidence_threshold=(
                    self.cfg.POSE_CONFIDENCE_THRESHOLD
                ),

                keypoint_confidence=(
                    self.cfg.POSE_KEYPOINT_CONFIDENCE
                ),

                wrist_ids=(
                    self.cfg.WRIST_KEYPOINT_IDS
                ),

                imgsz=(
                    self.cfg.POSE_IMGSZ
                ),

                device=(
                    self.cfg.DEVICE
                ),

                verbose=(
                    self.cfg.VERBOSE_INFERENCE
                ),
            )

            print(
                "[ZONE X] Pose model loaded."
            )

        except ModelLoadError as exc:

            self.pose = None

            print(
                "\n[ZONE X] POSE MODEL UNAVAILABLE"
            )

            print(
                "[ZONE X] Continuing without "
                "hand / reach-in detection."
            )

            print(
                "{}".format(exc),
                file=sys.stderr,
            )

    # ========================================================================
    # LOG SESSION
    # ========================================================================

    def _open_log_session(self):
        """
        Start a fresh logs/<timestamp>/ directory.
        """

        if not self.cfg.ENABLE_JSON_LOGGING:
            return

        events_path = self.events.open_session(
            log_root=self.cfg.LOG_ROOT,

            metadata={
                "source_type": (
                    self.source.source_type
                ),

                "frame_width": (
                    self.source.frame_width
                ),

                "frame_height": (
                    self.source.frame_height
                ),

                "model": (
                    os.path.basename(
                        self.cfg.MODEL_PATH
                    )
                ),

                "pose_model": (
                    os.path.basename(
                        self.cfg.POSE_MODEL_PATH
                    )
                    if self.pose is not None
                    else None
                ),

                "machine_on_at_start": (
                    self.machine_on
                ),

                "danger_time_threshold_sec": (
                    self.cfg.DANGER_TIME_THRESHOLD_SEC
                ),

                "zone_priority": (
                    list(
                        self.cfg.ZONE_PRIORITY
                    )
                ),
            },
        )

        if events_path:

            print(
                "[ZONE X] Logging events to {}".format(
                    events_path
                )
            )

    # ========================================================================
    # CLEANUP
    # ========================================================================

    def cleanup(self):
        """
        Release every resource safely.
        """

        # --------------------------------------------------------------------
        # Stop alarm first
        # --------------------------------------------------------------------

        try:

            self.alarm.stop_alarm()

        except Exception as exc:

            print(
                "[ZONE X] Alarm cleanup error: {}".format(
                    exc
                ),
                file=sys.stderr,
            )

        # --------------------------------------------------------------------
        # Stop web server
        # --------------------------------------------------------------------

        if self.web_server is not None:

            try:

                self.web_server.stop()

            except Exception as exc:

                print(
                    "[ZONE X] Web server cleanup error: {}".format(
                        exc
                    ),
                    file=sys.stderr,
                )

            finally:

                self.web_server = None

        # --------------------------------------------------------------------
        # Close EventLog session
        # --------------------------------------------------------------------

        try:

            self.events.close_session()

        except Exception as exc:

            print(
                "[ZONE X] Event log cleanup error: {}".format(
                    exc
                ),
                file=sys.stderr,
            )

        # --------------------------------------------------------------------
        # Release frame source
        # --------------------------------------------------------------------

        if self.source is not None:

            try:

                self.source.release()

            except Exception as exc:

                print(
                    "[ZONE X] Frame source cleanup error: {}".format(
                        exc
                    ),
                    file=sys.stderr,
                )

            finally:

                self.source = None

        # --------------------------------------------------------------------
        # OpenCV cleanup
        # --------------------------------------------------------------------

        try:

            cv2.destroyAllWindows()

            for _ in range(3):
                cv2.waitKey(1)

        except cv2.error:
            pass

    # ========================================================================
    # MAIN LOOP
    # ========================================================================

    def run(self):
        """
        Run the real-time processing loop.
        """

        self.running = True

        empty_frame_count = 0

        while self.running:

            # =================================================================
            # ACTIVE
            # =================================================================

            if not self.paused:

                # -------------------------------------------------------------
                # Should we process another frame?
                # -------------------------------------------------------------

                if self.schedule.due():

                    ok, frame = self.source.read()

                    # =========================================================
                    # VALID FRAME
                    # =========================================================

                    if ok:

                        self.schedule.mark()

                        empty_frame_count = 0

                        started = (
                            time.perf_counter()
                        )

                        # -----------------------------------------------------
                        # Complete CV pipeline
                        # -----------------------------------------------------

                        self.process_frame(
                            frame
                        )

                        # -----------------------------------------------------
                        # Processing statistics
                        # -----------------------------------------------------

                        self.source.stats.note_process_time(
                            time.perf_counter()
                            - started
                        )

                        # -----------------------------------------------------
                        # Keep latest raw frame
                        # -----------------------------------------------------

                        self.last_frame = frame

                        # -----------------------------------------------------
                        # FPS
                        # -----------------------------------------------------

                        self.fps_meter.tick()

                    # =========================================================
                    # SOURCE FINISHED
                    # =========================================================

                    elif self._source_finished():

                        break

                    # =========================================================
                    # NO FRAME
                    # =========================================================

                    elif (
                        self._handle_no_frame(
                            empty_frame_count
                        )
                        is False
                    ):

                        break

                    else:

                        empty_frame_count += 1

                else:

                    self._idle()

            # =================================================================
            # PAUSED
            # =================================================================

            else:

                if self.last_frame is not None:

                    frame = (
                        self.last_frame.copy()
                    )

                    self.render(frame)

                    # ---------------------------------------------------------
                    # Show paused frame locally
                    # ---------------------------------------------------------

                    cv2.imshow(
                        self.cfg.WINDOW_NAME,
                        frame,
                    )

                    # ---------------------------------------------------------
                    # Also publish paused frame to dashboard
                    # ---------------------------------------------------------

                    self._publish_frame(
                        frame
                    )

            # =================================================================
            # KEYBOARD
            # =================================================================

            if self._handle_keys() is False:

                break

            # =================================================================
            # WINDOW CLOSED
            # =================================================================

            if self._window_closed():

                print(
                    "[ZONE X] Window closed by the user."
                )

                break

            # =================================================================
            # STATS
            # =================================================================

            self._maybe_print_stats()

        self.running = False

    # ========================================================================
    # SOURCE
    # ========================================================================

    def _source_finished(self):

        if not self.source.ended:

            return False

        print(
            "[ZONE X] End of video reached."
        )

        return True

    def _handle_no_frame(
        self,
        empty_frame_count,
    ):
        """
        Handle temporary frame loss or stream failure.
        """

        # --------------------------------------------------------------------
        # Reader-thread source
        # --------------------------------------------------------------------

        if self.source.uses_reader_thread:

            stalled = (
                self.source.stalled_for
            )

            if (
                stalled
                >= self.cfg.SOURCE_STALL_TIMEOUT
            ):

                detail = (
                    self.source.reader_message
                )

                raise FrameSourceError(
                    "No frames from the {} for {:.1f} seconds.\n"
                    "{}".format(
                        self.source.description,
                        stalled,
                        (
                            "Last reader error: {}\n".format(
                                detail
                            )
                            if detail
                            else ""
                        ),
                    )
                )

            return True

        # --------------------------------------------------------------------
        # Normal source
        # --------------------------------------------------------------------

        if (
            empty_frame_count + 1
            >= self.cfg.MAX_EMPTY_FRAMES
        ):

            raise FrameSourceError(
                "Lost the {} ({} empty frames in a row).\n"
                "The device may have been unplugged, "
                "the stream may have dropped, or another "
                "program may have claimed it.".format(
                    self.source.description,
                    empty_frame_count + 1,
                )
            )

        return True

    # ========================================================================
    # IDLE
    # ========================================================================

    def _idle(self):

        remaining = (
            self.schedule.seconds_until_due()
        )

        if remaining > 0.0:

            time.sleep(
                min(
                    0.004,
                    remaining,
                )
            )

    # ========================================================================
    # STATISTICS
    # ========================================================================

    def _maybe_print_stats(self):

        interval = getattr(
            self.cfg,
            "STATS_CONSOLE_INTERVAL_SEC",
            0.0,
        )

        if interval <= 0:

            return

        now = time.monotonic()

        if (
            now - self._last_stats_print
            < interval
        ):

            return

        self._last_stats_print = now

        print(
            self.source.stats.console_line()
        )

    # ========================================================================
    # FRAME PROCESSING
    # ========================================================================

    def process_frame(self, frame):
        """
        Run the complete Zone X safety pipeline.

        Pipeline:

            frame
              ↓
            detector
              ↓
            tracker
              ↓
            pose
              ↓
            risk engine
              ↓
            EventLog
              ↓
            alarm
              ↓
            render
              ↓
            OpenCV + Web
        """

        cfg = self.cfg

        # --------------------------------------------------------------------
        # Frame dimensions
        # --------------------------------------------------------------------

        height, width = frame.shape[:2]

        self.zones.set_frame_size(
            width,
            height,
        )

        self.frame_index += 1

        # ====================================================================
        # 1. PERSON DETECTION
        # ====================================================================

        detections = self.detector.detect(
            frame
        )

        # ====================================================================
        # 2. TRACKING
        # ====================================================================

        self.people = self.tracker.update(
            detections,
            self.zones,
        )

        # ====================================================================
        # 3. POSE / HAND STATE
        # ====================================================================

        self._update_hand_state(
            frame
        )

        # ====================================================================
        # 4. RISK ENGINE
        # ====================================================================

        self.risks = {
            person["id"]:
                self.risk_engine.evaluate_person(
                    person,
                    self.machine_on,
                )

            for person in self.people
        }

        # ====================================================================
        # 5. FIND HIGHEST RISK
        # ====================================================================

        (
            self.worst_id,
            self.worst,
        ) = self._worst_risk()

        # ====================================================================
        # 6. EVENT LOG
        # ====================================================================
        #
        # IMPORTANT:
        #
        # Do not duplicate event creation here.
        #
        # EventLog remains responsible for:
        #
        #   - event detection
        #   - event JSON
        #   - incident image
        #   - EventLog callback
        #
        # _handle_event() forwards the event to Spring Boot.
        #
        # ====================================================================

        self.events.update(
            self.people,

            self.risks,

            self.machine_on,

            cfg,

            live_ids=(
                self.tracker.live_ids
            ),

            frame=frame,
        )

        # ====================================================================
        # 7. ALARM
        # ====================================================================

        self._update_alarm()

        # ====================================================================
        # 8. ALERT TIMING
        # ====================================================================

        now = time.time()

        # Any danger zone or hand-danger state.
        if any(
            person["zone"]
            == cfg.ZONE_DANGER
            or person["hand_in_danger"]
            for person in self.people
        ):

            self.last_violation_time = now

        # Critical risk.
        if (
            self.worst is not None
            and self.risk_engine.is_critical(
                self.worst["risk"]
            )
        ):

            self.last_critical_time = now

        # ====================================================================
        # 9. RENDER
        # ====================================================================

        self.render(
            frame
        )

        # ====================================================================
        # 10. LOCAL OPENCV DISPLAY
        # ====================================================================

        cv2.imshow(
            self.cfg.WINDOW_NAME,
            frame,
        )

        # ====================================================================
        # 11. WEB DASHBOARD
        # ====================================================================
        #
        # IMPORTANT:
        #
        # The dashboard receives the FINAL annotated frame.
        #
        # Therefore the web dashboard shows:
        #
        #   - people
        #   - IDs
        #   - zones
        #   - risk
        #   - alerts
        #   - HUD
        #   - worker cards
        #   - danger borders
        #   - hand alerts
        #
        # exactly like the OpenCV window.
        #
        # ====================================================================

        self._publish_frame(
            frame
        )

    # ========================================================================
    # WEB FRAME PUBLISH
    # ========================================================================

    def _publish_frame(self, frame):
        """
        Publish the final annotated frame to the web dashboard.

        FrameBroadcaster is deliberately isolated from the CV pipeline.

        If web streaming fails, the local CV application continues.
        """

        if self.web_server is None:

            return

        try:

            self.frame_broadcaster.update(
                frame
            )

        except Exception as exc:

            print(
                "[WEB] Failed to publish frame: {}".format(
                    exc
                ),
                file=sys.stderr,
            )

    # ========================================================================
    # HAND STATE
    # ========================================================================

    def _update_hand_state(self, frame):
        """
        Run pose detection periodically.

        Pose detection is intentionally not performed on every frame if
        POSE_EVERY_N_FRAMES is greater than 1.
        """

        if (
            self.pose is None
            or not self.people
        ):

            return

        every = max(
            1,
            int(
                self.cfg.POSE_EVERY_N_FRAMES
            ),
        )

        if (
            self.frame_index % every
            != 0
        ):

            return

        from src.pose.pose_detector import (
            associate_wrists,
        )

        # --------------------------------------------------------------------
        # Pose detection
        # --------------------------------------------------------------------

        poses = self.pose.detect(
            frame
        )

        # --------------------------------------------------------------------
        # Match wrists to tracked people
        # --------------------------------------------------------------------

        wrist_map = associate_wrists(
            self.people,

            poses,

            self.cfg.POSE_MATCH_MIN_IOU,
        )

        # --------------------------------------------------------------------
        # Update tracker hand state
        # --------------------------------------------------------------------

        self.tracker.update_hands(
            self.people,

            wrist_map,

            self.zones,
        )

    # ========================================================================
    # CRITICAL STATE
    # ========================================================================

    def _is_critical(self, person):

        return (
            person.get("zone")
            == self.cfg.ZONE_DANGER
            or bool(
                person.get(
                    "hand_in_danger"
                )
            )
        )

    # ========================================================================
    # ALARM OFFENDERS
    # ========================================================================

    def _alarm_offenders(
        self,
        dwelling,
    ):
        """
        Return all workers that should currently trigger the alarm.
        """

        offenders = [
            person
            for person in self.people
            if self._is_critical(
                person
            )
        ]

        claimed = {
            person["id"]
            for person in offenders
        }

        # --------------------------------------------------------------------
        # Add workers responsible for warning dwell violations
        # --------------------------------------------------------------------

        for incident in dwelling:

            if (
                incident.track_id
                in claimed
            ):

                continue

            claimed.add(
                incident.track_id
            )

            visible = self._person(
                incident.track_id
            )

            if visible is not None:

                offenders.append(
                    visible
                )

            else:

                offenders.append(
                    _incident_offender(
                        incident
                    )
                )

        return offenders

    # ========================================================================
    # ALARM
    # ========================================================================

    def _update_alarm(self):
        """
        Update PC alarm and alarm-related EventLog state.
        """

        if not self.cfg.ENABLE_PC_ALARM:

            return

        # --------------------------------------------------------------------
        # Warning dwell violations
        # --------------------------------------------------------------------

        dwelling = (
            self.events.warning_dwell_violations(
                frame=self.last_frame
            )
        )

        # --------------------------------------------------------------------
        # Determine offenders
        # --------------------------------------------------------------------

        offenders = (
            self._alarm_offenders(
                dwelling
            )
        )

        # --------------------------------------------------------------------
        # Start/stop alarm
        # --------------------------------------------------------------------

        if offenders:

            self.alarm.start_alarm()

        else:

            self.alarm.stop_alarm()

        # --------------------------------------------------------------------
        # Mark alarm-triggered incidents
        # --------------------------------------------------------------------

        if self.alarm.is_alarm_active():

            for incident in dwelling:

                incident.alarm_triggered = True

        # --------------------------------------------------------------------
        # Notify EventLog
        # --------------------------------------------------------------------

        self.events.update_alarm(
            self.alarm.is_alarm_active(),

            offenders,

            self.risks,

            self.machine_on,

            self.cfg,
        )

    # ========================================================================
    # PERSON LOOKUP
    # ========================================================================

    def _person(self, track_id):

        if track_id is None:

            return None

        for person in self.people:

            if person["id"] == track_id:

                return person

        return None

    # ========================================================================
    # WORST RISK
    # ========================================================================

    def _worst_risk(self):

        worst_id = None

        worst = None

        for (
            person_id,
            verdict,
        ) in self.risks.items():

            if (
                worst is None
                or verdict["risk"]
                > worst["risk"]
            ):

                worst_id = person_id

                worst = verdict

        return (
            worst_id,
            worst,
        )

    @property
    def highest_risk(self):

        if self.worst is None:

            return 0

        return self.worst["risk"]

    # ========================================================================
    # ZONE COUNTS
    # ========================================================================

    def _zone_counts(self):

        counts = {}

        for person in self.people:

            zone = person.get(
                "zone"
            )

            counts[zone] = (
                counts.get(
                    zone,
                    0,
                )
                + 1
            )

        return counts

    # ========================================================================
    # RENDER
    # ========================================================================

    def render(self, frame):
        """
        Draw all Zone X visual information onto the frame.
        """

        cfg = self.cfg

        # --------------------------------------------------------------------
        # Zone counts
        # --------------------------------------------------------------------

        counts = self._zone_counts()

        # --------------------------------------------------------------------
        # Current time
        # --------------------------------------------------------------------

        now = time.time()

        # --------------------------------------------------------------------
        # Alert state
        # --------------------------------------------------------------------

        alert_active = (
            self.zones.any_ready
            and (
                now
                - self.last_violation_time
            )
            <= cfg.ALERT_HOLD_SECONDS
        )

        critical_active = (
            self.zones.any_ready
            and (
                now
                - self.last_critical_time
            )
            <= cfg.ALERT_HOLD_SECONDS
        )

        # --------------------------------------------------------------------
        # Violated zone IDs
        # --------------------------------------------------------------------

        violated_ids = set()

        for person in self.people:

            # ---------------------------------------------------------------
            # Worker inside warning/danger zone
            # ---------------------------------------------------------------

            if person.get(
                "zone"
            ) in (
                cfg.ZONE_WARNING,
                cfg.ZONE_DANGER,
            ):

                if person.get(
                    "zone_id"
                ):

                    violated_ids.add(
                        person["zone_id"]
                    )

            # ---------------------------------------------------------------
            # Hand inside danger zone
            # ---------------------------------------------------------------

            if (
                person.get(
                    "hand_in_danger"
                )
                and person.get(
                    "hand_zone_id"
                )
            ):

                violated_ids.add(
                    person["hand_zone_id"]
                )

        # ====================================================================
        # ZONES
        # ====================================================================

        self.zones.draw(
            frame,

            violated_ids=(
                violated_ids
            ),
        )

        # ====================================================================
        # PEOPLE
        # ====================================================================

        ui.draw_people(
            frame,

            self.people,

            cfg,
        )

        # ====================================================================
        # STATUS
        # ====================================================================

        status = self._current_status(
            alert_active,

            critical_active,
        )

        # ====================================================================
        # ALERT BORDER
        # ====================================================================

        if status == "CRITICAL":

            ui.draw_alert_border(
                frame,

                cfg,

                cfg.CRITICAL_BANNER_TEXT,
            )

        elif status == "DANGER":

            ui.draw_alert_border(
                frame,

                cfg,

                cfg.ALERT_TEXT,
            )

        # ====================================================================
        # HAND ALERT
        # ====================================================================

        ui.draw_hand_alert(
            frame,

            cfg,

            self.people,
        )

        # ====================================================================
        # HUD
        # ====================================================================

        ui.draw_hud(
            frame,

            cfg,

            people_count=len(
                self.people
            ),

            inside_count=counts.get(
                cfg.ZONE_DANGER,
                0,
            ),

            fps=self.fps_meter.fps,

            status=status,

            zone_info=(
                self.zones.summary()
            ),

            paused=self.paused,

            machine_on=self.machine_on,

            zone_counts=counts,

            banner_text=(
                self._banner_override(
                    status
                )
            ),

            highest_risk=(
                self.highest_risk
            ),

            critical=(
                self.risk_engine.is_critical(
                    self.highest_risk
                )
            ),
        )

        # ====================================================================
        # WORKER PANEL
        # ====================================================================

        if cfg.SHOW_WORKER_PANEL:

            ui.draw_worker_panel(
                frame,

                cfg,

                self.people,

                self.risks,
            )

        # ====================================================================
        # REASON PANEL
        # ====================================================================

        if (
            cfg.SHOW_REASON_PANEL
            and self.worst is not None
            and self.worst["risk"] > 0
        ):

            ui.draw_reason_panel(
                frame,

                cfg,

                self.worst,

                self._person(
                    self.worst_id
                ),
            )

        # ====================================================================
        # ZONE MESSAGE
        # ====================================================================

        ui.draw_zone_message(
            frame,

            cfg,

            self.zones.last_message,
        )

        # ====================================================================
        # SOURCE STATS
        # ====================================================================

        if getattr(
            cfg,
            "SHOW_SOURCE_STATS",
            False,
        ):

            ui.draw_source_stats(
                frame,

                cfg,

                self.source.stats,
            )

        # ====================================================================
        # HELP BAR
        # ====================================================================

        if cfg.SHOW_HELP_BAR:

            ui.draw_help_bar(
                frame,

                cfg,

                defining=(
                    self.zones.is_defining
                ),
            )

        # ====================================================================
        # PERSON CARDS
        # ====================================================================

        ui.draw_person_cards(
            frame,

            self.people,

            cfg,

            self.risks,
        )

        # ====================================================================
        # PAUSED MESSAGE
        # ====================================================================

        if self.paused:

            ui.draw_center_message(
                frame,

                cfg,

                [
                    "PAUSED",
                    "press P to resume",
                ],

                cfg.COLOR_ACCENT,
            )

    # ========================================================================
    # CURRENT STATUS
    # ========================================================================

    def _current_status(
        self,
        alert_active,
        critical_active,
    ):

        cfg = self.cfg

        if self.paused:

            return "PAUSED"

        if self.zones.is_defining:

            return "DEFINING"

        if not self.zones.any_ready:

            return "NO_ZONE"

        if critical_active:

            return "CRITICAL"

        if alert_active:

            return "DANGER"

        if any(
            person["zone"]
            == cfg.ZONE_WARNING
            for person in self.people
        ):

            return "WARNING"

        return "CLEAR"

    # ========================================================================
    # BANNER
    # ========================================================================

    def _banner_override(
        self,
        status,
    ):

        if status != "DEFINING":

            return None

        zone_type = (
            self.zones.defining_type
        )

        label = self.cfg.ZONE_LABELS.get(
            zone_type,
            "ZONE",
        )

        return (
            self.cfg.DEFINING_ZONE_BANNER_TEXT
            .format(label)
        )

    # ========================================================================
    # EVENT PRINTER
    # ========================================================================

    @staticmethod
    def _print_event(record):
        """
        Print a compact event line.
        """

        who = (
            record.get(
                "person_id"
            )
            or "system"
        )

        where = (
            "  {}".format(
                record["zone_id"]
            )
            if record.get(
                "zone_id"
            )
            else ""
        )

        risk = record.get(
            "risk"
        )

        score = (
            ""
            if risk is None
            else "  risk={}".format(
                risk
            )
        )

        duration = record.get(
            "duration_sec"
        )

        if duration is not None:

            score = "{:.1f}s{}".format(
                duration,

                (
                    "  ALARM"
                    if record.get(
                        "alarm_triggered"
                    )
                    else ""
                ),
            )

        stamp = (
            record.get(
                "timestamp"
            )
            or record.get(
                "exit_time"
            )
            or ""
        )[11:19]

        print(
            "[Event] {}  {}  {}{}{}".format(
                stamp,

                record.get(
                    "event_type",
                    "UNKNOWN",
                ),

                who,

                where,

                score,
            )
        )

    # ========================================================================
    # WINDOW
    # ========================================================================

    def _window_closed(self):

        try:

            return (
                cv2.getWindowProperty(
                    self.cfg.WINDOW_NAME,

                    cv2.WND_PROP_VISIBLE,
                )
                < 1
            )

        except cv2.error:

            return True

    # ========================================================================
    # KEYBOARD
    # ========================================================================

    def _handle_keys(self):
        """
        Handle OpenCV keyboard controls.
        """

        cfg = self.cfg

        key = (
            cv2.waitKey(1)
            & 0xFF
        )

        if key == 255:

            return True

        # ====================================================================
        # QUIT
        # ====================================================================

        if key in (
            ord("q"),
            ord("Q"),
            KEY_ESC,
        ):

            print(
                "[ZONE X] Quit requested."
            )

            return False

        # ====================================================================
        # ZONE KEYS
        # ====================================================================

        zone_keys = {

            ord("s"): cfg.ZONE_SAFE,

            ord("S"): cfg.ZONE_SAFE,

            ord("w"): cfg.ZONE_WARNING,

            ord("W"): cfg.ZONE_WARNING,

            ord("d"): cfg.ZONE_DANGER,

            ord("D"): cfg.ZONE_DANGER,

            ord("r"): cfg.ZONE_DANGER,

            ord("R"): cfg.ZONE_DANGER,
        }

        if key in zone_keys:

            zone_type = (
                zone_keys[key]
            )

            self.zones.start_definition(
                zone_type
            )

            self._forget_zone_state()

            self.paused = False

            print(
                "[ZONE X] Defining {}: "
                "click points, Enter to finish.".format(
                    self.zones.defining_id
                    or cfg.ZONE_LABELS[
                        zone_type
                    ]
                )
            )

            return True

        # ====================================================================
        # CLEAR ZONES
        # ====================================================================

        if key in (
            ord("c"),
            ord("C"),
        ):

            if self.zones.is_defining:

                zone_type = (
                    self.zones.cancel_definition()
                )

                print(
                    "[ZONE X] Cancelled {}.".format(
                        cfg.ZONE_LABELS[
                            zone_type
                        ]
                    )
                )

            else:

                self.zones.clear()

                print(
                    "[ZONE X] All zones cleared."
                )

            self._forget_zone_state()

            return True

        # ====================================================================
        # MACHINE TOGGLE
        # ====================================================================

        if key in (
            ord("m"),
            ord("M"),
        ):

            self.machine_on = (
                not self.machine_on
            )

            self.zones.last_message = (
                "Machine {}.".format(
                    "ON"
                    if self.machine_on
                    else "OFF"
                )
            )

            print(
                "[ZONE X] Machine {}.".format(
                    "ON"
                    if self.machine_on
                    else "OFF"
                )
            )

            return True

        # ====================================================================
        # PAUSE
        # ====================================================================

        if key in (
            ord("p"),
            ord("P"),
        ):

            if self.zones.is_defining:

                self.zones.last_message = (
                    "Finish or cancel the "
                    "zone before pausing."
                )

            else:

                self.paused = (
                    not self.paused
                )

                print(
                    "[ZONE X] {}.".format(
                        "Paused."
                        if self.paused
                        else "Resumed."
                    )
                )

            return True

        # ====================================================================
        # ENTER
        # ====================================================================

        if key in KEY_ENTER:

            if self.zones.is_defining:

                (
                    ok,
                    _,
                ) = (
                    self.zones.finish_definition()
                )

                if ok:

                    zone = (
                        self.zones.last_finished
                    )

                    self._forget_zone_state()

                    print(
                        "[ZONE X] {} set with "
                        "{} points (area {:.0f} px^2).".format(
                            zone.zone_id,

                            zone.point_count,

                            zone.area,
                        )
                    )

                else:

                    print(
                        "[ZONE X] {}".format(
                            self.zones.last_message
                        )
                    )

            return True

        return True

    # ========================================================================
    # FORGET ZONE STATE
    # ========================================================================

    def _forget_zone_state(self):
        """
        Reset tracking/event state after zone configuration changes.
        """

        self.tracker.reset()

        self.events.reset()

        self.risks = {}

        self.worst_id = None

        self.worst = None

        self.last_violation_time = 0.0

        self.last_critical_time = 0.0


# ============================================================================
# CONTROLS
# ============================================================================

def print_controls():

    print("")

    print(
        "  ------------------------------------------------------------"
    )

    print(
        "   ZONE X - Smart Safety Monitoring System"
    )

    print(
        "  ------------------------------------------------------------"
    )

    print(
        "   s      add a SAFE zone"
    )

    print(
        "   w      add a WARNING zone"
    )

    print(
        "   d      add a DANGER zone"
    )

    print(
        "   r      add a DANGER zone"
    )

    print(
        "   click  add a polygon point"
    )

    print(
        "   Enter  finish the polygon"
    )

    print(
        "   c      clear all zones"
    )

    print(
        "   m      toggle simulated machine ON / OFF"
    )

    print(
        "   p      pause / resume"
    )

    print(
        "   q      quit"
    )

    print(
        "   Esc    quit"
    )

    print(
        "  ------------------------------------------------------------"
    )

    print(
        "   Draw DANGER around each machine,"
    )

    print(
        "   WARNING around the danger area,"
    )

    print(
        "   and SAFE over the walkway."
    )

    print(
        "   Each polygon receives its own tag."
    )

    print(
        "  ------------------------------------------------------------"
    )

    print("")


# ============================================================================
# ENTRY POINT
# ============================================================================

def main():
    """
    Application entry point.
    """

    # ------------------------------------------------------------------------
    # Always execute from project root.
    #
    # This makes relative paths such as:
    #
    #   models/yolov8n.pt
    #   logs/
    #   incidents/
    #
    # resolve correctly.
    # ------------------------------------------------------------------------

    project_root = os.path.dirname(
        os.path.abspath(
            __file__
        )
    )

    os.chdir(
        project_root
    )

    # ------------------------------------------------------------------------
    # Create application
    # ------------------------------------------------------------------------

    app = ZoneXApp(
        config
    )

    exit_code = 0

    try:

        # --------------------------------------------------------------------
        # Setup
        # --------------------------------------------------------------------

        app.setup()

        # --------------------------------------------------------------------
        # Run
        # --------------------------------------------------------------------

        app.run()

    # ========================================================================
    # MODEL ERROR
    # ========================================================================

    except ModelLoadError as exc:

        print(
            "\n[ZONE X] MODEL ERROR\n{}\n".format(
                exc
            ),
            file=sys.stderr,
        )

        exit_code = 2

    # ========================================================================
    # FRAME SOURCE ERROR
    # ========================================================================

    except FrameSourceError as exc:

        print(
            "\n[ZONE X] FRAME SOURCE ERROR ({})\n{}\n".format(
                config.SOURCE_TYPE,

                exc,
            ),
            file=sys.stderr,
        )

        exit_code = 3

    # ========================================================================
    # KEYBOARD INTERRUPT
    # ========================================================================

    except KeyboardInterrupt:

        print(
            "\n[ZONE X] Interrupted with Ctrl+C."
        )

    # ========================================================================
    # UNEXPECTED ERROR
    # ========================================================================

    except Exception:

        import traceback

        print(
            "\n[ZONE X] UNEXPECTED ERROR",
            file=sys.stderr,
        )

        traceback.print_exc()

        exit_code = 1

    # ========================================================================
    # ALWAYS CLEANUP
    # ========================================================================

    finally:

        app.cleanup()

    print(
        "[ZONE X] Shutdown complete."
    )

    return exit_code


# ============================================================================
# SCRIPT ENTRY
# ============================================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )