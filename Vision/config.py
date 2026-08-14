"""
ZONE X - Smart Safety Monitoring System
Phase 1: Local Computer Vision Prototype

config.py
---------
All tunable settings live here. Edit this file instead of hunting through
the source code. Every value is documented so a beginner can safely change it.

Colors are given as OpenCV BGR tuples: (Blue, Green, Red) -- NOT RGB.
"""

# ---------------------------------------------------------------------------
# YOLO MODEL
# ---------------------------------------------------------------------------

# Where the YOLOv8 weights live. If the file is missing, the app will try to
# download it automatically into this exact location on first run.
MODEL_PATH = "models/yolov8n.pt"

# Which pretrained weights to fetch if MODEL_PATH does not exist yet.
# yolov8n = "nano" = smallest + fastest, which is what we want for real time.
MODEL_DOWNLOAD_NAME = "yolov8n.pt"

# In the COCO dataset that YOLOv8 is pretrained on, class 0 is "person".
# We ignore every other class (cars, chairs, dogs, ...).
PERSON_CLASS_ID = 0

# Detections below this confidence are thrown away.
# Raise it (e.g. 0.6) if you get false detections.
# Lower it (e.g. 0.35) if real people are being missed.
CONFIDENCE_THRESHOLD = 0.5

# Non-Maximum Suppression IoU threshold. Merges duplicate boxes on one person.
IOU_THRESHOLD = 0.45

# Image size fed to the network. 640 is the YOLOv8 default.
# Drop to 480 or 416 for more FPS on a weak CPU (accuracy drops a little).
INFERENCE_IMGSZ = 640

# "cpu", "cuda", "0", or None to let ultralytics pick automatically.
DEVICE = None

# Print the per-frame ultralytics log line to the console. Keep False.
VERBOSE_INFERENCE = False


# ---------------------------------------------------------------------------
# FRAME SOURCE  (where the video comes from)
# ---------------------------------------------------------------------------
#
# THE ONLY PLACE THE INPUT IS CHOSEN. src/frame_source.py reads these four
# settings and hands the rest of the program plain frames; nothing downstream
# knows or cares whether they came from a webcam, a phone, a CCTV camera or a
# file. Switching source is a config edit, never a code edit.
#
# SOURCE_TYPE must be one of:
#
#   "webcam"  a locally attached camera        -> uses CAMERA_INDEX
#   "ip"      phone / HTTP network camera      -> uses SOURCE_URL (http://...)
#   "cctv"    CCTV / RTSP network camera       -> uses SOURCE_URL (rtsp://...)
#   "video"   a recorded file                  -> uses VIDEO_PATH

#
# "ip" and "cctv" are read identically by OpenCV; they are kept separate purely
# so the event log records which kind of installation an event came from.
SOURCE_TYPE ="webcam"

# --- used when SOURCE_TYPE = "webcam" ---
# 0 is usually the built-in/first camera. Try 1, 2, ... for USB cameras.
# This must be an INTEGER. A URL belongs in SOURCE_URL with SOURCE_TYPE = "ip".
CAMERA_INDEX = 0

# --- used when SOURCE_TYPE = "ip" or "cctv" ---
# Never commit a real address here. Examples of the shape expected:
#     ip    "http://<phone-ip>:8080/video"        (IP Webcam)
#           "http://<phone-ip>:4747/video"        (DroidCam)
#     cctv  "rtsp://<user>:<pass>@<camera-ip>:554/Streaming/Channels/101"
SOURCE_URL = "http://172.22.0.144:8080/video" #added by admin

# --- used when SOURCE_TYPE = "video" ---
# Relative paths are resolved from the project root, so this works no matter
# which directory python was launched from.
VIDEO_PATH = "videos/factory_test.mp4"

# Play a video file at its own recorded frame rate instead of as fast as the CPU
# can decode it. Keep True: the danger dwell timer and the hysteresis confirm
# counts are wall-clock/frame based, so a file decoded at 300 FPS would escalate
# on a completely different schedule from the live camera it is standing in for.
VIDEO_REALTIME = True

# Requested capture resolution. Cameras may ignore this and pick their own;
# video files always keep their recorded size.
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720

