"""
ZONE X - Smart Safety Monitoring System
Phase 1: Local Computer Vision Prototype

main.py
-------

Application entry point and pipeline orchestrator.

Pipeline:

    Camera/Video (FrameSource)
        ↓
    Person Detection (PersonDetector)
        ↓
    Person Tracking (PersonTracker)
        ↓
    Zone Checking (ZoneManager)
        ↓
    Hand/Wrist Detection (Pose / WristDetector)
        ↓
    Risk Calculation (RiskEngine)
        ↓
    Event Recording (EventLog)
        ↓
    Alarm (Alarm)
        ↓
    Show on Screen (UI Rendering)

MQTT:

    Vision
        ↓
    sentinel/hardware/command
        ↓
    ESP32
        ↓
    WARNING / DANGER / CLEAR
"""

import collections
import os
import sys
import time

import cv2

import config

from src.visualization import ui
from src.alarm.alarm import Alarm
from src.detection.detector import ModelLoadError, PersonDetector
from src.detection.tracker import PersonTracker
from src.events.events import EventLog
from src.camera.frame_source import (
    FrameSource,
    FrameSourceError,
    ProcessingScheduler,
)
from src.camera.frame_broadcaster import FrameBroadcaster
from src.fastapi.web_server import WebServer
from src.safety.risk_engine import RiskEngine
from src.zones.zone import ZoneManager
from src.integration.connection import BackendClient
from src.integration.mqtt_client import MQTTClient


KEY_ENTER = (13, 10)
KEY_ESC = 27


class FpsMeter:
    """Measures how many frames per second we're processing."""

    def __init__(self, window=30):
        self._durations = collections.deque(
            maxlen=max(1, int(window))
        )
        self._last_time = None

    def tick(self):
        """Record the processing time between frames."""
        now = time.perf_counter()

        if self._last_time is not None:
            self._durations.append(
                now - self._last_time
            )

        self._last_time = now

    @property
    def fps(self):
        """Return the current FPS."""
        if not self._durations:
            return 0.0

        average = (
            sum(self._durations)
            / len(self._durations)
        )

        if average <= 0:
            return 0.0

        return 1.0 / average


