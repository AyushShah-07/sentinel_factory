import cv2
import threading


class FrameBroadcaster:
    """
    Keeps the latest video frame as JPEG so web browser can see it
    """

    def __init__(self, jpeg_quality=80):
        # How much to compress the JPEG (higher = better quality but bigger file)
        self.jpeg_quality = jpeg_quality
        
        # Lock so only one thread can access the frame at a time (prevents crashes)
        self._lock = threading.Lock()
        # Store the latest JPEG frame here
        self._latest_frame = None

    def update(self, frame):
        """
        Give this function the latest processed frame to share with web browser
        """

        if frame is None:
            return  # Nothing to share

        # Convert OpenCV frame to JPEG format
        success, encoded = cv2.imencode(
            ".jpg",
            frame,
            [
                cv2.IMWRITE_JPEG_QUALITY,
                self.jpeg_quality,
            ],
        )

        if not success:
            return  # JPEG conversion failed

        # Save the JPEG (as bytes) safely with lock
        with self._lock:
            self._latest_frame = encoded.tobytes()

    def get_frame(self):
        """
        Return the latest JPEG to send to web browser
        """

        # Safely read the frame with lock
        with self._lock:
            return self._latest_frame