# Mirror the image like a selfie camera. The flip happens inside the frame
# source, i.e. BEFORE detection and BEFORE drawing, so mouse clicks and the
# drawn zones stay correctly aligned.
# Set False for CCTV and for recorded footage -- mirroring real factory footage
# only makes it harder to compare against the real floor.
MIRROR_PREVIEW = True

# How many blank/corrupt frames in a row we tolerate before giving up.
# Only used for sequential reading (a video file with VIDEO_REALTIME = False).
# Live sources are governed by SOURCE_STALL_TIMEOUT below.
MAX_EMPTY_FRAMES = 30

# Seconds to wait for the source to open before declaring failure.
CAMERA_OPEN_TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# LATEST-FRAME READING AND PROCESSING RATE
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS
# A phone or CCTV stream delivers frames whether or not we are ready for them.
# If we read them one after another in the main loop, the frames waiting in the
# network/OpenCV buffer pile up, and YOLO ends up analysing what the camera saw
# a second ago. For safety monitoring that is the wrong trade: we would rather
# know where the worker is NOW than eventually see every frame they were in.
#
# So a single reader thread continuously drains the source and keeps only the
# NEWEST frame. The main loop takes that newest frame whenever it is ready for
# one. Frames in between are dropped, ON PURPOSE -- see FRAME_READER_BUFFER_SIZE.

# How many frames per second we TRY to push through the AI pipeline.
#
# This is a TIME-based target, not "every Nth frame": the scheduler uses a
# monotonic clock, so it behaves the same whether the source runs at 15, 25, 30
# or 60 FPS. If the pipeline is slower than this on your machine, you simply get
# fewer -- nothing queues up and nothing breaks.
#
# Raise it for snappier detection at the cost of CPU; lower it on a weak CPU.
# NOTE: ENTER_CONFIRM_FRAMES / EXIT_CONFIRM_FRAMES and POSE_EVERY_N_FRAMES count
# PROCESSED frames, so this value also scales how quickly a zone entry is
# confirmed (at 10 FPS, 2 confirm frames is about 0.2 s). The 5-second danger
# dwell rule is wall-clock based and is NOT affected.
TARGET_PROCESSING_FPS = 10.0

# How many frames the reader may hold for the pipeline.
#
# KEEP THIS AT 1. One slot means "the newest frame, and nothing else", which is
# the lowest latency this pipeline can offer. Larger values trade latency for
# smoothness and are only useful if you want to look at near-neighbour frames.
FRAME_READER_BUFFER_SIZE = 1

# Rebuild the connection automatically when a network stream drops (phones
# sleep, wifi hiccups, "Stream ends prematurely"). A single bad frame is never
# fatal; the reader retries, and reconnects if the failures continue.
FRAME_SOURCE_RECONNECT = True

# Seconds with NO new frame at all before the app gives up and exits with an
# explanation. Reconnect attempts happen inside this window, so keep it several
# seconds -- a phone switching wifi bands can be quiet for a moment.
SOURCE_STALL_TIMEOUT = 8.0


# ---------------------------------------------------------------------------
# FRAME-SOURCE DIAGNOSTICS
# ---------------------------------------------------------------------------
#
# These are DIAGNOSTICS, not safety events: they measure our own plumbing and
# are deliberately never written to the JSON event log.

# Show the source/processing panel in the corner of the window.
SHOW_SOURCE_STATS = True

# Print one summary line to the console this often (seconds). 0 disables it.
STATS_CONSOLE_INTERVAL_SEC = 5.0


# ---------------------------------------------------------------------------
# WINDOW
# ---------------------------------------------------------------------------

WINDOW_NAME = "ZONE X - Smart Safety Monitoring System"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 720


# ---------------------------------------------------------------------------
# RESTRICTED ZONE
# ---------------------------------------------------------------------------

# A polygon needs at least 3 points to enclose an area.
MIN_POLYGON_POINTS = 3

# Reject accidental "polygons" that are just a few pixels wide, in px^2.
MIN_POLYGON_AREA = 500.0

# Radius (px) of the little circles drawn on each polygon vertex.
POLYGON_POINT_RADIUS = 6

# Thickness of the polygon outline.
POLYGON_THICKNESS = 2

