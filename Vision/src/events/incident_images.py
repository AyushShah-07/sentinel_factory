"""
Incident image storage for the Sentinel Factory event system.
"""

import os
from datetime import datetime

import cv2


# ============================================================
# INCIDENT IMAGE SAVER
# ============================================================

class IncidentImageSaver:

    def __init__(
        self,
        log_root="logs",
        save_images=True,
    ):

        self.log_root = log_root

        self.save_images = bool(
            save_images
        )

        # ----------------------------------------------------
        # IMAGE DIRECTORIES
        # ----------------------------------------------------

        self.danger_image_dir = os.path.join(
            self.log_root,
            "incidents",
            "danger",
        )

        self.warning_image_dir = os.path.join(
            self.log_root,
            "incidents",
            "warning",
        )

        os.makedirs(
            self.danger_image_dir,
            exist_ok=True,
        )

        os.makedirs(
            self.warning_image_dir,
            exist_ok=True,
        )

    # ========================================================
    # SAVE FRAME
    # ========================================================

    def save(
        self,
        frame,
        person_id,
        incident_type,
    ):
        """
        Save an incident frame.

        incident_type:

            danger
            warning
        """

        if not self.save_images:
            return None

        # ----------------------------------------------------
        # FRAME CHECK
        # ----------------------------------------------------

        if frame is None:

            print(
                "[Event] ERROR: frame is None."
            )

            return None

        if not hasattr(
            frame,
            "shape",
        ):

            print(
                "[Event] ERROR: Invalid frame."
            )

            return None

        if frame.size == 0:

            print(
                "[Event] ERROR: Empty frame."
            )

            return None

        # ----------------------------------------------------
        # TIMESTAMP
        # ----------------------------------------------------

        timestamp = datetime.now()

        timestamp_text = timestamp.strftime(
            "%Y-%m-%d_%H-%M-%S-%f"
        )

        # ----------------------------------------------------
        # DIRECTORY
        # ----------------------------------------------------

        if incident_type == "danger":

            directory = (
                self.danger_image_dir
            )

        elif incident_type == "warning":

            directory = (
                self.warning_image_dir
            )

        else:

            print(
                "[Event] ERROR: Unknown incident "
                f"type: {incident_type}"
            )

            return None

        os.makedirs(
            directory,
            exist_ok=True,
        )

        # ----------------------------------------------------
        # FILE
        # ----------------------------------------------------

        filename = (
            f"{person_id}_{timestamp_text}.jpg"
        )

        path = os.path.abspath(
            os.path.join(
                directory,
                filename,
            )
        )

        # ----------------------------------------------------
        # WRITE
        # ----------------------------------------------------

        success = cv2.imwrite(
            path,
            frame,
        )

        if not success:

            print(
                "[Event] ERROR: cv2.imwrite failed:"
            )

            print(
                f"         {path}"
            )

            return None

        # ----------------------------------------------------
        # VERIFY
        # ----------------------------------------------------

        if not os.path.exists(path):

            print(
                "[Event] ERROR: Image was not created:"
            )

            print(
                f"         {path}"
            )

            return None

        print(
            f"[Event] IMAGE SAVED: {path}"
        )

        return path