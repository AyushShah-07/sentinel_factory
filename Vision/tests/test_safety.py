"""
tests/test_safety.py
--------------------
Headless test suite for the Phase 2 multi-zone safety system.

Runs with NO camera, NO GUI window and NO model weights: everything here uses
synthetic polygons, synthetic bounding boxes and an injected clock. That is
possible only because the risk engine is a pure function of measured facts and
the tracker accepts `now` as a parameter -- which is the main practical payoff of
keeping the rule engine separate from the vision code.

Run:
    python tests/test_safety.py
    python -m unittest discover -s tests -t .

Covers:
  * Test 1-10 from the requirements
  * regression on the Phase 1 behaviour (feet point, polygon test, hysteresis,
    dead-band, RestrictedZone itself)
  * the event log's transition detection
  * a rendering smoke test over every new UI panel
"""

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta

# Allow running this file directly from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np

import config
import main
from src.visualization import ui
from src.events.event_logger import EventLogger, json_safe
from src.alarm.alarm import Alarm
from src.events import (
    ALARM_STARTED,
    ALARM_STOPPED,
    CRITICAL_RISK,
    DANGER_DWELL_EXCEEDED,
    DANGER_ZONE_ENTERED,
    DANGER_ZONE_LEFT,
    HAND_ENTERED_DANGER,
    HAND_ENTERED_WARNING,
    HAND_LEFT_DANGER,
    HAND_LEFT_WARNING,
    MACHINE_STATE_CHANGED,
    PERSON_DETECTED,
    PERSON_LOST,
    RISK_CHANGED,
    ZONE_ENTERED,
    ZONE_LEFT,
    EventLog,
)
from src.events.incident_manager import (
    WARNING_ZONE_INCIDENT,
    IncidentManager,
    WarningIncident,
)
from src.camera.frame_source import (
    FrameSource,
    FrameSourceError,
    LatestFrame,
    ProcessingScheduler,
    SourceStats,
)
from src.safety.risk_engine import RiskEngine
from src.detection.tracker import PersonTracker, format_person_id, make_identity
from src.zones.zone import RestrictedZone, SafetyZone, ZoneIdFactory, ZoneManager
from utils.geometry import get_bottom_center, point_in_polygon, to_polygon_array

# A nested layout, which is how these zones are drawn in practice:
# DANGER around the machine, WARNING around that, SAFE over the walkway.
SAFE_POLY = [(100, 100), (1100, 100), (1100, 700), (100, 700)]
WARNING_POLY = [(300, 200), (900, 200), (900, 600), (300, 600)]
DANGER_POLY = [(500, 300), (700, 300), (700, 500), (500, 500)]

# Points chosen to sit well clear of every boundary, so the dead-band never
# makes a test ambiguous.
PT_DANGER = (600, 400)
PT_WARNING = (350, 250)
PT_SAFE = (150, 150)
PT_OUTSIDE = (40, 40)


def make_zones():
    """One zone of each type: SAFE_1, WARNING_1, DANGER_1."""
    zones = ZoneManager(config)
    zones.set_frame_size(1280, 720)
    zones.set_zone_points(config.ZONE_SAFE, SAFE_POLY)
    zones.set_zone_points(config.ZONE_WARNING, WARNING_POLY)
    zones.set_zone_points(config.ZONE_DANGER, DANGER_POLY)
    return zones


def zone_id_for(zone_type):
    """The tag the first zone of a type gets, for building fake person dicts."""
    if zone_type in (None, config.ZONE_OUTSIDE):
        return None
    return "{}_1".format(zone_type)


def box_at(feet_x, feet_y, width=60, height=160, confidence=0.9):
    """A detection whose bottom-center lands exactly on (feet_x, feet_y)."""
    return {
        "bbox": (int(feet_x - width / 2), int(feet_y - height),
                 int(feet_x + width / 2), int(feet_y)),
        "confidence": confidence,
    }


def make_tracker(enabled=True):
    return PersonTracker(
        enabled=enabled,
        max_distance=config.TRACK_MAX_DISTANCE,
        max_missing_frames=config.TRACK_MAX_MISSING_FRAMES,
        zone_margin_px=config.ZONE_MARGIN_PX,
        enter_confirm_frames=config.ENTER_CONFIRM_FRAMES,
        exit_confirm_frames=config.EXIT_CONFIRM_FRAMES,
        hand_margin_px=config.HAND_MARGIN_PX,
        hand_enter_confirm_frames=config.HAND_ENTER_CONFIRM_FRAMES,
        hand_exit_confirm_frames=config.HAND_EXIT_CONFIRM_FRAMES,
    )


def settle(tracker, zones, detections, frames=None, start=1000.0, step=0.1):
    """Feed the same detections until the hysteresis has confirmed a state."""
    if frames is None:
        frames = config.ENTER_CONFIRM_FRAMES + 1
    people = []
    for index in range(frames):
        people = tracker.update(detections, zones, now=start + index * step)
    return people


# ===========================================================================
# Phase 1 regression -- the working MVP must not have changed
# ===========================================================================

class TestPhase1Regression(unittest.TestCase):
    """The original feet-point restricted-area behaviour, unchanged."""

    def test_foot_point_is_bottom_center(self):
        self.assertEqual(get_bottom_center((100, 50, 200, 400)), (150.0, 400.0))

    def test_bounding_box_overlap_is_not_used(self):
        # The whole point of the foot heuristic: a tall person standing BEHIND
        # the machine overlaps the danger polygon with their torso while their
        # feet are well clear. That must read as not-in-danger.
        zones = make_zones()
        detection = box_at(600, 620, height=400)   # box spans y 220..620
        # The box top is above the DANGER polygon's top edge, so a box-overlap
        # test would alarm...
        self.assertLess(detection["bbox"][1], 300)
        self.assertGreater(zones.signed_distance(config.ZONE_DANGER, PT_DANGER), 0)
        # ...but the feet are 120 px below the danger zone, out on the walkway.
        feet = get_bottom_center(detection["bbox"])
        self.assertEqual(zones.classify(feet), config.ZONE_SAFE)
        self.assertAlmostEqual(zones.distance_to_danger(feet), 120.0)

    def test_concave_polygon_notch_is_outside(self):
        # L-shape: the notch must not count as inside.
        poly = to_polygon_array([(0, 0), (100, 0), (100, 40), (40, 40),
                                 (40, 100), (0, 100)])
        self.assertTrue(point_in_polygon((20, 20), poly))
        self.assertFalse(point_in_polygon((70, 70), poly))

    def test_restricted_zone_class_still_works(self):
        zone = RestrictedZone()
        zone.start_definition()
        self.assertTrue(zone.is_defining)
        zone.points = list(DANGER_POLY)
        self.assertTrue(zone.finish_definition())
        self.assertTrue(zone.is_ready)
        self.assertTrue(zone.contains(PT_DANGER))
        self.assertFalse(zone.contains(PT_OUTSIDE))
        self.assertGreater(zone.signed_distance(PT_DANGER), 0)

    def test_polygon_validation_still_rejects_junk(self):
        zone = RestrictedZone()
        zone.start_definition()
        zone.points = [(0, 0), (1, 0)]
        self.assertFalse(zone.finish_definition())      # too few points
        zone.points = [(0, 0), (2, 0), (2, 2)]
        self.assertFalse(zone.finish_definition())      # too small
        self.assertIn("too small", zone.last_message.lower())

    def test_dead_band_stops_flicker_on_the_boundary(self):
        # A person parked exactly on the DANGER edge must not change state.
        zones = make_zones()
        tracker = make_tracker()
        settle(tracker, zones, [box_at(*PT_DANGER)])
        edge_detection = box_at(600, 500)   # y = 500 is the polygon edge itself

        states = set()
        for index in range(20):
            people = tracker.update([edge_detection], zones, now=2000.0 + index * 0.03)
            states.add(people[0]["zone"])
        self.assertEqual(states, {config.ZONE_DANGER},
                         "state changed while sitting in the dead-band")

    def test_phase1_keys_still_present_in_results(self):
        zones = make_zones()
        tracker = make_tracker()
        people = settle(tracker, zones, [box_at(*PT_DANGER)])
        self.assertTrue(people[0]["is_inside"])
        self.assertGreater(people[0]["distance"], 0)


# ===========================================================================
# Feature 1 / 2 -- three zones and foot-point classification
# ===========================================================================

class TestZones(unittest.TestCase):

    def setUp(self):
        self.zones = make_zones()

    def test_all_three_zones_defined(self):
        self.assertEqual(sorted(self.zones.ready_types),
                         sorted([config.ZONE_SAFE, config.ZONE_WARNING,
                                 config.ZONE_DANGER]))

    def test_each_zone_carries_its_metadata(self):
        for zone_type, risk in ((config.ZONE_SAFE, 0),
                                (config.ZONE_WARNING, 30),
                                (config.ZONE_DANGER, 70)):
            zone = self.zones.zone(zone_type)
            self.assertIsInstance(zone, SafetyZone)
            self.assertEqual(zone.zone_type, zone_type)
            self.assertEqual(zone.risk_level, risk)
            self.assertEqual(zone.name, config.ZONE_LABELS[zone_type])
            self.assertEqual(zone.color, config.ZONE_COLORS[zone_type])
            self.assertGreaterEqual(len(zone.points), 3)

    def test_classification_of_each_region(self):
        self.assertEqual(self.zones.classify(PT_DANGER), config.ZONE_DANGER)
        self.assertEqual(self.zones.classify(PT_WARNING), config.ZONE_WARNING)
        self.assertEqual(self.zones.classify(PT_SAFE), config.ZONE_SAFE)
        self.assertEqual(self.zones.classify(PT_OUTSIDE), config.ZONE_OUTSIDE)

    def test_danger_wins_when_polygons_are_nested(self):
        # PT_DANGER is geometrically inside all three polygons.
        for zone_type in (config.ZONE_SAFE, config.ZONE_WARNING, config.ZONE_DANGER):
            self.assertTrue(self.zones.contains(zone_type, PT_DANGER))
        self.assertEqual(self.zones.classify(PT_DANGER), config.ZONE_DANGER)

    def test_no_zones_means_outside(self):
        empty = ZoneManager(config)
        self.assertFalse(empty.any_ready)
        self.assertEqual(empty.classify(PT_DANGER), config.ZONE_OUTSIDE)
        self.assertIsNone(empty.distance_to_danger(PT_DANGER))

    def test_clearing_one_zone_leaves_the_others(self):
        self.zones.clear(config.ZONE_WARNING)
        self.assertIsNone(self.zones.zone(config.ZONE_WARNING))
        self.assertNotIn(config.ZONE_WARNING, self.zones.ready_types)
        self.assertTrue(self.zones.zone(config.ZONE_DANGER).is_ready)
        self.assertTrue(self.zones.zone(config.ZONE_SAFE).is_ready)
        self.assertEqual(self.zones.classify(PT_WARNING), config.ZONE_SAFE)

    def test_only_one_zone_can_be_defined_at_a_time(self):
        self.zones.start_definition(config.ZONE_SAFE)
        self.assertEqual(self.zones.defining_type, config.ZONE_SAFE)
        self.zones.start_definition(config.ZONE_DANGER)
        self.assertEqual(self.zones.defining_type, config.ZONE_DANGER)
        self.assertFalse(self.zones.zone(config.ZONE_SAFE).is_defining)


# ===========================================================================
# Feature 7 -- image-space distance to the danger zone
# ===========================================================================

class TestDistanceToDanger(unittest.TestCase):

    def setUp(self):
        self.zones = make_zones()

    def test_zero_inside_the_danger_zone(self):
        self.assertEqual(self.zones.distance_to_danger(PT_DANGER), 0.0)

    def test_perpendicular_distance_to_the_nearest_edge(self):
        # DANGER spans x 500..700, y 300..500.
        self.assertAlmostEqual(self.zones.distance_to_danger((600, 200)), 100.0)
        self.assertAlmostEqual(self.zones.distance_to_danger((400, 400)), 100.0)
        self.assertAlmostEqual(self.zones.distance_to_danger((600, 550)), 50.0)

    def test_diagonal_distance_is_to_the_corner(self):
        # 30 px left and 40 px above the (500, 300) corner -> 50 px.
        self.assertAlmostEqual(self.zones.distance_to_danger((470, 260)), 50.0)

    def test_none_when_no_danger_zone_drawn(self):
        self.zones.clear(config.ZONE_DANGER)
        self.assertIsNone(self.zones.distance_to_danger(PT_SAFE))

    def test_distance_reaches_the_person_dict(self):
        tracker = make_tracker()
        people = settle(tracker, self.zones, [box_at(600, 200)])
        self.assertAlmostEqual(people[0]["distance_to_danger"], 100.0)


# ===========================================================================
# Tests 1-5 -- the documented risk rules
# ===========================================================================

class TestRiskRules(unittest.TestCase):

    def setUp(self):
        self.engine = RiskEngine(config)

    def test_1_person_in_safe_zone(self):
        verdict = self.engine.calculate_risk(zone=config.ZONE_SAFE)
        self.assertEqual(verdict["risk"], 0)
        self.assertEqual(verdict["level"], "SAFE")

    def test_2_person_in_warning_zone(self):
        verdict = self.engine.calculate_risk(zone=config.ZONE_WARNING)
        self.assertEqual(verdict["risk"], 30)

    def test_3_person_in_danger_zone(self):
        verdict = self.engine.calculate_risk(zone=config.ZONE_DANGER)
        self.assertEqual(verdict["risk"], 70)
        self.assertIn("Worker is inside Danger Zone", verdict["reasons"])

    def test_4_danger_for_more_than_five_seconds(self):
        verdict = self.engine.calculate_risk(zone=config.ZONE_DANGER,
                                             danger_duration=7.2)
        self.assertEqual(verdict["risk"], 90)
        self.assertIn("DANGER_TIMEOUT", verdict["conditions"])

    def test_4b_just_under_the_threshold_does_not_escalate(self):
        verdict = self.engine.calculate_risk(zone=config.ZONE_DANGER,
                                             danger_duration=4.9)
        self.assertEqual(verdict["risk"], 70)

    def test_5_danger_plus_machine_on(self):
        verdict = self.engine.calculate_risk(zone=config.ZONE_DANGER,
                                             machine_on=True)
        self.assertEqual(verdict["risk"], 100)
        self.assertEqual(verdict["level"], "CRITICAL")
        self.assertIn("Machine is ON", verdict["reasons"])

    def test_6_wrist_in_danger_while_feet_are_safe(self):
        verdict = self.engine.calculate_risk(zone=config.ZONE_SAFE,
                                             hand_in_danger=True)
        self.assertEqual(verdict["risk"], 90)
        self.assertIn("Worker hand is inside Danger Zone", verdict["reasons"])

    def test_7_wrist_in_danger_plus_machine_on(self):
        verdict = self.engine.calculate_risk(zone=config.ZONE_SAFE,
                                             hand_in_danger=True,
                                             machine_on=True)
        self.assertEqual(verdict["risk"], 100)

    def test_5b_danger_plus_machine_on_is_immediate(self):
        # Machine ON must reach 100 straight away -- it does not wait for dwell.
        verdict = self.engine.calculate_risk(zone=config.ZONE_DANGER,
                                             danger_duration=0.0,
                                             machine_on=True)
        self.assertEqual(verdict["risk"], 100)

    def test_danger_at_exactly_the_threshold_escalates(self):
        self.assertEqual(
            self.engine.calculate_risk(
                zone=config.ZONE_DANGER,
                danger_duration=config.DANGER_TIME_THRESHOLD_SEC)["risk"], 90)

    def test_danger_at_zero_seconds_is_70(self):
        verdict = self.engine.calculate_risk(zone=config.ZONE_DANGER,
                                             danger_duration=0.0)
        self.assertEqual(verdict["risk"], 70)
        self.assertNotIn("DANGER_TIMEOUT", verdict["conditions"])

    def test_standing_in_danger_does_not_jump_to_90_via_the_hand_rule(self):
        # REGRESSION. A worker standing inside the danger zone has their wrists
        # inside it too. The reach-in rule used to fire there and pin the score
        # at 90 from the moment they stepped in, hiding the 0-5 s window.
        for duration in (0.0, 0.5, 2.0, 4.0, 4.9):
            verdict = self.engine.calculate_risk(
                zone=config.ZONE_DANGER,
                danger_duration=duration,
                hand_zone=config.ZONE_DANGER,
            )
            self.assertEqual(verdict["risk"], 70,
                             "risk jumped early at {}s".format(duration))
            # The fact is still reported, it just does not escalate.
            self.assertIn("HAND_IN_DANGER", verdict["conditions"])

        # And it still escalates on schedule.
        self.assertEqual(self.engine.calculate_risk(
            zone=config.ZONE_DANGER, danger_duration=5.5,
            hand_zone=config.ZONE_DANGER)["risk"], 90)
        self.assertEqual(self.engine.calculate_risk(
            zone=config.ZONE_DANGER, danger_duration=1.0, machine_on=True,
            hand_zone=config.ZONE_DANGER)["risk"], 100)

    def test_hand_in_warning_while_body_is_safe(self):
        verdict = self.engine.calculate_risk(zone=config.ZONE_SAFE,
                                             hand_zone=config.ZONE_WARNING)
        self.assertEqual(verdict["risk"], 30)
        self.assertIn("HAND_IN_WARNING", verdict["conditions"])
        self.assertIn("Worker hand is inside Warning Zone", verdict["reasons"])

    def test_hand_in_warning_does_not_change_the_body_zone(self):
        # The engine reports on both independently; it never rewrites the body.
        verdict = self.engine.calculate_risk(zone=config.ZONE_SAFE,
                                             hand_zone=config.ZONE_WARNING)
        self.assertIn("BODY_IN_SAFE", verdict["conditions"])
        self.assertNotIn("BODY_IN_WARNING", verdict["conditions"])

    def test_hand_zone_supersedes_the_legacy_bool(self):
        by_bool = self.engine.calculate_risk(zone=config.ZONE_SAFE,
                                             hand_in_danger=True)
        by_zone = self.engine.calculate_risk(zone=config.ZONE_SAFE,
                                             hand_zone=config.ZONE_DANGER)
        self.assertEqual(by_bool["risk"], by_zone["risk"])

    def test_hand_in_safe_zone_adds_nothing(self):
        verdict = self.engine.calculate_risk(zone=config.ZONE_SAFE,
                                             hand_zone=config.ZONE_SAFE)
        self.assertEqual(verdict["risk"], 0)

    def test_machine_on_alone_does_not_raise_risk(self):
        verdict = self.engine.calculate_risk(zone=config.ZONE_SAFE,
                                             machine_on=True)
        self.assertEqual(verdict["risk"], 0)

    def test_risk_is_max_not_overwrite(self):
        # All four escalations at once: 70, 90, 100, 90 -> 100, and every reason
        # is still reported rather than the earlier ones being replaced.
        verdict = self.engine.calculate_risk(
            zone=config.ZONE_DANGER, danger_duration=7.2,
            machine_on=True, hand_in_danger=True,
        )
        self.assertEqual(verdict["risk"], 100)
        self.assertEqual(len(verdict["reasons"]), 4)
        for condition in ("BODY_IN_DANGER", "DANGER_TIMEOUT",
                          "MACHINE_ON", "HAND_IN_DANGER"):
            self.assertIn(condition, verdict["conditions"])

    def test_reasons_are_deduplicated(self):
        # "Machine is ON" fires from both the body rule and the hand rule.
        verdict = self.engine.calculate_risk(
            zone=config.ZONE_DANGER, machine_on=True, hand_in_danger=True,
        )
        self.assertEqual(verdict["reasons"].count("Machine is ON"), 1)

    def test_proximity_is_explanation_only(self):
        # Proximity is an uncalibrated pixel measurement, so by default it
        # explains without scoring: a worker in WARNING reads exactly 30
        # whether or not they are near the machine.
        verdict = self.engine.calculate_risk(zone=config.ZONE_WARNING,
                                             distance_to_danger=10.0)
        self.assertEqual(verdict["risk"], 30)
        self.assertIn("NEAR_DANGER", verdict["conditions"])
        self.assertTrue(any("image distance" in r for r in verdict["reasons"]))

    def test_proximity_not_double_counted_inside_danger(self):
        verdict = self.engine.calculate_risk(zone=config.ZONE_DANGER,
                                             distance_to_danger=0.0)
        self.assertEqual(verdict["risk"], 70)
        self.assertNotIn("NEAR_DANGER", verdict["conditions"])

    def test_far_away_contributes_nothing(self):
        verdict = self.engine.calculate_risk(zone=config.ZONE_SAFE,
                                             distance_to_danger=500.0)
        self.assertEqual(verdict["risk"], 0)

    def test_levels_map_as_documented(self):
        self.assertEqual(self.engine.risk_level(0), "SAFE")
        self.assertEqual(self.engine.risk_level(30), "WARNING")
        self.assertEqual(self.engine.risk_level(70), "HIGH")
        self.assertEqual(self.engine.risk_level(90), "CRITICAL")
        self.assertEqual(self.engine.risk_level(100), "CRITICAL")