# How strongly the zone is shaded (0.0 = invisible, 1.0 = solid).
ZONE_FILL_ALPHA = 0.22


# ---------------------------------------------------------------------------
# ALERT LOGIC / HYSTERESIS (false-positive handling)
# ---------------------------------------------------------------------------

# A person's feet point must be at least this many pixels INSIDE the polygon
# before we call it a violation, and at least this many pixels OUTSIDE before
# we clear it. This dead-band stops SAFE/RESTRICTED flicker on the boundary.
ZONE_MARGIN_PX = 6.0

# On top of the margin, the new state must hold for this many consecutive
# frames before it is accepted. Higher = calmer but slower to react.
ENTER_CONFIRM_FRAMES = 2
EXIT_CONFIRM_FRAMES = 5

# Keep the big screen warning up for this long after the last violation ends,
# so a 1-frame breach is still clearly visible to a human operator.
ALERT_HOLD_SECONDS = 1.0


# ---------------------------------------------------------------------------
# LIGHTWEIGHT TRACKER (optional, keeps IDs stable across frames)
# ---------------------------------------------------------------------------

# Turn off to skip tracking entirely; hysteresis then works per-frame only.
ENABLE_TRACKING = True

# Max distance (px) between a detection's feet point and a known track's feet
# point for them to be considered the same person.
TRACK_MAX_DISTANCE = 120.0

# Drop a track after this many frames without a matching detection.
TRACK_MAX_MISSING_FRAMES = 15


# ---------------------------------------------------------------------------
# PERSON IDENTITY (session IDs now, employee names later)
# ---------------------------------------------------------------------------

# Tracks are labelled P1, P2, P3 ... for the length of one monitoring session.
# The number is the tracker's own stable track id, so the label is stable for as
# long as the tracker considers it the same person, and a genuinely new person
# always gets the next unused number (IDs are never recycled within a run).
PERSON_ID_PREFIX = "P"

# Every person carries an identity block:
#     {"person_id": "P1", "employee_id": None,
#      "person_name": None, "identity_status": "UNIDENTIFIED"}
#
# THERE IS NO FACE RECOGNITION IN THIS PHASE. The three name fields exist so a
# later face-recognition pass can fill them in (person_name = "Rahul Kumar",
# identity_status = "IDENTIFIED") without touching the tracker, the risk engine
# or the event schema. Nothing in the safety path may ever read them: a worker
# whose face is not recognised must still be protected exactly as well.
IDENTITY_UNIDENTIFIED = "UNIDENTIFIED"


# ---------------------------------------------------------------------------
# UI / COLORS (BGR)
# ---------------------------------------------------------------------------

COLOR_SAFE = (80, 220, 100)        # green  - person outside the zone
COLOR_ALERT = (60, 60, 255)        # red    - person inside the zone
COLOR_ZONE = (0, 165, 255)         # orange - restricted polygon
COLOR_ZONE_ALERT = (60, 60, 255)   # red    - polygon while violated
COLOR_TEXT = (255, 255, 255)       # white
COLOR_MUTED = (185, 185, 185)      # light grey - secondary text
COLOR_PANEL = (28, 28, 32)         # near-black - HUD background
COLOR_ACCENT = (255, 200, 60)      # cyan/amber - branding accent
COLOR_POINT = (255, 255, 255)      # white - feet marker

# Opacity of the dark HUD panels behind the text.
PANEL_ALPHA = 0.62

# Bounding box line thickness.
BOX_THICKNESS_SAFE = 2
BOX_THICKNESS_ALERT = 3

# Radius of the bottom-center ("feet") marker.
FEET_MARKER_RADIUS = 5

# Draw a thin vertical line from box center down to the feet point, so it is
# obvious which point the decision is based on. Purely educational.
SHOW_FEET_GUIDE = True

# Status strings. OpenCV's built-in fonts are ASCII-only, so we use plain text
# markers such as [!] and [OK] instead of emoji -- emoji render as "?" boxes.
ALERT_TEXT = "RESTRICTED AREA"
ALERT_BANNER_TEXT = "[!] RESTRICTED AREA VIOLATION"
CLEAR_BANNER_TEXT = "[OK] AREA CLEAR"
NO_ZONE_BANNER_TEXT = "[i] NO RESTRICTED AREA DEFINED - PRESS 'R'"
DEFINING_BANNER_TEXT = "[+] DEFINING RESTRICTED AREA - ENTER TO FINISH"
PAUSED_BANNER_TEXT = "[||] PAUSED - PRESS 'P' TO RESUME"

