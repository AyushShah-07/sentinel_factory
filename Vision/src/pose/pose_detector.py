"""
src/pose_detector.py
--------------------
The SECOND, optional vision model: pose keypoints, used to find wrists, feet,
shoulders, hips, elbows, and knees.
 
WHY THIS IS A SEPARATE FILE
detector.py stays exactly what it was -- "find people" -- and remains the only
thing the core safety pipeline depends on. Pose is an ADDITIONAL signal layered
on top. If the pose weights cannot be downloaded, or pose is switched off in
config.py, the whole restricted-area system keeps working unchanged; we simply
stop reporting reach-in violations.
 
WHY WRISTS
A reach-in injury happens when a hand crosses into the machine while the body
stays safely outside. The foot point cannot see that, by construction. COCO pose
gives us keypoint 9 (left wrist) and 10 (right wrist), which is a good enough
stand-in for "the hand" -- fingertip or palm precision buys nothing here,
because the danger zone boundary is drawn by hand to within a few pixels anyway.
 
WHY FEET (ANKLES)
Feet/ankles (keypoints 15 and 16) tell us if a person has entered a restricted
zone. This is critical for safety monitoring.
 
WHY SHOULDERS, HIPS, ELBOWS, KNEES
- Shoulders (5, 6): Detect leaning into machinery
- Hips (11, 12): Body position and zone entry
- Elbows (7, 8): Reaching behavior
- Knees (13, 14): Crouching/squatting detection
 
WHY WE ASSOCIATE INSTEAD OF REPLACING
The pose model detects its own people, so it would be tempting to throw away
detector.py and use pose for everything. We deliberately do not: that would
couple the working person-detection MVP to a heavier model, and would change the
boxes the tracker has been tuned against. Instead each pose skeleton is matched
back onto an existing person track by bounding-box overlap, so YOLO person
detection remains the single source of truth for "who is in the frame".
 
COST
This is a second full network pass per frame. On CPU that roughly halves the
frame rate, which is why config.POSE_EVERY_N_FRAMES exists.
"""
 
from src.detection.detector import ModelLoadError, ensure_model_file
 
 
class WristDetector:
    """
    Thin wrapper around a YOLOv8 pose model that returns multiple keypoints.
   
    Returns: wrists, feet, shoulders, hips, elbows, knees.
    """
 
    def __init__(
        self,
        model_path="models/yolov8n-pose.pt",
        download_name="yolov8n-pose.pt",
        confidence_threshold=0.4,
        keypoint_confidence=0.5,
        # Primary keypoints (existing)
        wrist_ids=(9, 10),
        # NEW: Additional keypoints
        foot_ids=(15, 16),          # Ankles (feet)
        shoulder_ids=(5, 6),        # Shoulders
        hip_ids=(11, 12),           # Hips
        elbow_ids=(7, 8),           # Elbows
        knee_ids=(13, 14),          # Knees
        imgsz=640,
        device=None,
        verbose=False,
    ):
        self.confidence_threshold = confidence_threshold
        self.keypoint_confidence = keypoint_confidence
       
        # Store all keypoint IDs
        self.wrist_ids = tuple(wrist_ids)
        self.foot_ids = tuple(foot_ids)
        self.shoulder_ids = tuple(shoulder_ids)
        self.hip_ids = tuple(hip_ids)
        self.elbow_ids = tuple(elbow_ids)
        self.knee_ids = tuple(knee_ids)
       
        self.imgsz = imgsz
        self.device = device
        self.verbose = verbose
 
        # Same one-time download-and-tidy-into-models/ behaviour as the person
        # detector, reused rather than reimplemented.
        resolved_path = ensure_model_file(model_path, download_name)
 
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise ModelLoadError(
                "The 'ultralytics' package is not installed.\n"
                "  Fix: pip install -r requirements.txt\n"
                "  Details: {}".format(exc)
            )
 
        try:
            self.model = YOLO(resolved_path)
        except Exception as exc:
            raise ModelLoadError(
                "Failed to load YOLO pose weights from '{path}'.\n"
                "  The file may be corrupt or incomplete -- delete it and rerun "
                "to re-download.\n"
                "  You can also set ENABLE_POSE = False in config.py to run "
                "without reach-in detection.\n"
                "  Details: {exc}".format(path=resolved_path, exc=exc)
            )
 
        self.model_path = resolved_path
        print("[ZONE X] YOLOv8 pose model loaded: {}".format(resolved_path))
 
    def detect(self, frame):
        """
        Run one pose pass and return one entry per detected skeleton:
 
            [{"bbox": (x1,y1,x2,y2), "confidence": float,
              "wrists": [(x, y), ...],
              "feet": [(x, y), ...],
              "shoulders": [(x, y), ...],
              "hips": [(x, y), ...],
              "elbows": [(x, y), ...],
              "knees": [(x, y), ...]}, ...]
 
        Each keypoint list holds 0, 1 or 2 points. Empty lists are normal
        (e.g., occluded joints).
        """
        if frame is None or getattr(frame, "size", 0) == 0:
            return []
 
        predict_kwargs = {
            "source": frame,
            "conf": self.confidence_threshold,
            "imgsz": self.imgsz,
            "verbose": self.verbose,
        }
        if self.device is not None:
            predict_kwargs["device"] = self.device
 
        try:
            results = self.model.predict(**predict_kwargs)
        except Exception as exc:
            # Pose is an optional extra: one bad frame must never take down a
            # live safety monitor, exactly as in detector.py.
            print("[ZONE X] Warning: pose inference failed on this frame: {}".format(exc))
            return []
 
        frame_height, frame_width = frame.shape[:2]
        poses = []
 
        # Helper function to extract keypoints for a given set of IDs
        def extract_keypoints(person_keypoints, keypoint_ids):
            """Extract keypoints with confidence filtering."""
            points = []
            for kp_id in keypoint_ids:
                if kp_id >= len(person_keypoints):
                    continue
                kp = person_keypoints[kp_id]
                kx, ky = float(kp[0]), float(kp[1])
                kconf = float(kp[2]) if len(kp) > 2 else 1.0
 
                # A low-confidence keypoint is usually the model guessing at
                # an occluded joint. Acting on that would produce phantom
                # alarms, so we drop it instead.
                if kconf < self.keypoint_confidence:
                    continue
                if kx <= 0 and ky <= 0:
                    continue  # ultralytics reports a missing joint as (0, 0)
                points.append((kx, ky))
            return points
 
        for result in results:
            boxes = getattr(result, "boxes", None)
            keypoints = getattr(result, "keypoints", None)
            if boxes is None or keypoints is None or len(boxes) == 0:
                continue
 
            # .data is (num_people, num_keypoints, 3) -> x, y, confidence
            data = getattr(keypoints, "data", None)
            if data is None:
                continue
 
            for index in range(len(boxes)):
                if index >= len(data):
                    break
 
                try:
                    x1, y1, x2, y2 = boxes.xyxy[index].tolist()
                    confidence = float(boxes.conf[index])
                    person_keypoints = data[index].tolist()
                except Exception:
                    continue
 
                bbox = (
                    int(max(0, min(x1, frame_width - 1))),
                    int(max(0, min(y1, frame_height - 1))),
                    int(max(0, min(x2, frame_width - 1))),
                    int(max(0, min(y2, frame_height - 1))),
                )
                if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                    continue
 
                # Extract ALL keypoint groups using the helper function
                wrists = extract_keypoints(person_keypoints, self.wrist_ids)
                feet = extract_keypoints(person_keypoints, self.foot_ids)
                shoulders = extract_keypoints(person_keypoints, self.shoulder_ids)
                hips = extract_keypoints(person_keypoints, self.hip_ids)
                elbows = extract_keypoints(person_keypoints, self.elbow_ids)
                knees = extract_keypoints(person_keypoints, self.knee_ids)
 
                poses.append({
                    "bbox": bbox,
                    "confidence": confidence,
                    "wrists": wrists,
                    "feet": feet,
                    "shoulders": shoulders,
                    "hips": hips,
                    "elbows": elbows,
                    "knees": knees,
                })
 
        return poses
 
 