# ===========================================================================
# Feature 5 / Test 8 -- per-person danger timers
# ===========================================================================

class TestDangerTimer(unittest.TestCase):

    def setUp(self):
        self.zones = make_zones()
        self.tracker = make_tracker()

    def test_timer_starts_on_entry_and_accumulates(self):
        detection = [box_at(*PT_DANGER)]
        self.tracker.update(detection, self.zones, now=100.0)
        self.tracker.update(detection, self.zones, now=100.1)  # confirmed here
        people = self.tracker.update(detection, self.zones, now=106.5)
        self.assertEqual(people[0]["zone"], config.ZONE_DANGER)
        self.assertAlmostEqual(people[0]["danger_duration"], 6.4, places=5)

    def test_8_timer_resets_when_the_person_leaves(self):
        detection = [box_at(*PT_DANGER)]
        for index in range(4):
            self.tracker.update(detection, self.zones, now=100.0 + index)
        people = self.tracker.update(detection, self.zones, now=110.0)
        self.assertGreater(people[0]["danger_duration"], 5.0)

        # Walk out gradually so the tracker keeps the same ID, and give the exit
        # hysteresis the frames it needs.
        people = None
        for index, feet_y in enumerate([520, 540, 560, 580, 600, 620]):
            people = self.tracker.update([box_at(600, feet_y)], self.zones,
                                          now=111.0 + index * 0.1)
        self.assertEqual(people[0]["zone"], config.ZONE_WARNING)
        self.assertEqual(people[0]["danger_duration"], 0.0)

    def test_timer_restarts_from_zero_on_re_entry(self):
        detection = [box_at(*PT_DANGER)]
        for index in range(3):
            self.tracker.update(detection, self.zones, now=100.0 + index)
        for index, feet_y in enumerate([520, 540, 560, 580, 600, 620]):
            self.tracker.update([box_at(600, feet_y)], self.zones,
                                now=110.0 + index * 0.1)
        # Walk back in.
        people = None
        for index, feet_y in enumerate([580, 540, 480, 440]):
            people = self.tracker.update([box_at(600, feet_y)], self.zones,
                                          now=120.0 + index * 0.1)
        self.assertEqual(people[0]["zone"], config.ZONE_DANGER)
        self.assertLess(people[0]["danger_duration"], 1.0)

    def test_9_two_people_have_independent_timers(self):
        # Worker A stands in DANGER the whole time; worker B arrives later.
        a = box_at(*PT_DANGER)
        b = box_at(*PT_SAFE)
        for index in range(3):
            self.tracker.update([a, b], self.zones, now=200.0 + index * 0.1)
        people = self.tracker.update([a, b], self.zones, now=207.8)

        by_zone = {p["zone"]: p for p in people}
        self.assertIn(config.ZONE_DANGER, by_zone)
        self.assertIn(config.ZONE_SAFE, by_zone)
        self.assertGreater(by_zone[config.ZONE_DANGER]["danger_duration"], 7.0)
        self.assertEqual(by_zone[config.ZONE_SAFE]["danger_duration"], 0.0)
        # ...and their risk scores differ accordingly.
        engine = RiskEngine(config)
        self.assertEqual(engine.evaluate_person(by_zone[config.ZONE_SAFE],
                                                 False)["risk"], 0)
        self.assertEqual(engine.evaluate_person(by_zone[config.ZONE_DANGER],
                                                 False)["risk"], 90)

    def test_there_is_no_global_timer(self):
        # Two people in the danger zone must not share one clock: the one who
        # arrived second must have the shorter dwell.
        first = box_at(600, 400)
        second = box_at(660, 480)
        for index in range(3):
            self.tracker.update([first], self.zones, now=300.0 + index * 0.1)
        people = None
        for index in range(3):
            people = self.tracker.update([first, second], self.zones,
                                          now=305.0 + index * 0.1)
        durations = sorted(p["danger_duration"] for p in people)
        self.assertLess(durations[0], 1.0)
        self.assertGreater(durations[1], 5.0)


# ===========================================================================
# Features 8-11 / Tests 6, 7, 10 -- hand / reach-in detection
# ===========================================================================

class TestHandReachIn(unittest.TestCase):

    def setUp(self):
        self.zones = make_zones()
        self.tracker = make_tracker()
        # Feet planted in the SAFE zone for every test in this class.
        self.detection = [box_at(*PT_SAFE)]
        self.people = settle(self.tracker, self.zones, self.detection)

    def _pose_frame(self, wrists, now=None):
        people = self.tracker.update(self.detection, self.zones,
                                      now=now or 1500.0)
        self.tracker.update_hands(people, {people[0]["id"]: wrists}, self.zones)
        return people[0]

    def test_10_feet_safe_hand_danger_are_reported_separately(self):
        person = None
        for index in range(config.HAND_ENTER_CONFIRM_FRAMES):
            person = self._pose_frame([PT_DANGER], now=1500.0 + index * 0.1)
        self.assertEqual(person["zone"], config.ZONE_SAFE,
                         "the body must NOT be labelled as in the danger zone")
        self.assertTrue(person["hand_in_danger"])
        self.assertEqual(person["hand_zone"], config.ZONE_DANGER)

    def test_6_risk_is_90_with_machine_off(self):
        person = None
        for index in range(config.HAND_ENTER_CONFIRM_FRAMES):
            person = self._pose_frame([PT_DANGER], now=1500.0 + index * 0.1)
        verdict = RiskEngine(config).evaluate_person(person, machine_on=False)
        self.assertEqual(verdict["risk"], 90)

    def test_7_risk_is_100_with_machine_on(self):
        person = None
        for index in range(config.HAND_ENTER_CONFIRM_FRAMES):
            person = self._pose_frame([PT_DANGER], now=1500.0 + index * 0.1)
        verdict = RiskEngine(config).evaluate_person(person, machine_on=True)
        self.assertEqual(verdict["risk"], 100)

    def test_single_noisy_frame_does_not_trigger(self):
        person = self._pose_frame([PT_SAFE], now=1500.0)
        self.assertFalse(person["hand_in_danger"])
        person = self._pose_frame([PT_DANGER], now=1500.1)
        self.assertFalse(person["hand_in_danger"],
                         "one frame must never raise a reach-in violation")

    def test_confirmation_takes_exactly_the_configured_frames(self):
        states = []
        for index in range(config.HAND_ENTER_CONFIRM_FRAMES + 1):
            person = self._pose_frame([PT_DANGER], now=1500.0 + index * 0.1)
            states.append(person["hand_in_danger"])
        self.assertEqual(states[:config.HAND_ENTER_CONFIRM_FRAMES - 1],
                         [False] * (config.HAND_ENTER_CONFIRM_FRAMES - 1))
        self.assertTrue(states[config.HAND_ENTER_CONFIRM_FRAMES - 1])

    def test_violation_clears_after_the_exit_threshold(self):
        for index in range(config.HAND_ENTER_CONFIRM_FRAMES):
            person = self._pose_frame([PT_DANGER], now=1500.0 + index * 0.1)
        self.assertTrue(person["hand_in_danger"])

        cleared_at = None
        for index in range(config.HAND_EXIT_CONFIRM_FRAMES + 2):
            person = self._pose_frame([PT_SAFE], now=1600.0 + index * 0.1)
            if not person["hand_in_danger"] and cleared_at is None:
                cleared_at = index + 1
        self.assertEqual(cleared_at, config.HAND_EXIT_CONFIRM_FRAMES)

    def test_missing_wrist_eventually_clears_the_violation(self):
        for index in range(config.HAND_ENTER_CONFIRM_FRAMES):
            person = self._pose_frame([PT_DANGER], now=1500.0 + index * 0.1)
        self.assertTrue(person["hand_in_danger"])
        for index in range(config.HAND_EXIT_CONFIRM_FRAMES):
            person = self._pose_frame([], now=1700.0 + index * 0.1)
        self.assertFalse(person["hand_in_danger"])

    def test_either_wrist_counts(self):
        person = None
        for index in range(config.HAND_ENTER_CONFIRM_FRAMES):
            # Left wrist safe, right wrist in the machine.
            person = self._pose_frame([PT_SAFE, PT_DANGER],
                                       now=1500.0 + index * 0.1)
        self.assertTrue(person["hand_in_danger"])

    def test_only_the_offending_wrist_is_flagged(self):
        person = None
        for index in range(config.HAND_ENTER_CONFIRM_FRAMES):
            person = self._pose_frame([PT_SAFE, PT_DANGER],
                                       now=1500.0 + index * 0.1)
        # The violation is real, but only the second wrist caused it -- the UI
        # must not redden the hand that is safely at the worker's side.
        self.assertEqual(person["wrist_flags"], [False, True])

    def test_hand_zone_is_debounced_into_warning(self):
        person = None
        for index in range(config.HAND_ENTER_CONFIRM_FRAMES):
            person = self._pose_frame([PT_WARNING], now=1500.0 + index * 0.1)
        self.assertEqual(person["zone"], config.ZONE_SAFE)
        self.assertEqual(person["hand_zone"], config.ZONE_WARNING)
        self.assertFalse(person["hand_in_danger"])
        self.assertEqual(
            RiskEngine(config).evaluate_person(person, False)["risk"], 30)

    def test_hand_zone_is_none_before_any_wrist_is_seen(self):
        people = settle(self.tracker, self.zones, self.detection)
        self.assertIsNone(people[0]["hand_zone"])

    def test_hand_zone_moves_warning_to_danger_independently_of_the_body(self):
        for index in range(config.HAND_ENTER_CONFIRM_FRAMES):
            person = self._pose_frame([PT_WARNING], now=1500.0 + index * 0.1)
        self.assertEqual(person["hand_zone"], config.ZONE_WARNING)
        for index in range(config.HAND_ENTER_CONFIRM_FRAMES
                           + config.HAND_EXIT_CONFIRM_FRAMES):
            person = self._pose_frame([PT_DANGER], now=1600.0 + index * 0.1)
        self.assertEqual(person["hand_zone"], config.ZONE_DANGER)
        # ...and the body never moved.
        self.assertEqual(person["zone"], config.ZONE_SAFE)

    def test_single_noisy_warning_frame_does_not_change_the_hand_zone(self):
        person = self._pose_frame([PT_SAFE], now=1500.0)
        first = person["hand_zone"]
        person = self._pose_frame([PT_WARNING], now=1500.1)
        self.assertEqual(person["hand_zone"], first)

    def test_no_danger_zone_means_no_violation(self):
        self.zones.clear(config.ZONE_DANGER)
        person = None
        for index in range(config.HAND_ENTER_CONFIRM_FRAMES + 2):
            person = self._pose_frame([PT_DANGER], now=1500.0 + index * 0.1)
        self.assertFalse(person["hand_in_danger"])


class TestWristAssociation(unittest.TestCase):
    """Pose skeletons are matched back onto YOLO person tracks by overlap."""

    def setUp(self):
        # Imported here: the module only needs ultralytics when a model is
        # actually constructed, and these helpers construct nothing.
        from src.pose.pose_detector import associate_wrists, box_iou
        self.associate_wrists = associate_wrists
        self.box_iou = box_iou

    def test_iou_basics(self):
        self.assertAlmostEqual(self.box_iou((0, 0, 10, 10), (0, 0, 10, 10)), 1.0)
        self.assertEqual(self.box_iou((0, 0, 10, 10), (20, 20, 30, 30)), 0.0)
        self.assertAlmostEqual(self.box_iou((0, 0, 10, 10), (5, 0, 15, 10)),
                               50.0 / 150.0)

    def test_wrists_go_to_the_overlapping_person(self):
        people = [{"id": 1, "bbox": (0, 0, 100, 200)},
                  {"id": 2, "bbox": (400, 0, 500, 200)}]
        poses = [{"bbox": (405, 5, 495, 195), "wrists": [(450, 100)]},
                 {"bbox": (2, 2, 98, 198), "wrists": [(50, 90)]}]
        wrist_map = self.associate_wrists(people, poses, min_iou=0.2)
        self.assertEqual(wrist_map[1], [(50, 90)])
        self.assertEqual(wrist_map[2], [(450, 100)])

    def test_one_skeleton_cannot_be_claimed_twice(self):
        people = [{"id": 1, "bbox": (0, 0, 100, 200)},
                  {"id": 2, "bbox": (10, 0, 110, 200)}]
        poses = [{"bbox": (0, 0, 100, 200), "wrists": [(50, 50)]}]
        wrist_map = self.associate_wrists(people, poses, min_iou=0.2)
        self.assertEqual(len(wrist_map), 1)

    def test_non_overlapping_skeleton_is_ignored(self):
        people = [{"id": 1, "bbox": (0, 0, 100, 200)}]
        poses = [{"bbox": (600, 600, 700, 800), "wrists": [(650, 700)]}]
        self.assertEqual(self.associate_wrists(people, poses, min_iou=0.2), {})


# ===========================================================================
# Feature 12 -- the combined engine, driven end to end from the tracker
# ===========================================================================

class TestCombinedPipeline(unittest.TestCase):
    """tracker -> risk engine, the way main.py actually calls them."""

    def setUp(self):
        self.zones = make_zones()
        self.tracker = make_tracker()
        self.engine = RiskEngine(config)

    def test_escalation_sequence(self):
        detection = [box_at(*PT_DANGER)]
        # Confirmed in the danger zone.
        for index in range(3):
            people = self.tracker.update(detection, self.zones, now=10.0 + index * 0.1)
        self.assertEqual(self.engine.evaluate_person(people[0], False)["risk"], 70)

        # ...for more than five seconds.
        people = self.tracker.update(detection, self.zones, now=20.0)
        self.assertEqual(self.engine.evaluate_person(people[0], False)["risk"], 90)

        # ...while the machine is running.
        verdict = self.engine.evaluate_person(people[0], True)
        self.assertEqual(verdict["risk"], 100)
        self.assertEqual(verdict["level"], "CRITICAL")
        self.assertEqual(len(verdict["reasons"]), 3)

    def test_tracking_disabled_still_classifies_zones(self):
        tracker = make_tracker(enabled=False)
        people = tracker.update([box_at(*PT_DANGER), box_at(*PT_SAFE)], self.zones)
        zones_found = {p["zone"] for p in people}
        self.assertEqual(zones_found, {config.ZONE_DANGER, config.ZONE_SAFE})
        # Per-person dwell timing is impossible without stable IDs.
        self.assertTrue(all(p["danger_duration"] == 0.0 for p in people))

    def test_no_people_is_a_normal_result(self):
        people = self.tracker.update([], self.zones, now=1.0)
        self.assertEqual(people, [])


# ===========================================================================
# Feature 15 -- event log transitions
# ===========================================================================

def fake_person(person_id=1, zone=config.ZONE_SAFE, duration=0.0, hand=False,
                hand_zone=None, zone_id=None, hand_zone_id=None, distance=None):
    """
    A person dict shaped exactly like the one tracker.update() returns, without
    needing a tracker, a model or a camera.
    """
    if hand and hand_zone is None:
        hand_zone = config.ZONE_DANGER
    return {
        "id": person_id,
        "person_id": format_person_id(person_id),
        "identity": make_identity(person_id),
        "zone": zone,
        "zone_id": zone_id if zone_id is not None else zone_id_for(zone),
        "danger_duration": duration,
        "hand_in_danger": hand or hand_zone == config.ZONE_DANGER,
        "hand_zone": hand_zone,
        "hand_zone_id": (hand_zone_id if hand_zone_id is not None
                         else zone_id_for(hand_zone)),
        "distance_to_danger": distance,
    }


