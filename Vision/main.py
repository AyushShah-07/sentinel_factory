# """
# ZONE X - Smart Safety Monitoring System
# Phase 1: Local Computer Vision Prototype

# main.py
# -------
# Wires the modules together and runs the real-time loop:

#     FrameSource        (webcam / phone / CCTV / video file -- main.py cannot tell)
#       -> frame
#       -> detector        (YOLO person detection)
#       -> tracker         (P1/P2 IDs + hysteresis + per-person danger timer)
#       -> foot point
#       -> ZoneManager     (SAFE_n / WARNING_n / DANGER_n)
#            |                    |
#            v                    v
#       pose_detector        machine state
#       (wrist point)        (simulated)
#            |                    |
#            +---------+----------+
#                      v
#                  RiskEngine     (rules only -- no model, no LLM)
#                      v
#               risk + reasons
#                      v
#                  UI / events -> JSON log

# There is deliberately no camera code in this file. Which input is used is decided
# entirely by config.SOURCE_TYPE and implemented in src/frame_source.py, so adding
# a source later cannot require a change here.

# Run it with:   python main.py

# Keyboard:
#     s      add a SAFE zone
#     w      add a WARNING zone
#     d      add a DANGER zone   (r does the same, kept from Phase 1)
#     Enter  finish the current polygon
#     c      clear all zones (or cancel the polygon being drawn)
#     m      toggle the simulated machine ON / OFF
#     p      pause / resume
#     q/Esc  quit
# """

# import collections
# import os
# import sys
# import time

# import cv2

# import config
# from src.visualization import ui
# from src.alarm.alarm import Alarm
# from src.detection.detector import ModelLoadError, PersonDetector
# from src.events.events import EventLog
# from src.camera.frame_source import FrameSource, FrameSourceError, ProcessingScheduler
# from src.safety.risk_engine import RiskEngine
# from src.detection.tracker import PersonTracker
# from src.zones.zone import ZoneManager
# from src.integration.connection import BackendClient

# # Key codes. OpenCV reports Enter as 13 on Windows and 10 on some Linux builds,
# # so we accept both. Esc is 27.
# KEY_ENTER = (13, 10)
# KEY_ESC = 27


# # ---------------------------------------------------------------------------
# # fps
# # ---------------------------------------------------------------------------

# class FpsMeter:
#     """Rolling-average FPS, so the number on screen does not jitter."""

#     def __init__(self, window=30):
#         self._durations = collections.deque(maxlen=max(1, window))
#         self._last_time = None

#     def tick(self):
#         now = time.perf_counter()
#         if self._last_time is not None:
#             self._durations.append(now - self._last_time)
#         self._last_time = now

#     @property
#     def fps(self):
#         if not self._durations:
#             return 0.0
#         average = sum(self._durations) / len(self._durations)
#         return 1.0 / average if average > 0 else 0.0


# def _incident_offender(incident):
#     """
#     A minimal person stand-in built from an open warning incident.

#     Used only when a dwell violation belongs to a worker who is not in the current
#     frame (the detector missed them, but the tracker still holds the track). It
#     carries exactly the fields the alarm event reports -- who, and which zone.
#     """
#     return {
#         "id": incident.track_id,
#         "person_id": incident.person_id,
#         "zone": incident.zone_type,
#         "zone_id": incident.zone_id,
#         "hand_zone": None,
#         "hand_zone_id": None,
#         "hand_in_danger": False,
#         "danger_duration": 0.0,
#         "distance_to_danger": None,
#     }


# # ---------------------------------------------------------------------------
# # application
# # ---------------------------------------------------------------------------

# class ZoneXApp:
#     """The prototype: one frame source, YOLO person detection, safety zones."""

#     def __init__(self, cfg):
#         self.cfg = cfg
#         self.source = None
#         # Decides WHEN the pipeline takes another frame. Built in setup(), once the
#         # source is known -- a sequentially read file wants no rate limit at all.
#         self.schedule = ProcessingScheduler(None)
#         self._last_stats_print = 0.0
#         self.paused = False
#         self.running = False

#         # Latest results, kept so the display stays populated while paused.
#         self.people = []
#         self.last_frame = None
#         self.risks = {}             # person_id -> risk_engine verdict
#         self.worst_id = None        # the person driving the headline alarm
#         self.worst = None
#         self.frame_index = 0

#         # ALERT_HOLD_SECONDS keeps the big warning visible briefly after the
#         # last violation, so a momentary breach is not missed by the operator.
#         self.last_violation_time = 0.0
#         self.last_critical_time = 0.0

#         # Simulated machine state. Phase 3 replaces this with ESP32 telemetry;
#         # for now the operator toggles it with 'm'.
#         self.machine_on = bool(cfg.MACHINE_ON)

#         self.fps_meter = FpsMeter(cfg.FPS_SMOOTHING_WINDOW)

#         self.zones = ZoneManager(cfg)

#         self.tracker = PersonTracker(
#             enabled=cfg.ENABLE_TRACKING,
#             max_distance=cfg.TRACK_MAX_DISTANCE,
#             max_missing_frames=cfg.TRACK_MAX_MISSING_FRAMES,
#             zone_margin_px=cfg.ZONE_MARGIN_PX,
#             enter_confirm_frames=cfg.ENTER_CONFIRM_FRAMES,
#             exit_confirm_frames=cfg.EXIT_CONFIRM_FRAMES,
#             hand_margin_px=cfg.HAND_MARGIN_PX,
#             hand_enter_confirm_frames=cfg.HAND_ENTER_CONFIRM_FRAMES,
#             hand_exit_confirm_frames=cfg.HAND_EXIT_CONFIRM_FRAMES,
#         )

#         self.risk_engine = RiskEngine(cfg)

#         self.events = EventLog(
#             max_events=cfg.MAX_STORED_EVENTS,
#             danger_time_threshold=cfg.DANGER_TIME_THRESHOLD_SEC,
#             critical_threshold=cfg.RISK_CRITICAL_THRESHOLD,
#             on_event=self._print_event if cfg.LOG_EVENTS_TO_CONSOLE else None,
#             # Stamped onto every event so the backend knows whether a record came
#             # from a webcam test, a phone, real CCTV, or recorded footage.
#             source_type=cfg.SOURCE_TYPE,
#             schema_version=cfg.EVENT_SCHEMA_VERSION,
#             log_risk_changes=cfg.LOG_RISK_CHANGES,
#             log_root=cfg.LOG_ROOT,
#             # Warning-zone incidents: how long a visit may last before the buzzer,
#             # and where the one-per-incident evidence photo is written.
#             warning_dwell_threshold=cfg.WARNING_DWELL_THRESHOLD_SEC,
#             incident_dir=cfg.INCIDENT_DIR,
#             save_incident_images=cfg.SAVE_INCIDENT_IMAGES,
#         )

#         self.backend = BackendClient(
#     base_url=cfg.BACKEND_URL,
#     endpoint=cfg.BACKEND_INCIDENT_ENDPOINT,
# )


#         # The PC buzzer. It makes noise and nothing else -- the decision to sound
#         # it is taken in _update_alarm() from the tracker's CONFIRMED state.
#         self.alarm = Alarm.from_config(cfg)

#         self.detector = None  # created in setup(), which can fail loudly
#         self.pose = None      # optional; None means reach-in detection is off

#     # -- setup / teardown ----------------------------------------------------

