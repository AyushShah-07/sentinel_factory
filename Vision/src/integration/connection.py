import json
import os
import queue
import threading
import requests


class BackendClient:
    """
    Sends safety incidents from the Python CV system
    to the Spring Boot backend.

    Runs HTTP requests in a background thread so the
    OpenCV/YOLO processing loop never waits for the backend.
    """

    def __init__(
        self,
        base_url="http://localhost:8080",
        endpoint="/api/incidents",
    ):
        self.url = base_url.rstrip("/") + endpoint

        self._queue = queue.Queue()
        self._running = True

        self._worker = threading.Thread(
            target=self._process_queue,
            daemon=True,
        )
        self._worker.start()

        print("[BACKEND] Client started")
        print(f"[BACKEND] Endpoint: {self.url}")

    # ---------------------------------------------------------
    # PUBLIC
    # ---------------------------------------------------------

    def send_incident(self, event):
        """
        Queue one incident for backend delivery.

        Does NOT block the camera pipeline.
        """

        if not event:
            return

        self._queue.put(event)

    # ---------------------------------------------------------
    # WORKER
    # ---------------------------------------------------------

    def _process_queue(self):
        while self._running:

            try:
                event = self._queue.get(timeout=0.5)

            except queue.Empty:
                continue

            try:
                self._send_incident(event)

            except Exception as exc:
                print(
                    f"[BACKEND] ERROR sending incident: {exc}"
                )

            finally:
                self._queue.task_done()

    # ---------------------------------------------------------
    # HTTP
    # ---------------------------------------------------------

    def _send_incident(self, event):

        picture = event.get("picture")

        if not picture:
            print(
                "[BACKEND] Incident has no picture path"
            )
            return

        if not os.path.exists(picture):
            print(
                f"[BACKEND] Image not found: {picture}"
            )
            return

        # Send the JSON object as one multipart field.
        event_json = json.dumps(event)

        with open(picture, "rb") as image_file:

            files = {
                "image": (
                    os.path.basename(picture),
                    image_file,
                    "image/jpeg",
                )
            }

            data = {
                "event": event_json
            }

            response = requests.post(
                self.url,
                data=data,
                files=files,
                timeout=5,
            )

        if response.ok:

            print(
                f"[BACKEND] Incident sent successfully "
                f"({response.status_code})"
            )

        else:

            print(
                f"[BACKEND] Backend rejected incident: "
                f"{response.status_code}"
            )

            print(response.text)

    # ---------------------------------------------------------
    # SHUTDOWN
    # ---------------------------------------------------------

    def close(self):

        self._running = False

        if self._worker.is_alive():
            self._worker.join(timeout=2)

        print("[BACKEND] Client stopped")