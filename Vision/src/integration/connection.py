
import queue
import threading

import requests


class BackendClient:
    """
    Sends Python CV records to the Spring Boot backend.

    Supported records:

        PERSON_DETECTED
            -> POST /api/yolo/detections

        DANGER / WARNING
            -> POST /api/yolo/incidents

    All HTTP requests run in a background worker so the CV pipeline
    is not blocked by backend communication.
    """

    def __init__(
        self,
        base_url="http://localhost:8080",
        incident_endpoint="/api/yolo/incidents",
        detection_endpoint="/api/yolo/detections",
        camera_id="CAM-01",
    ):
        self.base_url = base_url.rstrip("/")

        self.incident_url = (
            self.base_url + incident_endpoint
        )

        self.detection_url = (
            self.base_url + detection_endpoint
        )

        self.camera_id = camera_id

        self._queue = queue.Queue()
        self._running = True

        self._worker = threading.Thread(
            target=self._process_queue,
            name="backend-worker",
            daemon=True,
        )

        self._worker.start()

        print("[BACKEND] Client started")
        print(
            "[BACKEND] Detection endpoint: {}".format(
                self.detection_url
            )
        )
        print(
            "[BACKEND] Incident endpoint: {}".format(
                self.incident_url
            )
        )
        print(
            "[BACKEND] Camera ID: {}".format(
                self.camera_id
            )
        )

    # =========================================================
    # PUBLIC
    # =========================================================

    def send_incident(self, record):
        """
        Queue one backend record.

        This method is used by EventLog for both:
            PERSON_DETECTED
            DANGER
            WARNING
        """

        if not record:
            return

        self._queue.put(record)

    # =========================================================
    # WORKER
    # =========================================================

    def _process_queue(self):

        while self._running:

            try:
                record = self._queue.get(
                    timeout=0.5
                )

            except queue.Empty:
                continue

            try:
                self._send_record(record)

            except Exception as exc:

                print(
                    "[BACKEND] ERROR: {}".format(
                        exc
                    )
                )

            finally:

                self._queue.task_done()

    # =========================================================
    # ROUTING
    # =========================================================

    def _send_record(self, record):

        # -----------------------------------------------------
        # PERSON DETECTED
        # -----------------------------------------------------

        if (
            record.get("event")
            == "PERSON_DETECTED"
        ):

            self._send_detection(
                record
            )

            return

        # -----------------------------------------------------
        # DANGER / WARNING
        # -----------------------------------------------------

        incident_type = str(
            record.get(
                "incident_type",
                "",
            )
        ).upper()

        if incident_type in {
            "DANGER",
            "WARNING",
        }:

            self._send_incident_http(
                record
            )

            return

        print(
            "[BACKEND] Ignoring unsupported record: {}".format(
                record
            )
        )

    # =========================================================
    # PERSON DETECTION
    # =========================================================

    def _send_detection(self, record):

        person_id = (
            record.get("person_id")
        )

        timestamp = (
            record.get("timestamp")
        )

        payload = [
            {
                "timestamp": timestamp,

                # Included in both common naming styles so the
                # current PersonDetectionRequest can deserialize
                # regardless of whether it uses @JsonProperty.
                "person_id": person_id,
                "personId": person_id,

                "event": "PERSON_DETECTED",
            }
        ]

        response = requests.post(
            self.detection_url,
            params={
                "cameraId": self.camera_id,
            },
            json=payload,
            timeout=5,
        )

        if response.ok:

            print(
                "[BACKEND] PERSON_DETECTED saved "
                "for {} ({})".format(
                    person_id,
                    response.status_code,
                )
            )

        else:

            print(
                "[BACKEND] PERSON_DETECTED rejected: "
                "{}".format(
                    response.status_code
                )
            )

            print(
                response.text
            )

    # =========================================================
    # DANGER / WARNING
    # =========================================================

    def _send_incident_http(self, event):

        incident_type = str(
            event.get(
                "incident_type",
                "",
            )
        ).upper()

        if incident_type not in {
            "DANGER",
            "WARNING",
        }:
            return

        incident = {
            "timestamp": event.get(
                "timestamp"
            ),

            "person_id": event.get(
                "person_id"
            ),

            "incident_name": event.get(
                "incident_name"
            ),

            "incident_type": incident_type,

            "source": event.get(
                "source"
            ),

            "body_zone": event.get(
                "body_zone"
            ),

            "body_zone_id": event.get(
                "body_zone_id"
            ),

            "hand_zone": event.get(
                "hand_zone"
            ),

            "hand_zone_id": event.get(
                "hand_zone_id"
            ),

            "picture": event.get(
                "picture"
            ),

            "dwell_seconds": event.get(
                "dwell_seconds"
            ),
        }

        # WARNING records use generic zone / zone_id.
        if incident_type == "WARNING":

            if incident["body_zone"] is None:

                incident["body_zone"] = event.get(
                    "zone"
                )

            if incident["body_zone_id"] is None:

                incident["body_zone_id"] = event.get(
                    "zone_id"
                )

        response = requests.post(
            self.incident_url,
            params={
                "cameraId": self.camera_id,
            },
            json=[incident],
            timeout=5,
        )

        if response.ok:

            print(
                "[BACKEND] {} incident saved ({})".format(
                    incident_type,
                    response.status_code,
                )
            )

        else:

            print(
                "[BACKEND] {} incident rejected: {}".format(
                    incident_type,
                    response.status_code,
                )
            )

            print(
                response.text
            )

    # =========================================================
    # SHUTDOWN
    # =========================================================

    def close(self):

        self._running = False

        if self._worker.is_alive():

            self._worker.join(
                timeout=2
            )

        print(
            "[BACKEND] Client stopped"
        )