import cv2
import threading


class FrameBroadcaster:
    """
    Stores the latest processed frame and converts it to JPEG.

    The web server reads the latest JPEG and sends it to the browser.
    """

    def __init__(self, jpeg_quality=80):
        self.jpeg_quality = jpeg_quality

        self._lock = threading.Lock()
        self._latest_frame = None

    def update(self, frame):
        """
        Receive the latest annotated OpenCV frame.
        """

        if frame is None:
            return

        success, encoded = cv2.imencode(
            ".jpg",
            frame,
            [
                cv2.IMWRITE_JPEG_QUALITY,
                self.jpeg_quality,
            ],
        )

        if not success:
            return

        with self._lock:
            self._latest_frame = encoded.tobytes()

    def get_frame(self):
        """
        Return the latest JPEG frame.
        """

        with self._lock:
            return self._latest_frame