class TestEventLog(unittest.TestCase):
    """Transition detection: one event per change, never one per frame."""

    def setUp(self):
        self.log = EventLog(
            max_events=config.MAX_STORED_EVENTS,
            danger_time_threshold=config.DANGER_TIME_THRESHOLD_SEC,
            critical_threshold=config.RISK_CRITICAL_THRESHOLD,
            source_type="video",
        )
        self.engine = RiskEngine(config)

    def _emit(self, people, machine_on=False, live_ids=None):
        """Run one frame; returns the full event records."""
        risks = {p["id"]: self.engine.evaluate_person(p, machine_on)
                 for p in people}
        return self.log.update(people, risks, machine_on, config,
                               live_ids=live_ids)

    def _frame(self, people, machine_on=False, live_ids=None):
        """Run one frame; returns just the event type names."""
        return [e["event_type"] for e in self._emit(people, machine_on, live_ids)]

    @staticmethod
    def _zones(events, event_type):
        """The zone tags carried by every event of one type."""
        return [e["zone_id"] for e in events if e["event_type"] == event_type]

    person = staticmethod(fake_person)

    # -- entering / leaving --------------------------------------------------

    def test_1_entering_danger_emits_danger_zone_entered(self):
        events = self._emit([self.person(zone=config.ZONE_DANGER)])
        self.assertIn(DANGER_ZONE_ENTERED, [e["event_type"] for e in events])
        self.assertEqual(self._zones(events, DANGER_ZONE_ENTERED), ["DANGER_1"])

    def test_2_staying_in_danger_does_not_repeat_the_event(self):
        # 100 frames of standing still must produce ONE entry event.
        self._frame([self.person(zone=config.ZONE_DANGER)], live_ids=[1])
        for _ in range(100):
            events = self._frame([self.person(zone=config.ZONE_DANGER)],
                                 live_ids=[1])
            self.assertNotIn(DANGER_ZONE_ENTERED, events)
        entries = [e for e in self.log.events
                   if e["event_type"] == DANGER_ZONE_ENTERED]
        self.assertEqual(len(entries), 1)

    def test_3_leaving_danger_emits_danger_zone_left(self):
        self._frame([self.person(zone=config.ZONE_DANGER)])
        events = self._emit([self.person(zone=config.ZONE_SAFE)])
        self.assertEqual(self._zones(events, DANGER_ZONE_LEFT), ["DANGER_1"])
        # ...and entering the safe zone is the generic event, with the tag on it.
        self.assertEqual(self._zones(events, ZONE_ENTERED), ["SAFE_1"])

    def test_a_warning_zone_logs_one_incident_instead_of_transitions(self):
        # Entering writes nothing at all...
        events = self._emit([self.person(zone=config.ZONE_WARNING)])
        self.assertEqual([e["event_type"] for e in events], [PERSON_DETECTED])
        # ...and the whole visit becomes ONE line when it ends.
        events = self._emit([self.person(zone=config.ZONE_DANGER)])
        types = [e["event_type"] for e in events]
        self.assertEqual(types.count(WARNING_ZONE_INCIDENT), 1)
        self.assertIn(DANGER_ZONE_ENTERED, types)

    def test_one_transition_produces_exactly_one_event(self):
        # Entering DANGER must NOT emit both ZONE_ENTERED and DANGER_ZONE_ENTERED.
        events = self._frame([self.person(zone=config.ZONE_DANGER)])
        self.assertEqual(events.count(DANGER_ZONE_ENTERED), 1)
        self.assertNotIn(ZONE_ENTERED, events)

    def test_re_entry_produces_a_second_enter_event(self):
        self._frame([self.person(zone=config.ZONE_DANGER)])
        self._frame([self.person(zone=config.ZONE_SAFE)])
        events = self._emit([self.person(zone=config.ZONE_DANGER)])
        self.assertEqual(self._zones(events, DANGER_ZONE_ENTERED), ["DANGER_1"])

    def test_moving_between_two_zones_of_the_same_type_is_a_real_move(self):
        # DANGER_1 -> DANGER_2 is an exit and an entry, not "no change".
        self._frame([self.person(zone=config.ZONE_DANGER, zone_id="DANGER_1")])
        events = self._emit([self.person(zone=config.ZONE_DANGER,
                                         zone_id="DANGER_2")])
        self.assertEqual(self._zones(events, DANGER_ZONE_LEFT), ["DANGER_1"])
        self.assertEqual(self._zones(events, DANGER_ZONE_ENTERED), ["DANGER_2"])

    def test_walking_off_the_zones_entirely_only_leaves(self):
        self._frame([self.person(zone=config.ZONE_SAFE)])
        events = self._emit([self.person(zone=config.ZONE_OUTSIDE)])
        self.assertEqual(self._zones(events, ZONE_LEFT), ["SAFE_1"])
        self.assertNotIn(ZONE_ENTERED, [e["event_type"] for e in events])

    # -- escalation ----------------------------------------------------------

    def test_4_dwell_exceeded_after_the_threshold(self):
        self._frame([self.person(zone=config.ZONE_DANGER)])
        events = self._frame([self.person(zone=config.ZONE_DANGER, duration=6.0)])
        self.assertIn(DANGER_DWELL_EXCEEDED, events)
        self.assertIn(CRITICAL_RISK, events)

    def test_dwell_fires_once_per_stay(self):
        self._frame([self.person(zone=config.ZONE_DANGER)])
        self._frame([self.person(zone=config.ZONE_DANGER, duration=5.2)])
        for duration in (6.0, 7.0, 8.0, 20.0):
            events = self._frame([self.person(zone=config.ZONE_DANGER,
                                              duration=duration)])
            self.assertNotIn(DANGER_DWELL_EXCEEDED, events)

    def test_dwell_does_not_fire_before_the_threshold(self):
        for duration in (0.0, 1.0, 4.0, 4.9):
            events = self._frame([self.person(zone=config.ZONE_DANGER,
                                              duration=duration)])
            self.assertNotIn(DANGER_DWELL_EXCEEDED, events)

    def test_dwell_carries_the_duration_and_the_score(self):
        self._frame([self.person(zone=config.ZONE_DANGER)])
        events = self._emit([self.person(zone=config.ZONE_DANGER, duration=5.4)])
        dwell = [e for e in events if e["event_type"] == DANGER_DWELL_EXCEEDED][0]
        self.assertEqual(dwell["danger_duration_sec"], 5.4)
        self.assertEqual(dwell["risk"], 90)
        self.assertEqual(dwell["zone_id"], "DANGER_1")

    def test_critical_risk_carries_the_engine_reasons(self):
        events = self._emit([self.person(zone=config.ZONE_DANGER)],
                            machine_on=True)
        critical = [e for e in events if e["event_type"] == CRITICAL_RISK][0]
        self.assertEqual(critical["risk"], 100)
        self.assertIn("Machine is ON", critical["reasons"])

    def test_critical_fires_once_per_escalation(self):
        self._frame([self.person(zone=config.ZONE_DANGER)], machine_on=True)
        events = self._frame([self.person(zone=config.ZONE_DANGER)],
                             machine_on=True)
        self.assertNotIn(CRITICAL_RISK, events)

    # -- hand / reach-in -----------------------------------------------------

    def test_5_hand_entering_danger_is_reported(self):
        events = self._emit([self.person(zone=config.ZONE_SAFE, hand=True)])
        self.assertEqual(self._zones(events, HAND_ENTERED_DANGER), ["DANGER_1"])
        # The BODY is still in the safe zone; the hand must not move it.
        hand_event = [e for e in events
                      if e["event_type"] == HAND_ENTERED_DANGER][0]
        self.assertEqual(hand_event["body_zone"], config.ZONE_SAFE)
        self.assertEqual(hand_event["hand_zone"], config.ZONE_DANGER)

    def test_hand_events_are_independent_of_the_body(self):
        events = self._emit([self.person(zone=config.ZONE_SAFE,
                                         hand_zone=config.ZONE_WARNING)])
        types = [e["event_type"] for e in events]
        self.assertIn(HAND_ENTERED_WARNING, types)
        self.assertEqual(self._zones(events, ZONE_ENTERED), ["SAFE_1"])

        events = self._emit([self.person(zone=config.ZONE_SAFE,
                                         hand_zone=config.ZONE_DANGER)])
        types = [e["event_type"] for e in events]
        self.assertIn(HAND_LEFT_WARNING, types)
        self.assertIn(HAND_ENTERED_DANGER, types)
        self.assertNotIn(DANGER_ZONE_ENTERED, types)

    def test_hand_leaving_danger_is_reported(self):
        self._frame([self.person(zone=config.ZONE_SAFE, hand=True)])
        events = self._emit([self.person(zone=config.ZONE_SAFE, hand_zone=None)])
        self.assertIn(HAND_LEFT_DANGER, [e["event_type"] for e in events])

    def test_hand_in_a_safe_zone_is_not_an_event(self):
        events = self._frame([self.person(zone=config.ZONE_SAFE,
                                          hand_zone=config.ZONE_SAFE)])
        for event_type in (HAND_ENTERED_DANGER, HAND_ENTERED_WARNING,
                           HAND_LEFT_DANGER, HAND_LEFT_WARNING):
            self.assertNotIn(event_type, events)

    # -- appearing / disappearing -------------------------------------------

    def test_person_detected_and_lost(self):
        self.assertIn(PERSON_DETECTED, self._frame([self.person()]))
        self.assertNotIn(PERSON_DETECTED, self._frame([self.person()]))
        self.assertIn(PERSON_LOST, self._frame([]))

    def test_lost_track_inside_danger_still_leaves_the_zone(self):
        self._frame([self.person(zone=config.ZONE_DANGER)])
        events = self._emit([])
        self.assertEqual(self._zones(events, DANGER_ZONE_LEFT), ["DANGER_1"])
        self.assertIn(PERSON_LOST, [e["event_type"] for e in events])

    def test_a_one_frame_detection_gap_is_not_a_lost_person(self):
        # THE REGRESSION THAT MADE THE OLD LOG UNUSABLE. The tracker keeps a person
        # for TRACK_MAX_MISSING_FRAMES; while it still lists them as live, a frame
        # where YOLO missed them is not "the worker left".
        person = self.person(zone=config.ZONE_WARNING)
        self._frame([person], live_ids=[1])
        for _ in range(5):
            self.assertEqual(self._frame([], live_ids=[1]), [])
        self.assertIn(PERSON_LOST, self._frame([], live_ids=[]))

    def test_person_lost_is_emitted_once(self):
        self._frame([self.person()], live_ids=[1])
        self.assertIn(PERSON_LOST, self._frame([], live_ids=[]))
        self.assertEqual(self._frame([], live_ids=[]), [])

    # -- risk / machine ------------------------------------------------------

    def test_risk_changed_fires_once_per_value(self):
        self._frame([self.person(zone=config.ZONE_DANGER)])            # risk 70
        first = self._frame([self.person(zone=config.ZONE_DANGER,
                                          duration=6.0)])              # -> 90
        self.assertIn(RISK_CHANGED, first)
        # Same risk on the next frame: nothing new to say.
        second = self._frame([self.person(zone=config.ZONE_DANGER,
                                           duration=6.5)])
        self.assertNotIn(RISK_CHANGED, second)

    def test_risk_changes_can_be_switched_off(self):
        log = EventLog(danger_time_threshold=config.DANGER_TIME_THRESHOLD_SEC,
                       critical_threshold=config.RISK_CRITICAL_THRESHOLD,
                       log_risk_changes=False)
        engine = RiskEngine(config)

        def frame(people):
            risks = {p["id"]: engine.evaluate_person(p, False) for p in people}
            return [e["event_type"] for e in log.update(people, risks, False, config)]

        frame([self.person(zone=config.ZONE_DANGER)])
        events = frame([self.person(zone=config.ZONE_DANGER, duration=6.0)])
        self.assertNotIn(RISK_CHANGED, events)
        # The state events themselves are untouched.
        self.assertIn(DANGER_DWELL_EXCEEDED, events)

    def test_machine_state_changes_are_one_event_type(self):
        self._frame([], machine_on=False)          # first frame records the state
        events = self._emit([], machine_on=True)
        self.assertEqual(events[0]["event_type"], MACHINE_STATE_CHANGED)
        self.assertIs(events[0]["machine_on"], True)

        events = self._emit([], machine_on=False)
        self.assertEqual(events[0]["event_type"], MACHINE_STATE_CHANGED)
        self.assertIs(events[0]["machine_on"], False)

    def test_the_machine_state_is_not_reported_every_frame(self):
        self._frame([], machine_on=True)
        for _ in range(10):
            self.assertEqual(self._frame([], machine_on=True), [])

    # -- shape / volume ------------------------------------------------------

    def test_11_event_contains_every_required_schema_field(self):
        self._frame([self.person(person_id=1, zone=config.ZONE_DANGER,
                                 distance=0.0)])
        record = [e for e in self.log.events
                  if e["event_type"] == DANGER_ZONE_ENTERED][0]

        for key in ("timestamp", "session_id", "event_type", "person_id",
                    "zone_id", "body_zone", "hand_zone", "risk", "machine_on",
                    "danger_duration_sec", "distance_to_danger_px", "reasons",
                    "source_type"):
            self.assertIn(key, record)

        self.assertEqual(record["person_id"], "P1")
        self.assertEqual(record["zone_id"], "DANGER_1")
        self.assertEqual(record["body_zone"], "DANGER")
        self.assertIsNone(record["hand_zone"])
        self.assertEqual(record["risk"], 70)
        self.assertIs(record["machine_on"], False)
        self.assertEqual(record["danger_duration_sec"], 0.0)
        self.assertEqual(record["distance_to_danger_px"], 0.0)
        self.assertEqual(record["reasons"], ["Worker entered Danger Zone"])
        self.assertEqual(record["source_type"], "video")

    def test_person_ids_are_the_p_labels_not_raw_numbers(self):
        self._frame([self.person(person_id=7, zone=config.ZONE_SAFE)])
        self.assertEqual(self.log.events[0]["person_id"], "P7")

    def test_12_source_type_is_recorded_for_every_kind_of_source(self):
        for source_type in ("webcam", "ip", "cctv", "video"):
            log = EventLog(danger_time_threshold=config.DANGER_TIME_THRESHOLD_SEC,
                           source_type=source_type)
            record = log.emit(PERSON_DETECTED, person=fake_person(1))
            self.assertEqual(record["source_type"], source_type)

    def test_fields_that_do_not_apply_are_null_not_invented(self):
        record = self.log.emit(PERSON_LOST, person=fake_person(1))
        self.assertIsNone(record["risk"])
        self.assertIsNone(record["machine_on"])
        self.assertIsNone(record["danger_duration_sec"])
        self.assertIsNone(record["distance_to_danger_px"])
        self.assertIsNone(record["zone_id"])

    def test_event_ids_are_sequential_and_unique(self):
        self._frame([self.person(zone=config.ZONE_WARNING)])
        self._frame([self.person(zone=config.ZONE_DANGER)])
        ids = [e["event_id"] for e in self.log.events]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), len(ids))
        self.assertEqual(ids[0], "evt_000001")

    def test_events_without_a_person_have_a_null_person_id(self):
        self._frame([], machine_on=False)
        record = self._emit([], machine_on=True)[0]
        self.assertEqual(record["event_type"], MACHINE_STATE_CHANGED)
        self.assertIsNone(record["person_id"])
        self.assertIsNone(record["zone_id"])

    def test_no_events_at_all_when_nothing_changes(self):
        person = self.person(zone=config.ZONE_WARNING)
        self._frame([person])
        for _ in range(20):
            self.assertEqual(self._frame([person]), [],
                             "a steady state must not write any events")

    def test_ten_seconds_in_one_zone_is_almost_nothing(self):
        # 100 frames of standing in a WARNING zone: the person is reported, and then
        # there is silence until they leave.
        person = self.person(zone=config.ZONE_WARNING)
        for _ in range(100):
            self._frame([person], live_ids=[1])
        types = [e["event_type"] for e in self.log.events]
        self.assertEqual(types, [PERSON_DETECTED])

    def test_log_is_bounded(self):
        log = EventLog(max_events=5)
        for index in range(20):
            log.emit(CRITICAL_RISK, person=fake_person(index))
        self.assertEqual(len(log.events), 5)
        self.assertEqual(log.total_emitted, 20)


# ===========================================================================
# WARNING-ZONE INCIDENTS
#
# A warning-zone visit is NOT logged as transitions. It produces exactly ONE line,
# WARNING_ZONE_INCIDENT, written when the visit ends -- with the entry photo, the
# duration and whether the buzzer actually sounded.
# ===========================================================================