# ---------------------------------------------------------------------------
# associating skeletons with the people the person detector already found
# ---------------------------------------------------------------------------
 
def box_iou(a, b):
    """Intersection-over-union of two (x1, y1, x2, y2) boxes. 0.0 if disjoint."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
 
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = ix2 - ix1, iy2 - iy1
    if iw <= 0 or ih <= 0:
        return 0.0
 
    intersection = float(iw * ih)
    area_a = float(max(0, ax2 - ax1) * max(0, ay2 - ay1))
    area_b = float(max(0, bx2 - bx1) * max(0, by2 - by1))
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0
 
 
def associate_keypoints(people, poses, min_iou=0.20):
    """
    Attach pose skeletons to tracked people by bounding-box overlap.
 
        people: list from tracker.update() -- needs "id" and "bbox"
        poses:  list from WristDetector.detect()
 
    Returns {person_id: {"wrists": [...], "feet": [...], "shoulders": [...],
                         "hips": [...], "elbows": [...], "knees": [...]}}
 
    People with no matching skeleton are simply absent from the map.
    Greedy highest-overlap-first matching, and each skeleton is used at most
    once, so two people standing close together cannot both claim the same
    keypoints.
    """
    pairs = []
    for person_index, person in enumerate(people):
        for pose_index, pose in enumerate(poses):
            iou = box_iou(person["bbox"], pose["bbox"])
            if iou >= min_iou:
                pairs.append((iou, person_index, pose_index))
 
    # Highest overlap wins first; ties broken by index so the result is stable.
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
 
    used_people = set()
    used_poses = set()
    keypoint_map = {}
 
    for _, person_index, pose_index in pairs:
        if person_index in used_people or pose_index in used_poses:
            continue
        used_people.add(person_index)
        used_poses.add(pose_index)
       
        person_id = people[person_index]["id"]
        pose_data = poses[pose_index]
       
        # Store ALL keypoints for this person
        keypoint_map[person_id] = {
            "wrists": pose_data.get("wrists", []),
            "feet": pose_data.get("feet", []),
            "shoulders": pose_data.get("shoulders", []),
            "hips": pose_data.get("hips", []),
            "elbows": pose_data.get("elbows", []),
            "knees": pose_data.get("knees", []),
        }
 
    return keypoint_map
 
 
# ---------------------------------------------------------------------------
# LEGACY SUPPORT - Keep old function name for backward compatibility
# ---------------------------------------------------------------------------
 
def associate_wrists(people, poses, min_iou=0.20):
    """
    Legacy wrapper for backward compatibility.
    Returns {person_id: [(x,y), ...]} with wrist points only.
    """
    full_map = associate_keypoints(people, poses, min_iou)
   
    # Convert to old format: just wrists
    wrist_map = {}
    for person_id, keypoints in full_map.items():
        wrist_map[person_id] = keypoints.get("wrists", [])
   
    return wrist_map