def _incident_offender(incident):
    """
    Create a standard offender record from an incident.
    Used when the person who caused the incident is no longer visible.
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


class ZoneXApp:
    """Main application class that runs the whole system."""

    def __init__(self, cfg):
        self.cfg = cfg

        # ==============================================================
        # HARDWARE STATE
        # ==============================================================

        self.hardware_alarm_state = False
        self.hardware_zone_state = None

        # Hold danger briefly to avoid pose flicker
        self.hardware_danger_until = 0.0

        # ==============================================================
        # CAMERA / VIDEO SOURCE
        # ==============================================================

        self.source = None

        # ==============================================================
        # WEB DASHBOARD
        # ==============================================================

        self.frame_broadcaster = FrameBroadcaster(
            jpeg_quality=getattr(
                cfg,
                "WEB_STREAM_JPEG_QUALITY",
                80,
            )
        )

        self.web_server = None

        # ==============================================================
        # PROCESSING CONTROL
        # ==============================================================

        self.schedule = ProcessingScheduler(None)

        self.running = False
        self.paused = False
        self.frame_index = 0

        # ==============================================================
        # LATEST RESULTS
        # ==============================================================

        self.last_frame = None
        self.people = []
        self.risks = {}
        self.worst_id = None
        self.worst = None

        # ==============================================================
        # ALERT TIMING
        # ==============================================================

        self.last_violation_time = 0.0
        self.last_critical_time = 0.0

        # ==============================================================
        # MACHINE
        # ==============================================================

        self.machine_on = bool(
            cfg.MACHINE_ON
        )

        # ==============================================================
        # FPS
        # ==============================================================

        self.fps_meter = FpsMeter(
            cfg.FPS_SMOOTHING_WINDOW
        )

        self._last_stats_print = 0.0

        # ==============================================================
        # ZONES
        # ==============================================================

        self.zones = ZoneManager(cfg)

        # ==============================================================
        # TRACKER
        # ==============================================================

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

        # ==============================================================
        # RISK ENGINE
        # ==============================================================

        self.risk_engine = RiskEngine(
            cfg,
            zones=self.zones,
        )

        # ==============================================================
        # EVENTS
        # ==============================================================

        self.events = EventLog(
            max_events=cfg.MAX_STORED_EVENTS,
            danger_time_threshold=(
                cfg.DANGER_TIME_THRESHOLD_SEC
            ),
            critical_threshold=(
                cfg.RISK_CRITICAL_THRESHOLD
            ),
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

        # ==============================================================
        # SPRING BOOT BACKEND
        # ==============================================================

        self.backend = BackendClient(
            base_url=getattr(
                cfg,
                "BACKEND_URL",
                "http://localhost:8080",
            )
        )

# ==============================================================
# MQTT HARDWARE
# ==============================================================

        self.mqtt = MQTTClient(
            broker_host="172.22.2.158",
            broker_port=1883,
            topic="sentinel/hardware/command",
            client_id="zone-x-vision",
        )

        try:

            mqtt_ok = self.mqtt.connect()

            if mqtt_ok:
                print(
                    "[ZONE X] MQTT hardware connection READY."
                )
            else:
                print(
                    "[ZONE X] MQTT hardware connection FAILED."
                )

        except Exception as exc:

            print(
                "[MQTT] Connection failed: {}".format(
                    exc
                ),
                file=sys.stderr,
            )
        # ==============================================================
        # PC ALARM
        # ==============================================================

        self.alarm = Alarm.from_config(cfg)

        # ==============================================================
        # MODELS
        # ==============================================================

        self.detector = None
        self.pose = None

    # ==================================================================
    # EVENT HANDLER
    # ==================================================================

    def _handle_event(self, record):
        """
        Called whenever an event happens.
        Sends the event to the Spring Boot backend.
        """

        if self.cfg.LOG_EVENTS_TO_CONSOLE:
            self._print_event(record)

        if self.backend is None:
            return

        try:
            self.backend.send_incident(record)

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

    # ==================================================================
    # ESP32 HARDWARE OUTPUT
    # ==================================================================

    def _set_hardware_state(self, state):
        """
        Control the physical ESP32.

        WARNING -> Yellow LED
        DANGER  -> Red LED + Buzzer
        CLEAR   -> Everything OFF

        Only publish when the state changes.
        """

        if self.mqtt is None:
            return

        state = str(
            state
        ).upper().strip()

        if state == self.hardware_zone_state:
            return

        try:
            if state == "WARNING":
                success = self.mqtt.warning()

            elif state == "DANGER":
                success = self.mqtt.danger()

            elif state == "CLEAR":
                success = self.mqtt.clear()

            else:
                print(
                    "[HARDWARE] Invalid state: {}".format(
                        state
                    )
                )
                return

            if success:
                self.hardware_zone_state = state

                self.hardware_alarm_state = (
                    state == "DANGER"
                )

                print(
                    "[HARDWARE] ESP32 state = {}".format(
                        state
                    )
                )

            else:
                print(
                    "[HARDWARE] Failed to send state = {}".format(
                        state
                    )
                )

        except Exception as exc:
            print(
                "[HARDWARE] MQTT error: {}".format(
                    exc
                ),
                file=sys.stderr,
            )

    # ==================================================================
    # ESP32 HARDWARE DECISION
    # ==================================================================

    def _update_hardware(self):
        """
        Determine the physical hardware state.

        Priority:

            DANGER > WARNING > CLEAR

        DANGER sources:

            body in danger
            hand in danger
            feet in danger

        WARNING sources:

            body in warning
            hand in warning
        """

        cfg = self.cfg
        now = time.monotonic()

        danger_detected = False
        warning_detected = False

        # --------------------------------------------------------------
        # Check every tracked person
        # --------------------------------------------------------------

        for person in self.people:

            body_zone = person.get(
                "zone"
            )

            hand_zone = person.get(
                "hand_zone"
            )

            hand_in_danger = bool(
                person.get(
                    "hand_in_danger",
                    False,
                )
            )

            feet_in_danger = bool(
                person.get(
                    "feet_in_danger",
                    False,
                )
            )

            # ----------------------------------------------------------
            # DANGER
            # ----------------------------------------------------------

            if (
                body_zone == cfg.ZONE_DANGER
                or hand_zone == cfg.ZONE_DANGER
                or hand_in_danger
                or feet_in_danger
            ):
                danger_detected = True

            # ----------------------------------------------------------
            # WARNING
            # ----------------------------------------------------------

            elif (
                body_zone == cfg.ZONE_WARNING
                or hand_zone == cfg.ZONE_WARNING
            ):
                warning_detected = True

            print(
                "[HARDWARE DEBUG] "
                "body_zone={} hand_zone={} "
                "hand_in_danger={} feet_in_danger={}".format(
                    body_zone,
                    hand_zone,
                    hand_in_danger,
                    feet_in_danger,
                )
            )

        # --------------------------------------------------------------
        # DANGER HAS HIGHEST PRIORITY
        # --------------------------------------------------------------

        if danger_detected:

            # Hold DANGER for 2 seconds.
            # This prevents pose-detection flicker from immediately
            # turning the physical alarm off.
            self.hardware_danger_until = (
                now + 2.0
            )

            self._set_hardware_state(
                "DANGER"
            )

            print(
                "[HARDWARE DEBUG] DANGER detected"
            )

            return

        # --------------------------------------------------------------
        # HOLD DANGER
        # --------------------------------------------------------------

        if now < self.hardware_danger_until:

            self._set_hardware_state(
                "DANGER"
            )

            return

        # --------------------------------------------------------------
        # WARNING
        # --------------------------------------------------------------

        if warning_detected:

            self._set_hardware_state(
                "WARNING"
            )

            print(
                "[HARDWARE DEBUG] WARNING detected"
            )

            return

        # --------------------------------------------------------------
        # CLEAR
        # --------------------------------------------------------------

        self._set_hardware_state(
            "CLEAR"
        )

    # ==================================================================
    # SETUP
    # ==================================================================

    def setup(self):
        """Load models, open source, start web server and UI."""

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
            imgsz=self.cfg.INFERENCE_IMGSZ,
            device=self.cfg.DEVICE,
            verbose=self.cfg.VERBOSE_INFERENCE,
        )

        self._setup_pose()

        self.source = (
            FrameSource
            .from_config(self.cfg)
            .open()
        )

        self.schedule = ProcessingScheduler(
            self.cfg.TARGET_PROCESSING_FPS
            if self.source.uses_reader_thread
            else None
        )

        self._setup_web_server()
        self._open_log_session()

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

    # ==================================================================
    # WEB SERVER
    # ==================================================================

    def _setup_web_server(self):
        """Start the FastAPI server."""

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
            zone_manager=self.zones,
            command_handler=self._handle_web_command,
            host=host,
            port=port,
        )

        self.web_server.start()

        print(
            "[ZONE X] Web dashboard started: "
            "http://localhost:{}".format(port)
        )

    # ==================================================================
    # WEB COMMANDS
    # ==================================================================

    def _handle_web_command(self, command, **kwargs):
        """Handle commands from the React web dashboard."""

        if command == "key":

            key = (
                kwargs.get("key", "")
                .strip()
                .lower()
            )

            key_map = {
                "s": ord("s"),
                "w": ord("w"),
                "d": ord("d"),
                "r": ord("r"),
                "c": ord("c"),
                "m": ord("m"),
                "p": ord("p"),
                "q": ord("q"),
                "escape": KEY_ESC,
                "esc": KEY_ESC,
                "enter": 13,
                "return": 13,
            }

            if key not in key_map:
                return {
                    "handled": False,
                    "message": "Unknown key: {}".format(
                        key
                    ),
                }

            keep_running = (
                self._apply_control_key(
                    key_map[key]
                )
            )

            return {
                "handled": True,
                "keep_running": keep_running,
                "message": self.zones.last_message,
            }

        if command == "start_zone":

            zone_type = (
                kwargs.get(
                    "zone_type",
                    "",
                )
                .strip()
                .upper()
            )

            if zone_type not in self.zones.zone_types:
                return {
                    "handled": False,
                    "message": "Unknown zone type",
                }

            self.zones.start_definition(
                zone_type
            )

            self._forget_zone_state()
            self.paused = False

            return {
                "handled": True,
                "zone_type": zone_type,
                "zone_id": self.zones.defining_id,
                "message": self.zones.last_message,
            }

        if command == "zone_point":

            x = int(
                round(
                    float(
                        kwargs.get(
                            "x",
                            0,
                        )
                    )
                )
            )

            y = int(
                round(
                    float(
                        kwargs.get(
                            "y",
                            0,
                        )
                    )
                )
            )

            self.zones.handle_mouse(
                cv2.EVENT_LBUTTONDOWN,
                x,
                y,
                0,
                None,
            )

            return {
                "handled": True,
                "message": self.zones.last_message,
            }

        if command == "zone_hover":

            x = int(
                round(
                    float(
                        kwargs.get(
                            "x",
                            0,
                        )
                    )
                )
            )

            y = int(
                round(
                    float(
                        kwargs.get(
                            "y",
                            0,
                        )
                    )
                )
            )

            self.zones.handle_mouse(
                cv2.EVENT_MOUSEMOVE,
                x,
                y,
                0,
                None,
            )

            return {
                "handled": True,
                "message": self.zones.last_message,
            }

        if command == "zone_undo":

            self.zones.handle_mouse(
                cv2.EVENT_RBUTTONDOWN,
                0,
                0,
                0,
                None,
            )

            return {
                "handled": True,
                "message": self.zones.last_message,
            }

        if command == "zone_finish":

            ok, _ = (
                self.zones.finish_definition()
            )

            if ok:

                zone = self.zones.last_finished

                self._forget_zone_state()

                return {
                    "handled": True,
                    "success": True,
                    "zone_id": (
                        zone.zone_id
                        if zone
                        else None
                    ),
                    "zone_type": (
                        zone.zone_type
                        if zone
                        else None
                    ),
                    "message": (
                        zone.last_message
                        if zone
                        else ""
                    ),
                }

            return {
                "handled": True,
                "success": False,
                "message": self.zones.last_message,
            }

        if command == "zone_cancel":

            if self.zones.is_defining:

                zone_type = (
                    self.zones.cancel_definition()
                )

                self._forget_zone_state()

                return {
                    "handled": True,
                    "cancelled": True,
                    "zone_type": zone_type,
                    "message": self.zones.last_message,
                }

            return {
                "handled": True,
                "cancelled": False,
                "message": "No zone is being defined.",
            }

        if command == "clear":

            if self.zones.is_defining:
                self.zones.cancel_definition()
            else:
                self.zones.clear()

            self._forget_zone_state()

            return {
                "handled": True,
                "message": self.zones.last_message,
            }

        return {
            "handled": False,
            "message": (
                "Unknown command: {}".format(
                    command
                )
            ),
        }

    # ==================================================================
    # KEYBOARD CONTROL
    # ==================================================================

    def _apply_control_key(self, key):
        """Handle keyboard shortcuts."""

        cfg = self.cfg

        if key in (
            ord("q"),
            ord("Q"),
            KEY_ESC,
        ):

            print(
                "[ZONE X] Quit requested."
            )

            self.running = False

            return False

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

            zone_type = zone_keys[key]

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

        if key in KEY_ENTER:

            if self.zones.is_defining:

                ok, _ = (
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

    # ==================================================================
    # POSE DETECTION
    # ==================================================================

    def _setup_pose(self):
        """Load the hand/wrist detection model."""

        if not self.cfg.ENABLE_POSE:

            print(
                "[ZONE X] Pose disabled "
                "(ENABLE_POSE = False)."
            )

            print(
                "[ZONE X] Hand / reach-in detection disabled."
            )

            return

        from src.pose.pose_detector import WristDetector

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

    # ==================================================================
    # LOGGING SESSION
    # ==================================================================

    def _open_log_session(self):
        """Start a new logging session."""

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

    # ==================================================================
    # CLEANUP
    # ==================================================================

    def cleanup(self):
        """Clean up everything before shutting down."""

        # --------------------------------------------------------------
        # CLEAR ESP32 HARDWARE
        # --------------------------------------------------------------

        if self.mqtt is not None:

            try:

                self.mqtt.clear()

                self.hardware_zone_state = "CLEAR"
                self.hardware_alarm_state = False

                print(
                    "[HARDWARE] ESP32 cleared."
                )

            except Exception as exc:

                print(
                    "[HARDWARE] Failed to clear ESP32: {}".format(
                        exc
                    ),
                    file=sys.stderr,
                )

        # --------------------------------------------------------------
        # STOP PC ALARM
        # --------------------------------------------------------------

        try:

            self.alarm.stop_alarm()

        except Exception as exc:

            print(
                "[ZONE X] Alarm cleanup error: {}".format(
                    exc
                ),
                file=sys.stderr,
            )

        # --------------------------------------------------------------
        # DISCONNECT MQTT
        # --------------------------------------------------------------

        if self.mqtt is not None:

            try:

                self.mqtt.disconnect()

                print(
                    "[ZONE X] MQTT disconnected"
                )

            except Exception as exc:

                print(
                    "[ZONE X] MQTT cleanup error: {}".format(
                        exc
                    ),
                    file=sys.stderr,
                )

        # --------------------------------------------------------------
        # WEB SERVER
        # --------------------------------------------------------------

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

        # --------------------------------------------------------------
        # EVENT LOG
        # --------------------------------------------------------------

        try:

            self.events.close_session()

        except Exception as exc:

            print(
                "[ZONE X] Event log cleanup error: {}".format(
                    exc
                ),
                file=sys.stderr,
            )

        # --------------------------------------------------------------
        # CAMERA
        # --------------------------------------------------------------

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

        # --------------------------------------------------------------
        # OPENCV
        # --------------------------------------------------------------

        try:

            cv2.destroyAllWindows()

            for _ in range(3):
                cv2.waitKey(1)

        except cv2.error:
            pass

    # ==================================================================
    # MAIN LOOP
    # ==================================================================

    def run(self):
        """Main processing loop."""

        self.running = True
        empty_frame_count = 0

        while self.running:

            if not self.paused:

                if self.schedule.due():

                    ok, frame = self.source.read()

                    if ok:

                        self.schedule.mark()

                        empty_frame_count = 0

                        started = time.perf_counter()

                        self.process_frame(
                            frame
                        )

                        self.source.stats.note_process_time(
                            time.perf_counter()
                            - started
                        )

                        self.last_frame = frame

                        self.fps_meter.tick()

                    elif self._source_finished():

                        break

                    elif (
                        self._handle_no_frame(
                            empty_frame_count
                        ) is False
                    ):

                        break

                    else:

                        empty_frame_count += 1

                else:

                    self._idle()

            else:

                if self.last_frame is not None:

                    frame = (
                        self.last_frame.copy()
                    )

                    self.render(frame)

                    cv2.imshow(
                        self.cfg.WINDOW_NAME,
                        frame,
                    )

                    self._publish_frame(
                        frame
                    )

            if self._handle_keys() is False:
                break

            if self._window_closed():

                print(
                    "[ZONE X] Window closed by the user."
                )

                break

            self._maybe_print_stats()

        self.running = False

    # ==================================================================
    # FRAME SOURCE HELPERS
    # ==================================================================

    def _source_finished(self):
        """Check if video has ended."""

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
        """Handle a frame read failure."""

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

    def _idle(self):
        """Wait briefly when processing isn't due yet."""

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

    # ==================================================================
    # STATISTICS
    # ==================================================================

    def _maybe_print_stats(self):
        """Print performance statistics."""

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

    # ==================================================================
    # PROCESS ONE FRAME
    # ==================================================================

    def process_frame(self, frame):
        """
        Process one image frame.

        Existing Vision logic is preserved.
        """

        cfg = self.cfg

        # ==============================================================
        # FRAME SIZE
        # ==============================================================

        height, width = frame.shape[:2]

        self.zones.set_frame_size(
            width,
            height,
        )

        self.frame_index += 1

        # ==============================================================
        # 1. PERSON DETECTION
        # ==============================================================

        detections = self.detector.detect(
            frame
        )

        # ==============================================================
        # 2. PERSON TRACKING
        # ==============================================================

        self.people = self.tracker.update(
            detections,
            self.zones,
        )

        # ==============================================================
        # 3. HAND / FOOT DETECTION
        # ==============================================================

        self._update_hand_state(
            frame
        )

        # ==============================================================
        # 4. RISK CALCULATION
        # ==============================================================

        self.risks = {
            person["id"]:
                self.risk_engine.evaluate_person(
                    person,
                    self.machine_on,
                )
            for person in self.people
        }

        # ==============================================================
        # 4.5 ESP32 HARDWARE
        # ==============================================================

        self._update_hardware()

        # ==============================================================
        # 5. HIGHEST RISK
        # ==============================================================

        self.worst_id, self.worst = (
            self._worst_risk()
        )

        # ==============================================================
        # 6. EVENTS
        # ==============================================================

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

        # ==============================================================
        # 7. EXISTING PC ALARM
        # ==============================================================

        self._update_alarm()

        # ==============================================================
        # 8. ALERT TIMING
        # ==============================================================

        now = time.time()

        if any(
            person.get("zone")
            == cfg.ZONE_DANGER
            or person.get(
                "hand_in_danger"
            )
            for person in self.people
        ):

            self.last_violation_time = now

        if (
            self.worst is not None
            and self.risk_engine.is_critical(
                self.worst["risk"]
            )
        ):

            self.last_critical_time = now

        # ==============================================================
        # 9. RENDER
        # ==============================================================

        self.render(frame)

        cv2.imshow(
            self.cfg.WINDOW_NAME,
            frame,
        )

        self._publish_frame(
            frame
        )

    # ==================================================================
    # WEB FRAME PUBLISH
    # ==================================================================

    def _publish_frame(self, frame):
        """Send frame to web browsers."""

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

    # ==================================================================
    # HAND / FOOT STATE UPDATE
    # ==================================================================

    def _update_hand_state(self, frame):
        """Update wrist and foot state from pose model."""

        if self.pose is None or not self.people:
            return

        every = max(
            1,
            int(
                self.cfg.POSE_EVERY_N_FRAMES
            ),
        )

        if self.frame_index % every != 0:
            return

        from src.pose.pose_detector import (
            associate_keypoints,
        )

        poses = self.pose.detect(
            frame
        )

        keypoint_map = associate_keypoints(
            self.people,
            poses,
            self.cfg.POSE_MATCH_MIN_IOU,
        )

        # --------------------------------------------------------------
        # FEET
        # --------------------------------------------------------------

        for person in self.people:

            person_id = person["id"]

            if person_id in keypoint_map:

                keypoints = (
                    keypoint_map[
                        person_id
                    ]
                )

                person["feet"] = (
                    keypoints.get(
                        "feet",
                        [],
                    )
                )

                feet_in_danger = False

                for foot_x, foot_y in (
                    person["feet"]
                ):

                    zone_type, _ = (
                        self.zones.get_zone_at(
                            foot_x,
                            foot_y,
                        )
                    )

                    if (
                        zone_type
                        == self.cfg.ZONE_DANGER
                    ):

                        feet_in_danger = True
                        break

                person["feet_in_danger"] = (
                    feet_in_danger
                )

        # --------------------------------------------------------------
        # WRISTS
        # --------------------------------------------------------------

        wrist_map = {}

        for person_id, keypoints in (
            keypoint_map.items()
        ):

            wrist_map[person_id] = (
                keypoints.get(
                    "wrists",
                    [],
                )
            )

        self.tracker.update_hands(
            self.people,
            wrist_map,
            self.zones,
        )

    # ==================================================================
    # CHECK CRITICAL
    # ==================================================================

    def _is_critical(self, person):
        """
        A person is critical when:

        - body is in danger
        - hand is in danger
        - feet are in danger
        """

        return (
            person.get("zone")
            == self.cfg.ZONE_DANGER
            or bool(
                person.get(
                    "hand_in_danger"
                )
            )
            or bool(
                person.get(
                    "feet_in_danger"
                )
            )
        )

    # ==================================================================
    # ALARM OFFENDERS
    # ==================================================================

    def _alarm_offenders(self, dwelling):
        """
        Existing PC-alarm offender logic.
        """

        offenders = [
            person
            for person in self.people
            if self._is_critical(person)
        ]

        warning_threshold = getattr(
            self.cfg,
            "WARNING_ALARM_THRESHOLD_SEC",
            self.cfg.WARNING_DWELL_THRESHOLD_SEC,
        )

        warning_offenders = [
            person
            for person in self.people
            if (
                person.get("zone")
                == self.cfg.ZONE_WARNING
                and person.get(
                    "warning_duration",
                    0,
                )
                >= warning_threshold
            )
        ]

        offenders.extend(
            warning_offenders
        )

        claimed = {
            person["id"]
            for person in offenders
        }

        for incident in dwelling:

            if incident.track_id in claimed:
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

    # ==================================================================
    # UPDATE PC ALARM ONLY
    # ==================================================================

    def _update_alarm(self):
        """
        Update the existing PC alarm and event logging.

        IMPORTANT:
        ESP32 hardware is NOT controlled here.
        _update_hardware() is the only ESP32 controller.
        """

        if not self.cfg.ENABLE_PC_ALARM:
            return

        dwelling = (
            self.events.warning_dwell_violations(
                frame=self.last_frame
            )
        )

        offenders = (
            self._alarm_offenders(
                dwelling
            )
        )

        alarm_should_be_active = bool(
            offenders
        )

        if alarm_should_be_active:

            self.alarm.start_alarm()

        else:

            self.alarm.stop_alarm()

        if self.alarm.is_alarm_active():

            for incident in dwelling:
                incident.alarm_triggered = True

        self.events.update_alarm(
            self.alarm.is_alarm_active(),
            offenders,
            self.risks,
            self.machine_on,
            self.cfg,
        )

    # ==================================================================
    # FIND PERSON
    # ==================================================================

    def _person(self, track_id):
        """Find a person by tracking ID."""

        if track_id is None:
            return None

        for person in self.people:

            if person["id"] == track_id:
                return person

        return None

    # ==================================================================
    # FIND WORST RISK
    # ==================================================================

    def _worst_risk(self):
        """Find the person with the highest risk score."""

        worst_id = None
        worst = None

        for person_id, verdict in (
            self.risks.items()
        ):

            if (
                worst is None
                or verdict["risk"]
                > worst["risk"]
            ):

                worst_id = person_id
                worst = verdict

        return worst_id, worst

    # ==================================================================
    # HIGHEST RISK
    # ==================================================================

    @property
    def highest_risk(self):
        """Return the highest risk score."""

        if self.worst is None:
            return 0

        return self.worst["risk"]

    # ==================================================================
    # ZONE COUNTS
    # ==================================================================

    def _zone_counts(self):
        """Count people in each zone."""

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

    # ==================================================================
    # RENDER
    # ==================================================================

    def render(self, frame):
        """Draw all visual elements."""

        cfg = self.cfg

        counts = self._zone_counts()
        now = time.time()

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

        violated_ids = set()

        for person in self.people:

            if person.get(
                "zone"
            ) in (
                cfg.ZONE_WARNING,
                cfg.ZONE_DANGER,
            ):

                if person.get("zone_id"):

                    violated_ids.add(
                        person["zone_id"]
                    )

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

        self.zones.draw(
            frame,
            violated_ids=(
                violated_ids
            ),
        )

        ui.draw_people(
            frame,
            self.people,
            cfg,
        )

        status = self._current_status(
            alert_active,
            critical_active,
        )

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

        ui.draw_hand_alert(
            frame,
            cfg,
            self.people,
        )

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

        if cfg.SHOW_WORKER_PANEL:

            ui.draw_worker_panel(
                frame,
                cfg,
                self.people,
                self.risks,
            )

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

        ui.draw_zone_message(
            frame,
            cfg,
            self.zones.last_message,
        )

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

        if cfg.SHOW_HELP_BAR:

            ui.draw_help_bar(
                frame,
                cfg,
                defining=(
                    self.zones.is_defining
                ),
            )

        ui.draw_person_cards(
            frame,
            self.people,
            cfg,
            self.risks,
        )

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

    # ==================================================================
    # CURRENT STATUS
    # ==================================================================

    def _current_status(
        self,
        alert_active,
        critical_active,
    ):
        """Determine current system status."""

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

    # ==================================================================
    # BANNER
    # ==================================================================

    def _banner_override(
        self,
        status,
    ):
        """Return custom banner text."""

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

    # ==================================================================
    # PRINT EVENT
    # ==================================================================

    @staticmethod
    def _print_event(record):
        """Print event."""

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

    # ==================================================================
    # WINDOW
    # ==================================================================

    def _window_closed(self):
        """Check whether OpenCV window is closed."""

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

    def _handle_keys(self):
        """Check keyboard input."""

        key = (
            cv2.waitKey(1)
            & 0xFF
        )

        if key == 255:
            return True

        return self._apply_control_key(
            key
        )

    # ==================================================================
    # RESET STATE WHEN ZONES CHANGE
    # ==================================================================

    def _forget_zone_state(self):
        """
        Reset tracking/risk/event state when zones change.
        """

        self.tracker.reset()
        self.events.reset()

        self.risks = {}

        self.worst_id = None
        self.worst = None

        self.last_violation_time = 0.0
        self.last_critical_time = 0.0

        # Reset hardware state tracking.
        self.hardware_zone_state = None

        # Reset danger hold timer.
        self.hardware_danger_until = 0.0