APP_TITLE = "ZONE X"
APP_SUBTITLE = "Smart Safety Monitoring System"

# Pulse the red alert border on/off this many times per second.
ALERT_FLASH_HZ = 2.5

# Number of recent frames averaged together for the FPS readout.
FPS_SMOOTHING_WINDOW = 30

# Show the keyboard-shortcut bar along the bottom of the window.
SHOW_HELP_BAR = True


# ===========================================================================
# PHASE 2 -- MULTI-ZONE SAFETY MONITORING
# Everything below was added on top of the Phase 1 restricted-area prototype.
# Nothing above this line changed.
# ===========================================================================

# ---------------------------------------------------------------------------
# THREE SAFETY ZONES
# ---------------------------------------------------------------------------

# The operator draws three independent polygons. Each one carries a base risk
# score, used by src/risk_engine.py.
ZONE_SAFE = "SAFE"
ZONE_WARNING = "WARNING"
ZONE_DANGER = "DANGER"
ZONE_OUTSIDE = "OUTSIDE"        # not inside any drawn polygon

# Base risk contributed by simply standing in a zone.
ZONE_RISK = {
    ZONE_SAFE: 0,
    ZONE_WARNING: 30,
    ZONE_DANGER: 70,
    ZONE_OUTSIDE: 0,
}

# Human-readable zone-type names, used in banners and operator messages.
ZONE_LABELS = {
    ZONE_SAFE: "SAFE ZONE",
    ZONE_WARNING: "WARNING ZONE",
    ZONE_DANGER: "DANGER ZONE",
}

# ---------------------------------------------------------------------------
# ZONE TAGS / IDS
# ---------------------------------------------------------------------------
#
# The operator may draw as many polygons of each type as the floor needs. Each
# one gets a unique tag from a single centralized counter in src/zone.py:
#
#     SAFE_1  SAFE_2  WARNING_1  DANGER_1  DANGER_2
#
# The tag is unique across ALL zone types, is never reused inside a run (even
# after a zone is deleted), and is what every event in the log refers to. IDs are
# deliberately NOT derived from list positions -- deleting SAFE_1 and drawing a
# new safe zone would then produce a second "SAFE_1" describing different floor.
SHOW_ZONE_TAG = True

# Minimum vertical gap (px) kept between two zone tags whose polygons sit on top
# of each other, so nested zones never print their labels over one another.
ZONE_TAG_MIN_GAP_PX = 4

# WHICH ZONE WINS WHEN POLYGONS OVERLAP.
#
# Operators usually draw these nested: a small DANGER polygon around the
# machine, a WARNING ring around that, SAFE floor around everything. A foot
# point inside the machine is then geometrically inside all three polygons.
#
# We resolve that DANGER-first ("fail dangerous"), which is the only defensible
# default for a safety system -- checking SAFE first would report a worker
# standing on the machine as SAFE.
#
# Set this to (ZONE_SAFE, ZONE_WARNING, ZONE_DANGER) if you instead draw three
# strictly non-overlapping polygons and prefer the literal SAFE-first order.
ZONE_PRIORITY = (ZONE_DANGER, ZONE_WARNING, ZONE_SAFE)

# Per-zone colours (BGR).
COLOR_ZONE_SAFE = (80, 220, 100)      # green
COLOR_ZONE_WARNING = (0, 195, 255)    # amber
COLOR_ZONE_DANGER = (60, 60, 255)     # red

ZONE_COLORS = {
    ZONE_SAFE: COLOR_ZONE_SAFE,
    ZONE_WARNING: COLOR_ZONE_WARNING,
    ZONE_DANGER: COLOR_ZONE_DANGER,
}

# Colour of the person's bounding box per body zone.
PERSON_COLORS = {
    ZONE_SAFE: COLOR_ZONE_SAFE,
    ZONE_WARNING: COLOR_ZONE_WARNING,
    ZONE_DANGER: COLOR_ZONE_DANGER,
    ZONE_OUTSIDE: (210, 210, 210),    # light grey - outside every polygon
}