class TestWarningIncidents(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="zonex-incidents-")
        self.engine = RiskEngine(config)
        self.log = EventLog(
            danger_time_threshold=config.DANGER_TIME_THRESHOLD_SEC,
            critical_threshold=config.RISK_CRITICAL_THRESHOLD,
            warning_dwell_threshold=config.WARNING_DWELL_THRESHOLD_SEC,
            source_type="webcam",
            log_root=self.root,
            incident_dir=os.path.join(self.root, "incidents"),
        )
        # A tiny frame stands in for the camera image.
        self.frame = np.full((48, 64, 3), 120, dtype=np.uint8)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _emit(self, people, machine_on=False, live_ids=None):
        risks = {p["id"]: self.engine.evaluate_person(p, machine_on)
                 for p in people}
        return self.log.update(people, risks, machine_on, config,
                               live_ids=live_ids, frame=self.frame)

    def _frame(self, people, **kwargs):
        return [e["event_type"] for e in self._emit(people, **kwargs)]

    def _of_type(self, event_type):
        return [e for e in self.log.events if e["event_type"] == event_type]

    def _incidents(self):
        return self._of_type(WARNING_ZONE_INCIDENT)

    def _images(self):
        """Every incident image written, whatever day-folder it landed in."""
        found = []
        for folder, _, files in os.walk(os.path.join(self.root, "incidents")):
            found.extend(os.path.join(folder, name) for name in sorted(files))
        return found

    @staticmethod
    def warning_person(person_id=1, zone_id="WARNING_1", distance=125.4):
        return fake_person(person_id, zone=config.ZONE_WARNING,
                           zone_id=zone_id, distance=distance)

    def _age_incident(self, track_id, zone_id, seconds):
        incident = self.log.incidents.get(track_id, zone_id)
        self.assertIsNotNone(incident, "no incident open for that person and zone")
        incident.entry_time = incident.entry_time - timedelta(seconds=seconds)
        return incident

    # -- entering logs NOTHING ----------------------------------------------

    def test_entering_a_warning_zone_writes_no_event_at_all(self):
        events = self._frame([self.warning_person()])
        # PERSON_DETECTED still happens -- a person appearing is not a zone event.
        self.assertEqual(events, [PERSON_DETECTED])
        self.assertEqual(self._incidents(), [],
                         "the incident must not be written until the visit ends")
        self.assertEqual(len(self.log.incidents.open_incidents), 1)

    def test_staying_inside_writes_nothing(self):
        person = self.warning_person()
        self._frame([person], live_ids=[1])
        for _ in range(100):                      # ~10 s at 10 FPS
            self.assertEqual(self._frame([person], live_ids=[1]), [])
        self.assertEqual(self._incidents(), [])

    def test_no_transition_or_risk_events_are_produced_for_a_visit(self):
        # The whole point: a visit is one line, not a stream of state changes.
        self._frame([fake_person(1, zone=config.ZONE_SAFE)])
        types = []
        types += self._frame([self.warning_person()], live_ids=[1])
        for _ in range(20):
            types += self._frame([self.warning_person()], live_ids=[1])
        types += self._frame([fake_person(1, zone=config.ZONE_SAFE)], live_ids=[1])

        self.assertNotIn(RISK_CHANGED, types,
                         "a warning visit must not log its own risk changes")
        self.assertEqual(types.count(WARNING_ZONE_INCIDENT), 1)
        # Leaving the SAFE zone on the way in is still a plain zone transition.
        self.assertIn(ZONE_LEFT, types)

    # -- leaving writes exactly one complete line ---------------------------

    def test_leaving_writes_exactly_one_incident(self):
        self._frame([self.warning_person()])
        events = self._frame([fake_person(1, zone=config.ZONE_SAFE)])
        self.assertEqual(events.count(WARNING_ZONE_INCIDENT), 1)
        self.assertEqual(len(self._incidents()), 1)
        self.assertEqual(self.log.incidents.open_incidents, [])

    def test_the_incident_has_exactly_the_agreed_nine_fields(self):
        self._frame([self.warning_person()])
        self._frame([fake_person(1, zone=config.ZONE_SAFE)])
        record = self._incidents()[0]

        self.assertEqual(sorted(record.keys()), sorted([
            "event_type", "person_id", "zone_id", "entry_time", "exit_time",
            "duration_sec", "alarm_triggered", "source_type", "incident_image"]))

        self.assertEqual(record["event_type"], "WARNING_ZONE_INCIDENT")
        self.assertEqual(record["person_id"], "P1")
        self.assertEqual(record["zone_id"], "WARNING_1")
        self.assertIsInstance(record["entry_time"], str)
        self.assertIsInstance(record["exit_time"], str)
        self.assertIsInstance(record["duration_sec"], float)
        self.assertIs(record["alarm_triggered"], False)
        self.assertEqual(record["source_type"], "webcam")
        self.assertIsInstance(record["incident_image"], str)

    def test_duration_is_exit_minus_entry(self):
        self._frame([self.warning_person()])
        self._age_incident(1, "WARNING_1", 8.314)
        self._frame([fake_person(1, zone=config.ZONE_SAFE)])
        record = self._incidents()[0]

        self.assertAlmostEqual(record["duration_sec"], 8.314, places=1)
        entered = datetime.fromisoformat(record["entry_time"])
        left = datetime.fromisoformat(record["exit_time"])
        self.assertAlmostEqual((left - entered).total_seconds(),
                               record["duration_sec"], places=2)

    def test_person_ids_stay_p1_p2(self):
        self._frame([self.warning_person(7)])
        self._frame([fake_person(7, zone=config.ZONE_SAFE)])
        self.assertEqual(self._incidents()[0]["person_id"], "P7")

    # -- the entry photo -----------------------------------------------------

    def test_the_entry_frame_is_saved_exactly_once(self):
        person = self.warning_person()
        for _ in range(50):
            self._frame([person], live_ids=[1])
        self.assertEqual(len(self._images()), 1,
                         "one photo per incident, not per frame")

    def test_the_image_path_matches_the_agreed_layout(self):
        self._frame([self.warning_person()])
        self._frame([fake_person(1, zone=config.ZONE_SAFE)])
        path = self._incidents()[0]["incident_image"]

        # incidents/<YYYY-MM-DD>/P1_WARNING_1_<HHMMSS>.jpg
        self.assertRegex(path, r"^incidents/\d{4}-\d{2}-\d{2}/"
                               r"P1_WARNING_1_\d{6}\.jpg$")
        self.assertNotIn("\\", path, "a backend cannot use a Windows path")
        self.assertFalse(os.path.isabs(path))

    def test_the_logged_path_resolves_to_the_saved_image(self):
        self._frame([self.warning_person()])
        self._frame([fake_person(1, zone=config.ZONE_SAFE)])
        path = self._incidents()[0]["incident_image"]

        # The path is relative to the log root, which is where events.jsonl lives.
        on_disk = os.path.join(self.root, path)
        self.assertTrue(os.path.isfile(on_disk), on_disk)
        # ...and it is the frame we handed in, with no overlays drawn on it.
        self.assertTrue(np.array_equal(cv2.imread(on_disk)[0, 0], self.frame[0, 0]))

    def test_images_can_be_switched_off(self):
        log = EventLog(source_type="webcam", save_incident_images=False,
                       log_root=self.root,
                       incident_dir=os.path.join(self.root, "off"))
        engine = RiskEngine(config)
        person = self.warning_person()
        log.update([person], {1: engine.evaluate_person(person, False)},
                   False, config, frame=self.frame)
        log.update([fake_person(1, zone=config.ZONE_SAFE)],
                   {1: engine.evaluate_person(fake_person(1), False)},
                   False, config, frame=self.frame)
        record = [e for e in log.events
                  if e["event_type"] == WARNING_ZONE_INCIDENT][0]
        self.assertIsNone(record["incident_image"])
        self.assertFalse(os.path.isdir(os.path.join(self.root, "off")))

    def test_a_missing_frame_still_produces_the_incident(self):
        person = self.warning_person()
        self.log.update([person], {1: self.engine.evaluate_person(person, False)},
                        False, config, frame=None)
        self.log.update([fake_person(1, zone=config.ZONE_SAFE)], {}, False, config,
                        frame=None)
        record = self._incidents()[0]
        self.assertIsNone(record["incident_image"])
        self.assertIsNotNone(record["duration_sec"])

    # -- alarm_triggered -----------------------------------------------------

    def test_alarm_triggered_is_false_for_a_short_visit(self):
        self._frame([self.warning_person()])
        self._frame([fake_person(1, zone=config.ZONE_SAFE)])
        self.assertIs(self._incidents()[0]["alarm_triggered"], False)

    def test_alarm_triggered_is_false_until_something_sets_it(self):
        # Passing the threshold alone is not enough: main.py sets the flag only when
        # the buzzer really sounds. See TestWarningDwellAlarm for the wired-up case.
        self._frame([self.warning_person()])
        self._age_incident(1, "WARNING_1", 30.0)
        self._frame([self.warning_person()], live_ids=[1])
        self._frame([fake_person(1, zone=config.ZONE_SAFE)], live_ids=[1])
        self.assertIs(self._incidents()[0]["alarm_triggered"], False)

    def test_alarm_triggered_is_reported_when_it_was_set(self):
        self._frame([self.warning_person()])
        self.log.incidents.get(1, "WARNING_1").alarm_triggered = True
        self._frame([fake_person(1, zone=config.ZONE_SAFE)])
        self.assertIs(self._incidents()[0]["alarm_triggered"], True)

    # -- repeat visits -------------------------------------------------------

    def test_a_second_visit_is_a_second_incident_with_its_own_photo(self):
        for _ in range(2):
            self._frame([self.warning_person()], live_ids=[1])
            self._frame([fake_person(1, zone=config.ZONE_SAFE)], live_ids=[1])

        records = self._incidents()
        self.assertEqual(len(records), 2)
        self.assertEqual(len(self._images()), 2)
        self.assertNotEqual(records[0]["incident_image"],
                            records[1]["incident_image"])

    # -- every way out -------------------------------------------------------

    def _assert_one_incident_after(self, next_person):
        self._frame([self.warning_person()])
        self._frame([next_person])
        records = self._incidents()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["zone_id"], "WARNING_1")
        self.assertIsNotNone(records[0]["exit_time"])
        self.assertEqual(self.log.incidents.open_incidents, [])
        return records[0]

    def test_warning_to_danger_closes_the_incident(self):
        self._assert_one_incident_after(fake_person(1, zone=config.ZONE_DANGER))
        # ...and the danger events still happen, untouched.
        self.assertTrue(self._of_type(DANGER_ZONE_ENTERED))

    def test_warning_to_safe_closes_the_incident(self):
        self._assert_one_incident_after(fake_person(1, zone=config.ZONE_SAFE))
        self.assertTrue(self._of_type(ZONE_ENTERED))

    def test_warning_to_outside_closes_the_incident(self):
        self._assert_one_incident_after(fake_person(1, zone=config.ZONE_OUTSIDE))

    def test_warning_to_another_warning_zone_is_two_incidents(self):
        self._frame([self.warning_person(zone_id="WARNING_1")])
        events = self._frame([self.warning_person(zone_id="WARNING_2")])

        # The first visit is written; the second is now open and unwritten.
        self.assertEqual(events.count(WARNING_ZONE_INCIDENT), 1)
        self.assertEqual(self._incidents()[0]["zone_id"], "WARNING_1")
        self.assertEqual([i.zone_id for i in self.log.incidents.open_incidents],
                         ["WARNING_2"])

        self._frame([fake_person(1, zone=config.ZONE_SAFE)])
        self.assertEqual([r["zone_id"] for r in self._incidents()],
                         ["WARNING_1", "WARNING_2"])

    def test_two_people_in_one_zone_are_two_incidents(self):
        self._frame([self.warning_person(1), self.warning_person(2)])
        self.assertEqual(len(self.log.incidents.open_incidents), 2)
        self._frame([fake_person(1, zone=config.ZONE_SAFE),
                     fake_person(2, zone=config.ZONE_SAFE)])
        self.assertEqual([r["person_id"] for r in self._incidents()], ["P1", "P2"])

    # -- losing the track ----------------------------------------------------

    def test_a_brief_detection_gap_does_not_close_the_incident(self):
        self._frame([self.warning_person()], live_ids=[1])
        for _ in range(6):
            self.assertEqual(self._frame([], live_ids=[1]), [])
        self.assertEqual(len(self.log.incidents.open_incidents), 1)
        self.assertEqual(self._incidents(), [])

    def test_a_confirmed_lost_track_writes_the_incident(self):
        self._frame([self.warning_person()], live_ids=[1])
        events = self._frame([], live_ids=[])      # the tracker has given up

        self.assertIn(WARNING_ZONE_INCIDENT, events)
        record = self._incidents()[0]
        self.assertIsNotNone(record["exit_time"])
        self.assertIsNotNone(record["duration_sec"])
        self.assertEqual(self.log.incidents.open_incidents, [])
        # PERSON_LOST is still reported: that is about the person, not the zone.
        self.assertIn(PERSON_LOST, events)

    def test_redrawing_the_zones_still_writes_an_open_visit(self):
        self._frame([self.warning_person()], live_ids=[1])
        written = self.log.reset()
        self.assertEqual([e["event_type"] for e in written],
                         [WARNING_ZONE_INCIDENT])
        self.assertEqual(self.log.incidents.open_incidents, [])

    # -- source / json -------------------------------------------------------

    def test_source_type_is_recorded_for_every_kind_of_source(self):
        for source_type in ("webcam", "ip", "cctv", "video"):
            log = EventLog(source_type=source_type, log_root=self.root,
                           incident_dir=os.path.join(self.root, source_type))
            engine = RiskEngine(config)
            person = self.warning_person()
            log.update([person], {1: engine.evaluate_person(person, False)},
                       False, config, frame=self.frame)
            log.update([fake_person(1, zone=config.ZONE_SAFE)], {}, False, config)
            record = [e for e in log.events
                      if e["event_type"] == WARNING_ZONE_INCIDENT][0]
            self.assertEqual(record["source_type"], source_type)

    def test_the_incident_is_json_safe(self):
        self._frame([self.warning_person()])
        self._frame([fake_person(1, zone=config.ZONE_SAFE)])
        record = self._incidents()[0]
        _assert_json_primitives(self, json.loads(json.dumps(record)))

    def test_other_events_do_not_carry_incident_fields(self):
        # The nine incident fields belong to WARNING_ZONE_INCIDENT alone.
        self._frame([fake_person(1, zone=config.ZONE_DANGER)])
        record = self._of_type(DANGER_ZONE_ENTERED)[0]
        for key in ("entry_time", "exit_time", "duration_sec",
                    "alarm_triggered", "incident_image"):
            self.assertNotIn(key, record)