#     def setup(self):
#         """Load the models, open the frame source, create the window."""
#         self.detector = PersonDetector(
#             model_path=self.cfg.MODEL_PATH,
#             download_name=self.cfg.MODEL_DOWNLOAD_NAME,
#             confidence_threshold=self.cfg.CONFIDENCE_THRESHOLD,
#             iou_threshold=self.cfg.IOU_THRESHOLD,
#             person_class_id=self.cfg.PERSON_CLASS_ID,
#             imgsz=self.cfg.INFERENCE_IMGSZ,
#             device=self.cfg.DEVICE,
#             verbose=self.cfg.VERBOSE_INFERENCE,
#         )

#         self._setup_pose()

#         # ONE line, whatever the input is. Webcam, phone, CCTV and video file are
#         # all resolved inside FrameSource from config.SOURCE_TYPE.
#         self.source = FrameSource.from_config(self.cfg).open()

#         # Rate-limit the pipeline on a monotonic clock, NOT by counting frames, so
#         # a 15 FPS phone and a 60 FPS webcam both process at the same real rate.
#         # A file read sequentially (VIDEO_REALTIME = False) is left ungated: there
#         # every frame is wanted and wall-clock time is irrelevant.
#         self.schedule = ProcessingScheduler(
#             self.cfg.TARGET_PROCESSING_FPS if self.source.uses_reader_thread else None)
#         if self.schedule.enabled:
#             print("[ZONE X] Processing target: {:.0f} FPS "
#                   "(newest frame only; older frames are dropped by design)."
#                   .format(self.schedule.target_fps))
#         else:
#             print("[ZONE X] Processing every frame in order "
#                   "(VIDEO_REALTIME = False).")

#         self._open_log_session()

#         cv2.namedWindow(self.cfg.WINDOW_NAME, cv2.WINDOW_NORMAL)
#         cv2.resizeWindow(self.cfg.WINDOW_NAME, self.cfg.WINDOW_WIDTH, self.cfg.WINDOW_HEIGHT)
#         # The zone manager owns all mouse handling and routes clicks to whichever
#         # zone is being drawn; it ignores clicks otherwise, so a live perimeter
#         # cannot be moved by accident.
#         cv2.setMouseCallback(self.cfg.WINDOW_NAME, self.zones.handle_mouse)

#         print_controls()

#     def _setup_pose(self):
#         """
#         Load the pose model for reach-in detection, if it is enabled.

#         Pose is an OPTIONAL extra signal. If the weights cannot be downloaded we
#         warn and carry on with pose disabled -- losing reach-in detection is bad,
#         but refusing to start the whole safety monitor over it would be worse.
#         """
#         if not self.cfg.ENABLE_POSE:
#             print("[ZONE X] Pose disabled (ENABLE_POSE = False): "
#                   "no hand / reach-in detection.")
#             return

#         # Imported here so a missing pose model cannot break the core pipeline
#         # at import time.
#         from src.pose.pose_detector import WristDetector

#         try:
#             self.pose = WristDetector(
#                 model_path=self.cfg.POSE_MODEL_PATH,
#                 download_name=self.cfg.POSE_MODEL_DOWNLOAD_NAME,
#                 confidence_threshold=self.cfg.POSE_CONFIDENCE_THRESHOLD,
#                 keypoint_confidence=self.cfg.POSE_KEYPOINT_CONFIDENCE,
#                 wrist_ids=self.cfg.WRIST_KEYPOINT_IDS,
#                 imgsz=self.cfg.POSE_IMGSZ,
#                 device=self.cfg.DEVICE,
#                 verbose=self.cfg.VERBOSE_INFERENCE,
#             )
#         except ModelLoadError as exc:
#             self.pose = None
#             print("\n[ZONE X] POSE MODEL UNAVAILABLE -- continuing without "
#                   "hand / reach-in detection.\n{}\n".format(exc), file=sys.stderr)

#     def _open_log_session(self):
#         """
#         Start a fresh logs/<timestamp>/ directory for this run.

#         Called once the frame source is open, so a session file always
#         corresponds to a run that actually saw frames.

#         NOTE what is NOT recorded: the source URL. It can contain camera
#         credentials, and these files are meant to be shipped to a backend.
#         """
#         if not self.cfg.ENABLE_JSON_LOGGING:
#             return

#         events_path = self.events.open_session(
#             log_root=self.cfg.LOG_ROOT,
#             metadata={
#                 "source_type": self.source.source_type,
#                 "frame_width": self.source.frame_width,
#                 "frame_height": self.source.frame_height,
#                 "model": os.path.basename(self.cfg.MODEL_PATH),
#                 "pose_model": (os.path.basename(self.cfg.POSE_MODEL_PATH)
#                                if self.pose is not None else None),
#                 "machine_on_at_start": self.machine_on,
#                 "danger_time_threshold_sec": self.cfg.DANGER_TIME_THRESHOLD_SEC,
#                 "zone_priority": list(self.cfg.ZONE_PRIORITY),
#             },
#         )
#         if events_path:
#             print("[ZONE X] Logging events to {}".format(events_path))

#     def cleanup(self):
#         # Silence the buzzer first: whatever else goes wrong during shutdown, the
#         # room must not be left beeping.
#         self.alarm.stop_alarm()

#         # Stamp the session file before anything else, so a failure releasing the
#         # camera cannot cost us the log summary.
#         self.events.close_session()

#         if self.source is not None:
#             self.source.release()
#         cv2.destroyAllWindows()
#         # Some OpenCV/Qt builds need a few extra waitKey calls to actually close.
#         for _ in range(3):
#             cv2.waitKey(1)

#     # -- main loop -----------------------------------------------------------

#     def run(self):
#         """
#         The processing loop.

#         It does NOT read every frame the camera produces. It asks the frame source
#         for the NEWEST frame roughly TARGET_PROCESSING_FPS times a second; the
#         source's reader thread has been throwing away everything older in the
#         meantime. On a 30 FPS stream with a 10 FPS pipeline that means about 20
#         frames a second are dropped, which is the intended behaviour -- a fresh
#         frame is worth more to a safety monitor than a complete one.
#         """
#         self.running = True
#         empty_frame_count = 0

#         while self.running:
#             if not self.paused:
#                 if self.schedule.due():
#                     # The pipeline starts here and asks for nothing but a frame.
#                     # Mirroring, playback pacing, dropping and reconnection all
#                     # belong to the source, not to this loop.
#                     ok, frame = self.source.read()

#                     if ok:
#                         # Mark BEFORE processing: the interval is "time between
#                         # starts", so a slow frame does not earn extra delay.
#                         self.schedule.mark()
#                         empty_frame_count = 0

#                         started = time.perf_counter()
#                         self.process_frame(frame)
#                         self.source.stats.note_process_time(
#                             time.perf_counter() - started)

#                         self.last_frame = frame
#                         self.fps_meter.tick()
#                     elif self._source_finished():
#                         break
#                     elif self._handle_no_frame(empty_frame_count) is False:
#                         break
#                     else:
#                         empty_frame_count += 1
#                 else:
#                     # Not due yet. Idle briefly instead of spinning a whole core.
#                     self._idle()
#             else:
#                 # Paused: redraw the frozen frame so the UI stays interactive.
#                 if self.last_frame is not None:
#                     frame = self.last_frame.copy()
#                     self.render(frame)
#                     cv2.imshow(self.cfg.WINDOW_NAME, frame)

#             if self._handle_keys() is False:
#                 break

#             if self._window_closed():
#                 print("[ZONE X] Window closed by the user.")
#                 break

#             self._maybe_print_stats()

