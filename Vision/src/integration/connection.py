import requests
import queue
import threading


class BackendClient:

    def __init__(self, base_url="http://localhost:8080"):

        self.detection_url = (
            base_url.rstrip("/")
            + "/api/yolo/detections"
        )

        self.incident_url = (
            base_url.rstrip("/")
            + "/api/yolo/incidents"
        )

        self._queue = queue.Queue()
        self._running = True

        self._worker = threading.Thread(
            target=self._process_queue,
            daemon=True
        )

        self._worker.start()

        print("[BACKEND] Client started")
        print(
            "[BACKEND] Detection endpoint:",
            self.detection_url
        )
        print(
            "[BACKEND] Incident endpoint:",
            self.incident_url
        )

    # =====================================================
    # PERSON DETECTION
    # =====================================================

    def send_detection(self, detection, camera_id):

        if not detection:
            return

        self._queue.put(
            (
                "detection",
                detection,
                camera_id
            )
        )

    # =====================================================
    # SAFETY INCIDENT
    # =====================================================

    def send_incident(self, incident, camera_id):

        if not incident:
            return

        self._queue.put(
            (
                "incident",
                incident,
                camera_id
            )
        )

    # =====================================================
    # BACKGROUND WORKER
    # =====================================================

    def _process_queue(self):

        while self._running:

            try:

                item = self._queue.get(
                    timeout=0.5
                )

            except queue.Empty:

                continue

            try:

                data_type, data, camera_id = item

                if data_type == "detection":

                    self._send_detection(
                        data,
                        camera_id
                    )

                elif data_type == "incident":

                    self._send_incident(
                        data,
                        camera_id
                    )

            except Exception as exc:

                print(
                    "[BACKEND] ERROR:",
                    exc
                )

            finally:

                self._queue.task_done()

    # =====================================================
    # SEND PERSON DETECTION
    # =====================================================

    def _send_detection(
        self,
        detection,
        camera_id
    ):

        payload = [
            {
                "timestamp":
                    detection.get("timestamp"),

                "event":
                    detection.get("event"),

                "person_id":
                    detection.get("person_id")
            }
        ]

        try:

            response = requests.post(
                self.detection_url,
                params={
                    "cameraId": camera_id
                },
                json=payload,
                timeout=5
            )

            if response.ok:

                print(
                    "[BACKEND] Detection sent:",
                    response.status_code
                )

            else:

                print(
                    "[BACKEND] Detection rejected:",
                    response.status_code
                )

                print(response.text)

        except requests.RequestException as exc:

            print(
                "[BACKEND] Detection connection error:",
                exc
            )

    # =====================================================
    # SEND SAFETY INCIDENT
    # =====================================================

    def _send_incident(
        self,
        incident,
        camera_id
    ):

        payload = [
            incident
        ]

        try:

            response = requests.post(
                self.incident_url,
                params={
                    "cameraId": camera_id
                },
                json=payload,
                timeout=5
            )

            if response.ok:

                print(
                    "[BACKEND] Incident sent:",
                    response.status_code
                )

            else:

                print(
                    "[BACKEND] Incident rejected:",
                    response.status_code
                )

                print(response.text)

        except requests.RequestException as exc:

            print(
                "[BACKEND] Incident connection error:",
                exc
            )

    # =====================================================
    # CLOSE
    # =====================================================

    def close(self):

        self._running = False

        if self._worker.is_alive():

            self._worker.join(
                timeout=2
            )

        print(
            "[BACKEND] Client stopped"
        )