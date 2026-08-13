# ZONE X — Smart Safety Monitoring System

### Phase 1: Local Computer Vision Prototype

---

## 1. Project purpose

ZONE X turns an ordinary webcam into a **virtual safety fence** for a
factory floor.

An operator draws an invisible polygon on the live camera view — around a press,
a robot cell, a conveyor pinch point — and the system watches for anyone stepping
into it. There are no physical light curtains, no floor mats and no wiring: the
perimeter exists only in software, so it can be moved in five seconds.

This repository is **Phase 1 only**: a self-contained local prototype using
Python, YOLOv8 and OpenCV. There is deliberately no backend, no database, no
dashboard and no microcontroller yet — see [Future integration](#13-future-integration).

**What it does right now**

- Opens a webcam / USB camera
- Detects people in real time with a pretrained YOLOv8 model
- Lets you draw a restricted polygon with the mouse
- Decides whether each person has entered it, using their **feet**, not their whole box
- Shows an unmissable red alarm when someone is inside, and `AREA CLEAR` when nobody is

---

## 2. Architecture

Data flows one way through five small modules. Nothing loops back, which is what
makes each piece replaceable later.

```
   ┌──────────────┐  frame   ┌──────────────┐  boxes+conf   ┌──────────────┐
   │ frame_source │ ───────► │   detector   │ ────────────► │   tracker    │
   │ webcam / ip  │          │  (YOLOv8n)   │               │ P1, P2, ...  │
   │ cctv / video │          └──────────────┘               │ + debounce   │
   └──────────────┘                                         └──────┬───────┘
                                                                   │ people
   ┌──────────────┐  polygons + tags   ┌──────────────┐            ▼
   │     zone     │ ─────────────────► │   geometry   │◄───  which zone?
   │ SAFE_1 ...   │                    │ feet point + │      SAFE_1/DANGER_2
   │ DANGER_2     │                    │ point-in-poly│      ────────┬──────
   └──────────────┘                    └──────────────┘              │
                                                                     ▼
   ┌──────────────┐   wrists    ┌──────────────┐   facts     ┌──────────────┐
   │ pose_detector│ ──────────► │  risk_engine │ ◄────────── │ machine state│
   │  (optional)  │             │  RULES ONLY  │             │  (simulated) │
   └──────────────┘             └──────┬───────┘             └──────────────┘
                                       │ risk + reasons
                        ┌──────────────┴───────────────┐
                        ▼                              ▼
                 ┌──────────────┐              ┌──────────────┐
                 │      ui      │              │    events    │
                 │ boxes + HUD  │              │ transitions  │
                 └──────┬───────┘              └──────┬───────┘
                        ▼                             ▼
                   your screen                 ┌──────────────┐
                                               │ event_logger │
                                               │ JSONL on disk│
                                               └──────────────┘
```

Nothing loops back, and each stage only knows about the one before it. That is
what makes the pieces replaceable: `main.py` cannot tell a webcam from an mp4,
and `risk_engine.py` cannot tell YOLO from a unit test.

### File layout

```
Restricted_area_yolov8/
│
├── main.py              # the real-time loop; wires every module together
├── config.py            # ALL tunable settings live here
├── requirements.txt
├── README.md
│
├── models/
│   ├── yolov8n.pt       # downloaded automatically on first run (~6.5 MB)
│   └── yolov8n-pose.pt  # optional, for wrist / reach-in detection
│
├── videos/              # recorded footage, for SOURCE_TYPE = "video"
│
├── logs/
│   ├── events_2026-08-11.jsonl   # one JSON event per line, per day
│   ├── incidents/                # one evidence photo per warning incident
│   └── sessions/                 # one metadata file per run
│
├── src/
│   ├── frame_source.py  # THE ONLY file that knows what a camera is
│   ├── detector.py      # loads YOLOv8, runs inference, keeps only "person"
│   ├── pose_detector.py # optional second model: wrist keypoints
│   ├── tracker.py       # P1/P2/P3 identity + anti-flicker hysteresis + timers
│   ├── zone.py          # polygons, unique zone tags, all mouse interaction
│   ├── risk_engine.py   # the safety RULES. no model, no LLM, no learning
│   ├── events.py        # decides WHAT is an event (state transitions)
│   ├── event_logger.py  # decides WHERE it goes (the only file that writes)
│   ├── incident_manager.py  # warning-zone incidents + their evidence photo
│   ├── alarm.py         # the PC buzzer. knows nothing about anything else
│   └── ui.py            # bounding boxes, labels, HUD, panels, help bar
│
├── tests/
│   ├── test_safety.py            # 290 headless tests: no camera, no GUI
│   └── measure_frame_source.py   # frame-pipeline latency measurement
│
└── utils/
    └── geometry.py      # bottom-center point, point-in-polygon, validation
```

### Module responsibilities

| Module | Owns | Never touches |
|---|---|---|
| `frame_source.py` | opening webcam / IP / CCTV / video, mirroring, playback pacing | detection, zones, risk |
| `detector.py` | YOLO model loading, inference, person/confidence filtering | drawing, zones |
| `pose_detector.py` | wrist keypoints and matching them onto person tracks | decisions |
| `tracker.py` | person identity (P1, P2, …), enter/exit debouncing, dwell timers | drawing, YOLO |
| `zone.py` | polygon points, unique zone tags, mouse events, inside tests | YOLO, risk |
| `risk_engine.py` | the risk score and the reasons for it | measuring anything |
| `events.py` | what counts as an event, and never repeating one | risk maths, files |
| `event_logger.py` | writing JSONL, JSON-safety at the boundary | deciding anything |
| `incident_manager.py` | warning incidents: id, entry time, duration, photo | detection, risk, events |
| `alarm.py` | making a noise, on one thread | detection, zones, risk, events |
| `ui.py` | every pixel drawn except the polygons themselves | decisions |
| `utils/geometry.py` | pure maths: feet point, point-in-polygon, area | everything else |
| `main.py` | the loop, keyboard, error reporting | cameras, detection maths |

`detector.py` and `pose_detector.py` are the only files that import
`ultralytics`; `frame_source.py` is the only one that opens a capture device; and
`event_logger.py` is the only one that writes to disk. Each of those swaps is a
one-file change.

---

## 3. Requirements

**Hardware**

- Any webcam or USB camera
- A normal CPU is enough (an NVIDIA GPU roughly triples the frame rate)

**Python packages** (see `requirements.txt`)

| Package | Why |
|---|---|
| `ultralytics` | the YOLOv8 model and its pretrained weights |
| `opencv-python` | camera capture, drawing, window/mouse/keyboard events |
| `numpy` | the array maths behind the geometry helpers |

`torch` and `torchvision` install automatically as ultralytics dependencies.

**Internet** is needed once, on the very first run, to download `yolov8n.pt`.
After that the app runs fully offline.

---

## 4. Python version

Python **3.9 or newer**.

> Verified working on **Python 3.14.6** with `ultralytics 8.4.98`,
> `opencv-python 5.0.0` and `numpy 2.4.6` on Windows 11.

---

## 5. Installation

Open a terminal in the project folder (`Restricted_area_yolov8`).

```bash
python -m venv venv
```

**Windows**

```bat
venv\Scripts\activate
```

**macOS / Linux**

```bash
source venv/bin/activate
```

---

## 6. How to install dependencies

```bash
pip install -r requirements.txt
```

That pulls in PyTorch too, so expect a few hundred MB and a couple of minutes.

For an **NVIDIA GPU**, install a CUDA build of PyTorch instead of the default
CPU one — see <https://pytorch.org/get-started/locally/> — then set
`DEVICE = 0` in `config.py`.

---

## 7. How to run

```bash
python main.py
```

On the **first** run you will see the weights being fetched:

```
[ZONE X] Model not found at 'models/yolov8n.pt'.
[ZONE X] Downloading 'yolov8n.pt' (one-time, needs internet)...
[ZONE X] Model saved to 'models/yolov8n.pt'.
[ZONE X] YOLOv8 model loaded: models/yolov8n.pt
[ZONE X] Source ready: webcam index 0 (1280x720, 30 FPS) via DSHOW
[ZONE X] Logging events to logs\events_2026-08-11.jsonl
```

A window opens showing your camera with live person detection. There is no
restricted area yet, so the status reads `NO RESTRICTED AREA DEFINED — PRESS 'R'`.

You can run `python main.py` from any directory; it relocates itself to the
project root so `models/yolov8n.pt` always resolves.

---

## 8. Choosing the video source

**Everything about the input lives in `config.py`. Switching source never
requires a code change.** `src/frame_source.py` reads these settings and hands
the rest of the program plain frames.

### Laptop / USB webcam

```python
SOURCE_TYPE  = "webcam"
CAMERA_INDEX = 0            # try 1, 2, ... for USB cameras
```

### Phone / HTTP network camera

```python
SOURCE_TYPE = "ip"
SOURCE_URL  = "http://192.168.1.42:8080/video"     # IP Webcam
# SOURCE_URL = "http://192.168.1.42:4747/video"    # DroidCam
```

The phone and the computer must be on the **same** network. Open the URL in a
browser on the computer first — if the browser cannot show it, neither can OpenCV.

### CCTV / RTSP camera

```python
SOURCE_TYPE    = "cctv"
SOURCE_URL     = "rtsp://user:pass@192.168.1.50:554/Streaming/Channels/101"
MIRROR_PREVIEW = False      # never mirror a fixed wall camera
```

Most cameras need a full stream path (`/Streaming/Channels/101`, `/stream1`, …);
the bare IP address is usually not enough.

### Recorded video file

```python
SOURCE_TYPE    = "video"
VIDEO_PATH     = "videos/factory_test.mp4"
MIRROR_PREVIEW = False
```

This is how to test the **whole** system — detection, zones, the danger timer,
the risk rules, the event log — with no camera plugged in at all. Playback is
paced to the file's own frame rate (`VIDEO_REALTIME = True`) so the 5-second
danger dwell rule behaves exactly as it does on a live camera. The app exits
cleanly with `End of video reached` when the file runs out.

### Why the phone stream no longer lags behind

A camera sends frames whether or not we are ready for them. Read them one per loop
pass and they queue up in the network/OpenCV buffer, so YOLO ends up analysing what
the camera saw a second ago — and the gap grows the longer the app runs.

So one reader thread continuously drains the source and keeps **only the newest
frame**. The main loop takes that frame about `TARGET_PROCESSING_FPS` times a
second. Everything in between is dropped:

```
source at 30 FPS, pipeline at 10 FPS
    -> ~10 frames/second processed
    -> ~20 frames/second dropped      <-- EXPECTED, not a fault
```

For a safety monitor a fresh frame is worth more than a complete one: we would
rather know where the worker is **now** than eventually see every frame they were
in. The `DROPPED` counter in the corner panel is supposed to be large.

Measured on a 30 FPS source with a 10 FPS pipeline (`tests/measure_frame_source.py`):

| | reading one frame per pass | reader thread + latest frame |
|---|---|---|
| frames received | 97 | 303 |
| frames processed | 97 | 100 |
| frames dropped | 0 | 202 |
| **fell behind real time by** | **6.8 s in 10 s** | **0.0 s** |

The relevant settings, all in `config.py`:

```python
TARGET_PROCESSING_FPS   = 10.0   # time-based, not "every Nth frame"
FRAME_READER_BUFFER_SIZE = 1     # keep only the newest frame
FRAME_SOURCE_RECONNECT  = True   # rebuild a dropped stream automatically
SOURCE_STALL_TIMEOUT    = 8.0    # give up only after this much silence
```

`TARGET_PROCESSING_FPS` is a **time** target on a monotonic clock, so it behaves
the same on a 15, 25, 30 or 60 FPS source — unlike "process every 3rd frame",
which would mean a different real rate on every camera.

What this cannot fix: latency created **before** the frame reaches us. Wifi
quality, the phone's camera app, encoding, bitrate, resolution and network
congestion all add delay of their own, and no OpenCV arrangement can promise zero
latency on a network stream. This removes the delay *our* pipeline was adding.

### If a source will not open

Configuration mistakes produce a plain explanation and exit code 3 — never a
stack trace:

```
[ZONE X] FRAME SOURCE ERROR (video)
Video file not found.
  VIDEO_PATH: videos/factory_test.mp4
  Looked in:  C:\...\Restricted_area_yolov8\videos\factory_test.mp4
  Put the recording there, or fix VIDEO_PATH in config.py.
```

Notes that apply to every source:

- The app **requests** 1280×720 (`FRAME_WIDTH` / `FRAME_HEIGHT`), but a camera is
  free to ignore that — the app adapts and the HUD scales down on small frames
- Only **one** program can hold a webcam at a time. Close Zoom, Teams, Discord
  and the Windows Camera app first
- On Windows the app tries DirectShow and Media Foundation before the default
  backend, because the default is often slow or refuses USB cameras
- A dead network URL takes OpenCV/FFmpeg **10–15 seconds** to give up before the
  error appears. That delay is inside FFmpeg, not in this project;
  `CAMERA_OPEN_TIMEOUT` covers the handshake that follows

### Mounting advice

The feet-based logic assumes you can **see the floor where people walk**. Mount
the camera high, angled slightly down, so a person's feet stay in frame. A camera
pointed straight ahead at chest height cannot tell where anyone is standing.

---

## 9. How to create a restricted area

1. Press **`r`** — the status changes to `DEFINING RESTRICTED AREA`
2. **Left-click** each corner of the area you want to protect. Points are
   numbered as you go, with a rubber-band line following your cursor
3. **Right-click** to undo the last point if you misclick
4. Press **`Enter`** to close the polygon

The zone turns into a shaded orange region labelled `RESTRICTED AREA` and
monitoring begins immediately.

```
              CAMERA VIEW
   +--------------------------------------+
   |                                      |
   |             PERSON                   |
   |               O                      |
   |              /|\                     |
   |              / \                     |
   |       +----------------------+       |
   |       |                      |       |
   |       |   RESTRICTED AREA    |       |
   |       |                      |       |
   |       |        MACHINE       |       |
   |       +----------------------+       |
   |                                      |
   +--------------------------------------+
```

**Notes**

- You need at least **3** points, and the shape must enclose more than
  `MIN_POLYGON_AREA` (500 px² by default). Press Enter with a bad shape and the
  app tells you why and lets you keep clicking — it will not accept a broken zone
- **Concave shapes work.** An L-shape correctly treats its notch as outside
- Press **`r`** again at any time to redraw from scratch, or **`c`** to remove
  the zone entirely
- Mouse clicks are **ignored** unless you are in defining mode, so you cannot
  nudge a live safety perimeter by accident
- The zone is stored in pixel coordinates and is **not** saved to disk — redraw
  it after restarting (persisting zones is a Phase 3 job)

---

## 10. Keyboard controls

| Key | Action |
|---|---|
| `r` | Start / restart defining the restricted area |
| Left click | Add a polygon point *(while defining)* |
| Right click | Undo the last point *(while defining)* |
| `Enter` | Finish the polygon |
| `c` | Clear the restricted area / cancel drawing |
| `p` | Pause / resume |
| `q` or `Esc` | Quit |

Closing the window with its **X** button also exits cleanly.

---

## 11. Detection logic

### The key idea: judge people by their feet

A standing person's bounding box is tall and narrow. If we asked *"does the box
overlap the zone?"*, someone standing safely **behind** a machine would trigger a
false alarm — their head and torso visually overlap the danger zone even though
they are metres away from it.

So every person is reduced to **one point**: the **bottom-center** of their box,
which approximates where their feet meet the floor.

```
   x1,y1 ┌───────────┐
         │    O      │
         │   /|\     │        person_x = (x1 + x2) / 2
         │   / \     │        person_y =  y2
         └─────●─────┘ x2,y2
               ▲
        this point decides — not the box
```

Only that point is tested against the polygon
(`cv2.pointPolygonTest` in `utils/geometry.py`).

**This is verified behaviour, not theory.** In testing, a person whose *box*
clearly overlapped the zone but whose *feet* were 46 px outside it was correctly
reported as `SAFE`, while a person standing 70 px inside it alarmed.

### Per-person pipeline, each frame

```
1.  YOLOv8n runs on the frame, restricted to COCO class 0 ("person")
2.  Detections below CONFIDENCE_THRESHOLD (0.5) are discarded
3.  Boxes are clipped to the frame
4.  Each box becomes a feet point:  ((x1+x2)/2 , y2)
5.  The tracker matches that point to the nearest known person (≤120 px)
6.  The signed distance from the point to the polygon edge is measured
7.  Hysteresis decides the confirmed state (below)
8.  ui.py draws green (safe) or red (violation)
```

### Multiple people

Every person is judged independently, so mixed states are normal and each gets
its own colour and label:

```
Worker 1  →  SAFE               (green box,  "ID 1  0.87")
Worker 2  →  RESTRICTED AREA    (red box,    "ID 2  RESTRICTED AREA")
Worker 3  →  SAFE               (green box,  "ID 3  0.83")
```

The screen alarm fires because **at least one** person is inside. The HUD shows
both totals: `People Detected: 3` and `In Restricted Area: 1`.

### Anti-flicker (hysteresis)

A person standing exactly on the boundary would otherwise flip
`SAFE / RESTRICTED / SAFE / RESTRICTED` many times a second. Two cheap
mechanisms prevent that:

1. **Dead-band** — the feet point must be `ZONE_MARGIN_PX` (6 px) *past* the
   edge, not merely across it. Inside that band, nothing changes.
2. **Confirmation frames** — the new state must hold for
   `ENTER_CONFIRM_FRAMES` (2) or `EXIT_CONFIRM_FRAMES` (5) consecutive frames.
   Entering is confirmed fast (safety first); leaving is confirmed slowly.

Verified: a person parked **exactly on the polygon edge for 20 straight frames
produced zero state changes.**

`ALERT_HOLD_SECONDS` additionally keeps the big warning on screen for 1 second
after the last violation, so a momentary breach cannot be missed by a human.

### What is on screen

- Camera feed, mirrored like a selfie camera (`MIRROR_PREVIEW`)
- Bounding boxes — green = safe, thick red + corner brackets = violation
- Confidence score per person, and a stable `ID`
- The restricted polygon, shaded and labelled
- The white **feet marker** each decision is based on
- HUD: title, `People Detected`, `In Restricted Area`, `FPS`, zone point count
- A colour-coded status banner, plus a flashing red border and giant
  `RESTRICTED AREA` text during a violation
- The keyboard help bar

---

## 12. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `CAMERA ERROR — could not start camera index 0` | Another app holds the camera. Close Zoom / Teams / Camera. Or try `CAMERA_INDEX = 1`. On Windows check **Settings → Privacy & security → Camera → Let desktop apps access your camera** |
| `camera opened but returned no frames` | Usually the same cause: the device is claimed by another program |
| `MODEL ERROR — could not download yolov8n.pt` | No internet on first run. Download `yolov8n.pt` manually from <https://github.com/ultralytics/assets/releases> and drop it in `models/` |
| Model file corrupt / fails to load | Delete `models/yolov8n.pt` and rerun; it re-downloads |
| Low FPS | See the table below — lower `INFERENCE_IMGSZ` first |
| People not detected | Lower `CONFIDENCE_THRESHOLD` to ~0.35. Improve lighting. Make sure people are not tiny in frame |
| False detections | Raise `CONFIDENCE_THRESHOLD` to ~0.6 |
| Alarm fires when a person is clearly outside | Their **feet** are probably inside, or their feet are cut off by the frame edge so `y2` lands somewhere misleading. Re-aim the camera so the floor is visible |
| Person inside the zone is not flagged | Their feet may be hidden behind a machine, so YOLO's box bottom is too high. Raise the camera |
| `Invalid zone: Need at least 3 points` | Keep clicking before pressing Enter |
| `Invalid zone: Zone too small` | Your clicks were nearly collinear or almost on top of each other. Draw a bigger shape |
| Status jitters near the edge | Raise `ZONE_MARGIN_PX` and `EXIT_CONFIRM_FRAMES` in `config.py` |
| Left/right feels reversed | Set `MIRROR_PREVIEW = False` |
| Window won't close | Press `q` or `Esc` with the **video window** focused, or `Ctrl+C` in the terminal |
| Emoji show as `?` boxes | Expected. OpenCV's built-in fonts are ASCII-only, which is why the app uses `[!]` and `[OK]` markers |

### Performance tuning

Measured on this machine (CPU-only, 1280×720 input):

| `INFERENCE_IMGSZ` | ms / frame | Inference FPS | Accuracy |
|---|---|---|---|
| 640 *(default)* | 65 | ~15 | best |
| 512 | 45 | ~22 | good |
| 416 | 43 | ~23 | acceptable |
| 320 | 32 | ~32 | misses small/distant people |

End-to-end app throughput was **~10 FPS** at `imgsz=640` on CPU, since camera
read, drawing and window compositing cost time on top of inference.

To speed things up, in `config.py`:

1. Lower `INFERENCE_IMGSZ` to `416`
2. Lower `FRAME_WIDTH` / `FRAME_HEIGHT` to `640` / `480`
3. Set `SHOW_FEET_GUIDE = False` and `SHOW_HELP_BAR = False`
4. Best of all: use an NVIDIA GPU and set `DEVICE = 0`

Because YOLOv8n is a **pretrained** COCO model, no training is required — and
none should be attempted for this phase.

---

## 13. Person IDs and zone tags

### People are P1, P2, P3 …

Each tracked worker gets a label for the length of one monitoring session:

```
first person detected   ->  P1
second person           ->  P2
P1 walks out, someone new arrives  ->  P3      (never a recycled P1)
```

The number is the tracker's own track id, so the label is **stable for as long as
the tracker believes it is the same person** — P1 on frame 1 is still P1 on frame
400, and a frame or two where YOLO misses them does not change it
(`TRACK_MAX_MISSING_FRAMES`). IDs are never reused inside a run, so a log line
about P1 can only ever mean one person.

Every person also carries an identity block, which is what future face
recognition will fill in:

```json
{
  "person_id": "P1",
  "employee_id": null,
  "person_name": null,
  "identity_status": "UNIDENTIFIED"
}
```

**There is no face recognition in this phase**, and the safety pipeline must never
depend on it: a worker whose face is not recognised has to be protected exactly as
well as one who is. `person_name` stays `null` — never a placeholder or a guess.

### Zones are SAFE_1, WARNING_1, DANGER_2 …

Draw as many polygons of each type as the floor needs. Every one gets a unique tag
from a single centralized counter (`ZoneIdFactory` in `src/zone.py`):

```
SAFE_1   SAFE_2   WARNING_1   DANGER_1   DANGER_2
```

- The tag is unique across **all** zone types
- It is **never reused inside a run**, not even after a zone is deleted or a
  half-drawn polygon is abandoned — reusing `SAFE_1` for different floor would
  quietly corrupt the event log
- IDs are **not** derived from list positions (`len(safe_zones)`), which is exactly
  the scheme that collides after a delete
- The tag is drawn on its polygon, and nested polygons push their labels apart so
  they never print on top of each other

Pressing `s` / `w` / `d` **adds** a zone; the ones already drawn are untouched.
`c` clears them all.

When polygons overlap, the existing priority is unchanged:

```
DANGER  >  WARNING  >  SAFE
```

…and within one type, the polygon drawn first wins. A worker standing on the
machine reads as DANGER, never as SAFE.

---

## 14. The event log

`logs/` holds only **state changes**, never per-frame status. Standing in a warning
zone for ten seconds writes one `ZONE_ENTER`, not 100 of them.

```
logs/
├── events_2026-08-11.jsonl     every event of that day, one JSON object per line
└── sessions/
    └── 2026-08-11_12-30-45.json    metadata + summary for one run
```

One line = one complete, independently parseable event. There is no enclosing
array to close, so a log still being written — or one cut short by a hard kill — is
readable up to its last complete line. Every line is flushed as it is written.

### What gets logged

| Event | When |
|---|---|
| `PERSON_DETECTED` | a new track appears |
| `PERSON_LOST` | the **tracker** gives up on the person |
| `DANGER_ZONE_ENTERED` / `_LEFT` | the body enters / leaves a DANGER polygon |
| `WARNING_ZONE_ENTERED` / `_LEFT` | the body enters / leaves a WARNING polygon |
| `ZONE_ENTERED` / `ZONE_LEFT` | the same, for any other zone type (in practice SAFE) |
| `DANGER_DWELL_EXCEEDED` | once, when `DANGER_TIME_THRESHOLD_SEC` (5 s) is crossed |
| `WARNING_DWELL_EXCEEDED` | once per incident, when `WARNING_DWELL_THRESHOLD_SEC` (5 s) is crossed |
| `HAND_ENTERED_DANGER` / `HAND_LEFT_DANGER` | a confirmed reach-in, and its end |
| `HAND_ENTERED_WARNING` / `HAND_LEFT_WARNING` | the wrist crosses a WARNING boundary |
| `CRITICAL_RISK` | once, when the score reaches `RISK_CRITICAL_THRESHOLD` (90) |
| `RISK_CHANGED` | the score changed value (set `LOG_RISK_CHANGES = False` to drop these) |
| `MACHINE_STATE_CHANGED` | the machine was toggled; `machine_on` carries the new state |
| `ALARM_STARTED` / `ALARM_STOPPED` | the buzzer began / stopped sounding |

One transition produces exactly **one** event. Entering a danger polygon writes
`DANGER_ZONE_ENTERED` only — never that *and* a generic `ZONE_ENTERED`. Which
polygon it was is always in `zone_id`.

### What is deliberately NOT logged

- per-frame status, and nothing at all per YOLO inference
- **one line per beep** — the alarm logs only its start and stop transitions
- a hand in a SAFE zone (that is just a hand)
- entering "OUTSIDE" — walking off the polygons writes a `ZONE_LEFT` only
- a repeat of an event whose state has not changed
- the source URL, which can carry camera credentials

### Example

```json
{
  "timestamp": "2026-08-11T16:53:12.472",
  "session_id": "2026-08-11_16-53-12",
  "event_type": "DANGER_DWELL_EXCEEDED",
  "person_id": "P1",
  "zone_id": "DANGER_1",
  "body_zone": "DANGER",
  "hand_zone": null,
  "risk": 90,
  "machine_on": false,
  "danger_duration_sec": 6.4,
  "distance_to_danger_px": 0.0,
  "reasons": ["Worker remained in Danger Zone for more than 5 seconds"],
  "source_type": "ip",
  "schema_version": "1.0",
  "event_id": "evt_000010"
}
```

The shape is **flat**, so a backend maps it straight onto columns with nothing to
unpack. Every field is present on every event, and a field that does not apply is
`null` rather than invented — a `PERSON_LOST` has no zone, so its `zone_id`,
`risk` and `machine_on` are null.

`zone_id` is the tag `zone.py` issued for that polygon. There is no second
zone-id system anywhere in the project.

`schema_version` and `event_id` are the only two fields beyond the agreed list:
one to migrate on, one to make backend ingestion idempotent. Delete the two lines
at the end of `EventLog.emit()` if the backend does not want them.

Timestamps are **local wall-clock with milliseconds**, matching `session_id` and
the log filename. A backend ingesting these needs to be told which timezone the
machine runs in.

### Warning-zone incidents

An **event** is a state change. An **incident** is a complete occurrence, and the
two events that open and close it share one `incident_id`:

```
P1 enters WARNING_1   ->  WARNING_ZONE_ENTERED   incident_id, entry_time, ONE photo
P1 stays                  (nothing logged, nothing saved)
after 5 s                 WARNING_DWELL_EXCEEDED + the buzzer starts
P1 leaves             ->  WARNING_ZONE_LEFT      exit_time, duration_sec, same id
```

```
logs/
├── events_2026-08-11.jsonl
└── incidents/
    └── 2026-08-11_16-03-25_P1_WARNING_1.jpg
```

- **entry_time** is stamped when the entry becomes CONFIRMED by the existing
  hysteresis — never on a single noisy frame.
- **exit_time** is stamped on the confirmed exit, or when the tracker finally gives
  up on the person (`TRACK_MAX_MISSING_FRAMES`), in which case the reason says
  `Worker tracking lost while inside Warning Zone`. A brief detection gap does
  **not** close an incident.
- **duration_sec** is `exit_time − entry_time`, so anyone reading the log can
  verify it from the two timestamps on the same line.
- **evidence_image** is the camera frame at the moment of entry, saved once, before
  any overlay is drawn. The path is relative to the project root with forward
  slashes, ready to be mapped onto uploaded evidence by a backend.
- Leaving and returning is a **new** incident: new id, new photo. Ids are never
  reused within a run.
- `WARNING_1 → WARNING_2` closes the first incident and opens a second. One
  incident per person per zone.

Settings in `config.py`:

```python
WARNING_DWELL_THRESHOLD_SEC = 5.0    # linger longer than this -> alarm
SAVE_INCIDENT_IMAGES        = True   # False keeps the records, writes no photos
INCIDENT_DIR                = "logs/incidents"
```

### The PC alarm

A repeating beep sounds while **any** worker is in a confirmed critical condition:

```
body zone == DANGER        someone standing in a danger zone
or hand_in_danger          a confirmed reach-in, wherever their feet are
or warning dwell           in a warning zone longer than 5 s
          ↓
     BEEP BEEP BEEP BEEP  (pause)  BEEP BEEP BEEP BEEP ...
```

It stops only when the **last** critical condition clears. With P1 in a danger
zone and P2 safe it keeps sounding; P1 walking out while P2 is still inside does
not silence it.

The condition is read from the debounced tracker state, so
`ENTER_CONFIRM_FRAMES` / `HAND_ENTER_CONFIRM_FRAMES` gate the buzzer exactly as
they gate everything else — a single noisy detection cannot sound it, and a single
missed frame cannot silence it. There is no second danger-decision system.

`src/alarm.py` uses `winsound` from the Python standard library, so **no extra
dependency**. Off Windows it falls back to the terminal bell. All timings live in
`config.py`:

```python
ENABLE_PC_ALARM         = True
ALARM_REPEAT            = True
ALARM_BEEP_FREQUENCY    = 1000   # Hz
ALARM_BEEP_DURATION_MS  = 180
ALARM_BEEP_GAP_MS       = 90
ALARM_BEEPS_PER_BURST   = 4
ALARM_PAUSE_DURATION_MS = 600
```

Exactly one alarm thread exists at a time; `start_alarm()` is a no-op while it is
already sounding, so calling it every frame is safe. `stop_alarm()` takes effect
within one beep length, because `winsound.Beep` cannot be interrupted mid-tone.

### Ready for the Spring Boot backend

- Only JSON primitives are written: strings, numbers, booleans, null, arrays,
  objects. `json_safe()` in `event_logger.py` converts numpy scalars and arrays at
  the boundary, so no numpy / OpenCV / YOLO object can reach the file
- The event type strings are a **contract**, meant to map onto a database enum
- `schema_version` is stamped on every record (`EVENT_SCHEMA_VERSION` in
  `config.py`), so a consumer can reject or migrate older records
- `event_logger.py` is the only file that writes anything. Posting events to HTTP
  instead of a file means replacing that one class — `events.py` would not change

---

## 15. Future integration

Phase 1 is deliberately standalone, but the seams are already in the right
places. **None of the following is implemented yet.**

**Phase 2 — Physical alarm**

```
Python CV  ──►  MQTT  ──►  ESP32  ──►  Buzzer + warning light
```
*Hook:* the moment `inside_count` transitions above zero in `main.py` is the
single place to publish an MQTT message.

**Phase 3 — Backend and dashboard**

```
Python CV  ──►  Spring Boot  ──►  PostgreSQL  ──►  React dashboard
```
*Hook:* the JSONL in `logs/` is already the wire format — stable schema, plain
JSON values, one parseable record per line, every event tagged with `session_id`,
`zone_id` and `person_id`. Replacing `EventLogger` with an HTTP POST is the whole
integration.

**Face recognition — replacing P1 with a name**

```
P1  ──►  face recognition  ──►  "Rahul Kumar" / EMP_023
```
*Hook:* every person already carries `identity` = `{person_id, employee_id,
person_name, identity_status}`, and events already publish all four. A recognition
pass fills them in on the track; the event schema, the risk rules and the backend
contract do not change. `ui.person_tag()` is the single function that decides what
is drawn above a worker, so preferring a real name there is a one-line change.
The safety path must keep working when recognition fails or is switched off.

**Phase 4 — Risk engine**

```
Machine state + person tracking + distance + time in zone  ──►  Risk Engine
```
*Hook:* `signed_distance` is already computed per person per frame — that is the
"distance to danger" signal, and track IDs give you dwell time for free.

**Phase 5 — Safety knowledge**

```
RAG + Ollama + safety SOPs
```

**Phase 6 — Agentic AI**

```
Agentic AI + tool calling
```

### Why the current structure supports this

- `detector.py` is the only file importing `ultralytics` — swap the model freely
- `frame_source.py` is the only file that opens a capture device — adding a new
  input type touches nothing else
- `geometry.py` is pure maths with no I/O
- `risk_engine.py` is a pure function of measured facts: no camera, no model, no
  clock, which is why the rules are testable and auditable
- `event_logger.py` is the only file that writes anything
- Every threshold already lives in `config.py`, ready to be driven by a backend

---

## Testing

```bash
python tests/test_safety.py
```

290 tests, all headless: no camera, no GUI window and no model weights are
required. They cover the risk rules, the hysteresis, the per-person dwell timers,
reach-in detection, zone tag uniqueness, person ID stability, event transitions,
the JSON schema, the latest-frame buffer, the processing scheduler, the reader
thread, the PC alarm state machine, warning-zone incidents, and the four frame-source configurations (the video path is exercised
against a file the suite generates itself).

### Measuring your own source

```bash
python tests/measure_frame_source.py              # uses config.SOURCE_TYPE
python tests/measure_frame_source.py --seconds 20
```

Runs the currently configured source both ways — one frame per pass, and with the
reader thread — and prints source FPS, process FPS, dropped frames and how much
stale video was sitting in the buffer. Point `config.py` at your phone and run it
to see the difference on your own wifi.

Not covered automatically, because they need hardware — see section 8:

- opening a real webcam, phone, or RTSP camera
- how a specific camera behaves when it is unplugged mid-run
- true glass-to-screen latency, which needs a stopwatch in front of the lens

---

## Quick reference

```bash
python -m venv venv
venv\Scripts\activate            # Windows
pip install -r requirements.txt
python main.py
```

Pick the input in `config.py` (`SOURCE_TYPE` = `webcam` / `ip` / `cctv` / `video`),
then: **`d`** → click corners → **`Enter`** → step into `DANGER_1`.