# The SAFE zone is drawn faintly; it is information, not a warning.
ZONE_FILL_ALPHA_SAFE = 0.12


# ---------------------------------------------------------------------------
# DANGER-ZONE DWELL TIMER (per person)
# ---------------------------------------------------------------------------

# Standing in DANGER longer than this escalates the risk score to
# RISK_DANGER_TIMEOUT. Timed per tracked person, never globally.
DANGER_TIME_THRESHOLD_SEC = 5.0

# NOTE: per-person timing needs stable IDs, so it requires ENABLE_TRACKING.
# With tracking off, danger_duration stays 0.0 and this rule never fires.


# ---------------------------------------------------------------------------
# WARNING-ZONE INCIDENTS  (src/incident_manager.py)
# ---------------------------------------------------------------------------
#
# Entering a warning zone opens an INCIDENT, which is a complete occurrence rather
# than a state change:
#
#     WARNING_ZONE_ENTERED  -> incident_id, entry_time, one evidence photo
#     ...person stays...       nothing is logged, nothing is saved
#     WARNING_ZONE_LEFT     -> exit_time, duration_sec, SAME incident_id
#
# Leaving and coming back is a NEW incident with a new id and a new photo.

# Seconds a worker may stay in a warning zone before the buzzer sounds. Entering is
# not itself an alarm -- lingering is. Timed from the incident's entry time, on the
# wall clock, so it is a real 5 seconds at any processing frame rate.
WARNING_DWELL_THRESHOLD_SEC = 5.0

# Save one photo per incident, taken the moment the entry is confirmed. Set False
# to keep the incident records but write no images (e.g. a privacy-sensitive site,
# or a machine short on disk).
SAVE_INCIDENT_IMAGES = True

# Where those photos go. Kept beside the event log, since they are the evidence for
# the lines in it. Paths recorded in the log are relative to the project root, so a
# backend can map them onto uploaded files later.
INCIDENT_DIR = "logs/incidents"


# ---------------------------------------------------------------------------
# PC ALARM / BEEPER  (src/alarm.py)
# ---------------------------------------------------------------------------
#
# A repeating beep while any worker is in a CONFIRMED critical condition:
#
#     body zone == DANGER        (someone standing in a danger zone)
#     or a confirmed reach-in    (a wrist inside a danger zone)
#     or a warning-zone visit that has passed WARNING_DWELL_THRESHOLD_SEC
#
# The condition is read off the DEBOUNCED tracker state, so the existing
# hysteresis decides -- a single noisy detection can never sound the buzzer. The
# alarm stops only when NO worker is in either condition any more.
#
# Uses winsound, which is part of the Python standard library on Windows. NO extra
# dependency. On other platforms it falls back to the terminal bell.

ENABLE_PC_ALARM = True

# Keep beeping for as long as the danger lasts. False plays one burst only.
ALARM_REPEAT = True

# Tone in Hz. 1000 is a clear, hard-to-ignore alarm pitch; 800-2000 all work.
# winsound accepts 37-32767.
ALARM_BEEP_FREQUENCY = 1000

# One beep, in milliseconds. Short beeps read as "alarm"; long ones as "error".
ALARM_BEEP_DURATION_MS = 180

# Silence between the beeps inside one burst.
ALARM_BEEP_GAP_MS = 90

# BEEP BEEP BEEP BEEP  <- how many per burst
ALARM_BEEPS_PER_BURST = 4

# Silence between bursts, which is what makes the pattern recognisable rather
# than a continuous tone.
ALARM_PAUSE_DURATION_MS = 600


# ---------------------------------------------------------------------------
# MACHINE STATE (simulated for the prototype)
# ---------------------------------------------------------------------------

# Starting state of the simulated machine. Toggle live with the 'm' key.
# Phase 3 replaces this with real ESP32 / backend telemetry.
MACHINE_ON = False


# ---------------------------------------------------------------------------
# DISTANCE TO DANGER ZONE (image space, NOT metres)
# ---------------------------------------------------------------------------