def print_controls():
    """Print keyboard shortcut guide."""

    print("")
    print(
        "  ------------------------------------------------------------"
    )
    print(
        "   ZONE X - sentinel factory safety monitoring"
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


def main():
    """Program entry point."""

    project_root = os.path.dirname(
        os.path.abspath(
            __file__
        )
    )

    os.chdir(
        project_root
    )

    # ==============================================================
    # SOURCE SELECTOR
    # ==============================================================

    show_selector = True

    if len(sys.argv) > 1:
        show_selector = False

    if (
        os.environ.get(
            "ZONE_SKIP_SELECTOR",
            "",
        ).lower()
        == "true"
    ):
        show_selector = False

    if show_selector:

        try:

            print(
                "\n[ZONE X] Opening source selector..."
            )

            selection_made = (
                config.show_source_selector()
            )

            if selection_made:

                print(
                    "[ZONE X] Source selected: {}".format(
                        config.SOURCE_TYPE
                    )
                )

                if (
                    config.SOURCE_TYPE
                    == "webcam"
                ):

                    print(
                        "[ZONE X] Camera index: {}".format(
                            config.CAMERA_INDEX
                        )
                    )

                elif config.SOURCE_TYPE in [
                    "ip",
                    "cctv",
                ]:

                    print(
                        "[ZONE X] URL: {}".format(
                            config.SOURCE_URL
                        )
                    )

                elif (
                    config.SOURCE_TYPE
                    == "video"
                ):

                    print(
                        "[ZONE X] Video path: {}".format(
                            config.VIDEO_PATH
                        )
                    )

            else:

                print(
                    "[ZONE X] Using default source: "
                    "webcam (camera 0)"
                )

        except Exception as exc:

            print(
                "[ZONE X] Error showing source selector: {}".format(
                    exc
                )
            )

            print(
                "[ZONE X] Continuing with default webcam source..."
            )

    # ==============================================================
    # CREATE APP
    # ==============================================================

    app = ZoneXApp(
        config
    )

    exit_code = 0

    try:

        app.setup()
        app.run()

    except ModelLoadError as exc:

        print(
            "\n[ZONE X] MODEL ERROR\n{}\n".format(
                exc
            ),
            file=sys.stderr,
        )

        exit_code = 2

    except FrameSourceError as exc:

        print(
            "\n[ZONE X] FRAME SOURCE ERROR ({})\n{}\n".format(
                config.SOURCE_TYPE,
                exc,
            ),
            file=sys.stderr,
        )

        exit_code = 3

    except KeyboardInterrupt:

        print(
            "\n[ZONE X] Interrupted with Ctrl+C."
        )

    except Exception:

        import traceback

        print(
            "\n[ZONE X] UNEXPECTED ERROR",
            file=sys.stderr,
        )

        traceback.print_exc()

        exit_code = 1

    finally:

        app.cleanup()

    print(
        "[ZONE X] Shutdown complete."
    )

    return exit_code


if __name__ == "__main__":
    sys.exit(
        main()
    )