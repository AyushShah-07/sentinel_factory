"""
Event constants used by the Sentinel Factory event system.
"""

# ============================================================
# EVENT NAMES
# ============================================================

PERSON_DETECTED = "PERSON_DETECTED"

DANGER_INCIDENT_NAME = (
    "Person enters the danger zone"
)

HAND_DANGER_INCIDENT_NAME = (
    "Person reaches into the danger zone"
)

WARNING_INCIDENT_NAME = (
    "Person stays in the warning zone for more than 10s"
)