class TestIncidentManager(unittest.TestCase):
    """The state machine on its own: no events, no risk engine, no camera."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="zonex-manager-")
        self.manager = IncidentManager(
            incident_dir=os.path.join(self.root, "incidents"),
            relative_to=self.root,
            dwell_threshold=5.0)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_opening_twice_returns_the_same_incident(self):
        person = fake_person(1, zone=config.ZONE_WARNING, zone_id="WARNING_1")
        first = self.manager.open_incident(person, "WARNING_1", "WARNING")
        second = self.manager.open_incident(person, "WARNING_1", "WARNING")
        self.assertIs(first, second, "a second incident was created for one visit")
        self.assertEqual(len(self.manager.open_incidents), 1)

    def test_closing_an_unknown_incident_is_harmless(self):
        self.assertIsNone(self.manager.close_incident(99, "WARNING_9"))

    def test_dwell_violations_only_lists_the_ones_past_the_threshold(self):
        old = fake_person(1, zone=config.ZONE_WARNING)
        fresh = fake_person(2, zone=config.ZONE_WARNING)
        now = datetime(2026, 8, 11, 16, 0, 10)
        self.manager.open_incident(old, "WARNING_1", "WARNING",
                                   now=now - timedelta(seconds=6))
        self.manager.open_incident(fresh, "WARNING_1", "WARNING",
                                   now=now - timedelta(seconds=1))

        violations = self.manager.dwell_violations(now=now)
        self.assertEqual([i.person_id for i in violations], ["P1"])

    def test_close_all_for_one_person(self):
        person = fake_person(1, zone=config.ZONE_WARNING)
        self.manager.open_incident(person, "WARNING_1", "WARNING")
        self.manager.open_incident(person, "WARNING_2", "WARNING")
        self.manager.open_incident(fake_person(2), "WARNING_1", "WARNING")

        closed = self.manager.close_all_for(1)
        self.assertEqual(len(closed), 2)
        self.assertEqual([i.person_id for i in self.manager.open_incidents], ["P2"])

    def test_take_open_hands_over_and_forgets(self):
        person = fake_person(1, zone=config.ZONE_WARNING)
        self.manager.open_incident(person, "WARNING_1", "WARNING")
        taken = self.manager.take_open()
        self.assertEqual(len(taken), 1)
        self.assertIsNone(taken[0].exit_time, "take_open must not stamp an exit")
        self.assertEqual(self.manager.open_incidents, [])

    def test_the_event_record_is_the_agreed_shape(self):
        incident = WarningIncident("P1", 1, "WARNING_1", "WARNING",
                                   datetime(2026, 8, 11, 15, 32, 10, 421000))
        incident.exit_time = datetime(2026, 8, 11, 15, 32, 18, 735000)
        incident.alarm_triggered = True
        incident.incident_image = "incidents/2026-08-11/P1_WARNING_1_153210.jpg"

        self.assertEqual(incident.as_event("webcam"), {
            "event_type": "WARNING_ZONE_INCIDENT",
            "person_id": "P1",
            "zone_id": "WARNING_1",
            "entry_time": "2026-08-11T15:32:10.421",
            "exit_time": "2026-08-11T15:32:18.735",
            "duration_sec": 8.314,
            "alarm_triggered": True,
            "source_type": "webcam",
            "incident_image": "incidents/2026-08-11/P1_WARNING_1_153210.jpg",
        })

    def test_elapsed_tracks_an_open_visit(self):
        incident = WarningIncident("P1", 1, "WARNING_1", "WARNING",
                                   datetime(2026, 8, 11, 16, 0, 0))
        self.assertTrue(incident.is_open)
        self.assertAlmostEqual(
            incident.elapsed(now=datetime(2026, 8, 11, 16, 0, 8)), 8.0)
        incident.exit_time = datetime(2026, 8, 11, 16, 0, 8, 500000)
        self.assertFalse(incident.is_open)
        self.assertEqual(incident.as_event()["duration_sec"], 8.5)

    def test_images_go_in_a_day_folder(self):
        person = fake_person(1, zone=config.ZONE_WARNING)
        incident = self.manager.open_incident(
            person, "WARNING_1", "WARNING",
            frame=np.zeros((10, 10, 3), dtype=np.uint8),
            now=datetime(2026, 8, 11, 15, 32, 10))
        self.assertEqual(incident.incident_image,
                         "incidents/2026-08-11/P1_WARNING_1_153210.jpg")
        self.assertTrue(os.path.isfile(
            os.path.join(self.root, incident.incident_image)))

    def test_filenames_are_windows_safe(self):
        person = fake_person(1, zone=config.ZONE_WARNING)
        person["person_id"] = "P1:bad*name?"
        incident = self.manager.open_incident(
            person, "WARNING/1", "WARNING",
            frame=np.zeros((10, 10, 3), dtype=np.uint8))
        self.assertIsNotNone(incident.incident_image)
        name = os.path.basename(incident.incident_image)
        for character in '<>:"\\|?*':
            self.assertNotIn(character, name)

    def test_a_second_photo_in_the_same_second_never_overwrites(self):
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        person = fake_person(1, zone=config.ZONE_WARNING)
        moment = datetime(2026, 8, 11, 16, 3, 25)
        first = self.manager.open_incident(person, "WARNING_1", "WARNING",
                                          frame=frame, now=moment)
        self.manager.close_incident(1, "WARNING_1")
        second = self.manager.open_incident(person, "WARNING_1", "WARNING",
                                           frame=frame, now=moment)
        self.assertNotEqual(first.incident_image, second.incident_image)
        for incident in (first, second):
            self.assertTrue(os.path.isfile(
                os.path.join(self.root, incident.incident_image)))

    def test_an_unwritable_directory_does_not_stop_monitoring(self):
        blocked = os.path.join(self.root, "blocked")
        with open(blocked, "w", encoding="utf-8") as handle:
            handle.write("not a directory")
        manager = IncidentManager(incident_dir=os.path.join(blocked, "incidents"))
        incident = manager.open_incident(
            fake_person(1, zone=config.ZONE_WARNING), "WARNING_1", "WARNING",
            frame=np.zeros((10, 10, 3), dtype=np.uint8))
        self.assertIsNotNone(incident.entry_time)
        self.assertIsNone(incident.incident_image)

# ===========================================================================
# ALARM EVENTS -- one line per transition, never one per beep
# ===========================================================================

class TestAlarmEvents(unittest.TestCase):

    def setUp(self):
        self.log = EventLog(
            danger_time_threshold=config.DANGER_TIME_THRESHOLD_SEC,
            critical_threshold=config.RISK_CRITICAL_THRESHOLD,
            source_type="webcam",
        )
        self.engine = RiskEngine(config)

    def _alarm(self, active, offenders=()):
        risks = {p["id"]: self.engine.evaluate_person(p, False)
                 for p in offenders}
        return self.log.update_alarm(active, offenders, risks, machine_on=False,
                                     cfg=config)

    def test_a_reach_in_alarm_names_the_zone_the_hand_is_in(self):
        # The feet are in SAFE_1, which is not what made this critical.
        person = fake_person(1, zone=config.ZONE_SAFE, hand=True)
        record = self._alarm(True, [person])[0]
        self.assertEqual(record["zone_id"], "DANGER_1")
        self.assertEqual(record["body_zone"], config.ZONE_SAFE)
        self.assertEqual(record["hand_zone"], config.ZONE_DANGER)

    def test_a_body_alarm_names_the_zone_the_body_is_in(self):
        person = fake_person(1, zone=config.ZONE_DANGER)
        record = self._alarm(True, [person])[0]
        self.assertEqual(record["zone_id"], "DANGER_1")

    def test_alarm_started_is_written_once(self):
        person = fake_person(1, zone=config.ZONE_DANGER)
        events = self._alarm(True, [person])
        self.assertEqual([e["event_type"] for e in events], [ALARM_STARTED])
        # Still sounding on the next 50 frames: nothing more is written.
        for _ in range(50):
            self.assertEqual(self._alarm(True, [person]), [])

    def test_alarm_stopped_is_written_once(self):
        person = fake_person(1, zone=config.ZONE_DANGER)
        self._alarm(True, [person])
        events = self._alarm(False, [])
        self.assertEqual([e["event_type"] for e in events], [ALARM_STOPPED])
        for _ in range(50):
            self.assertEqual(self._alarm(False, []), [])

    def test_alarm_started_carries_the_offender_context(self):
        person = fake_person(1, zone=config.ZONE_DANGER, duration=2.5,
                             distance=0.0)
        record = self._alarm(True, [person])[0]
        self.assertEqual(record["person_id"], "P1")
        self.assertEqual(record["zone_id"], "DANGER_1")
        self.assertEqual(record["body_zone"], "DANGER")
        self.assertEqual(record["risk"], 70)
        self.assertEqual(record["danger_duration_sec"], 2.5)
        self.assertEqual(record["source_type"], "webcam")

    def test_alarm_started_names_every_contributing_person(self):
        a = fake_person(1, zone=config.ZONE_DANGER)
        b = fake_person(2, zone=config.ZONE_SAFE, hand=True)
        record = self._alarm(True, [a, b])[0]
        joined = " ".join(record["reasons"])
        self.assertIn("P1", joined)
        self.assertIn("P2", joined)
        self.assertIn("hand", joined.lower())

    def test_the_worst_offender_is_the_one_reported(self):
        mild = fake_person(1, zone=config.ZONE_DANGER)                  # 70
        severe = fake_person(2, zone=config.ZONE_SAFE, hand=True)       # 90
        record = self._alarm(True, [mild, severe])[0]
        self.assertEqual(record["person_id"], "P2")
        self.assertEqual(record["risk"], 90)

    def test_alarm_stopped_has_no_person_but_explains_itself(self):
        self._alarm(True, [fake_person(1, zone=config.ZONE_DANGER)])
        record = self._alarm(False, [])[0]
        self.assertIsNone(record["person_id"])
        self.assertTrue(record["reasons"])

    def test_alarm_events_carry_the_full_schema(self):
        record = self._alarm(True, [fake_person(1, zone=config.ZONE_DANGER)])[0]
        for key in ("timestamp", "session_id", "event_type", "person_id",
                    "zone_id", "body_zone", "hand_zone", "risk", "machine_on",
                    "danger_duration_sec", "source_type", "reasons"):
            self.assertIn(key, record)

    def test_resetting_person_state_does_not_forget_the_alarm(self):
        # Redrawing the zones clears per-person state, but a sounding alarm must
        # still be able to report that it stopped.
        self._alarm(True, [fake_person(1, zone=config.ZONE_DANGER)])
        self.log.reset()
        events = self._alarm(False, [])
        self.assertEqual([e["event_type"] for e in events], [ALARM_STOPPED])


# ===========================================================================
# THE ALARM ITSELF -- one thread, driven by the confirmed critical condition
# ===========================================================================

class TestAlarm(unittest.TestCase):
    """
    Uses a short, quiet configuration. On Windows this really does beep, briefly;
    off Windows it falls back to the terminal bell. Either way the state machine is
    what is under test.
    """

    def _alarm(self, **kwargs):
        kwargs.setdefault("frequency", 500)
        kwargs.setdefault("beep_ms", 20)
        kwargs.setdefault("gap_ms", 5)
        kwargs.setdefault("beeps_per_burst", 2)
        kwargs.setdefault("pause_ms", 10)
        alarm = Alarm(**kwargs)
        self.addCleanup(alarm.stop_alarm)
        return alarm

    def test_it_starts_and_stops(self):
        alarm = self._alarm()
        self.assertFalse(alarm.is_alarm_active())
        self.assertTrue(alarm.start_alarm())
        self.assertTrue(alarm.is_alarm_active())
        self.assertTrue(alarm.stop_alarm())
        self.assertFalse(alarm.is_alarm_active())

    def test_10_starting_repeatedly_does_not_create_more_threads(self):
        # main.py calls this every processed frame; it must be idempotent.
        alarm = self._alarm()
        before = threading.active_count()
        self.assertTrue(alarm.start_alarm())
        for _ in range(200):
            self.assertFalse(alarm.start_alarm(),
                             "start_alarm() started a second alarm")
        self.assertLessEqual(threading.active_count(), before + 1,
                             "more than one alarm thread is running")
        alarm.stop_alarm()

    def test_stopping_when_already_silent_is_harmless(self):
        alarm = self._alarm()
        self.assertFalse(alarm.stop_alarm())
        for _ in range(10):
            self.assertFalse(alarm.stop_alarm())

    def test_the_thread_goes_away_when_stopped(self):
        alarm = self._alarm()
        alarm.start_alarm()
        time.sleep(0.05)
        alarm.stop_alarm()
        time.sleep(0.05)
        names = [t.name for t in threading.enumerate()]
        self.assertNotIn("alarm", names)

    def test_it_can_be_restarted(self):
        alarm = self._alarm()
        for _ in range(3):
            self.assertTrue(alarm.start_alarm())
            self.assertTrue(alarm.is_alarm_active())
            self.assertTrue(alarm.stop_alarm())
            self.assertFalse(alarm.is_alarm_active())

    def test_a_non_repeating_alarm_finishes_on_its_own(self):
        alarm = self._alarm(repeat=False)
        alarm.start_alarm()
        deadline = time.time() + 5.0
        while alarm.is_alarm_active() and time.time() < deadline:
            time.sleep(0.02)
        self.assertFalse(alarm.is_alarm_active(),
                         "a one-shot alarm never cleared its own state")

    def test_disabled_alarm_never_starts(self):
        alarm = self._alarm(enabled=False)
        self.assertFalse(alarm.start_alarm())
        self.assertFalse(alarm.is_alarm_active())

    def test_frequency_is_clamped_to_what_winsound_accepts(self):
        self.assertEqual(Alarm(frequency=1).frequency, 37)
        self.assertEqual(Alarm(frequency=99999).frequency, 32767)

    def test_config_is_read_without_touching_the_speaker(self):
        alarm = Alarm.from_config(config)
        self.assertEqual(alarm.enabled, config.ENABLE_PC_ALARM)
        self.assertEqual(alarm.frequency, config.ALARM_BEEP_FREQUENCY)
        self.assertEqual(alarm.beep_ms, config.ALARM_BEEP_DURATION_MS)
        self.assertEqual(alarm.beeps_per_burst, config.ALARM_BEEPS_PER_BURST)
        self.assertEqual(alarm.repeat, config.ALARM_REPEAT)
        self.assertFalse(alarm.is_alarm_active())


class TestWarningDwellAlarm(unittest.TestCase):
    """
    The 5-second warning-dwell alarm, driven through the REAL ZoneXApp methods
    (_alarm_offenders / _update_alarm), the real EventLog + IncidentManager and the
    real Alarm class. No camera, no YOLO, no window.

    The beeps are configured tiny so the suite stays quick.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="zonex-dwell-")
        self.frame = np.full((48, 64, 3), 90, dtype=np.uint8)

        self.app = main.ZoneXApp(config)
        # A real Alarm, just quiet and short.
        self.app.alarm = Alarm(frequency=500, beep_ms=20, gap_ms=5,
                               beeps_per_burst=1, pause_ms=10)
        self.addCleanup(self.app.alarm.stop_alarm)
        # A real EventLog, writing its incidents into the temp folder.
        self.app.events = EventLog(
            source_type="webcam",
            danger_time_threshold=config.DANGER_TIME_THRESHOLD_SEC,
            critical_threshold=config.RISK_CRITICAL_THRESHOLD,
            warning_dwell_threshold=config.WARNING_DWELL_THRESHOLD_SEC,
            incident_dir=os.path.join(self.root, "incidents"),
        )

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _tick(self, people, machine_on=False):
        """One processed frame through the real app, minus YOLO and drawing."""
        self.app.people = people
        self.app.machine_on = machine_on
        self.app.risks = {p["id"]: self.app.risk_engine.evaluate_person(p, machine_on)
                          for p in people}
        self.app.events.update(people, self.app.risks, machine_on, config,
                               live_ids=[p["id"] for p in people],
                               frame=self.frame)
        self.app._update_alarm()
        return self.app.alarm.is_alarm_active()

    def _age_incident(self, track_id, zone_id, seconds):
        """Pretend the visit started `seconds` ago, so no test has to wait."""
        incident = self.app.events.incidents.get(track_id, zone_id)
        self.assertIsNotNone(incident, "no incident open for that person and zone")
        incident.entry_time = incident.entry_time - timedelta(seconds=seconds)
        return incident

    @staticmethod
    def _warning(person_id, zone_id="WARNING_1"):
        return fake_person(person_id, zone=config.ZONE_WARNING, zone_id=zone_id)

    def _events_of(self, event_type):
        return [e for e in self.app.events.events if e["event_type"] == event_type]

    # -- 9 -------------------------------------------------------------------

    def test_9_warning_dwell_past_the_threshold_starts_the_alarm(self):
        # Entering is not an alarm.
        self.assertFalse(self._tick([self._warning(1)]),
                         "the buzzer must not sound just for entering WARNING")

        # Still inside, but under the threshold.
        self._age_incident(1, "WARNING_1", config.WARNING_DWELL_THRESHOLD_SEC - 1.0)
        self.assertFalse(self._tick([self._warning(1)]))

        # Past the threshold: the existing alarm starts.
        self._age_incident(1, "WARNING_1", 2.0)
        self.assertTrue(self._tick([self._warning(1)]),
                        "the warning dwell did not raise the alarm")
        # No dwell EVENT exists any more -- the visit records it instead.
        incident = self.app.events.incidents.get(1, "WARNING_1")
        self.assertTrue(incident.alarm_triggered,
                        "the incident must remember that the buzzer sounded")

    def test_the_alarm_is_not_restarted_every_frame(self):
        self._tick([self._warning(1)])
        self._age_incident(1, "WARNING_1", 9.0)
        for _ in range(40):
            self.assertTrue(self._tick([self._warning(1)]))
        # One start, no stop, and still nothing written for the visit itself.
        self.assertEqual(len(self._events_of(ALARM_STARTED)), 1)
        self.assertEqual(self._events_of(ALARM_STOPPED), [])
        self.assertEqual(self._events_of(WARNING_ZONE_INCIDENT), [])

    def test_the_alarm_event_explains_that_it_was_a_dwell(self):
        self._tick([self._warning(1)])
        self._age_incident(1, "WARNING_1", 8.0)
        self._tick([self._warning(1)])
        started = self._events_of(ALARM_STARTED)[0]
        self.assertEqual(started["person_id"], "P1")
        self.assertEqual(started["zone_id"], "WARNING_1")
        self.assertIn("dwell", " ".join(started["reasons"]).lower())

    # -- 10 ------------------------------------------------------------------

    def test_10_leaving_the_warning_zone_stops_the_alarm(self):
        self._tick([self._warning(1)])
        self._age_incident(1, "WARNING_1", 7.0)
        self.assertTrue(self._tick([self._warning(1)]))

        self.assertFalse(self._tick([fake_person(1, zone=config.ZONE_SAFE)]),
                         "the alarm should stop once the visit ends")
        self.assertEqual(len(self._events_of(ALARM_STOPPED)), 1)
        # The visit is now written as one line, with alarm_triggered set.
        record = self._events_of(WARNING_ZONE_INCIDENT)[0]
        self.assertGreaterEqual(record["duration_sec"], 7.0)
        self.assertIs(record["alarm_triggered"], True)

    def test_a_danger_condition_keeps_the_alarm_on_after_the_warning_ends(self):
        # WARNING_1 -> DANGER_1 while beyond the dwell threshold: the warning
        # incident closes, but the existing danger behaviour keeps the buzzer on.
        self._tick([self._warning(1)])
        self._age_incident(1, "WARNING_1", 7.0)
        self.assertTrue(self._tick([self._warning(1)]))
        self.assertTrue(self._tick([fake_person(1, zone=config.ZONE_DANGER)]))
        self.assertEqual(self._events_of(ALARM_STOPPED), [])

    # -- 11 ------------------------------------------------------------------

    def test_11_one_person_leaving_does_not_silence_the_others_alarm(self):
        p1, p2 = self._warning(1, "WARNING_1"), self._warning(2, "WARNING_2")
        self._tick([p1, p2])
        self._age_incident(1, "WARNING_1", 7.0)
        self._age_incident(2, "WARNING_2", 6.0)
        self.assertTrue(self._tick([p1, p2]))

        # P1 walks out to safety. P2 is still lingering in WARNING_2.
        still_on = self._tick([fake_person(1, zone=config.ZONE_SAFE), p2])
        self.assertTrue(still_on, "P2 is still past the dwell limit -- stay ON")
        self.assertEqual(self._events_of(ALARM_STOPPED), [])

        # Now P2 leaves as well.
        self.assertFalse(self._tick([fake_person(1, zone=config.ZONE_SAFE),
                                     fake_person(2, zone=config.ZONE_SAFE)]))
        self.assertEqual(len(self._events_of(ALARM_STOPPED)), 1)

    def test_two_people_in_the_same_zone_each_have_their_own_dwell(self):
        p1, p2 = self._warning(1), self._warning(2)
        self._tick([p1, p2])
        self._age_incident(1, "WARNING_1", 7.0)      # only P1 has lingered
        self.assertTrue(self._tick([p1, p2]))
        # Only P1 lingered, so only P1's incident is flagged.
        self.assertTrue(self.app.events.incidents.get(1, "WARNING_1").alarm_triggered)
        self.assertFalse(self.app.events.incidents.get(2, "WARNING_1").alarm_triggered)
        started = self._events_of(ALARM_STARTED)[0]
        self.assertEqual(started["person_id"], "P1")

    # -- stability -----------------------------------------------------------

    def test_a_detection_gap_does_not_stutter_the_alarm(self):
        # The incident stays open while the tracker holds the track, so a frame
        # where the detector missed the person must not silence the buzzer.
        self._tick([self._warning(1)])
        self._age_incident(1, "WARNING_1", 8.0)
        self.assertTrue(self._tick([self._warning(1)]))

        for _ in range(5):
            self.app.people = []
            self.app.risks = {}
            self.app.events.update([], {}, False, config, live_ids=[1],
                                   frame=self.frame)
            self.app._update_alarm()
            self.assertTrue(self.app.alarm.is_alarm_active(),
                            "the alarm stuttered on a missed frame")
        self.assertEqual(self._events_of(ALARM_STOPPED), [])

    def test_a_confirmed_lost_track_stops_the_alarm(self):
        self._tick([self._warning(1)])
        self._age_incident(1, "WARNING_1", 8.0)
        self.assertTrue(self._tick([self._warning(1)]))

        # The tracker gives up: the incident closes and the buzzer stops.
        self.app.people = []
        self.app.risks = {}
        self.app.events.update([], {}, False, config, live_ids=[], frame=self.frame)
        self.app._update_alarm()
        self.assertFalse(self.app.alarm.is_alarm_active())
        self.assertEqual(self.app.events.incidents.open_incidents, [])

    def test_the_alarm_can_be_disabled_entirely(self):
        self.app.cfg = _ConfigWithout("ENABLE_PC_ALARM")
        self._tick([self._warning(1)])
        self._age_incident(1, "WARNING_1", 9.0)
        self._tick([self._warning(1)])
        self.assertFalse(self.app.alarm.is_alarm_active())
        self.assertEqual(self._events_of(ALARM_STARTED), [])


class _ConfigWithout:
    """config, with one flag forced off. Used to test ENABLE_PC_ALARM = False."""

    def __init__(self, name):
        self._off = name

    def __getattr__(self, name):
        if name == self._off:
            return False
        return getattr(config, name)


class TestAlarmDecision(unittest.TestCase):
    """
    The rule main.py applies: the alarm is ON while ANY worker is in a confirmed
    critical condition (body in DANGER, a confirmed reach-in, or a warning-zone
    visit past its dwell limit), and OFF only when the last one clears. Mirrors
    ZoneXApp._is_critical without needing a camera or a window.
    """

    @staticmethod
    def _is_critical(person):
        return (person.get("zone") == config.ZONE_DANGER
                or bool(person.get("hand_in_danger")))

    def _should_sound(self, people):
        return any(self._is_critical(person) for person in people)

    def test_6_one_person_entering_and_leaving_danger(self):
        p1 = fake_person(1, zone=config.ZONE_DANGER)
        self.assertTrue(self._should_sound([p1]))
        p1_safe = fake_person(1, zone=config.ZONE_SAFE)
        self.assertFalse(self._should_sound([p1_safe]))

    def test_7_p1_in_danger_and_p2_safe_keeps_the_alarm_on(self):
        people = [fake_person(1, zone=config.ZONE_DANGER),
                  fake_person(2, zone=config.ZONE_SAFE)]
        self.assertTrue(self._should_sound(people))

    def test_8_alarm_stays_on_while_the_other_person_is_still_in_danger(self):
        # P1 walks out, P2 is still standing in the danger zone.
        people = [fake_person(1, zone=config.ZONE_SAFE),
                  fake_person(2, zone=config.ZONE_DANGER)]
        self.assertTrue(self._should_sound(people))

    def test_9_alarm_turns_off_only_when_both_are_safe(self):
        people = [fake_person(1, zone=config.ZONE_SAFE),
                  fake_person(2, zone=config.ZONE_SAFE)]
        self.assertFalse(self._should_sound(people))

    def test_a_reach_in_sounds_the_alarm_even_with_safe_feet(self):
        person = fake_person(1, zone=config.ZONE_SAFE, hand=True)
        self.assertEqual(person["zone"], config.ZONE_SAFE)
        self.assertTrue(self._should_sound([person]),
                        "a hand in the machine is a critical condition")

    def test_a_hand_in_the_warning_zone_is_not_critical(self):
        person = fake_person(1, zone=config.ZONE_SAFE,
                             hand_zone=config.ZONE_WARNING)
        self.assertFalse(self._should_sound([person]))

    def test_an_empty_scene_is_silent(self):
        self.assertFalse(self._should_sound([]))

    def test_standing_in_a_warning_zone_is_not_itself_critical(self):
        # Entering WARNING must NOT sound the buzzer. Only lingering does.
        person = fake_person(1, zone=config.ZONE_WARNING, zone_id="WARNING_1")
        self.assertFalse(self._should_sound([person]))

    def test_the_decision_follows_the_confirmed_tracker_state(self):
        # Drive the REAL tracker: the alarm condition must only appear once the
        # hysteresis has confirmed the entry, never on the first noisy frame.
        zones = make_zones()
        tracker = make_tracker()
        detection = [box_at(*PT_DANGER)]

        first = tracker.update(detection, zones, now=100.0)
        self.assertFalse(self._should_sound(first),
                         "one frame must never sound the alarm")

        people = first
        for index in range(1, config.ENTER_CONFIRM_FRAMES + 1):
            people = tracker.update(detection, zones, now=100.0 + index * 0.1)
        self.assertTrue(self._should_sound(people))

        # Walking out gradually clears it, once the exit hysteresis agrees.
        for index, feet_y in enumerate([520, 540, 560, 580, 600, 620]):
            people = tracker.update([box_at(600, feet_y)], zones,
                                    now=110.0 + index * 0.1)
        self.assertFalse(self._should_sound(people))

    def test_two_tracked_people_are_handled_independently(self):
        zones = make_zones()
        tracker = make_tracker()
        in_danger = box_at(*PT_DANGER)
        in_safe = box_at(*PT_SAFE)

        people = []
        for index in range(config.ENTER_CONFIRM_FRAMES + 1):
            people = tracker.update([in_danger, in_safe], zones,
                                    now=200.0 + index * 0.1)
        zones_seen = {p["zone"] for p in people}
        self.assertEqual(zones_seen, {config.ZONE_DANGER, config.ZONE_SAFE})
        self.assertTrue(self._should_sound(people),
                        "one worker in danger is enough to sound the alarm")


