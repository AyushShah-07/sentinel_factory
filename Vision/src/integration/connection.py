"""
BackendClient.py

PURPOSE:
Sends Python CV (Computer Vision) records to the Spring Boot backend.

This class runs as a background worker that handles HTTP communication
between the Vision (YOLO) system and the Spring Boot backend.

SUPPORTED RECORDS:
    PERSON_DETECTED -> POST /api/yolo/detections
    DANGER          -> POST /api/yolo/incidents
    WARNING         -> POST /api/yolo/incidents

WHY BACKGROUND WORKER:
The CV pipeline should not be blocked by network communication.
All HTTP requests run in a separate thread using a queue.
"""

import queue          # For thread-safe queue (background worker)
import threading      # For running background thread
import requests       # For HTTP POST requests to backend


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
        base_url="http://localhost:8080",           # Backend base URL
        incident_endpoint="/api/yolo/incidents",    # Endpoint for DANGER/WARNING
        detection_endpoint="/api/yolo/detections",  # Endpoint for PERSON_DETECTED
        camera_id="CAM-01",                         # Camera identifier
    ):
        # Remove trailing slash from base URL if present
        self.base_url = base_url.rstrip("/")

        # Full URL for incident endpoint
        self.incident_url = (
            self.base_url + incident_endpoint
        )

        # Full URL for detection endpoint
        self.detection_url = (
            self.base_url + detection_endpoint
        )

        # Camera ID sent as query parameter
        self.camera_id = camera_id

        # Queue for storing records before sending
        self._queue = queue.Queue()

        # Flag to control background worker loop
        self._running = True

        # Create background thread for processing queue
        self._worker = threading.Thread(
            target=self._process_queue,
            name="backend-worker",
            daemon=True,  # Daemon thread stops when main program exits
        )

        # Start background worker
        self._worker.start()

        # Debug output
        print("[BACKEND] Client started")
        print("[BACKEND] Detection endpoint: {}".format(self.detection_url))
        print("[BACKEND] Incident endpoint: {}".format(self.incident_url))
        print("[BACKEND] Camera ID: {}".format(self.camera_id))

    # =========================================================
    # PUBLIC METHODS
    # =========================================================

    def send_incident(self, record):
        """
        Queue one backend record for sending.

        This method is called by EventLog for all record types:
            PERSON_DETECTED
            DANGER
            WARNING

        Args:
            record: Dictionary containing incident/detection data
        """
        # If record is empty or None, ignore
        if not record:
            return

        # Add record to queue for background processing
        self._queue.put(record)

    # =========================================================
    # BACKGROUND WORKER
    # =========================================================

    def _process_queue(self):
        """
        Background worker method.
        Continuously checks queue for new records and sends them.
        """
        while self._running:
            try:
                # Wait for record from queue (timeout prevents busy-wait)
                record = self._queue.get(timeout=0.5)

            except queue.Empty:
                # No records in queue, continue waiting
                continue

            try:
                # Send record to backend
                self._send_record(record)

            except Exception as exc:
                # Log any errors but don't crash worker
                print("[BACKEND] ERROR: {}".format(exc))

            finally:
                # Mark task as done (required for queue.join())
                self._queue.task_done()

    # =========================================================
    # RECORD ROUTING
    # =========================================================

    def _send_record(self, record):
        """
        Route record to appropriate endpoint based on type.

        Args:
            record: Dictionary containing record data
        """
        # -----------------------------------------------------
        # PERSON DETECTED
        # -----------------------------------------------------
        if record.get("event") == "PERSON_DETECTED":
            self._send_detection(record)
            return

        # -----------------------------------------------------
        # DANGER / WARNING INCIDENTS
        # -----------------------------------------------------
        incident_type = str(record.get("incident_type", "")).upper()

        if incident_type in {"DANGER", "WARNING"}:
            self._send_incident_http(record)
            return

        # Unsupported record type
        print("[BACKEND] Ignoring unsupported record: {}".format(record))

    # =========================================================
    # PERSON DETECTION SENDER
    # =========================================================

    def _send_detection(self, record):
        """
        Send PERSON_DETECTED record to backend.

        Endpoint: POST /api/yolo/detections

        Args:
            record: Dictionary with person detection data
        """
        # Extract person ID from record
        person_id = record.get("person_id")

        # Extract timestamp from record
        timestamp = record.get("timestamp")

        # Build payload as list (backend expects array)
        payload = [
            {
                "timestamp": timestamp,
                "person_id": person_id,
                "event": "PERSON_DETECTED",
            }
        ]

        # Send POST request to detection endpoint
        response = requests.post(
            self.detection_url,
            params={"cameraId": self.camera_id},  # Camera ID as query param
            json=payload,  # JSON body
            timeout=5,  # 5 second timeout
        )

        # Check if request succeeded
        if response.ok:
            print("[BACKEND] PERSON_DETECTED saved for {} ({})".format(
                person_id, response.status_code))
        else:
            print("[BACKEND] PERSON_DETECTED rejected: {}".format(
                response.status_code))
            print(response.text)

    # =========================================================
    # INCIDENT SENDER (DANGER / WARNING)
    # =========================================================

    def _send_incident_http(self, event):
        """
        Send DANGER or WARNING incident to backend.

        Endpoint: POST /api/yolo/incidents

        Args:
            event: Dictionary with incident data
        """
        # Get incident type and convert to uppercase
        incident_type = str(event.get("incident_type", "")).upper()

        # Only process DANGER and WARNING
        if incident_type not in {"DANGER", "WARNING"}:
            return

        # Build incident payload with correct field names
        # These field names must match backend JSON expectations
        incident = {
            # Timestamp of incident
            "timestamp": event.get("timestamp"),

            # Person ID (who triggered the incident)
            "person_id": event.get("person_id"),

            # Zone name (DANGER_1, WARNING_1, etc.)
            "zone_name": event.get("zone_name"),

            # Incident type (DANGER or WARNING)
            "incident_type": incident_type,

            # Picture URL/path (image evidence)
            "picture_url": event.get("picture_url"),

            # Time spent in zone (seconds)
            "dwell_seconds": event.get("dwell_seconds"),
        }

        # Debug: Print what we're sending to backend
        print("[BACKEND] Sending incident: {}".format(incident))

        # Send POST request to incident endpoint
        response = requests.post(
            self.incident_url,
            params={"cameraId": self.camera_id},  # Camera ID as query param
            json=[incident],  # Backend expects array
            timeout=5,  # 5 second timeout
        )

        # Check if request succeeded
        if response.ok:
            print("[BACKEND] {} incident saved ({})".format(
                incident_type, response.status_code))
        else:
            print("[BACKEND] {} incident rejected: {}".format(
                incident_type, response.status_code))
            print(response.text)

    # =========================================================
    # SHUTDOWN
    # =========================================================

    def close(self):
        """
        Gracefully shut down the background worker.
        """
        # Stop worker loop
        self._running = False

        # Wait for worker thread to finish
        if self._worker.is_alive():
            self._worker.join(timeout=2)

        print("[BACKEND] Client stopped")