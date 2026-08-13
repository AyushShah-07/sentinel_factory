"""
src/detector.py
---------------
Everything YOLO lives here. The rest of the program never imports ultralytics.

Responsibilities:
  * make sure the weights file exists (download once if needed)
  * load the YOLOv8 model
  * run inference on a frame
  * keep ONLY "person" detections above the confidence threshold
  * return plain Python data: [{"bbox": (x1,y1,x2,y2), "confidence": 0.87}, ...]

Returning plain dicts (instead of ultralytics objects) keeps the detector
swappable -- later phases could replace YOLOv8 with anything else and the UI,
zone and alert logic would not change one line.
"""

import os
import shutil


class ModelLoadError(Exception):
    """Raised when the YOLO weights cannot be found, downloaded, or loaded."""


def ensure_model_file(model_path, download_name):
    """
    Guarantee that `model_path` exists on disk, returning the usable path.

    Ultralytics can auto-download official weights, but it drops them in the
    current working directory. We want them tidy inside models/, so we let
    ultralytics fetch the file and then move it into place ourselves.
    """
    if os.path.isfile(model_path):
        return model_path

    model_dir = os.path.dirname(model_path)
    if model_dir:
        os.makedirs(model_dir, exist_ok=True)

    print("[ZONE X] Model not found at '{}'.".format(model_path))
    print("[ZONE X] Downloading '{}' (one-time, needs internet)...".format(download_name))

    reason = None
    try:
        from ultralytics.utils.downloads import attempt_download_asset

        downloaded = str(attempt_download_asset(download_name))

        if os.path.isfile(downloaded):
            if os.path.abspath(downloaded) != os.path.abspath(model_path):
                shutil.move(downloaded, model_path)
            print("[ZONE X] Model saved to '{}'.".format(model_path))
            return model_path

        # No exception, but no file either. Ultralytics returns the name
        # unchanged when it does not recognise the asset, so this is what an
        # unknown/misspelled MODEL_DOWNLOAD_NAME looks like.
        reason = ("'{}' is not a recognised ultralytics asset name, so nothing "
                  "was downloaded".format(download_name))

    except Exception as exc:
        reason = "{}: {}".format(type(exc).__name__, exc)

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
    """Thin, well-behaved wrapper around a pretrained YOLOv8 model."""

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
        self.confidence_threshold = confidence_threshold
        self.iou_threshold = iou_threshold
        self.person_class_id = person_class_id
        self.imgsz = imgsz
        self.device = device
        self.verbose = verbose

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
                "Failed to load YOLO weights from '{path}'.\n"
                "  The file may be corrupt or incomplete -- delete it and rerun to "
                "re-download.\n"
                "  Details: {exc}".format(path=resolved_path, exc=exc)
            )

        self.model_path = resolved_path
        print("[ZONE X] YOLOv8 model loaded: {}".format(resolved_path))

    def detect(self, frame):
        """
        Run one inference pass and return a list of person detections:

            [{"bbox": (x1, y1, x2, y2), "confidence": float}, ...]

        Coordinates are ints in the frame's own pixel space. An empty list is a
        completely normal result -- it just means nobody is in view.
        """
        if frame is None or getattr(frame, "size", 0) == 0:
            return []

        predict_kwargs = {
            "source": frame,
            "conf": self.confidence_threshold,
            "iou": self.iou_threshold,
            # Ask YOLO for the person class only. This is faster and safer than
            # detecting all 80 COCO classes and filtering afterwards.
            "classes": [self.person_class_id],
            "imgsz": self.imgsz,
            "verbose": self.verbose,
        }
        if self.device is not None:
            predict_kwargs["device"] = self.device

        try:
            results = self.model.predict(**predict_kwargs)
        except Exception as exc:
            # One bad frame must never kill a live safety monitor.
            print("[ZONE X] Warning: inference failed on this frame: {}".format(exc))
            return []

        detections = []
        frame_height, frame_width = frame.shape[:2]

        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None or len(boxes) == 0:
                continue

            for box in boxes:
                class_id = int(box.cls[0])
                confidence = float(box.conf[0])

                # Belt-and-braces: re-check class and confidence even though we
                # already asked YOLO to filter them.
                if class_id != self.person_class_id:
                    continue
                if confidence < self.confidence_threshold:
                    continue

                x1, y1, x2, y2 = box.xyxy[0].tolist()

                # Clip to the frame so boxes never extend past the image edges.
                x1 = int(max(0, min(x1, frame_width - 1)))
                y1 = int(max(0, min(y1, frame_height - 1)))
                x2 = int(max(0, min(x2, frame_width - 1)))
                y2 = int(max(0, min(y2, frame_height - 1)))

                if x2 <= x1 or y2 <= y1:
                    continue  # degenerate box, ignore

                detections.append({"bbox": (x1, y1, x2, y2), "confidence": confidence})

        return detections