# These are PIXEL distances measured from the person's foot point to the
# nearest edge of the DANGER polygon. Pixels are not metres: the same physical
# gap measures differently near the camera than far from it. Until a camera
# calibration step exists, treat this purely as a relative proximity signal --
# which is why the UI calls it "Distance to Danger Zone (px)".
WARNING_DISTANCE_PX = 120.0    # "getting close to the danger zone"
DANGER_DISTANCE_PX = 50.0      # "right up against the danger zone"

# Risk contributed by proximity alone.
#
# BOTH DEFAULT TO 0, i.e. proximity is a pure EXPLANATION and never changes the
# score. The documented rule table is exactly: SAFE 0 / WARNING 30 / DANGER 70 /
# DANGER>5s 90 / +machine 100 / hand-WARNING 30 / hand-DANGER 90 / +machine 100.
# A non-zero value here would break that table -- a worker standing in WARNING
# near the machine would read 40 instead of 30, which looks like a bug.
#
# The proximity reason still appears in `reasons` and `conditions`, so the
# dashboard and the event log can see it. Raise these only once you have a real
# camera calibration and want distance to influence the number.
RISK_NEAR_WARNING = 0
RISK_NEAR_DANGER = 0


# ---------------------------------------------------------------------------
# POSE / WRIST (REACH-IN) DETECTION
# ---------------------------------------------------------------------------

# Master switch. Pose is a SECOND network pass on every frame, so it costs
# FPS. If the weights cannot be obtained, the app logs a warning and keeps
# running with pose disabled -- it is an extra signal, not a hard dependency.
ENABLE_POSE = True

POSE_MODEL_PATH = "models/yolov8n-pose.pt"
POSE_MODEL_DOWNLOAD_NAME = "yolov8n-pose.pt"

# Run pose only on every Nth frame to claw back FPS. 1 = every frame.
# The hand confirmation counters below count POSE frames, not camera frames.
POSE_EVERY_N_FRAMES = 2

POSE_CONFIDENCE_THRESHOLD = 0.4   # minimum confidence for a pose detection
POSE_KEYPOINT_CONFIDENCE = 0.5    # minimum confidence for one keypoint
POSE_IMGSZ = 640

# COCO pose keypoint indices: 9 = left wrist, 10 = right wrist.
# The wrist is our stand-in for "the hand"; fingertip detection is not needed
# for a reach-in violation.
WRIST_KEYPOINT_IDS = (9, 10)

# A pose skeleton is attached to an existing person track when their boxes
# overlap by at least this IoU. Below it, the skeleton is ignored.
POSE_MATCH_MIN_IOU = 0.20


# ---------------------------------------------------------------------------
# HAND / REACH-IN VIOLATION PERSISTENCE
# ---------------------------------------------------------------------------

# A single noisy frame must not raise a reach-in alarm. The wrist must be
# measured inside DANGER for this many consecutive POSE frames first.
HAND_ENTER_CONFIRM_FRAMES = 3

# ...and clearly outside for this many before the violation is cleared.
HAND_EXIT_CONFIRM_FRAMES = 5

# Dead-band (px) around the DANGER edge for the wrist, same idea as
# ZONE_MARGIN_PX for the feet.
HAND_MARGIN_PX = 4.0


# ---------------------------------------------------------------------------
# RULE-BASED RISK ENGINE
# ---------------------------------------------------------------------------

# The risk score is the MAXIMUM of every applicable rule, never an overwrite.
RISK_DANGER_TIMEOUT = 90          # in DANGER longer than the threshold
RISK_DANGER_MACHINE_ON = 100      # in DANGER while the machine is running
RISK_HAND_IN_WARNING = 30         # wrist inside WARNING (body may be safe)
RISK_HAND_IN_DANGER = 90          # wrist inside DANGER, body NOT in danger
RISK_HAND_MACHINE_ON = 100        # wrist inside DANGER + machine running

# Score -> label. Checked top to bottom, first match wins.
RISK_LEVELS = (
    (90, "CRITICAL"),
    (70, "HIGH"),
    (30, "WARNING"),
    (1, "LOW"),
    (0, "SAFE"),
)

# At or above this score the screen flashes and the reason list is shown.
RISK_CRITICAL_THRESHOLD = 90