#     def _source_finished(self):
#         """True when a recording has played to its end and the buffer is empty."""
#         if not self.source.ended:
#             return False
#         print("[ZONE X] End of video reached.")
#         return True

#     def _handle_no_frame(self, empty_frame_count):
#         """
#         Decide what "no frame right now" means. Returns False to stop the app.

#         For a LIVE source it almost always means "the camera has not produced a new
#         frame since we last looked", which is completely normal -- we poll faster
#         than the camera sends. Only a source that has gone quiet for
#         SOURCE_STALL_TIMEOUT seconds is treated as lost, which gives the reader
#         thread time to reconnect first.

#         A sequentially read file has no reader thread, so it keeps the original
#         MAX_EMPTY_FRAMES rule.
#         """
#         if self.source.uses_reader_thread:
#             stalled = self.source.stalled_for
#             if stalled >= self.cfg.SOURCE_STALL_TIMEOUT:
#                 detail = self.source.reader_message
#                 raise FrameSourceError(
#                     "No frames from the {} for {:.1f} seconds.\n"
#                     "  {}"
#                     "  The device may have been unplugged, the stream may have "
#                     "dropped, the phone may have locked its screen, or another "
#                     "program may have claimed it."
#                     .format(self.source.description, stalled,
#                             "Last reader error: {}\n  ".format(detail) if detail else "")
#                 )
#             return True

#         if empty_frame_count + 1 >= self.cfg.MAX_EMPTY_FRAMES:
#             raise FrameSourceError(
#                 "Lost the {} ({} empty frames in a row).\n"
#                 "  The device may have been unplugged, the stream may "
#                 "have dropped, or another program may have claimed it."
#                 .format(self.source.description, empty_frame_count + 1)
#             )
#         return True

#     def _idle(self):
#         """
#         Wait out the gap until the next processing slot.

#         Sleeping in small slices keeps the window responsive (the keyboard handler
#         below runs every pass and is what pumps the OpenCV event queue) while
#         keeping CPU use near zero between frames.
#         """
#         remaining = self.schedule.seconds_until_due()
#         if remaining > 0.0:
#             time.sleep(min(0.004, remaining))

#     def _maybe_print_stats(self):
#         """
#         Print the frame-pipeline diagnostics every STATS_CONSOLE_INTERVAL_SEC.

#         DIAGNOSTICS ONLY. These never go into the JSON event log -- they describe
#         our own plumbing, not anything that happened on the factory floor.
#         """
#         interval = getattr(self.cfg, "STATS_CONSOLE_INTERVAL_SEC", 0.0)
#         if interval <= 0:
#             return
#         now = time.monotonic()
#         if (now - self._last_stats_print) < interval:
#             return
#         self._last_stats_print = now
#         print(self.source.stats.console_line())

#     def process_frame(self, frame):
#         """Detect, decide, draw, show. Called once per live frame."""
#         cfg = self.cfg
#         height, width = frame.shape[:2]
#         self.zones.set_frame_size(width, height)
#         self.frame_index += 1

#         detections = self.detector.detect(frame)

#         # The tracker applies the hysteresis and the per-person danger timer, so
#         # every person's `zone` and `danger_duration` are already debounced by
#         # the time they reach the rule engine.
#         self.people = self.tracker.update(detections, self.zones)

#         self._update_hand_state(frame)

#         # THE RULE ENGINE DECIDES. Everything above only measured facts.
#         self.risks = {
#             person["id"]: self.risk_engine.evaluate_person(person, self.machine_on)
#             for person in self.people
#         }
#         self.worst_id, self.worst = self._worst_risk()

#         # live_ids matters: it is what stops a one-frame YOLO miss being logged as
#         # the worker leaving and instantly coming back.
#         #
#         # `frame` is passed for ONE purpose: the evidence photo taken when a
#         # warning-zone entry is confirmed. This call happens BEFORE render(), so
#         # that image is the real camera frame with none of our overlays on it.
#         self.events.update(self.people, self.risks, self.machine_on, cfg,
#                            live_ids=self.tracker.live_ids, frame=frame)

#         self._update_alarm()

#         now = time.time()
#         if any(person["zone"] == cfg.ZONE_DANGER or person["hand_in_danger"]
#                for person in self.people):
#             self.last_violation_time = now
#         if self.worst is not None and self.risk_engine.is_critical(self.worst["risk"]):
#             self.last_critical_time = now

#         self.render(frame)
#         cv2.imshow(self.cfg.WINDOW_NAME, frame)

#     def _update_hand_state(self, frame):
#         """
#         Run the pose pass and fold wrist observations into each person's
#         confirmed hand state.

#         On frames where pose is skipped (POSE_EVERY_N_FRAMES) we do nothing at
#         all: the tracker already carried the previous confirmed hand state
#         forward, so the UI stays stable and no observation is invented.
#         """
#         if self.pose is None or not self.people:
#             return
#         every = max(1, int(self.cfg.POSE_EVERY_N_FRAMES))
#         if self.frame_index % every != 0:
#             return

#         from src.pose.pose_detector import associate_wrists

#         poses = self.pose.detect(frame)
#         wrist_map = associate_wrists(self.people, poses, self.cfg.POSE_MATCH_MIN_IOU)
#         self.tracker.update_hands(self.people, wrist_map, self.zones)

#     def _is_critical(self, person):
#         """
#         Is this worker in a condition that must sound the buzzer?

#         Two conditions, both read from the tracker's CONFIRMED state:

#             body zone == DANGER        standing in a danger zone
#             hand_in_danger             a confirmed reach-in, wherever they stand

#         Both have already been through the hysteresis in tracker.py
#         (ENTER_CONFIRM_FRAMES / HAND_ENTER_CONFIRM_FRAMES), so a single noisy
#         frame cannot trigger the alarm and a single missed frame cannot silence
#         it. This is NOT a second danger-decision system: it reads the same
#         debounced facts the risk engine reads.
#         """
#         return (person.get("zone") == self.cfg.ZONE_DANGER
#                 or bool(person.get("hand_in_danger")))

#     def _alarm_offenders(self, dwelling):
#         """
#         Everyone whose confirmed state currently justifies the buzzer.

#         `dwelling` is the open warning incidents already past the dwell threshold,
#         passed in so the caller can also mark them once the buzzer really sounds.

#         Three conditions, and a person only has to meet one:

#             body zone == DANGER            standing in a danger zone
#             hand_in_danger                 a confirmed reach-in
#             warning dwell exceeded         in a warning zone for longer than
#                                            WARNING_DWELL_THRESHOLD_SEC

#         The warning-dwell part comes from the open INCIDENT rather than from this
#         frame's people list, so a worker the detector misses for a frame or two
#         cannot silence the alarm. When that happens their person dict is not
#         available, so the incident stands in for it -- person_id and zone_id are
#         what the ALARM_STARTED event needs, and the incident has both.
#         """
#         offenders = [person for person in self.people if self._is_critical(person)]
#         claimed = {person["id"] for person in offenders}

#         for incident in dwelling:
#             if incident.track_id in claimed:
#                 continue
#             claimed.add(incident.track_id)
#             visible = self._person(incident.track_id)
#             offenders.append(visible if visible is not None
#                              else _incident_offender(incident))

#         return offenders

#     def _update_alarm(self):
#         """
#         Sound the buzzer while ANY worker is in a critical condition; silence it
#         when the last one clears.

#         The alarm is a function of the whole scene, not of one person: with P1 in
#         DANGER and P2 safe it stays on, and it only stops once nobody is left in a
#         danger zone, no reach-in remains, and no warning-zone visit is still past
#         its dwell threshold. start_alarm() is a no-op while the alarm is already
#         sounding, so this is safe to call every frame -- one alarm, one thread,
#         however many frames go past.
#         """
#         if not self.cfg.ENABLE_PC_ALARM:
#             return

