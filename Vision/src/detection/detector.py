"""
src/detector.py
Handles YOLO person detection - finds people in video frames
"""

import os
import shutil


class ModelLoadError(Exception):
    """Error when YOLO weights file can't be found or loaded"""
    pass


def ensure_model_file(model_path, download_name):
    """
    Make sure the YOLO weights file exists on disk.
    Downloads it automatically if missing.
    """
    # If file already exists, use it
    if os.path.isfile(model_path):
        return model_path

    # Create folder if needed
    model_dir = os.path.dirname(model_path)
    if model_dir:
        os.makedirs(model_dir, exist_ok=True)

    print("[ZONE X] Model not found at '{}'.".format(model_path))
    print("[ZONE X] Downloading '{}' (one-time, needs internet)...".format(download_name))

    reason = None
    try:
        from ultralytics.utils.downloads import attempt_download_asset

        # Download the model
        downloaded = str(attempt_download_asset(download_name))

        # Move downloaded file to correct location
        if os.path.isfile(downloaded):
            if os.path.abspath(downloaded) != os.path.abspath(model_path):
                shutil.move(downloaded, model_path)
            print("[ZONE X] Model saved to '{}'.".format(model_path))
            return model_path

        # If download didn't work
        reason = ("'{}' is not a recognised ultralytics asset name, so nothing "
                  "was downloaded".format(download_name))

    except Exception as exc:
        reason = "{}: {}".format(type(exc).__name__, exc)

    # If everything failed, show helpful error
    raise ModelLoadError(
        "Could not obtain the YOLO weights '{name}'.\n"
        "  Reason: {reason}\n"
        "  Fix one of the following:\n"
        "    * check your internet connection and rerun (the download is one-time)\n"
        "    * make sure MODEL_DOWNLOAD_NAME in config.py is a real file such as "
        "'yolov8n.pt'\n"
        "    * download it manually from "
        "https://github.com/ultralytics/assets/releases\n"
        "      and place it at: {path}".format(
            name=download_name, reason=reason, path=os.path.abspath(model_path)
        )
    )


class PersonDetector:
    """Wrapper around YOLOv8 that only detects people"""

    def __init__(
        self,
        model_path,
        download_name="yolov8n.pt",
        confidence_threshold=0.5,
        iou_threshold=0.45,
        person_class_id=0,
        imgsz=640,
        device=None,
        verbose=False,
    ):
        # Store settings
        self.confidence_threshold = confidence_threshold  # Minimum confidence to count detection
        self.iou_threshold = iou_threshold  # For removing duplicate boxes
        self.person_class_id = person_class_id  # COCO class ID for "person" is 0
        self.imgsz = imgsz  # Image size YOLO will resize to
        self.device = device  # CPU or GPU
        self.verbose = verbose  # Show/hide YOLO output

        # Make sure model file exists (download if missing)
        resolved_path = ensure_model_file(model_path, download_name)

        # Try importing ultralytics
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise ModelLoadError(
                "The 'ultralytics' package is not installed.\n"
                "  Fix: pip install -r requirements.txt\n"
                "  Details: {}".format(exc)
            )

        # Load the YOLO model
        try:
            self.model = YOLO(resolved_path)
        except Exception as exc:
            raise ModelLoadError(
                "Failed to load YOLO weights from '{path}'.\n"
                "  The file may be corrupt or incomplete -- delete it and rerun to "
                "re-download.\n"
                "  Details: {exc}".format(path=resolved_path, exc=exc)
            )

        self.model_path = resolved_path
        print("[ZONE X] YOLOv8 model loaded: {}".format(resolved_path))

    def detect(self, frame):
        """
        Find all people in a frame and return their bounding boxes.
        
        Returns: [{"bbox": (x1, y1, x2, y2), "confidence": 0.87}, ...]
        Empty list means no people found.
        """
        # Ignore empty frames
        if frame is None or getattr(frame, "size", 0) == 0:
            return []

        # Set up YOLO parameters
        predict_kwargs = {
            "source": frame,
            "conf": self.confidence_threshold,  # Filter by confidence
            "iou": self.iou_threshold,  # Remove overlapping boxes
            "classes": [self.person_class_id],  # Only detect people (not all 80 classes)
            "imgsz": self.imgsz,
            "verbose": self.verbose,
        }
        if self.device is not None:
            predict_kwargs["device"] = self.device

        # Run YOLO detection
        try:
            results = self.model.predict(**predict_kwargs)
        except Exception as exc:
            # Don't crash on one bad frame
            print("[ZONE X] Warning: inference failed on this frame: {}".format(exc))
            return []

        detections = []
        frame_height, frame_width = frame.shape[:2]

        # Process results
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None or len(boxes) == 0:
                continue  # No boxes in this result

            for box in boxes:
                class_id = int(box.cls[0])
                confidence = float(box.conf[0])

                # Double-check it's a person and meets confidence threshold
                if class_id != self.person_class_id:
                    continue
                if confidence < self.confidence_threshold:
                    continue

                # Get box coordinates (x1, y1, x2, y2)
                x1, y1, x2, y2 = box.xyxy[0].tolist()

                # Clip box to frame edges so it never goes outside image
                x1 = int(max(0, min(x1, frame_width - 1)))
                y1 = int(max(0, min(y1, frame_height - 1)))
                x2 = int(max(0, min(x2, frame_width - 1)))
                y2 = int(max(0, min(y2, frame_height - 1)))

                # Skip if box has zero width or height
                if x2 <= x1 or y2 <= y1:
                    continue

                # Save detection as simple dict (not YOLO object)
                detections.append({"bbox": (x1, y1, x2, y2), "confidence": confidence})

        return detections