# ---------------------------------------------------------------------------
# EVENT LOG (in-memory only -- no database in this phase)
# ---------------------------------------------------------------------------

# How many recent events to keep IN MEMORY for the on-screen ticker.
# The JSONL file below is not limited by this.
MAX_STORED_EVENTS = 200

# Print each event to the console as it happens.
LOG_EVENTS_TO_CONSOLE = True


# ---------------------------------------------------------------------------
# JSON EVENT LOGGING (JSON Lines, one file per day)
# ---------------------------------------------------------------------------

# Write a persistent log of every meaningful event. Still no database.
ENABLE_JSON_LOGGING = True

# On-disk layout:
#     logs/events_2026-08-11.jsonl     one JSON object per line, all runs of
#                                      that day, appended and flushed per line
#     logs/sessions/<session_id>.json  one file per run: metadata + summary
#
# One line = one complete, independently parseable event, which is exactly what
# the Spring Boot backend will read later (tail the file, parse each line, no
# multi-line JSON state machine needed). Events carry session_id, so a single
# run can still be picked out of a day's file.
#
# Kept outside src/ deliberately: logs are data, not code.
LOG_ROOT = "logs"

# Every event carries this. Bump it only when the event shape changes in a way a
# consumer must react to, so the backend can reject or migrate old records.
EVENT_SCHEMA_VERSION = "1.0"

# RISK_CHANGED fires once per distinct risk value per person. It is the chattiest
# event type (a worker walking in and out can produce a handful per minute). Set
# False to keep only the state events -- risk is still carried ON those events.
LOG_RISK_CHANGES = True

# IMPORTANT: only STATE TRANSITIONS are logged, never per-frame status. At 10 FPS
# a status log would write hundreds of lines a minute and be useless as an audit
# trail. See the module docstring in src/events.py.


# ---------------------------------------------------------------------------
# PHASE 2 UI
# ---------------------------------------------------------------------------

# Per-person card drawn next to each bounding box, showing that worker's own
# zone / hand zone / live danger timer / risk. This is what stops one worker's
# risk being misread as another's.
SHOW_PERSON_CARD = True
PERSON_CARD_SCALE = 0.45
PERSON_CARD_ALPHA = 0.72

# Append the risk LEVEL word (HIGH / CRITICAL) to the card's Risk line.
SHOW_LEVEL_ON_CARD = False

# Right-hand panel listing every worker with their zone, risk and dwell time.
SHOW_WORKER_PANEL = True
WORKER_PANEL_WIDTH = 250
WORKER_PANEL_MAX_ROWS = 6     # workers beyond this are summarised as "+N more"

# Bottom-left panel listing the deterministic reasons behind the worst risk.
SHOW_REASON_PANEL = True
MAX_DISPLAYED_REASONS = 5

# Wrist keypoint markers.
COLOR_WRIST = (255, 220, 90)       # pale blue/cyan - wrist outside danger
COLOR_WRIST_ALERT = (60, 60, 255)  # red - wrist inside danger
WRIST_MARKER_RADIUS = 6

# Phase 2 status banners (ASCII only -- see the note on emoji above).
CRITICAL_BANNER_TEXT = "[!!] CRITICAL SAFETY VIOLATION"
DANGER_BANNER_TEXT = "[!] WORKER IN DANGER ZONE"
WARNING_BANNER_TEXT = "[~] WORKER IN WARNING ZONE"
NO_ZONES_BANNER_TEXT = "[i] NO ZONES DEFINED - PRESS S / W / D"
DEFINING_ZONE_BANNER_TEXT = "[+] DEFINING {} - ENTER TO FINISH"
HAND_ALERT_TEXT = "[!] HAND ENTERED DANGER ZONE"


#BACKEND Connection
BACKEND_URL = "http://localhost:8080"
BACKEND_INCIDENT_ENDPOINT = "/api/incidents"
SEND_EVENTS_TO_BACKEND = True


# ============================================================
# WEB DASHBOARD
# ============================================================

WEB_SERVER_ENABLED = True

WEB_SERVER_HOST = "0.0.0.0"

WEB_SERVER_PORT = 8000

WEB_STREAM_JPEG_QUALITY = 80