#         dwelling = self.events.warning_dwell_violations(frame=self.last_frame) #added by admin
#         offenders = self._alarm_offenders(dwelling)

#         if offenders:
#             self.alarm.start_alarm()
#         else:
#             self.alarm.stop_alarm()

#         # Record on each warning incident that the buzzer REALLY sounded for it.
#         # That is what alarm_triggered means in the logged incident: not "passed the
#         # threshold", but "the alarm went off". With ENABLE_PC_ALARM off, or if the
#         # speaker refuses, it stays false.
#         if self.alarm.is_alarm_active():
#             for incident in dwelling:
#                 incident.alarm_triggered = True

#         # One ALARM_STARTED / ALARM_STOPPED per transition -- never one per beep.
#         self.events.update_alarm(self.alarm.is_alarm_active(), offenders,
#                                  self.risks, self.machine_on, self.cfg)

#     def _person(self, track_id):
#         """The person dict for a track id, or None. Used for display labels."""
#         if track_id is None:
#             return None
#         for person in self.people:
#             if person["id"] == track_id:
#                 return person
#         return None

#     def _worst_risk(self):
#         """The (track_id, verdict) with the highest score. (None, None) if empty."""
#         worst_id = None
#         worst = None
#         for person_id, verdict in self.risks.items():
#             if worst is None or verdict["risk"] > worst["risk"]:
#                 worst_id, worst = person_id, verdict
#         return worst_id, worst

#     @property
#     def highest_risk(self):
#         """Max risk across every tracked worker, for the global HUD summary."""
#         return self.worst["risk"] if self.worst is not None else 0

#     def _zone_counts(self):
#         """How many people are standing in each zone right now."""
#         counts = {}
#         for person in self.people:
#             zone = person.get("zone")
#             counts[zone] = counts.get(zone, 0) + 1
#         return counts

#     def render(self, frame):
#         """Compose the complete display for one frame."""
#         cfg = self.cfg
#         counts = self._zone_counts()
#         now = time.time()

#         # Hold the alert briefly after the last violation.
#         alert_active = (
#             self.zones.any_ready
#             and (now - self.last_violation_time) <= cfg.ALERT_HOLD_SECONDS
#         )
#         critical_active = (
#             self.zones.any_ready
#             and (now - self.last_critical_time) <= cfg.ALERT_HOLD_SECONDS
#         )

#         # Only light up the zones that actually have someone in them -- and, now
#         # that zones have their own tags, only the specific polygon involved.
#         # With two machines, a worker in DANGER_2 must not redden DANGER_1.
#         violated_ids = set()
#         for person in self.people:
#             if person.get("zone") in (cfg.ZONE_WARNING, cfg.ZONE_DANGER):
#                 if person.get("zone_id"):
#                     violated_ids.add(person["zone_id"])
#             if person.get("hand_in_danger") and person.get("hand_zone_id"):
#                 violated_ids.add(person["hand_zone_id"])

#         self.zones.draw(frame, violated_ids=violated_ids)
#         # Boxes, labels, feet and wrists now; the per-person cards are drawn at
#         # the very end of this method so nothing can cover them.
#         ui.draw_people(frame, self.people, cfg)

#         status = self._current_status(alert_active, critical_active)
#         if status == "CRITICAL":
#             ui.draw_alert_border(frame, cfg, cfg.CRITICAL_BANNER_TEXT)
#         elif status == "DANGER":
#             ui.draw_alert_border(frame, cfg, cfg.ALERT_TEXT)

#         # The reach-in banner is separate from the danger-zone alarm on purpose:
#         # a safe-standing worker with a hand in the machine must not be reported
#         # as "worker in the danger zone".
#         ui.draw_hand_alert(frame, cfg, self.people)

#         ui.draw_hud(
#             frame, cfg,
#             people_count=len(self.people),
#             inside_count=counts.get(cfg.ZONE_DANGER, 0),
#             fps=self.fps_meter.fps,
#             status=status,
#             zone_info=self.zones.summary(),
#             paused=self.paused,
#             machine_on=self.machine_on,
#             zone_counts=counts,
#             banner_text=self._banner_override(status),
#             # The global summary is the MAX across everyone on screen, not the
#             # risk of whichever worker happened to be first in the list.
#             highest_risk=self.highest_risk,
#             critical=self.risk_engine.is_critical(self.highest_risk),
#         )

#         if cfg.SHOW_WORKER_PANEL:
#             ui.draw_worker_panel(frame, cfg, self.people, self.risks)

#         if cfg.SHOW_REASON_PANEL and self.worst is not None and self.worst["risk"] > 0:
#             ui.draw_reason_panel(frame, cfg, self.worst, self._person(self.worst_id))

#         ui.draw_zone_message(frame, cfg, self.zones.last_message)

#         # Frame-pipeline diagnostics: source vs processing rate and dropped frames.
#         if getattr(cfg, "SHOW_SOURCE_STATS", False):
#             ui.draw_source_stats(frame, cfg, self.source.stats)

#         if cfg.SHOW_HELP_BAR:
#             ui.draw_help_bar(frame, cfg, defining=self.zones.is_defining)

#         # LAST: each worker's own zone / hand / timer / risk, on top of
#         # everything else, so it is always readable.
#         ui.draw_person_cards(frame, self.people, cfg, self.risks)

#         if self.paused:
#             ui.draw_center_message(frame, cfg, ["PAUSED", "press P to resume"],
#                                    cfg.COLOR_ACCENT)

#     def _current_status(self, alert_active, critical_active):
#         cfg = self.cfg
#         if self.paused:
#             return "PAUSED"
#         if self.zones.is_defining:
#             return "DEFINING"
#         if not self.zones.any_ready:
#             return "NO_ZONE"
#         if critical_active:
#             return "CRITICAL"
#         if alert_active:
#             return "DANGER"
#         if any(person["zone"] == cfg.ZONE_WARNING for person in self.people):
#             return "WARNING"
#         return "CLEAR"

#     def _banner_override(self, status):
#         """Name the zone being drawn in the status banner."""
#         if status != "DEFINING":
#             return None
#         zone_type = self.zones.defining_type
#         label = self.cfg.ZONE_LABELS.get(zone_type, "ZONE")
#         return self.cfg.DEFINING_ZONE_BANNER_TEXT.format(label)

#     @staticmethod
#     def _print_event(record):
#         """
#         Echo one safety event to the console (EventLog callback).

#             [Event] 12:30:45  ZONE_ENTER  P1  DANGER_1  risk=70

#         Reads the same flat fields that go into the JSON file, so what scrolls past
#         on the console is exactly what was logged.
#         """
#         who = record.get("person_id") or "system"
#         where = "  {}".format(record["zone_id"]) if record.get("zone_id") else ""
#         risk = record.get("risk")
#         score = "" if risk is None else "  risk={}".format(risk)

#         # A WARNING_ZONE_INCIDENT has no "timestamp" or "risk": it is a completed
#         # visit, so it reports its own duration instead.
#         duration = record.get("duration_sec")
#         if duration is not None:
#             score = "  {:.1f}s{}".format(
#                 duration, "  ALARM" if record.get("alarm_triggered") else "")

#         # Time only: the date is in the filename and on every logged record.
#         stamp = (record.get("timestamp") or record.get("exit_time") or "")[11:19]
#         print("[Event] {}  {}  {}{}{}".format(
#             stamp, record["event_type"], who, where, score))

