"""
src/risk_engine.py
------------------
The RULE ENGINE. This file decides how dangerous a situation is.

THE ARCHITECTURAL RULE THAT MATTERS MOST
    The vision models measure physical facts: where the feet are, where the
    wrist is, how many pixels to the machine.

    THIS file decides what those facts mean for safety.

    No model, no LLM and no learned classifier is allowed anywhere near a basic
    safety decision. Every number produced here comes from an explicit,
    readable threshold in config.py.

DIVISION OF LABOUR
    zone.py         "WHERE is the person?"           -> SAFE / WARNING / DANGER
    tracker.py      "is that stable, and for how long?"
    risk_engine.py  "HOW DANGEROUS is that?"         -> risk + reasons

HOW THE SCORE IS COMBINED
    Every rule is evaluated independently. Each one that applies contributes a
    score and a human-readable reason. The final score is the MAXIMUM:

        risk = max(applicable_rules)

    It is never an overwrite. A worker standing in DANGER (70) who has been
    there 7 seconds (90) while the machine runs (100) scores 100 and reports all
    three reasons -- the earlier rules are not lost, they are outranked.

    Combining by max (rather than by adding) means the score can never drift
    above 100 as we add more rules, and the worst single fact always sets the
    headline number.
"""


class RiskEngine:
    """
    Stateless, deterministic risk calculator.

    Everything time-dependent is measured by tracker.py and handed in as a
    plain number or bool.
    """

    def __init__(self, cfg=None, zones=None):
        """
        Initialize the RiskEngine.

        zones is optional because the existing main.py currently creates the
        engine using:

            self.risk_engine = RiskEngine(cfg)

        The ZoneManager can be assigned later through:

            self.risk_engine.zones = self.zones
        """

        if cfg is None:
            import config as cfg

        self.cfg = cfg

        # ZoneManager reference.
        #
        # This can be assigned later by main.py.
        self.zones = zones

    # -- main entry point ----------------------------------------------------

    def calculate_risk(
        self,
        zone,
        danger_duration=0.0,
        machine_on=False,
        hand_in_danger=False,
        distance_to_danger=None,
        hand_zone=None,
    ):
        """
        Evaluate every rule and return the combined verdict.

            zone                "SAFE" / "WARNING" / "DANGER" / "OUTSIDE"
                                the DEBOUNCED body zone, from the foot point

            hand_zone           the DEBOUNCED hand zone, from the wrists.
                                INDEPENDENT of `zone`

            danger_duration     seconds the BODY has been in DANGER

            machine_on          simulated machine state

            hand_in_danger      legacy bool, equivalent to hand_zone="DANGER"

            distance_to_danger  px from foot point to the DANGER edge, or None

        Returns:
            {
                "risk":       0..100,
                "level":      "SAFE" / "LOW" / "WARNING" / "HIGH" / "CRITICAL",
                "reasons":    [...],
                "conditions": [...]
            }
        """

        cfg = self.cfg

        fired = []

        in_danger = zone == cfg.ZONE_DANGER

        # The bool is the old single-signal API; the zone string supersedes it.
        if hand_zone is None and hand_in_danger:
            hand_zone = cfg.ZONE_DANGER

        # -- RULE 1-3: base risk of simply being in a zone -------------------

        if zone == cfg.ZONE_SAFE:
            fired.append(
                (
                    cfg.ZONE_RISK[cfg.ZONE_SAFE],
                    "Worker is inside Safe Zone",
                    "BODY_IN_SAFE",
                )
            )

        elif zone == cfg.ZONE_WARNING:
            fired.append(
                (
                    cfg.ZONE_RISK[cfg.ZONE_WARNING],
                    "Worker is inside Warning Zone",
                    "BODY_IN_WARNING",
                )
            )

        elif in_danger:
            fired.append(
                (
                    cfg.ZONE_RISK[cfg.ZONE_DANGER],
                    "Worker is inside Danger Zone",
                    "BODY_IN_DANGER",
                )
            )

        # -- RULE 4: lingering in the danger zone ----------------------------

        dwell_exceeded = (
            in_danger
            and danger_duration >= cfg.DANGER_TIME_THRESHOLD_SEC
        )

        if dwell_exceeded:
            fired.append(
                (
                    cfg.RISK_DANGER_TIMEOUT,
                    "Worker has remained in Danger Zone for {:.1f} seconds "
                    "(limit {:.0f}s)".format(
                        danger_duration,
                        cfg.DANGER_TIME_THRESHOLD_SEC,
                    ),
                    "DANGER_TIMEOUT",
                )
            )

        # -- RULE 5: body in danger while the machine runs -------------------

        if in_danger and machine_on:
            fired.append(
                (
                    cfg.RISK_DANGER_MACHINE_ON,
                    "Machine is ON",
                    "MACHINE_ON",
                )
            )

        # -- RULE 6: hand in the warning zone --------------------------------

        if hand_zone == cfg.ZONE_WARNING:
            fired.append(
                (
                    cfg.RISK_HAND_IN_WARNING,
                    "Worker hand is inside Warning Zone",
                    "HAND_IN_WARNING",
                )
            )

        # -- RULE 7-8: reach-in (wrist inside danger) ------------------------

        elif hand_zone == cfg.ZONE_DANGER:

            if in_danger:
                # Still worth stating, and recorded as a condition.
                fired.append(
                    (
                        0,
                        "Worker hand is inside Danger Zone",
                        "HAND_IN_DANGER",
                    )
                )

            else:
                fired.append(
                    (
                        cfg.RISK_HAND_IN_DANGER,
                        "Worker hand is inside Danger Zone",
                        "HAND_IN_DANGER",
                    )
                )

                if machine_on:
                    fired.append(
                        (
                            cfg.RISK_HAND_MACHINE_ON,
                            "Machine is ON",
                            "MACHINE_ON",
                        )
                    )

        # -- RULE 8: proximity to the danger zone ----------------------------

        if distance_to_danger is not None and not in_danger:

            if distance_to_danger <= cfg.DANGER_DISTANCE_PX:
                fired.append(
                    (
                        cfg.RISK_NEAR_DANGER,
                        "Worker is very close to the Danger Zone "
                        "({:.0f} px, image distance)".format(
                            distance_to_danger
                        ),
                        "NEAR_DANGER",
                    )
                )

            elif distance_to_danger <= cfg.WARNING_DISTANCE_PX:
                fired.append(
                    (
                        cfg.RISK_NEAR_WARNING,
                        "Worker is approaching the Danger Zone "
                        "({:.0f} px, image distance)".format(
                            distance_to_danger
                        ),
                        "NEAR_WARNING",
                    )
                )

        # -- combine ---------------------------------------------------------

        risk = max(
            (score for score, _, _ in fired),
            default=0,
        )

        return {
            "risk": int(risk),
            "level": self.risk_level(risk),
            "reasons": _dedupe(
                [reason for _, reason, _ in fired]
            ),
            "conditions": _dedupe(
                [cond for _, _, cond in fired]
            ),
        }

    # -- helpers -------------------------------------------------------------

    def risk_level(self, risk):
        """Map a numeric score onto its label using cfg.RISK_LEVELS."""

        for threshold, label in self.cfg.RISK_LEVELS:
            if risk >= threshold:
                return label

        return "SAFE"

    def is_critical(self, risk):
        """True when this score should flash the screen."""

        return risk >= self.cfg.RISK_CRITICAL_THRESHOLD

    # -- person-level evaluation --------------------------------------------

    def evaluate_person(self, person, machine_on):
        """
        Evaluate risk for a person using the existing ZoneManager.

        ZoneManager is responsible for determining the body zone using the
        person's feet point.

        RiskEngine then applies the safety rules.
        """

        # --------------------------------------------------------------
        # Make sure ZoneManager is available
        # --------------------------------------------------------------

        if self.zones is None:
            raise RuntimeError(
                "RiskEngine.zones is not configured. "
                "Assign the existing ZoneManager before calling "
                "evaluate_person()."
            )

        # --------------------------------------------------------------
        # Get person's feet point
        # --------------------------------------------------------------

        feet = person.get("feet")

        zone = self.cfg.ZONE_OUTSIDE
        trigger_point = "feet"
        weight = 1.0

        if feet is not None:
            try:
                x, y = feet
                feet_point = (float(x), float(y))

                # Use the REAL ZoneManager API from zone.py
                detected_zone = self.zones.classify_zone(feet_point)

                if detected_zone is not None:
                    zone = detected_zone.zone_type

            except (TypeError, ValueError):
                zone = self.cfg.ZONE_OUTSIDE

        # --------------------------------------------------------------
        # Store zone information on person
        # --------------------------------------------------------------

        person["zone"] = zone
        person["trigger_point"] = trigger_point
        person["entry_weight"] = weight

        # --------------------------------------------------------------
        # Check if head/feet are in danger specifically
        # --------------------------------------------------------------

        body_points = person.get("body_points", {})

        head_in_danger = False
        feet_in_danger = False

        if body_points:

            head = body_points.get("head")
            feet_center = body_points.get("feet_center")

            # Your ZoneManager API is:
            #
            # self.zones.contains("DANGER", point)
            #
            # NOT:
            #
            # self.zones.contains_point(point, "DANGER")

            if head is not None:
                try:
                    head_in_danger = self.zones.contains(
                        self.cfg.ZONE_DANGER,
                        head,
                    )
                except (TypeError, ValueError):
                    head_in_danger = False

            if feet_center is not None:
                try:
                    feet_in_danger = self.zones.contains(
                        self.cfg.ZONE_DANGER,
                        feet_center,
                    )
                except (TypeError, ValueError):
                    feet_in_danger = False

        # --------------------------------------------------------------
        # Store body danger state
        # --------------------------------------------------------------

        person["head_in_danger"] = head_in_danger
        person["feet_in_danger"] = feet_in_danger

        # --------------------------------------------------------------
        # Get warning duration
        # --------------------------------------------------------------

        warning_duration = (
            person.get("warning_duration", 0.0) or 0.0
        )

        # --------------------------------------------------------------
        # Calculate risk based on zone
        # --------------------------------------------------------------

        if zone == self.cfg.ZONE_DANGER:

            risk = 70

            if machine_on:
                risk = 100

            if (
                person.get("danger_duration", 0)
                > self.cfg.DANGER_TIME_THRESHOLD_SEC
            ):
                risk = max(risk, 90)

            if head_in_danger and not feet_in_danger:
                risk = min(risk + 10, 100)

        elif zone == self.cfg.ZONE_WARNING:

            # Base warning risk
            risk = 30

            # Warning timeout escalation
            #
            # FIX:
            # Use the existing configuration variable:
            # WARNING_DWELL_THRESHOLD_SEC
            #
            # WARNING_ALARM_THRESHOLD_SEC does not exist in config.py.
            if (
                warning_duration
                >= self.cfg.WARNING_DWELL_THRESHOLD_SEC
            ):

                risk = 70

                if machine_on:
                    risk = 90

            # Head in danger
            if head_in_danger and not feet_in_danger:
                risk = min(risk + 10, 100)

        else:

            risk = 0

        # --------------------------------------------------------------
        # Build reasons
        # --------------------------------------------------------------

        reasons = []

        if zone == self.cfg.ZONE_DANGER:

            reasons.append(
                "Person is in DANGER zone "
                f"(trigger: {trigger_point})"
            )

            if machine_on:
                reasons.append("Machine is ON")

            if head_in_danger and not feet_in_danger:
                reasons.append(
                    "Head is in DANGER zone (leaning in!)"
                )

        elif zone == self.cfg.ZONE_WARNING:

            reasons.append(
                "Person is in WARNING zone "
                f"(trigger: {trigger_point})"
            )

            if (
                warning_duration
                >= self.cfg.WARNING_DWELL_THRESHOLD_SEC
            ):

                reasons.append(
                    f"Person has been in WARNING for "
                    f"{warning_duration:.1f} seconds (ALARM!)"
                )

                reasons.append(
                    "WARNING duration exceeded "
                    f"{self.cfg.WARNING_DWELL_THRESHOLD_SEC:.0f} seconds"
                )

            if machine_on:
                reasons.append("Machine is ON")

        elif zone == self.cfg.ZONE_SAFE:

            reasons.append("Person is in SAFE zone")

        else:

            reasons.append(
                "Person is OUTSIDE all zones"
            )

        # --------------------------------------------------------------
        # Return result
        # --------------------------------------------------------------

        return {
            "risk": risk,
            "level": self.risk_level(risk),
            "reasons": reasons,
            "zone": zone,
            "trigger_point": trigger_point,
            "head_in_danger": head_in_danger,
            "feet_in_danger": feet_in_danger,
            "warning_duration": warning_duration,
        }


# ---------------------------------------------------------------------------
# module-level convenience
# ---------------------------------------------------------------------------

_default_engine = None


def calculate_risk(
    zone,
    danger_duration=0.0,
    machine_on=False,
    hand_in_danger=False,
    distance_to_danger=None,
    hand_zone=None,
):
    """
    Same as RiskEngine().calculate_risk, using the project's config.py.

    Handy for a quick check in the REPL. The application builds one RiskEngine
    and reuses it, so its thresholds can be swapped in tests.
    """

    global _default_engine

    if _default_engine is None:
        _default_engine = RiskEngine()

    return _default_engine.calculate_risk(
        zone,
        danger_duration,
        machine_on,
        hand_in_danger,
        distance_to_danger,
        hand_zone,
    )