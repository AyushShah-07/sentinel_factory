"""
Helper functions for the Sentinel Factory event system.

This module contains only small, reusable functions.
It does not write files, save images, or generate incidents.
"""

from datetime import datetime


# ============================================================
# TIME
# ============================================================

def now():
    """
    Return the current timestamp in ISO format.

    Example:
        2026-08-12T10:30:25.123
    """

    return datetime.now().isoformat(
        timespec="milliseconds"
    )


# ============================================================
# PERSON ID
# ============================================================

def person_id(person):
    """
    Convert tracker information into the project's
    standard person ID.

    Examples:

        tracker 1 -> P1
        tracker 2 -> P2
        tracker 3 -> P3
    """

    if person is None:
        return None

    # Already assigned person ID
    assigned_id = person.get("person_id")

    if assigned_id:
        return str(assigned_id)

    # Identity information
    identity = person.get("identity") or {}

    identity_id = identity.get("person_id")

    if identity_id:
        return str(identity_id)

    # Tracker ID
    raw_id = person.get("id")

    if raw_id is None:
        return None

    return f"P{raw_id}"


# ============================================================
# ZONE NORMALIZATION
# ============================================================

def zone_value(person):
    """
    Get the raw zone value.

    Examples:

        DANGER
        DANGER_1
        WARNING
        WARNING_1
        SAFE
        OUTSIDE
    """

    if not person:
        return None

    zone = person.get("zone")

    if zone is None:
        return None

    return str(zone).strip().upper()


def zone_type(person):
    """
    Normalize zone names.

    Examples:

        DANGER       -> DANGER
        DANGER_1     -> DANGER
        DANGER_2     -> DANGER

        WARNING      -> WARNING
        WARNING_1    -> WARNING
        WARNING_2    -> WARNING

        SAFE         -> SAFE
        SAFE_1       -> SAFE

        OUTSIDE      -> OUTSIDE
    """

    zone = zone_value(person)

    if not zone:
        return None

    # Exact zones
    if zone in {
        "DANGER",
        "WARNING",
        "SAFE",
        "OUTSIDE",
    }:
        return zone

    # Zone IDs
    if zone.startswith("DANGER_"):
        return "DANGER"

    if zone.startswith("WARNING_"):
        return "WARNING"

    if zone.startswith("SAFE_"):
        return "SAFE"

    if zone.startswith("OUTSIDE_"):
        return "OUTSIDE"

    return zone


# ============================================================
# HAND / WRIST DANGER
# ============================================================

def hand_is_in_danger(person):
    """
    Determine whether the person's hand is inside
    a danger zone.

    The pose/zone pipeline already provides:

        hand_in_danger = True / False

    This allows:

        body -> OUTSIDE
        hand -> DANGER

    to still create a danger incident.
    """

    if not person:
        return False

    return bool(
        person.get(
            "hand_in_danger",
            False,
        )
    )


def danger_source(person):
    """
    Determine why a danger incident occurred.

    Returns:

        BODY
        HAND
        BODY_AND_HAND
        None
    """

    body_danger = (
        zone_type(person) == "DANGER"
    )

    hand_danger = hand_is_in_danger(
        person
    )

    if body_danger and hand_danger:
        return "BODY_AND_HAND"

    if body_danger:
        return "BODY"

    if hand_danger:
        return "HAND"

    return None