#     def _window_closed(self):
#         """Detect the window's X button so we exit instead of hanging."""
#         try:
#             return cv2.getWindowProperty(self.cfg.WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1
#         except cv2.error:
#             return True

#     # -- keyboard ------------------------------------------------------------

#     def _handle_keys(self):
#         """
#         Process one keypress. Returns False to request shutdown.

#         waitKey(1) is what actually pumps the OpenCV window's event queue --
#         without it the window would never repaint or respond to the mouse.
#         """
#         cfg = self.cfg
#         key = cv2.waitKey(1) & 0xFF
#         if key == 255:
#             return True  # no key pressed

#         if key in (ord("q"), ord("Q"), KEY_ESC):
#             print("[ZONE X] Quit requested.")
#             return False

#         # s / w / d pick which of the three zones to draw. 'r' is kept from
#         # Phase 1 and maps to DANGER, the closest equivalent of the old
#         # single restricted area.
#         zone_keys = {
#             ord("s"): cfg.ZONE_SAFE, ord("S"): cfg.ZONE_SAFE,
#             ord("w"): cfg.ZONE_WARNING, ord("W"): cfg.ZONE_WARNING,
#             ord("d"): cfg.ZONE_DANGER, ord("D"): cfg.ZONE_DANGER,
#             ord("r"): cfg.ZONE_DANGER, ord("R"): cfg.ZONE_DANGER,
#         }

#         if key in zone_keys:
#             zone_type = zone_keys[key]
#             # ADDS a zone; the ones already drawn are untouched. Its unique tag
#             # (SAFE_2, DANGER_3, ...) is issued by the zone manager.
#             self.zones.start_definition(zone_type)
#             self._forget_zone_state()
#             self.paused = False
#             print("[ZONE X] Defining {}: click points, Enter to finish.".format(
#                 self.zones.defining_id or cfg.ZONE_LABELS[zone_type]))

#         elif key in (ord("c"), ord("C")):
#             # While drawing, 'c' cancels just that polygon (Phase 1 behaviour).
#             if self.zones.is_defining:
#                 zone_type = self.zones.cancel_definition()
#                 print("[ZONE X] Cancelled {}.".format(cfg.ZONE_LABELS[zone_type]))
#             else:
#                 self.zones.clear()
#                 print("[ZONE X] All zones cleared.")
#             self._forget_zone_state()

#         elif key in (ord("m"), ord("M")):
#             self.machine_on = not self.machine_on
#             self.zones.last_message = "Machine {}.".format(
#                 "ON" if self.machine_on else "OFF")
#             print("[ZONE X] Machine {}.".format("ON" if self.machine_on else "OFF"))

#         elif key in (ord("p"), ord("P")):
#             # Pausing mid-definition would strand the operator, so ignore it.
#             if self.zones.is_defining:
#                 self.zones.last_message = "Finish or cancel the zone before pausing."
#             else:
#                 self.paused = not self.paused
#                 print("[ZONE X] {}".format("Paused." if self.paused else "Resumed."))

#         elif key in KEY_ENTER:
#             if self.zones.is_defining:
#                 ok, _ = self.zones.finish_definition()
#                 if ok:
#                     zone = self.zones.last_finished
#                     self._forget_zone_state()
#                     print("[ZONE X] {} set with {} points (area {:.0f} px^2).".format(
#                         zone.zone_id, zone.point_count, zone.area))
#                 else:
#                     print("[ZONE X] {}".format(self.zones.last_message))

#         return True

#     def _forget_zone_state(self):
#         """
#         Drop everything derived from the previous zone layout.

#         Confirmed in/out states, dwell timers and event history all refer to the
#         polygons that existed when they were recorded, so they must not leak into
#         a freshly drawn zone.
#         """
#         self.tracker.reset()
#         self.events.reset()
#         self.risks = {}
#         self.worst_id = None
#         self.worst = None
#         self.last_violation_time = 0.0
#         self.last_critical_time = 0.0


# # ---------------------------------------------------------------------------
# # entry point
# # ---------------------------------------------------------------------------

# def print_controls():
#     print("")
#     print("  ------------------------------------------------------------")
#     print("   ZONE X - Smart Safety Monitoring System  (CV Prototype)")
#     print("  ------------------------------------------------------------")
#     print("   s      add a SAFE zone      (SAFE_1, SAFE_2, ...)")
#     print("   w      add a WARNING zone   (WARNING_1, WARNING_2, ...)")
#     print("   d      add a DANGER zone    (DANGER_1, DANGER_2, ...; r also works)")
#     print("   click  add a polygon point   (right click = undo)")
#     print("   Enter  finish the polygon")
#     print("   c      clear all zones  (cancel, while drawing)")
#     print("   m      toggle the simulated machine ON / OFF")
#     print("   p      pause / resume")
#     print("   q      quit  (Esc also works)")
#     print("  ------------------------------------------------------------")
#     print("   Draw DANGER around each machine, WARNING around that, and")
#     print("   SAFE over the walkway. Overlap is fine -- DANGER always wins.")
#     print("   Each polygon gets its own tag, and the tag is what the event")
#     print("   log records.")
#     print("  ------------------------------------------------------------")
#     print("")


# def main():
#     # Run from the project root so the relative models/ path always resolves,
#     # no matter which directory the user launched python from.
#     os.chdir(os.path.dirname(os.path.abspath(__file__)))

#     app = ZoneXApp(config)
#     exit_code = 0

#     try:
#         app.setup()
#         app.run()
#     except ModelLoadError as exc:
#         print("\n[ZONE X] MODEL ERROR\n{}\n".format(exc), file=sys.stderr)
#         exit_code = 2
#     except FrameSourceError as exc:
#         # A misconfigured source is an operator problem, not a bug: print the
#         # explanation FrameSource built and exit quietly, with no traceback.
#         print("\n[ZONE X] FRAME SOURCE ERROR ({})\n{}\n".format(
#             config.SOURCE_TYPE, exc), file=sys.stderr)
#         exit_code = 3
#     except KeyboardInterrupt:
#         print("\n[ZONE X] Interrupted with Ctrl+C.")
#     except Exception as exc:
#         import traceback

#         print("\n[ZONE X] UNEXPECTED ERROR")
#         traceback.print_exc()

#         exit_code = 1
#     finally:
#         app.cleanup()

#     print("[ZONE X] Shutdown complete.")
#     return exit_code


# if __name__ == "__main__":
#     sys.exit(main())