class TestJsonLogging(unittest.TestCase):
    """
    The on-disk contract: logs/events_<date>.jsonl plus logs/sessions/<id>.json,
    one independently parseable JSON object per line.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="sentinel-logs-")
        self.engine = RiskEngine(config)
        self.log = EventLog(
            danger_time_threshold=config.DANGER_TIME_THRESHOLD_SEC,
            critical_threshold=config.RISK_CRITICAL_THRESHOLD,
            source_type="cctv",
        )

    def tearDown(self):
        self.log.close_session()
        shutil.rmtree(self.root, ignore_errors=True)

    def _open(self, metadata=None):
        return self.log.open_session(log_root=self.root, metadata=metadata or {})

    def _frame(self, people, machine_on=False, live_ids=None):
        risks = {p["id"]: self.engine.evaluate_person(p, machine_on)
                 for p in people}
        return self.log.update(people, risks, machine_on, config,
                               live_ids=live_ids)

    def _lines(self):
        with open(self.log.events_path, "r", encoding="utf-8") as handle:
            return [line for line in handle.read().splitlines() if line.strip()]

    def _records(self):
        return [json.loads(line) for line in self._lines()]

    def _of_type(self, event_type):
        return [r for r in self._records() if r["event_type"] == event_type]

    @staticmethod
    def person(person_id=1, zone=config.ZONE_DANGER, duration=0.0,
               hand_zone=None, distance=None):
        return fake_person(person_id=person_id, zone=zone, duration=duration,
                           hand_zone=hand_zone, distance=distance)

    # -- file layout ---------------------------------------------------------

    def test_events_file_is_named_for_the_day(self):
        events_path = self._open()
        self.assertTrue(os.path.isfile(events_path))
        expected = "events_{}.jsonl".format(datetime.now().strftime("%Y-%m-%d"))
        self.assertEqual(os.path.basename(events_path), expected)
        self.assertEqual(os.path.dirname(events_path), self.root)

    def test_session_file_is_written_per_run(self):
        self._open()
        self.assertTrue(os.path.isfile(self.log.session_path))
        self.assertEqual(os.path.basename(self.log.session_path),
                         "{}.json".format(self.log.session_id))
        # The session id is a usable timestamp.
        datetime.strptime(self.log.session_id, "%Y-%m-%d_%H-%M-%S")

    def test_two_runs_share_one_events_file_and_keep_separate_sessions(self):
        first_path = self._open()
        self._frame([self.person()])
        first_lines = len(self._lines())
        self.log.close_session()

        # A second run. Its session id is given explicitly rather than sleeping a
        # whole second in a test, which would otherwise collide with the first.
        second = EventLogger(log_root=self.root)
        second_path = second.open("1999-01-01_00-00-00", {"source_type": "webcam"})
        second.write({"event_type": PERSON_DETECTED})
        second.close({"total_events": 1})

        # Same day -> ONE events file, appended to by both runs.
        self.assertEqual(first_path, second_path)
        self.assertEqual(len(self._lines()), first_lines + 1)
        # ...but a separate session file each.
        sessions = sorted(os.listdir(os.path.join(self.root, "sessions")))
        self.assertEqual(sessions, sorted(["{}.json".format(self.log.session_id),
                                           "1999-01-01_00-00-00.json"]))

    def test_session_json_contains_the_run_metadata(self):
        self._open({"source_type": "cctv", "model": "yolov8n.pt",
                    "pose_model": "yolov8n-pose.pt"})
        with open(self.log.session_path, "r", encoding="utf-8") as handle:
            session = json.load(handle)
        self.assertEqual(session["session_id"], self.log.session_id)
        self.assertIn("started_at", session)
        self.assertIn("source_type", session)
        self.assertEqual(session["source_type"], "cctv")
        self.assertEqual(session["model"], "yolov8n.pt")
        self.assertEqual(session["pose_model"], "yolov8n-pose.pt")

    def test_session_json_is_stamped_on_close(self):
        self._open()
        self.log.close_session()
        with open(self.log.session_path, "r", encoding="utf-8") as handle:
            session = json.load(handle)
        self.assertIn("ended_at", session)
        self.assertIn("duration_sec", session)
        self.assertGreaterEqual(session["duration_sec"], 0.0)
        self.assertIn("total_events", session)

    # -- content -------------------------------------------------------------

    def test_every_line_is_independently_parseable(self):
        self._open()
        self._frame([self.person(zone=config.ZONE_WARNING)])
        self._frame([self.person(zone=config.ZONE_DANGER)])
        self._frame([self.person(zone=config.ZONE_DANGER, duration=6.0)], True)
        self._frame([])
        lines = self._lines()
        self.assertGreater(len(lines), 3)
        for line in lines:
            record = json.loads(line)          # raises if not valid JSON
            self.assertIsInstance(record, dict)
            self.assertEqual(record["schema_version"], "1.0")
            self.assertIn("event_id", record)
            self.assertIn("event_type", record)
            self.assertIn("timestamp", record)
            self.assertEqual(record["session_id"], self.log.session_id)
            self.assertEqual(record["source_type"], "cctv")

    def test_records_carry_the_agreed_fields(self):
        self._open()
        self._frame([self.person(zone=config.ZONE_DANGER, distance=42.5)])
        entry = self._of_type(DANGER_ZONE_ENTERED)[0]
        self.assertEqual(entry["person_id"], "P1")
        self.assertEqual(entry["zone_id"], "DANGER_1")
        self.assertEqual(entry["body_zone"], "DANGER")
        self.assertIsNone(entry["hand_zone"])
        self.assertEqual(entry["risk"], 70)
        self.assertIs(entry["machine_on"], False)
        self.assertEqual(entry["danger_duration_sec"], 0.0)
        self.assertEqual(entry["distance_to_danger_px"], 42.5)
        self.assertEqual(entry["source_type"], "cctv")
        self.assertTrue(entry["reasons"])

    def test_only_json_primitives_reach_the_file(self):
        # Guards the Spring Boot contract: no numpy, no tuples, no objects.
        self._open()
        self._frame([self.person(zone=config.ZONE_DANGER,
                                 distance=np.float32(42.5))], True)
        for record in self._records():
            _assert_json_primitives(self, record)

    def test_one_entry_event_not_one_per_frame(self):
        self._open()
        person = self.person(zone=config.ZONE_DANGER)
        for _ in range(30):                    # 30 frames of standing still
            self._frame([person], live_ids=[1])
        self.assertEqual(len(self._of_type(DANGER_ZONE_ENTERED)), 1)
        # 30 frames must not produce 30 lines.
        self.assertLess(len(self._lines()), 6)

    def test_one_dwell_event_when_the_threshold_is_crossed(self):
        self._open()
        for duration in (0.0, 1.0, 2.0, 3.0, 4.0, 4.9):
            self._frame([self.person(zone=config.ZONE_DANGER, duration=duration)])
        self.assertEqual(self._of_type(DANGER_DWELL_EXCEEDED), [])

        for duration in (5.2, 6.0, 7.0, 8.0, 9.0):
            self._frame([self.person(zone=config.ZONE_DANGER, duration=duration)])
        dwells = self._of_type(DANGER_DWELL_EXCEEDED)
        self.assertEqual(len(dwells), 1)
        self.assertEqual(dwells[0]["risk"], 90)
        self.assertGreaterEqual(dwells[0]["danger_duration_sec"], 5.0)

    def test_risk_changed_is_written_once_per_change(self):
        self._open()
        self._frame([self.person(zone=config.ZONE_DANGER)])             # 70
        for _ in range(5):
            self._frame([self.person(zone=config.ZONE_DANGER, duration=6.0)])
        changes = self._of_type(RISK_CHANGED)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["risk"], 90)

    def test_events_survive_without_a_clean_close(self):
        # Simulate a hard kill: never call close_session.
        self._open()
        self._frame([self.person(zone=config.ZONE_DANGER)])
        # The line is already on disk because every write is flushed.
        self.assertTrue(self._of_type(DANGER_ZONE_ENTERED))

    def test_logging_off_still_works_in_memory(self):
        # No session opened at all -- update() must not raise.
        events = self._frame([self.person(zone=config.ZONE_DANGER)])
        self.assertTrue(events)
        self.assertIsNone(self.log.events_path)

    def test_an_unwritable_log_root_does_not_stop_monitoring(self):
        logger = EventLogger(log_root=os.path.join(self.root, "file-not-a-dir"))
        # Make the parent path a FILE, so makedirs must fail.
        with open(os.path.join(self.root, "file-not-a-dir"), "w") as handle:
            handle.write("x")
        self.assertIsNone(logger.open("2026-08-11_12-00-00"))
        self.assertFalse(logger.is_open)
        self.assertFalse(logger.write({"event_type": "X"}))   # no raise


def _assert_json_primitives(case, value, path="record"):
    """Recursively assert a value contains nothing but JSON primitives."""
    if isinstance(value, dict):
        for key, inner in value.items():
            case.assertIsInstance(key, str, path)
            _assert_json_primitives(case, inner, "{}.{}".format(path, key))
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            _assert_json_primitives(case, inner, "{}[{}]".format(path, index))
    else:
        case.assertIsInstance(value, (str, int, float, bool, type(None)), path)
        # numpy scalars pass isinstance(float) in some versions, so check the type.
        case.assertIn(type(value).__module__, ("builtins",), path)


# ===========================================================================
# The zone-redraw regression that made WARNING read as risk 0
# ===========================================================================

class TestZoneRedrawPreservesWork(unittest.TestCase):
    """
    Pressing 'w' and then changing your mind must never cost a working polygon.

    This used to be a real bug: the WARNING polygon was silently wiped, every foot
    point in it fell through to SAFE, and the risk sat at 0 with nothing on screen
    explaining why. Zones are now ADDED rather than replaced, so an existing
    polygon cannot be touched by starting or abandoning a new one at all.
    """

    def setUp(self):
        self.zones = make_zones()

    def test_switching_zone_mid_draw_keeps_the_existing_polygon(self):
        self.zones.start_definition(config.ZONE_WARNING)   # operator presses 'w'
        self.zones.start_definition(config.ZONE_DANGER)    # ...then 'd'
        self.assertTrue(self.zones.zone(config.ZONE_WARNING).is_ready)
        self.assertEqual(self.zones.classify(PT_WARNING), config.ZONE_WARNING)

    def test_cancelling_keeps_the_existing_polygon(self):
        self.zones.start_definition(config.ZONE_WARNING)
        self.zones.cancel_definition()
        self.assertTrue(self.zones.zone(config.ZONE_WARNING).is_ready)
        self.assertEqual(self.zones.classify(PT_WARNING), config.ZONE_WARNING)

    def test_abandoned_polygon_is_discarded_not_kept_empty(self):
        # The half-drawn zone must not linger in the manager as a stray object.
        before = len(self.zones.zones)
        self.zones.start_definition(config.ZONE_WARNING)
        self.assertEqual(len(self.zones.zones), before + 1)
        self.zones.cancel_definition()
        self.assertEqual(len(self.zones.zones), before)
        self.assertFalse(self.zones.is_defining)

    def test_warning_still_scores_30_after_a_cancelled_draw(self):
        self.zones.start_definition(config.ZONE_WARNING)
        self.zones.cancel_definition()
        tracker = make_tracker()
        people = settle(tracker, self.zones, [box_at(*PT_WARNING)])
        self.assertEqual(people[0]["zone"], config.ZONE_WARNING)
        self.assertEqual(
            RiskEngine(config).evaluate_person(people[0], False)["risk"], 30)

    def test_a_finished_draw_adds_a_second_polygon(self):
        # Drawing another warning zone ADDS WARNING_2 and keeps WARNING_1.
        self.zones.start_definition(config.ZONE_WARNING)
        zone = self.zones.defining_zone
        self.assertEqual(zone.zone_id, "WARNING_2")
        zone.points = [(400, 300), (500, 300), (500, 400), (400, 400)]
        ok, _ = self.zones.finish_definition()
        self.assertTrue(ok)
        self.assertEqual(zone.points[0], (400, 300))
        self.assertEqual([z.zone_id for z in self.zones.zones_of_type(
            config.ZONE_WARNING)], ["WARNING_1", "WARNING_2"])

    def test_explicit_clear_is_still_a_clear(self):
        # 'c' when NOT drawing must really remove the zones.
        self.zones.clear()
        self.assertFalse(self.zones.any_ready)
        self.assertEqual(self.zones.zones, [])


# ===========================================================================
# Features 13 / 14 -- rendering smoke test
#
# This does NOT prove the dashboard looks right -- only a human can judge that.
# It proves every new drawing path executes on a real frame without raising,
# across empty / normal / critical / crowded states and a small frame size.
# ===========================================================================

class TestRenderingSmoke(unittest.TestCase):

    def setUp(self):
        self.zones = make_zones()
        self.engine = RiskEngine(config)

    def _render(self, frame, people, machine_on, status):
        risks = {p["id"]: self.engine.evaluate_person(p, machine_on)
                 for p in people}
        worst_id = max(risks, key=lambda i: risks[i]["risk"]) if risks else None

        highest = max((v["risk"] for v in risks.values()), default=0)

        self.zones.draw(frame, violated_ids=["DANGER_1"])
        ui.draw_people(frame, people, config, risks)
        ui.draw_hand_alert(frame, config, people)
        ui.draw_alert_border(frame, config, config.CRITICAL_BANNER_TEXT)
        ui.draw_hud(frame, config, people_count=len(people), inside_count=1,
                    fps=23.4, status=status, zone_info=self.zones.summary(),
                    machine_on=machine_on,
                    zone_counts={config.ZONE_DANGER: 1, config.ZONE_SAFE: 1},
                    highest_risk=highest,
                    critical=highest >= config.RISK_CRITICAL_THRESHOLD)
        ui.draw_worker_panel(frame, config, people, risks)
        if worst_id is not None:
            worst = [p for p in people if p["id"] == worst_id][0]
            ui.draw_reason_panel(frame, config, risks[worst_id], worst)
        ui.draw_zone_message(frame, config, "Point 3 added at (400, 300).")
        ui.draw_help_bar(frame, config)
        ui.draw_event_ticker(frame, config, [
            {"person_id": "P1", "event_type": DANGER_ZONE_ENTERED},
            {"event_type": MACHINE_STATE_CHANGED},
        ])
        return frame

    @staticmethod
    def person(person_id, zone, duration=0.0, hand=False, wrists=(),
               distance=None, hand_zone=None):
        if hand and hand_zone is None:
            hand_zone = config.ZONE_DANGER
        return {
            "id": person_id,
            "person_id": format_person_id(person_id),
            "identity": make_identity(person_id),
            "bbox": (100, 100, 200, 400),
            "confidence": 0.88, "feet": (150.0, 400.0), "zone": zone,
            "zone_id": zone_id_for(zone),
            "danger_duration": duration, "distance_to_danger": distance,
            "hand_in_danger": hand, "hand_zone": hand_zone,
            "hand_zone_id": zone_id_for(hand_zone),
            "wrist_flags": [hand] * len(wrists),
            "wrists": list(wrists), "is_inside": zone == config.ZONE_DANGER,
            "distance": 0.0,
        }

    def test_full_dashboard_renders(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        people = [
            self.person(1, config.ZONE_SAFE, distance=380.0),
            self.person(2, config.ZONE_WARNING, distance=90.0),
            self.person(3, config.ZONE_DANGER, duration=6.2, hand=True,
                        wrists=[(600, 400), (250, 300)]),
        ]
        result = self._render(frame, people, True, "CRITICAL")
        self.assertTrue(result.any(), "nothing was drawn on the frame")

    def test_empty_scene_renders(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        self._render(frame, [], False, "CLEAR")

    def test_small_frame_renders(self):
        # The HUD scales down for a 640x480 webcam; make sure nothing goes
        # off-frame or divides by zero.
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        people = [self.person(1, config.ZONE_DANGER, duration=9.9, hand=True,
                              wrists=[(300, 200)])]
        self._render(frame, people, True, "CRITICAL")

    def test_crowd_is_truncated_not_crashed(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        people = [self.person(i, config.ZONE_WARNING, distance=80.0)
                  for i in range(1, 15)]
        self._render(frame, people, False, "WARNING")

    def test_wrist_marker_drawn_off_frame_is_safe(self):
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        people = [self.person(1, config.ZONE_SAFE, hand=True,
                              wrists=[(-50, -50), (5000, 5000)])]
        ui.draw_people(frame, people, config)


# ===========================================================================
# FRAME SOURCE -- configuration and error handling
#
# No camera, no phone and no CCTV stream is required. The four source types are
# checked through their CONFIGURATION and their error messages; the video path is
# exercised end to end against a file this test writes itself.
#
# Deliberately NOT tested here: actually opening a network stream. A dead URL
# takes OpenCV/FFmpeg 10-15 s to give up (that timeout lives inside FFmpeg, not
# in our code), which would make the suite unusable. See the README for the
# manual check.
# ===========================================================================

class TestFrameSourceConfiguration(unittest.TestCase):

    def test_config_is_read_without_touching_hardware(self):
        source = FrameSource.from_config(config)
        self.assertEqual(source.source_type, config.SOURCE_TYPE)
        self.assertEqual(source.camera_index, config.CAMERA_INDEX)
        self.assertEqual(source.video_path, config.VIDEO_PATH)
        self.assertEqual(source.mirror, config.MIRROR_PREVIEW)

    def test_no_stream_address_is_hardcoded_in_the_source_code(self):
        # SOURCE_URL in config.py is the operator's own setting and may hold a real
        # address. What must never happen is an address baked into the CODE, where
        # changing source would mean editing Python.
        with open(os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "src", "frame_source.py"),
                encoding="utf-8") as handle:
            code = handle.read()
        for octet in re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", code):
            # The documented placeholders are fine; a routable address is not.
            self.assertTrue(
                octet.startswith("192.168.") or octet.startswith("10.0."),
                "frame_source.py hardcodes the address {}".format(octet))

    def test_every_documented_source_type_is_accepted(self):
        for source_type in ("webcam", "ip", "cctv", "video"):
            source = FrameSource(source_type=source_type,
                                 source_url="http://example/video",
                                 video_path=__file__)
            source._validate()          # must not raise

    def test_unknown_source_type_is_reported_clearly(self):
        with self.assertRaises(FrameSourceError) as caught:
            FrameSource(source_type="phone").open()
        message = str(caught.exception)
        self.assertIn("Unknown SOURCE_TYPE", message)
        self.assertIn("webcam", message)

    def test_a_url_in_camera_index_is_explained(self):
        # The trap this replaces: CAMERA_INDEX used to accept a URL string.
        with self.assertRaises(FrameSourceError) as caught:
            FrameSource(source_type="webcam",
                        camera_index="http://192.168.1.5:8080/video").open()
        message = str(caught.exception)
        self.assertIn("CAMERA_INDEX must be a whole number", message)
        self.assertIn('SOURCE_TYPE = "ip"', message)

    def test_missing_ip_url_is_explained(self):
        with self.assertRaises(FrameSourceError) as caught:
            FrameSource(source_type="ip", source_url="").open()
        self.assertIn("SOURCE_URL is empty", str(caught.exception))

    def test_missing_cctv_url_suggests_an_rtsp_address(self):
        with self.assertRaises(FrameSourceError) as caught:
            FrameSource(source_type="cctv", source_url="").open()
        self.assertIn("rtsp://", str(caught.exception))

    def test_missing_video_path_is_explained(self):
        with self.assertRaises(FrameSourceError) as caught:
            FrameSource(source_type="video", video_path="").open()
        self.assertIn("VIDEO_PATH is empty", str(caught.exception))

    def test_missing_video_file_says_so(self):
        with self.assertRaises(FrameSourceError) as caught:
            FrameSource(source_type="video",
                        video_path="videos/does_not_exist.mp4").open()
        message = str(caught.exception)
        self.assertIn("Video file not found.", message)
        # The absolute path it looked at, so a wrong working directory is obvious.
        self.assertIn("does_not_exist.mp4", message)

    def test_network_error_messages_use_the_agreed_wording(self):
        ip = FrameSource(source_type="ip", source_url="http://host/video")
        self.assertIn("Could not connect to IP camera.",
                      ip._network_error("Could not connect to IP camera."))
        self.assertIn("SAME wifi",
                      ip._network_error("Could not connect to IP camera."))

        cctv = FrameSource(source_type="cctv", source_url="rtsp://host/s1")
        advice = cctv._network_error("Could not open CCTV stream.")
        self.assertIn("Could not open CCTV stream.", advice)
        self.assertIn("RTSP path", advice)

    def test_source_block_for_events_exposes_only_the_type(self):
        # A URL can carry camera credentials; it must never reach an event log.
        source = FrameSource(source_type="cctv",
                             source_url="rtsp://user:secret@10.0.0.5/stream1")
        self.assertEqual(source.describe(), {"source_type": "cctv"})
        self.assertNotIn("secret", json.dumps(source.describe()))

    def test_reading_before_open_is_not_a_crash(self):
        ok, frame = FrameSource(source_type="webcam").read()
        self.assertFalse(ok)
        self.assertIsNone(frame)

    def test_release_without_open_is_safe(self):
        FrameSource(source_type="webcam").release()   # must not raise


class TestFrameSourceVideoFile(unittest.TestCase):
    """The source that lets the whole system be tested with no camera at all."""

    FRAMES = 6
    SIZE = (64, 48)
    FPS = 10.0

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="sentinel-video-")
        cls.path = os.path.join(cls.root, "factory_test.mp4")
        writer = cv2.VideoWriter(cls.path, cv2.VideoWriter_fourcc(*"mp4v"),
                                 cls.FPS, cls.SIZE)
        cls.available = writer.isOpened()
        if cls.available:
            for index in range(cls.FRAMES):
                writer.write(np.full((cls.SIZE[1], cls.SIZE[0], 3),
                                     20 * index, dtype=np.uint8))
        writer.release()
        cls.available = cls.available and os.path.getsize(cls.path) > 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self):
        if not self.available:
            self.skipTest("no mp4 encoder available in this OpenCV build")

    def _source(self, **kwargs):
        kwargs.setdefault("realtime", False)   # keep the suite fast
        return FrameSource(source_type="video", video_path=self.path, **kwargs)

    def test_video_opens_and_reports_its_properties(self):
        source = self._source().open()
        self.addCleanup(source.release)
        self.assertTrue(source.is_opened())
        self.assertEqual((source.frame_width, source.frame_height), self.SIZE)
        self.assertAlmostEqual(source.fps, self.FPS, places=1)

    def test_every_frame_is_delivered_including_the_first(self):
        # open() probes one frame to verify the source; that frame must be handed
        # back by the first read() rather than silently dropped.
        source = self._source().open()
        self.addCleanup(source.release)
        count = 0
        while True:
            ok, frame = source.read()
            if not ok:
                break
            self.assertEqual(frame.shape[:2], (self.SIZE[1], self.SIZE[0]))
            count += 1
        self.assertEqual(count, self.FRAMES)

    def test_end_of_file_is_a_clean_finish_not_an_error(self):
        source = self._source().open()
        self.addCleanup(source.release)
        for _ in range(self.FRAMES):
            source.read()
        ok, frame = source.read()
        self.assertFalse(ok)
        self.assertIsNone(frame)
        self.assertTrue(source.ended, "main.py relies on .ended to stop cleanly")

    def test_mirroring_happens_inside_the_source(self):
        plain = self._source(mirror=False).open()
        self.addCleanup(plain.release)
        mirrored = self._source(mirror=True).open()
        self.addCleanup(mirrored.release)

        # A left/right gradient makes the flip visible.
        _, a = plain.read()
        _, b = mirrored.read()
        self.assertTrue(np.array_equal(b, cv2.flip(a, 1)))

    def test_realtime_playback_is_paced_by_the_file_fps(self):
        source = FrameSource(source_type="video", video_path=self.path,
                             realtime=True).open()
        self.addCleanup(source.release)
        self.assertAlmostEqual(source._frame_interval, 1.0 / self.FPS, places=3)

    def test_non_realtime_playback_is_not_paced(self):
        source = self._source().open()
        self.addCleanup(source.release)
        self.assertEqual(source._frame_interval, 0.0)

    def test_a_file_that_is_not_a_video_is_reported_clearly(self):
        junk = os.path.join(self.root, "not_really.mp4")
        with open(junk, "w", encoding="utf-8") as handle:
            handle.write("this is not a video")
        with self.assertRaises(FrameSourceError) as caught:
            FrameSource(source_type="video", video_path=junk).open()
        self.assertIn("Could not open the video file.", str(caught.exception))


# ===========================================================================
# LATEST-FRAME ARCHITECTURE
#
# The reader thread keeps only the newest frame and the scheduler decides when the
# pipeline takes one. Dropping frames is the intended behaviour, so these tests
# assert that frames ARE dropped -- and that the newest one always survives.
# ===========================================================================

class TestLatestFrameBuffer(unittest.TestCase):

    def test_a_new_frame_replaces_an_unprocessed_one(self):
        buffer = LatestFrame(depth=1)
        self.assertEqual(buffer.put("old", 1.0), 0)
        # The slot is still full, so "old" is displaced and reported as a drop.
        self.assertEqual(buffer.put("new", 2.0), 1)
        self.assertEqual(buffer.get(), ("new", 2.0))

    def test_only_the_newest_frame_survives_a_burst(self):
        buffer = LatestFrame(depth=1)
        dropped = sum(buffer.put(index, float(index)) for index in range(30))
        self.assertEqual(dropped, 29)
        frame, _ = buffer.get()
        self.assertEqual(frame, 29, "the newest frame must be the one kept")

    def test_taking_a_frame_empties_the_slot(self):
        # Otherwise the pipeline would re-run on pixels it has already analysed.
        buffer = LatestFrame()
        buffer.put("frame", 1.0)
        self.assertEqual(buffer.get()[0], "frame")
        self.assertEqual(buffer.get(), (None, None))

    def test_an_empty_buffer_is_not_an_error(self):
        self.assertEqual(LatestFrame().get(), (None, None))

    def test_depth_can_hold_more_than_one_in_order(self):
        buffer = LatestFrame(depth=2)
        self.assertEqual(buffer.put("a", 1.0), 0)
        self.assertEqual(buffer.put("b", 2.0), 0)
        self.assertEqual(buffer.put("c", 3.0), 1)     # "a" displaced
        self.assertEqual(buffer.get()[0], "b")
        self.assertEqual(buffer.get()[0], "c")

    def test_depth_is_never_below_one(self):
        self.assertEqual(LatestFrame(depth=0).depth, 1)

    def test_concurrent_writes_and_reads_do_not_corrupt_it(self):
        # A crude but real check that the lock does its job.
        buffer = LatestFrame()
        stop = threading.Event()
        seen = []

        def writer():
            index = 0
            while not stop.is_set():
                buffer.put(index, float(index))
                index += 1

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        try:
            for _ in range(2000):
                frame, stamp = buffer.get()
                if frame is not None:
                    seen.append((frame, stamp))
        finally:
            stop.set()
            thread.join(timeout=2.0)

        self.assertTrue(seen, "nothing was handed over at all")
        for frame, stamp in seen:
            self.assertEqual(float(frame), stamp, "a frame and its timestamp split")
        # Frames must arrive in order, with gaps where drops happened.
        numbers = [frame for frame, _ in seen]
        self.assertEqual(numbers, sorted(numbers))


class TestProcessingScheduler(unittest.TestCase):
    """Time-based gating on a monotonic clock -- never integer frame skipping."""

    def test_the_first_frame_is_always_due(self):
        self.assertTrue(ProcessingScheduler(10.0).due(now=0.0))

    def test_interval_comes_from_the_target_fps(self):
        self.assertAlmostEqual(ProcessingScheduler(10.0).interval, 0.1)
        self.assertAlmostEqual(ProcessingScheduler(25.0).interval, 0.04)

    def test_a_frame_is_not_due_again_until_the_interval_has_passed(self):
        schedule = ProcessingScheduler(10.0)
        schedule.mark(now=100.0)
        self.assertFalse(schedule.due(now=100.05))
        self.assertTrue(schedule.due(now=100.10))
        self.assertTrue(schedule.due(now=100.99))

    def test_the_rate_is_the_same_whatever_the_source_fps(self):
        # THE REASON THIS IS NOT "every Nth frame": the same schedule must yield
        # ~10 processed frames per second from a 15 FPS phone and a 60 FPS webcam.
        for source_fps in (15.0, 25.0, 30.0, 60.0):
            schedule = ProcessingScheduler(10.0)
            now, processed = 0.0, 0
            for _ in range(int(source_fps) * 2):        # two seconds of frames
                now += 1.0 / source_fps
                if schedule.due(now=now):
                    schedule.mark(now=now)
                    processed += 1
            self.assertIn(processed, (19, 20, 21),
                          "{} FPS source gave {} processed frames".format(
                              source_fps, processed))

    def test_seconds_until_due_counts_down(self):
        schedule = ProcessingScheduler(10.0)
        schedule.mark(now=50.0)
        self.assertAlmostEqual(schedule.seconds_until_due(now=50.0), 0.1)
        self.assertAlmostEqual(schedule.seconds_until_due(now=50.06), 0.04, places=6)
        self.assertEqual(schedule.seconds_until_due(now=50.5), 0.0)

    def test_slots_advance_instead_of_restarting_on_each_arrival(self):
        # A frame taken slightly late must not push the whole cadence later, or a
        # 15 FPS source would quantise to 7.5 FPS instead of the 10 asked for.
        schedule = ProcessingScheduler(10.0)
        schedule.mark(now=0.0)
        schedule.mark(now=0.13)             # 30 ms late
        self.assertAlmostEqual(schedule.seconds_until_due(now=0.13), 0.07, places=6)

    def test_falling_far_behind_resyncs_rather_than_bursting(self):
        # The pipeline stalled for a second (or the app was paused). We must not
        # then process ten frames back to back to "catch up".
        schedule = ProcessingScheduler(10.0)
        schedule.mark(now=0.0)
        schedule.mark(now=1.0)
        self.assertFalse(schedule.due(now=1.0))
        self.assertAlmostEqual(schedule.seconds_until_due(now=1.0), 0.1, places=6)

    def test_no_target_means_process_everything(self):
        # What a sequentially read video file uses: no gate at all.
        schedule = ProcessingScheduler(None)
        self.assertFalse(schedule.enabled)
        schedule.mark(now=1.0)
        self.assertTrue(schedule.due(now=1.0))
        self.assertEqual(schedule.seconds_until_due(now=1.0), 0.0)


class TestSourceStats(unittest.TestCase):
    """Diagnostics only -- these numbers never reach the JSON event log."""

    def test_rates_are_measured_from_timestamps(self):
        stats = SourceStats()
        for index in range(11):
            stats.note_received(now=index * 0.04)      # 25 FPS
        for index in range(11):
            stats.note_processed(0.01, now=index * 0.1)  # 10 FPS
        self.assertAlmostEqual(stats.source_fps, 25.0, places=4)
        self.assertAlmostEqual(stats.process_fps, 10.0, places=4)

    def test_drops_are_counted_and_reported(self):
        stats = SourceStats()
        for _ in range(30):
            stats.note_received(dropped=2)
        self.assertEqual(stats.received, 30)
        self.assertEqual(stats.dropped, 60)
        self.assertAlmostEqual(stats.drop_rate, 2.0)

    def test_snapshot_is_json_safe_and_complete(self):
        stats = SourceStats()
        stats.note_geometry("ip", 1280, 720, 30.0)
        stats.note_received()
        stats.note_processed(0.033)
        stats.note_process_time(0.096)
        snapshot = stats.snapshot()
        for key in ("source_type", "resolution", "source_fps", "process_fps",
                    "frames_received", "frames_processed", "frames_dropped",
                    "buffer_age_ms", "process_time_ms"):
            self.assertIn(key, snapshot)
        self.assertEqual(snapshot["source_type"], "ip")
        self.assertEqual(snapshot["resolution"], "1280x720")
        self.assertEqual(snapshot["buffer_age_ms"], 33.0)
        self.assertEqual(snapshot["process_time_ms"], 96.0)
        _assert_json_primitives(self, json.loads(json.dumps(snapshot)))

    def test_console_line_reports_the_requested_fields(self):
        stats = SourceStats()
        stats.note_geometry("ip", 1280, 720, 30.0)
        line = stats.console_line()
        for field in ("SOURCE:", "SOURCE FPS:", "PROCESS FPS:",
                      "RECEIVED:", "PROCESSED:", "DROPPED:"):
            self.assertIn(field, line)

    def test_hud_lines_are_label_value_pairs(self):
        stats = SourceStats()
        stats.note_geometry("cctv", 640, 480, 0.0)
        for label, value in stats.hud_lines():
            self.assertIsInstance(label, str)
            self.assertIsInstance(value, str)

    def test_fps_is_zero_before_anything_arrives(self):
        stats = SourceStats()
        self.assertEqual(stats.source_fps, 0.0)
        self.assertEqual(stats.process_fps, 0.0)
        self.assertEqual(stats.drop_rate, 0.0)


class TestReaderThread(unittest.TestCase):
    """
    The reader thread against a real video file played in realtime mode, which is
    the same code path a webcam or a phone uses.
    """

    FRAMES = 40
    SIZE = (64, 48)
    FPS = 25.0

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="sentinel-reader-")
        cls.path = os.path.join(cls.root, "stream.mp4")
        writer = cv2.VideoWriter(cls.path, cv2.VideoWriter_fourcc(*"mp4v"),
                                 cls.FPS, cls.SIZE)
        cls.available = writer.isOpened()
        if cls.available:
            for index in range(cls.FRAMES):
                writer.write(np.full((cls.SIZE[1], cls.SIZE[0], 3),
                                     (index * 6) % 256, dtype=np.uint8))
        writer.release()
        cls.available = cls.available and os.path.getsize(cls.path) > 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self):
        if not self.available:
            self.skipTest("no mp4 encoder available in this OpenCV build")

    def _live_source(self, **kwargs):
        """A realtime video file: uses the reader thread, exactly like a camera."""
        source = FrameSource(source_type="video", video_path=self.path,
                             realtime=True, **kwargs)
        self.addCleanup(source.release)
        return source

    def test_live_sources_use_the_reader_thread(self):
        for source_type in ("webcam", "ip", "cctv"):
            source = FrameSource(source_type=source_type,
                                 source_url="http://example/video")
            self.assertTrue(source.uses_reader_thread, source_type)

    def test_a_realtime_file_uses_the_thread_but_an_offline_one_does_not(self):
        self.assertTrue(FrameSource(source_type="video", video_path=self.path,
                                    realtime=True).uses_reader_thread)
        self.assertFalse(FrameSource(source_type="video", video_path=self.path,
                                     realtime=False).uses_reader_thread)

    def test_frames_keep_arriving_without_anyone_reading(self):
        # THE CORE PROPERTY: the reader does not wait for the AI pipeline.
        source = self._live_source().open()
        time.sleep(0.5)
        received = source.stats.received
        self.assertGreater(received, 1,
                           "the reader thread did not pull frames on its own")
        time.sleep(0.3)
        self.assertGreater(source.stats.received, received)

    def test_the_latest_frame_changes_over_time(self):
        source = self._live_source().open()
        ok, first = source.read()
        self.assertTrue(ok)
        time.sleep(0.4)
        ok, later = source.read()
        self.assertTrue(ok)
        self.assertFalse(np.array_equal(first, later),
                         "read() handed back the same frame twice")

    def test_a_slow_consumer_drops_frames_instead_of_queueing_them(self):
        # A 25 FPS source with a consumer taking one frame every ~200 ms. The
        # backlog must NOT grow: frames are replaced, and that is intended.
        source = self._live_source().open()
        for _ in range(4):
            time.sleep(0.2)
            source.read()
            self.assertLessEqual(len(source._buffer), source._buffer.depth,
                                 "a backlog is accumulating")
        self.assertGreater(source.stats.dropped, 0,
                           "nothing was dropped, so the consumer was not behind")
        self.assertGreater(source.stats.received, source.stats.processed)

    def test_the_frame_handed_over_is_the_newest_one_not_the_oldest(self):
        source = self._live_source().open()
        source.read()                      # clear the probe frame
        time.sleep(0.5)                    # let a dozen frames arrive and drop
        ok, frame = source.read()
        self.assertTrue(ok)
        # The generated video gets brighter over time, so a stale frame would be
        # noticeably darker than what the source has most recently produced.
        latest_seen = int(frame[0, 0, 0])
        time.sleep(0.05)
        ok, following = source.read()
        if ok:
            self.assertGreaterEqual(int(following[0, 0, 0]), latest_seen)

    def test_buffer_age_is_measured_and_small(self):
        source = self._live_source().open()
        time.sleep(0.3)
        source.read()
        # The frame we take should be young: it is whatever arrived most recently,
        # not something that sat in a queue. One frame interval plus slack.
        self.assertLess(source.stats.buffer_age, 0.5)

    def test_no_new_frame_yet_is_not_an_error(self):
        source = self._live_source().open()
        source.read()                       # take whatever is there
        ok, frame = source.read()           # immediately again: nothing new yet
        if not ok:
            self.assertIsNone(frame)
            self.assertFalse(source.ended)
            self.assertLess(source.stalled_for, 1.0)

    def test_realtime_pacing_still_applies_to_the_reader(self):
        # A 25 FPS file must not be drained at 500 FPS just because a thread reads
        # it. Roughly 0.4 s of playback is ~10 frames, definitely not all 40.
        source = self._live_source().open()
        time.sleep(0.4)
        self.assertLess(source.stats.received, self.FRAMES,
                        "the file was consumed far faster than its frame rate")

    def test_end_of_file_still_ends_cleanly_with_the_thread(self):
        source = self._live_source().open()
        deadline = time.time() + 10.0
        while not source.ended and time.time() < deadline:
            source.read()
            time.sleep(0.02)
        self.assertTrue(source.ended, "the reader never reported end of file")

    def test_release_stops_the_thread(self):
        source = self._live_source().open()
        time.sleep(0.1)
        source.release()
        self.assertIsNone(source._thread)
        self.assertFalse(source.is_opened())
        # And a read after release is harmless.
        self.assertEqual(source.read(), (False, None))

    def test_stalled_for_grows_when_frames_stop(self):
        source = self._live_source().open()
        time.sleep(0.1)
        source._stop.set()                  # freeze the reader without releasing
        time.sleep(0.25)
        self.assertGreater(source.stalled_for, 0.15)

    def test_offline_playback_is_still_sequential_and_lossless(self):
        # VIDEO_REALTIME = False must keep the deterministic path: every frame, in
        # order, nothing dropped -- that is what offline analysis needs.
        source = FrameSource(source_type="video", video_path=self.path,
                             realtime=False)
        self.addCleanup(source.release)
        source.open()
        self.assertFalse(source.uses_reader_thread)
        count = 0
        while True:
            ok, _ = source.read()
            if not ok:
                break
            count += 1
        self.assertEqual(count, self.FRAMES)
        self.assertEqual(source.stats.dropped, 0)


# ===========================================================================
# PERSON IDS -- P1, P2, P3 ... and the identity block face recognition will fill
# ===========================================================================

class TestPersonIds(unittest.TestCase):

    def setUp(self):
        self.zones = make_zones()
        self.tracker = make_tracker()

    def test_first_person_is_p1_and_second_is_p2(self):
        people = settle(self.tracker, self.zones,
                        [box_at(*PT_SAFE), box_at(*PT_WARNING)])
        self.assertEqual(sorted(p["person_id"] for p in people), ["P1", "P2"])

    def test_the_id_is_stable_across_frames(self):
        # THE POINT OF THE TRACKER: many frames of one person is still one id.
        detection = [box_at(*PT_SAFE)]
        seen = set()
        for index in range(40):
            people = self.tracker.update(detection, self.zones,
                                         now=1000.0 + index * 0.1)
            seen.add(people[0]["person_id"])
        self.assertEqual(seen, {"P1"})

    def test_the_id_survives_a_short_detection_gap(self):
        detection = [box_at(*PT_SAFE)]
        self.tracker.update(detection, self.zones, now=1000.0)
        # YOLO misses the person for a few frames, well under the drop threshold.
        for index in range(3):
            self.assertEqual(self.tracker.update([], self.zones,
                                                 now=1001.0 + index * 0.1), [])
            self.assertEqual(self.tracker.live_ids, [1])
        people = self.tracker.update(detection, self.zones, now=1002.0)
        self.assertEqual(people[0]["person_id"], "P1")

    def test_a_new_person_gets_the_next_id_not_a_recycled_one(self):
        # P1 and P2 are present, P1 leaves for good, a new worker arrives -> P3.
        a, b = box_at(*PT_SAFE), box_at(*PT_WARNING)
        self.tracker.update([a, b], self.zones, now=100.0)

        # lost_ids reports the tracks dropped by THAT call, so collect as we go.
        lost = []
        for index in range(config.TRACK_MAX_MISSING_FRAMES + 2):
            self.tracker.update([b], self.zones, now=101.0 + index * 0.1)
            lost.extend(self.tracker.lost_ids)
        self.assertEqual(lost, [1], "P1 should be dropped exactly once")
        self.assertNotIn(1, self.tracker.live_ids)

        people = self.tracker.update([b, box_at(700, 650)], self.zones, now=200.0)
        self.assertEqual(sorted(p["person_id"] for p in people), ["P2", "P3"])

    def test_identity_block_is_ready_for_face_recognition(self):
        people = settle(self.tracker, self.zones, [box_at(*PT_SAFE)])
        identity = people[0]["identity"]
        self.assertEqual(identity, {
            "person_id": "P1",
            "employee_id": None,
            "person_name": None,
            "identity_status": "UNIDENTIFIED",
        })

    def test_no_fake_names_are_invented(self):
        people = settle(self.tracker, self.zones, [box_at(*PT_SAFE)])
        self.assertIsNone(people[0]["identity"]["person_name"])
        self.assertIsNone(people[0]["identity"]["employee_id"])

    def test_format_person_id_is_the_single_source_of_the_label(self):
        self.assertEqual(format_person_id(1), "P1")
        self.assertEqual(format_person_id(7), "P7")
        self.assertEqual(make_identity(7)["person_id"], "P7")

    def test_the_ui_never_prints_person_hash_seven(self):
        people = settle(self.tracker, self.zones, [box_at(*PT_DANGER)])
        self.assertEqual(ui.person_tag(people[0]), "P1")
        # ...and it copes with the loose forms the panels may hand it.
        self.assertEqual(ui.person_tag(3), "P3")
        self.assertEqual(ui.person_tag("P4"), "P4")
        self.assertEqual(ui.person_tag({"person": None}), "?")

    def test_ids_are_not_reused_after_a_tracker_reset(self):
        # reset() happens when zones are redrawn; numbering restarts from P1
        # because every previous track is deliberately forgotten.
        settle(self.tracker, self.zones, [box_at(*PT_SAFE)])
        self.tracker.reset()
        people = settle(self.tracker, self.zones, [box_at(*PT_SAFE)])
        self.assertEqual(people[0]["person_id"], "P1")


# ===========================================================================
# ZONE IDS -- unique across every type, from one centralized counter
# ===========================================================================

class TestZoneIds(unittest.TestCase):

    def setUp(self):
        self.zones = ZoneManager(config)
        self.zones.set_frame_size(1280, 720)

    def _add(self, zone_type, points):
        ok, reason = self.zones.set_zone_points(zone_type, points)
        self.assertTrue(ok, reason)
        return self.zones.last_finished

    def test_the_first_zone_of_each_type_is_numbered_one(self):
        self.assertEqual(self._add(config.ZONE_SAFE, SAFE_POLY).zone_id, "SAFE_1")
        self.assertEqual(self._add(config.ZONE_WARNING, WARNING_POLY).zone_id,
                         "WARNING_1")
        self.assertEqual(self._add(config.ZONE_DANGER, DANGER_POLY).zone_id,
                         "DANGER_1")

    def test_the_requested_layout_produces_the_requested_tags(self):
        # SAFE_1, SAFE_2, WARNING_1, DANGER_1, DANGER_2
        self._add(config.ZONE_SAFE, SAFE_POLY)
        self._add(config.ZONE_SAFE, [(0, 0), (90, 0), (90, 90), (0, 90)])
        self._add(config.ZONE_WARNING, WARNING_POLY)
        self._add(config.ZONE_DANGER, DANGER_POLY)
        self._add(config.ZONE_DANGER, [(800, 300), (950, 300), (950, 450),
                                       (800, 450)])
        self.assertEqual([z.zone_id for z in self.zones.zones],
                         ["SAFE_1", "SAFE_2", "WARNING_1", "DANGER_1", "DANGER_2"])

    def test_no_duplicate_ids_anywhere(self):
        for _ in range(4):
            self._add(config.ZONE_SAFE, SAFE_POLY)
            self._add(config.ZONE_WARNING, WARNING_POLY)
            self._add(config.ZONE_DANGER, DANGER_POLY)
        ids = [zone.zone_id for zone in self.zones.zones]
        self.assertEqual(len(ids), len(set(ids)))

    def test_ids_do_not_collide_after_a_zone_is_deleted(self):
        # THE BUG A LIST-LENGTH ID WOULD HAVE: delete SAFE_1, add another safe
        # zone, and a length-based scheme hands out "SAFE_2" twice.
        self._add(config.ZONE_SAFE, SAFE_POLY)
        second = self._add(config.ZONE_SAFE, [(0, 0), (90, 0), (90, 90), (0, 90)])
        self.assertEqual(second.zone_id, "SAFE_2")

        self.zones.clear(config.ZONE_SAFE)
        third = self._add(config.ZONE_SAFE, SAFE_POLY)
        self.assertEqual(third.zone_id, "SAFE_3")
        self.assertNotIn(third.zone_id, ("SAFE_1", "SAFE_2"))

    def test_clearing_everything_still_does_not_reissue_a_tag(self):
        self._add(config.ZONE_DANGER, DANGER_POLY)
        self.zones.clear()
        self.assertEqual(self._add(config.ZONE_DANGER, DANGER_POLY).zone_id,
                         "DANGER_2")

    def test_an_abandoned_polygon_retires_its_tag(self):
        self.zones.start_definition(config.ZONE_DANGER)     # DANGER_1
        self.zones.cancel_definition()
        self.assertEqual(self._add(config.ZONE_DANGER, DANGER_POLY).zone_id,
                         "DANGER_2")

    def test_the_id_factory_is_the_only_counter(self):
        factory = ZoneIdFactory()
        issued = [factory.next_id("SAFE") for _ in range(3)]
        issued += [factory.next_id("DANGER") for _ in range(2)]
        self.assertEqual(issued, ["SAFE_1", "SAFE_2", "SAFE_3",
                                  "DANGER_1", "DANGER_2"])
        self.assertEqual(len(factory.issued), 5)

    def test_zone_identity_carries_everything_the_backend_needs(self):
        zone = self._add(config.ZONE_DANGER, DANGER_POLY)
        described = zone.describe()
        self.assertEqual(described["zone_id"], "DANGER_1")
        self.assertEqual(described["zone_type"], "DANGER")
        self.assertEqual(described["zone_name"], config.ZONE_LABELS["DANGER"])
        self.assertEqual(described["points"],
                         [[x, y] for x, y in DANGER_POLY])
        # Plain JSON only -- no numpy, no tuples.
        _assert_json_primitives(self, json.loads(json.dumps(described)))

    def test_the_tag_is_what_the_ui_draws(self):
        zone = self._add(config.ZONE_DANGER, DANGER_POLY)
        label, _, _ = zone.label_geometry()
        self.assertEqual(label, "DANGER_1")
        self.assertEqual(zone.tag, "DANGER_1")

    def test_zone_labels_do_not_overlap_when_polygons_are_nested(self):
        # Three zones sharing one centre: their tags must not print on top of
        # each other.
        centre = [(500, 300), (700, 300), (700, 500), (500, 500)]
        self._add(config.ZONE_SAFE, centre)
        self._add(config.ZONE_WARNING, centre)
        self._add(config.ZONE_DANGER, centre)

        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.zones.draw(frame)

        taken = []
        for zone in reversed(self.zones.all_zones):
            offset = ZoneManager._label_offset(zone, taken, config.ZONE_TAG_MIN_GAP_PX)
            self.assertIsNotNone(zone.label_geometry(offset))
        rects = taken
        self.assertEqual(len(rects), 3)
        for first in range(len(rects)):
            for second in range(first + 1, len(rects)):
                a, b = rects[first], rects[second]
                overlap = not (a[2] <= b[0] or b[2] <= a[0]
                               or a[3] <= b[1] or b[3] <= a[1])
                self.assertFalse(overlap, "zone tags {} and {} overlap".format(a, b))


class TestMultipleZonesOfOneType(unittest.TestCase):
    """
    Two machines, two danger zones. The safety semantics must not change:
    DANGER still beats WARNING beats SAFE, and the dwell timer is still per person.
    """

    DANGER_A = [(500, 300), (700, 300), (700, 500), (500, 500)]
    DANGER_B = [(900, 300), (1100, 300), (1100, 500), (900, 500)]

    def setUp(self):
        self.zones = ZoneManager(config)
        self.zones.set_frame_size(1280, 720)
        self.zones.set_zone_points(config.ZONE_SAFE, SAFE_POLY)
        self.zones.set_zone_points(config.ZONE_DANGER, self.DANGER_A)
        self.zones.set_zone_points(config.ZONE_DANGER, self.DANGER_B)
        self.tracker = make_tracker()
        self.engine = RiskEngine(config)

    def test_each_polygon_is_recognised_separately(self):
        self.assertEqual(self.zones.classify_zone((600, 400)).zone_id, "DANGER_1")
        self.assertEqual(self.zones.classify_zone((1000, 400)).zone_id, "DANGER_2")

    def test_both_count_as_the_danger_type(self):
        for point in ((600, 400), (1000, 400)):
            self.assertEqual(self.zones.classify(point), config.ZONE_DANGER)
            self.assertTrue(self.zones.contains(config.ZONE_DANGER, point))

    def test_distance_is_measured_to_the_nearest_danger_zone(self):
        # 750 is 50 px right of DANGER_1 and 150 px left of DANGER_2.
        self.assertAlmostEqual(self.zones.distance_to_danger((750, 400)), 50.0)

    def test_danger_still_wins_over_a_containing_safe_zone(self):
        people = settle(self.tracker, self.zones, [box_at(1000, 400)])
        self.assertEqual(people[0]["zone"], config.ZONE_DANGER)
        self.assertEqual(people[0]["zone_id"], "DANGER_2")
        self.assertEqual(self.engine.evaluate_person(people[0], False)["risk"], 70)

    def test_the_dwell_timer_follows_the_person_not_the_polygon(self):
        detection = [box_at(1000, 400)]
        self.tracker.update(detection, self.zones, now=100.0)
        self.tracker.update(detection, self.zones, now=100.1)
        people = self.tracker.update(detection, self.zones, now=106.0)
        self.assertGreater(people[0]["danger_duration"], 5.0)
        self.assertEqual(self.engine.evaluate_person(people[0], False)["risk"], 90)

    def test_each_polygon_has_its_own_hysteresis_counters(self):
        # Walking from DANGER_1 to DANGER_2 must not carry DANGER_1's streaks.
        for index in range(4):
            self.tracker.update([box_at(600, 400)], self.zones,
                                now=200.0 + index * 0.1)
        people = None
        for index in range(config.ENTER_CONFIRM_FRAMES + 1):
            people = self.tracker.update([box_at(1000, 400)], self.zones,
                                         now=210.0 + index * 0.1)
        self.assertEqual(people[0]["zone_id"], "DANGER_2")

    def test_a_hand_in_the_second_machine_is_a_reach_in(self):
        detection = [box_at(*PT_SAFE)]
        people = settle(self.tracker, self.zones, detection)
        for index in range(config.HAND_ENTER_CONFIRM_FRAMES):
            people = self.tracker.update(detection, self.zones,
                                         now=300.0 + index * 0.1)
            self.tracker.update_hands(people, {people[0]["id"]: [(1000, 400)]},
                                      self.zones)
        self.assertEqual(people[0]["zone"], config.ZONE_SAFE)
        self.assertTrue(people[0]["hand_in_danger"])
        self.assertEqual(people[0]["hand_zone_id"], "DANGER_2")
        self.assertEqual(self.engine.evaluate_person(people[0], False)["risk"], 90)


# ===========================================================================
# The JSON-safety boundary (Spring Boot must never receive a Python object)
# ===========================================================================

class TestJsonSafe(unittest.TestCase):

    def test_primitives_pass_through(self):
        for value in (None, True, 3, 4.5, "text"):
            self.assertEqual(json_safe(value), value)

    def test_numpy_scalars_become_plain_numbers(self):
        converted = json_safe({"a": np.float32(1.5), "b": np.int64(7)})
        self.assertEqual(converted, {"a": 1.5, "b": 7})
        for value in converted.values():
            self.assertEqual(type(value).__module__, "builtins")

    def test_numpy_arrays_and_tuples_become_lists(self):
        self.assertEqual(json_safe(np.array([[1, 2], [3, 4]])), [[1, 2], [3, 4]])
        self.assertEqual(json_safe(((1, 2), (3, 4))), [[1, 2], [3, 4]])

    def test_unknown_objects_are_stringified_rather_than_crashing(self):
        class Opaque:
            def __repr__(self):
                return "<Opaque>"

        self.assertEqual(json_safe({"o": Opaque()}), {"o": "<Opaque>"})

    def test_the_result_is_always_serialisable(self):
        payload = {"box": np.array([1, 2, 3]), "conf": np.float32(0.9),
                   "pt": (1.0, 2.0), "tags": {"a", "b"}}
        json.dumps(json_safe(payload))   # must not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