"""
ZONE X - Smart Safety Monitoring System
Phase 1: Local Computer Vision Prototype

main.py
-------

Wires the modules together and runs the real-time loop:

    FrameSource
        -> frame
        -> detector
        -> tracker
        -> ZoneManager
        -> pose_detector
        -> RiskEngine
        -> UI / EventLog
        -> Local JSON + Incident Image
        -> Spring Boot Backend

The backend integration is intentionally kept at the EventLog boundary.

When EventLog produces a real event:
    1. Existing JSON logging continues.
    2. Existing evidence image saving continues.
    3. The same event is sent to Spring Boot.
    4. The image path contained in the event is sent along with it.

There is deliberately no camera code in this file.
Which input is used is decided entirely by config.SOURCE_TYPE
and implemented in src/camera/frame_source.py.

Run it with:

    python main.py

Keyboard:
    s      add a SAFE zone
    w      add a WARNING zone
    d      add a DANGER zone
    r      add a DANGER zone
    Enter  finish the current polygon
    c      clear all zones
    m      toggle simulated machine ON / OFF
    p      pause / resume
    q/Esc  quit
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
from src.events.events import EventLog
from src.camera.frame_source import (
    FrameSource,
    FrameSourceError,
    ProcessingScheduler,
)
from src.safety.risk_engine import RiskEngine
from src.detection.tracker import PersonTracker
from src.zones.zone import ZoneManager
from src.integration.connection import BackendClient


# ---------------------------------------------------------------------------
# Key codes
# ---------------------------------------------------------------------------

# OpenCV reports Enter as 13 on Windows and 10 on some Linux builds.
KEY_ENTER = (13, 10)

# Escape
KEY_ESC = 27


# ---------------------------------------------------------------------------
# FPS
# ---------------------------------------------------------------------------

class FpsMeter:
    """Rolling-average FPS, so the number on screen does not jitter."""

    def __init__(self, window=30):
        self._durations = collections.deque(
            maxlen=max(1, window)
        )
        self._last_time = None

    def tick(self):
        now = time.perf_counter()

        if self._last_time is not None:
            self._durations.append(
                now - self._last_time
            )

        self._last_time = now

    @property
    def fps(self):
        if not self._durations:
            return 0.0

        average = sum(self._durations) / len(self._durations)

        return (
            1.0 / average
            if average > 0
            else 0.0
        )


# ---------------------------------------------------------------------------
# Incident offender
# ---------------------------------------------------------------------------

def _incident_offender(incident):
    """
    A minimal person stand-in built from an open warning incident.

    Used only when a dwell violation belongs to a worker who is not in the
    current frame.

    It carries exactly the fields the alarm event reports.
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


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class ZoneXApp:
    """
    The prototype:

        frame
          ↓
        detection
          ↓
        tracking
          ↓
        zones
          ↓
        risk engine
          ↓
        event log
          ↓
        local JSON + image
          ↓
        Spring Boot backend
    """

    def __init__(self, cfg):

        self.cfg = cfg

        self.source = None

        # Decides WHEN the pipeline takes another frame.
        self.schedule = ProcessingScheduler(None)

        self._last_stats_print = 0.0

        self.paused = False
        self.running = False

        # -------------------------------------------------------------------
        # Latest results
        # -------------------------------------------------------------------

        self.people = []
        self.last_frame = None

        self.risks = {}

        self.worst_id = None
        self.worst = None

        self.frame_index = 0

        # -------------------------------------------------------------------
        # Alert timing
        # -------------------------------------------------------------------

        self.last_violation_time = 0.0
        self.last_critical_time = 0.0

        # -------------------------------------------------------------------
        # Simulated machine state
        # -------------------------------------------------------------------

        self.machine_on = bool(
            cfg.MACHINE_ON
        )

        # -------------------------------------------------------------------
        # FPS
        # -------------------------------------------------------------------

        self.fps_meter = FpsMeter(
            cfg.FPS_SMOOTHING_WINDOW
        )

        # -------------------------------------------------------------------
        # Zones
        # -------------------------------------------------------------------

        self.zones = ZoneManager(cfg)

        # -------------------------------------------------------------------
        # Tracker
        # -------------------------------------------------------------------

        self.tracker = PersonTracker(
            enabled=cfg.ENABLE_TRACKING,
            max_distance=cfg.TRACK_MAX_DISTANCE,
            max_missing_frames=cfg.TRACK_MAX_MISSING_FRAMES,
            zone_margin_px=cfg.ZONE_MARGIN_PX,
            enter_confirm_frames=cfg.ENTER_CONFIRM_FRAMES,
            exit_confirm_frames=cfg.EXIT_CONFIRM_FRAMES,
            hand_margin_px=cfg.HAND_MARGIN_PX,
            hand_enter_confirm_frames=cfg.HAND_ENTER_CONFIRM_FRAMES,
            hand_exit_confirm_frames=cfg.HAND_EXIT_CONFIRM_FRAMES,
        )

        # -------------------------------------------------------------------
        # Risk engine
        # -------------------------------------------------------------------

        self.risk_engine = RiskEngine(cfg)

        # -------------------------------------------------------------------
        # Event log
        # -------------------------------------------------------------------

        self.events = EventLog(
            max_events=cfg.MAX_STORED_EVENTS,
            danger_time_threshold=cfg.DANGER_TIME_THRESHOLD_SEC,
            critical_threshold=cfg.RISK_CRITICAL_THRESHOLD,

            # IMPORTANT:
            #
            # Existing console logging is preserved.
            #
            # Backend sending is handled separately so that the existing
            # EventLog behavior remains unchanged.
            on_event=self._handle_event,

            source_type=cfg.SOURCE_TYPE,
            schema_version=cfg.EVENT_SCHEMA_VERSION,
            log_risk_changes=cfg.LOG_RISK_CHANGES,
            log_root=cfg.LOG_ROOT,

            warning_dwell_threshold=cfg.WARNING_DWELL_THRESHOLD_SEC,
            incident_dir=cfg.INCIDENT_DIR,
            save_incident_images=cfg.SAVE_INCIDENT_IMAGES,
        )

        # -------------------------------------------------------------------
        # Spring Boot backend
        # -------------------------------------------------------------------

        self.backend = BackendClient(
            base_url="http://localhost:8080",
            endpoint="/api/incidents",
        )

        # -------------------------------------------------------------------
        # PC alarm
        # -------------------------------------------------------------------

        self.alarm = Alarm.from_config(cfg)

        # -------------------------------------------------------------------
        # Models
        # -------------------------------------------------------------------

        self.detector = None
        self.pose = None

    # -----------------------------------------------------------------------
    # Backend event integration
    # -----------------------------------------------------------------------

    def _handle_event(self, record):
        """
        Handle one event produced by EventLog.

        IMPORTANT:

        This method does NOT create the event.

        EventLog is still responsible for:
            - deciding whether an event happened
            - generating the JSON record
            - saving the evidence image

        This method only forwards the already-created event to Spring Boot.

        Therefore the existing safety logic remains untouched.
        """

        # ---------------------------------------------------------------
        # Existing console output
        # ---------------------------------------------------------------

        if self.cfg.LOG_EVENTS_TO_CONSOLE:
            self._print_event(record)

        # ---------------------------------------------------------------
        # Send event to Spring Boot
        # ---------------------------------------------------------------

        try:

            if self.backend is None:
                return

            # The BackendClient receives the SAME event record that
            # EventLog has already generated.
            #
            # The record contains the incident image path when an image
            # was captured.
            self.backend.send_incident(record)

            print(
                "[BACKEND] Event sent successfully: {}".format(
                    record.get("event_type", "UNKNOWN")
                )
            )

        except Exception as exc:

            # Backend failure must NOT stop the computer-vision safety
            # pipeline.
            #
            # Local JSON logging and local evidence image storage have
            # already happened and therefore remain available.
            print(
                "[BACKEND] Failed to send event: {}".format(exc),
                file=sys.stderr,
            )

    # -----------------------------------------------------------------------
    # Setup
    # -----------------------------------------------------------------------

    def setup(self):
        """Load the models, open the frame source, create the window."""

        self.detector = PersonDetector(
            model_path=self.cfg.MODEL_PATH,
            download_name=self.cfg.MODEL_DOWNLOAD_NAME,
            confidence_threshold=self.cfg.CONFIDENCE_THRESHOLD,
            iou_threshold=self.cfg.IOU_THRESHOLD,
            person_class_id=self.cfg.PERSON_CLASS_ID,
            imgsz=self.cfg.INFERENCE_IMGSZ,
            device=self.cfg.DEVICE,
            verbose=self.cfg.VERBOSE_INFERENCE,
        )

        self._setup_pose()

        # One line regardless of source type.
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

        if self.schedule.enabled:

            print(
                "[ZONE X] Processing target: {:.0f} FPS "
                "(newest frame only; older frames are dropped by design)."
                .format(self.schedule.target_fps)
            )

        else:

            print(
                "[ZONE X] Processing every frame in order "
                "(VIDEO_REALTIME = False)."
            )

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

        print_controls()

    # -----------------------------------------------------------------------
    # Pose
    # -----------------------------------------------------------------------

    def _setup_pose(self):
        """
        Load the pose model for reach-in detection, if enabled.
        """

        if not self.cfg.ENABLE_POSE:

            print(
                "[ZONE X] Pose disabled (ENABLE_POSE = False): "
                "no hand / reach-in detection."
            )

            return

        from src.pose.pose_detector import WristDetector

        try:

            self.pose = WristDetector(
                model_path=self.cfg.POSE_MODEL_PATH,
                download_name=self.cfg.POSE_MODEL_DOWNLOAD_NAME,
                confidence_threshold=self.cfg.POSE_CONFIDENCE_THRESHOLD,
                keypoint_confidence=self.cfg.POSE_KEYPOINT_CONFIDENCE,
                wrist_ids=self.cfg.WRIST_KEYPOINT_IDS,
                imgsz=self.cfg.POSE_IMGSZ,
                device=self.cfg.DEVICE,
                verbose=self.cfg.VERBOSE_INFERENCE,
            )

        except ModelLoadError as exc:

            self.pose = None

            print(
                "\n[ZONE X] POSE MODEL UNAVAILABLE -- continuing without "
                "hand / reach-in detection.\n{}\n"
                .format(exc),
                file=sys.stderr,
            )

    # -----------------------------------------------------------------------
    # Log session
    # -----------------------------------------------------------------------

    def _open_log_session(self):
        """
        Start a fresh logs/<timestamp>/ directory for this run.
        """

        if not self.cfg.ENABLE_JSON_LOGGING:
            return

        events_path = self.events.open_session(
            log_root=self.cfg.LOG_ROOT,
            metadata={
                "source_type": self.source.source_type,
                "frame_width": self.source.frame_width,
                "frame_height": self.source.frame_height,

                "model": os.path.basename(
                    self.cfg.MODEL_PATH
                ),

                "pose_model": (
                    os.path.basename(
                        self.cfg.POSE_MODEL_PATH
                    )
                    if self.pose is not None
                    else None
                ),

                "machine_on_at_start": self.machine_on,

                "danger_time_threshold_sec":
                    self.cfg.DANGER_TIME_THRESHOLD_SEC,

                "zone_priority":
                    list(self.cfg.ZONE_PRIORITY),
            },
        )

        if events_path:

            print(
                "[ZONE X] Logging events to {}"
                .format(events_path)
            )

    # -----------------------------------------------------------------------
    # Cleanup
    # -----------------------------------------------------------------------

    def cleanup(self):

        # Silence the buzzer first.
        self.alarm.stop_alarm()

        # Close local session.
        self.events.close_session()

        if self.source is not None:
            self.source.release()

        cv2.destroyAllWindows()

        for _ in range(3):
            cv2.waitKey(1)

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------

    def run(self):

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

                        self.process_frame(frame)

                        self.source.stats.note_process_time(
                            time.perf_counter() - started
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

                    frame = self.last_frame.copy()

                    self.render(frame)

                    cv2.imshow(
                        self.cfg.WINDOW_NAME,
                        frame,
                    )

            if self._handle_keys() is False:
                break

            if self._window_closed():

                print(
                    "[ZONE X] Window closed by the user."
                )

                break

            self._maybe_print_stats()

    # -----------------------------------------------------------------------
    # Source
    # -----------------------------------------------------------------------

    def _source_finished(self):

        if not self.source.ended:
            return False

        print(
            "[ZONE X] End of video reached."
        )

        return True

    def _handle_no_frame(self, empty_frame_count):

        if self.source.uses_reader_thread:

            stalled = self.source.stalled_for

            if stalled >= self.cfg.SOURCE_STALL_TIMEOUT:

                detail = self.source.reader_message

                raise FrameSourceError(
                    "No frames from the {} for {:.1f} seconds.\n"
                    "  {}"
                    .format(
                        self.source.description,
                        stalled,
                        "Last reader error: {}\n  "
                        .format(detail)
                        if detail
                        else "",
                    )
                )

            return True

        if (
            empty_frame_count + 1
            >= self.cfg.MAX_EMPTY_FRAMES
        ):

            raise FrameSourceError(
                "Lost the {} ({} empty frames in a row).\n"
                "  The device may have been unplugged, the stream may "
                "have dropped, or another program may have claimed it."
                .format(
                    self.source.description,
                    empty_frame_count + 1,
                )
            )

        return True

    def _idle(self):

        remaining = (
            self.schedule.seconds_until_due()
        )

        if remaining > 0.0:

            time.sleep(
                min(0.004, remaining)
            )

    # -----------------------------------------------------------------------
    # Statistics
    # -----------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # Frame processing
    # -----------------------------------------------------------------------

    def process_frame(self, frame):
        """
        Detect, decide, draw, show.

        The existing event pipeline remains unchanged.
        """

        cfg = self.cfg

        height, width = frame.shape[:2]

        self.zones.set_frame_size(
            width,
            height,
        )

        self.frame_index += 1

        # ---------------------------------------------------------------
        # YOLO detection
        # ---------------------------------------------------------------

        detections = self.detector.detect(
            frame
        )

        # ---------------------------------------------------------------
        # Tracking
        # ---------------------------------------------------------------

        self.people = self.tracker.update(
            detections,
            self.zones,
        )

        # ---------------------------------------------------------------
        # Pose / hand state
        # ---------------------------------------------------------------

        self._update_hand_state(frame)

        # ---------------------------------------------------------------
        # Risk engine
        # ---------------------------------------------------------------

        self.risks = {
            person["id"]:
                self.risk_engine.evaluate_person(
                    person,
                    self.machine_on,
                )

            for person in self.people
        }

        self.worst_id, self.worst = (
            self._worst_risk()
        )

        # ===============================================================
        # EXISTING EVENT FLOW
        # ===============================================================
        #
        # DO NOT REMOVE OR MODIFY THIS.
        #
        # EventLog:
        #   - detects event
        #   - creates JSON
        #   - saves evidence image
        #   - calls _handle_event()
        #
        # _handle_event():
        #   - prints event
        #   - sends same event to Spring Boot
        #
        # ===============================================================

        self.events.update(
            self.people,
            self.risks,
            self.machine_on,
            cfg,
            live_ids=self.tracker.live_ids,
            frame=frame,
        )

        # ---------------------------------------------------------------
        # Alarm
        # ---------------------------------------------------------------

        self._update_alarm()

        now = time.time()

        if any(
            person["zone"] == cfg.ZONE_DANGER
            or person["hand_in_danger"]
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

        # ---------------------------------------------------------------
        # Render
        # ---------------------------------------------------------------

        self.render(frame)

        cv2.imshow(
            self.cfg.WINDOW_NAME,
            frame,
        )

    # -----------------------------------------------------------------------
    # Hand state
    # -----------------------------------------------------------------------

    def _update_hand_state(self, frame):

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

        if self.frame_index % every != 0:
            return

        from src.pose.pose_detector import (
            associate_wrists,
        )

        poses = self.pose.detect(frame)

        wrist_map = associate_wrists(
            self.people,
            poses,
            self.cfg.POSE_MATCH_MIN_IOU,
        )

        self.tracker.update_hands(
            self.people,
            wrist_map,
            self.zones,
        )

    # -----------------------------------------------------------------------
    # Critical state
    # -----------------------------------------------------------------------

    def _is_critical(self, person):

        return (
            person.get("zone")
            == self.cfg.ZONE_DANGER

            or bool(
                person.get("hand_in_danger")
            )
        )

    # -----------------------------------------------------------------------
    # Alarm offenders
    # -----------------------------------------------------------------------

    def _alarm_offenders(self, dwelling):

        offenders = [
            person
            for person in self.people
            if self._is_critical(person)
        ]

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

            offenders.append(
                visible
                if visible is not None
                else _incident_offender(
                    incident
                )
            )

        return offenders

    # -----------------------------------------------------------------------
    # Alarm
    # -----------------------------------------------------------------------

    def _update_alarm(self):

        if not self.cfg.ENABLE_PC_ALARM:
            return

        dwelling = (
            self.events.warning_dwell_violations(
                frame=self.last_frame
            )
        )

        offenders = self._alarm_offenders(
            dwelling
        )

        if offenders:

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

    # -----------------------------------------------------------------------
    # Person
    # -----------------------------------------------------------------------

    def _person(self, track_id):

        if track_id is None:
            return None

        for person in self.people:

            if person["id"] == track_id:
                return person

        return None

    # -----------------------------------------------------------------------
    # Worst risk
    # -----------------------------------------------------------------------

    def _worst_risk(self):

        worst_id = None
        worst = None

        for person_id, verdict in self.risks.items():

            if (
                worst is None
                or verdict["risk"]
                > worst["risk"]
            ):

                worst_id = person_id
                worst = verdict

        return worst_id, worst

    @property
    def highest_risk(self):

        return (
            self.worst["risk"]
            if self.worst is not None
            else 0
        )

    # -----------------------------------------------------------------------
    # Zone counts
    # -----------------------------------------------------------------------

    def _zone_counts(self):

        counts = {}

        for person in self.people:

            zone = person.get("zone")

            counts[zone] = (
                counts.get(zone, 0)
                + 1
            )

        return counts

    # -----------------------------------------------------------------------
    # Render
    # -----------------------------------------------------------------------

    def render(self, frame):

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

            if person.get("zone") in (
                cfg.ZONE_WARNING,
                cfg.ZONE_DANGER,
            ):

                if person.get("zone_id"):

                    violated_ids.add(
                        person["zone_id"]
                    )

            if (
                person.get("hand_in_danger")
                and person.get("hand_zone_id")
            ):

                violated_ids.add(
                    person["hand_zone_id"]
                )

        self.zones.draw(
            frame,
            violated_ids=violated_ids,
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
            zone_info=self.zones.summary(),
            paused=self.paused,
            machine_on=self.machine_on,
            zone_counts=counts,
            banner_text=self._banner_override(
                status
            ),
            highest_risk=self.highest_risk,
            critical=self.risk_engine.is_critical(
                self.highest_risk
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
                defining=self.zones.is_defining,
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

    # -----------------------------------------------------------------------
    # Current status
    # -----------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # Banner
    # -----------------------------------------------------------------------

    def _banner_override(self, status):

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

    # -----------------------------------------------------------------------
    # Event printer
    # -----------------------------------------------------------------------

    @staticmethod
    def _print_event(record):

        who = (
            record.get("person_id")
            or "system"
        )

        where = (
            "  {}".format(
                record["zone_id"]
            )
            if record.get("zone_id")
            else ""
        )

        risk = record.get("risk")

        score = (
            ""
            if risk is None
            else "  risk={}".format(risk)
        )

        duration = record.get(
            "duration_sec"
        )

        if duration is not None:

            score = "  {:.1f}s{}".format(
                duration,
                "  ALARM"
                if record.get(
                    "alarm_triggered"
                )
                else "",
            )

        stamp = (
            record.get("timestamp")
            or record.get("exit_time")
            or ""
        )[11:19]

        print(
            "[Event] {}  {}  {}{}{}"
            .format(
                stamp,
                record["event_type"],
                who,
                where,
                score,
            )
        )

    # -----------------------------------------------------------------------
    # Window
    # -----------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # Keyboard
    # -----------------------------------------------------------------------

    def _handle_keys(self):

        cfg = self.cfg

        key = cv2.waitKey(1) & 0xFF

        if key == 255:
            return True

        if key in (
            ord("q"),
            ord("Q"),
            KEY_ESC,
        ):

            print(
                "[ZONE X] Quit requested."
            )

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
                "click points, Enter to finish."
                .format(
                    self.zones.defining_id
                    or cfg.ZONE_LABELS[
                        zone_type
                    ]
                )
            )

        elif key in (
            ord("c"),
            ord("C"),
        ):

            if self.zones.is_defining:

                zone_type = (
                    self.zones.cancel_definition()
                )

                print(
                    "[ZONE X] Cancelled {}."
                    .format(
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

        elif key in (
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
                "[ZONE X] Machine {}."
                .format(
                    "ON"
                    if self.machine_on
                    else "OFF"
                )
            )

        elif key in (
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
                    "[ZONE X] {}."
                    .format(
                        "Paused."
                        if self.paused
                        else "Resumed."
                    )
                )

        elif key in KEY_ENTER:

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
                        "{} points (area {:.0f} px^2)."
                        .format(
                            zone.zone_id,
                            zone.point_count,
                            zone.area,
                        )
                    )

                else:

                    print(
                        "[ZONE X] {}"
                        .format(
                            self.zones.last_message
                        )
                    )

        return True

    # -----------------------------------------------------------------------
    # Forget zone state
    # -----------------------------------------------------------------------

    def _forget_zone_state(self):

        self.tracker.reset()

        self.events.reset()

        self.risks = {}

        self.worst_id = None
        self.worst = None

        self.last_violation_time = 0.0
        self.last_critical_time = 0.0


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------

def print_controls():

    print("")

    print(
        "  ------------------------------------------------------------"
    )

    print(
        "   ZONE X - Smart Safety Monitoring System  (CV Prototype)"
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
        "   m      toggle the simulated machine ON / OFF"
    )

    print(
        "   p      pause / resume"
    )

    print(
        "   q      quit  (Esc also works)"
    )

    print(
        "  ------------------------------------------------------------"
    )

    print(
        "   Draw DANGER around each machine, WARNING around that,"
    )

    print(
        "   and SAFE over the walkway."
    )

    print(
        "   Each polygon gets its own tag."
    )

    print(
        "  ------------------------------------------------------------"
    )

    print("")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():

    # Run from the project root so relative model paths resolve correctly.

    os.chdir(
        os.path.dirname(
            os.path.abspath(__file__)
        )
    )

    app = ZoneXApp(config)

    exit_code = 0

    try:

        app.setup()

        app.run()

    except ModelLoadError as exc:

        print(
            "\n[ZONE X] MODEL ERROR\n{}\n"
            .format(exc),
            file=sys.stderr,
        )

        exit_code = 2

    except FrameSourceError as exc:

        print(
            "\n[ZONE X] FRAME SOURCE ERROR ({})\n{}\n"
            .format(
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

    except Exception as exc:

        import traceback

        print(
            "\n[ZONE X] UNEXPECTED ERROR"
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
    sys.exit(main())

