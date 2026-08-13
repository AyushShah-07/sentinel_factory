"""
src/risk_engine.py
------------------
The RULE ENGINE. This file decides how dangerous a situation is.

THE ARCHITECTURAL RULE THAT MATTERS MOST
    The vision models measure physical facts:  where the feet are, where the
    wrist is, how many pixels to the machine.
    THIS file decides what those facts mean for safety.

    No model, no LLM and no learned classifier is allowed anywhere near a basic
    safety decision. Every number produced here comes from an explicit, readable
    threshold in config.py. That is what makes the behaviour auditable: if the
    system alarms, you can point at the exact line that fired.

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

    Stateless is deliberate: everything time-dependent (how long a person has
    stood in the danger zone, whether a wrist reading has been confirmed) is
    measured by tracker.py and handed in as a plain number or bool. That keeps
    this file trivially testable -- no camera, no model, no clock.
    """

    def __init__(self, cfg=None):
        if cfg is None:
            import config as cfg  # default to the project config
        self.cfg = cfg

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
                                the DEBOUNCED **body** zone, from the foot point
            hand_zone           the DEBOUNCED **hand** zone, from the wrists.
                                INDEPENDENT of `zone`: a worker can stand in SAFE
                                and reach into DANGER, and both stay true.
            danger_duration     seconds the BODY has been in DANGER (0.0 if not)
            machine_on          simulated machine state
            hand_in_danger      legacy bool, equivalent to hand_zone="DANGER"
            distance_to_danger  px from foot point to the DANGER edge, or None

        Returns:
            {
                "risk":       0..100,
                "level":      "SAFE" / "LOW" / "WARNING" / "HIGH" / "CRITICAL",
                "reasons":    ["Worker is inside Danger Zone", ...],
                "conditions": ["BODY_IN_DANGER", "MACHINE_ON", ...],
            }

        `reasons` is for humans and for the dashboard. `conditions` is the
        machine-readable version of the same facts, which the Phase 3 backend
        and event store can filter on without parsing English.
        """
        cfg = self.cfg
        fired = []  # (score, reason, condition) for every rule that applies

        in_danger = zone == cfg.ZONE_DANGER

        # The bool is the old single-signal API; the zone string supersedes it.
        if hand_zone is None and hand_in_danger:
            hand_zone = cfg.ZONE_DANGER

        # -- RULE 1-3: base risk of simply being in a zone -------------------
        # The zone itself is the primary signal; everything else escalates it.
        if zone == cfg.ZONE_SAFE:
            fired.append((cfg.ZONE_RISK[cfg.ZONE_SAFE],
                          "Worker is inside Safe Zone",
                          "BODY_IN_SAFE"))
        elif zone == cfg.ZONE_WARNING:
            fired.append((cfg.ZONE_RISK[cfg.ZONE_WARNING],
                          "Worker is inside Warning Zone",
                          "BODY_IN_WARNING"))
        elif in_danger:
            fired.append((cfg.ZONE_RISK[cfg.ZONE_DANGER],
                          "Worker is inside Danger Zone",
                          "BODY_IN_DANGER"))

        # -- RULE 4: lingering in the danger zone ----------------------------
        # Passing through a danger zone is bad. Standing in it is worse, because
        # it means the worker is not simply crossing -- they are working there.
        # >= so that a dwell of exactly the threshold escalates: "5.0+ sec -> 90".
        dwell_exceeded = (
            in_danger and danger_duration >= cfg.DANGER_TIME_THRESHOLD_SEC
        )
        if dwell_exceeded:
            fired.append((
                cfg.RISK_DANGER_TIMEOUT,
                "Worker has remained in Danger Zone for {:.1f} seconds "
                "(limit {:.0f}s)".format(danger_duration,
                                         cfg.DANGER_TIME_THRESHOLD_SEC),
                "DANGER_TIMEOUT",
            ))

        # -- RULE 5: body in danger while the machine runs -------------------
        # The machine being ON is what turns proximity into injury, so this is
        # the top of the scale.
        if in_danger and machine_on:
            fired.append((cfg.RISK_DANGER_MACHINE_ON,
                          "Machine is ON",
                          "MACHINE_ON"))

        # -- RULE 6: hand in the warning zone --------------------------------
        # The hand zone is scored independently of the body zone, so a worker
        # standing safely on the walkway who reaches towards the machine is no
        # longer reported as risk 0.
        if hand_zone == cfg.ZONE_WARNING:
            fired.append((cfg.RISK_HAND_IN_WARNING,
                          "Worker hand is inside Warning Zone",
                          "HAND_IN_WARNING"))

        # -- RULE 7-8: reach-in (wrist inside danger) ------------------------
        # Scored high even when the feet are safe: a hand in a running machine
        # is an injury regardless of where the worker is standing.
        #
        # BUT ONLY WHEN THE BODY IS NOT ALREADY IN THE DANGER ZONE. A worker
        # standing inside the danger zone necessarily has their hands in it too,
        # so firing the reach-in rule there would jump straight to 90 the moment
        # they stepped in -- which is exactly the bug this guard fixes. Inside
        # the danger zone the body rules govern, and escalation comes from the
        # dwell timer (RULE 4) or the machine (RULE 5).
        #
        # A reach-in is, by definition, the body being OUTSIDE and the hand IN.
        elif hand_zone == cfg.ZONE_DANGER:
            if in_danger:
                # Still worth stating, and still recorded as a condition for the
                # event log -- it simply does not raise the number any further.
                fired.append((0,
                              "Worker hand is inside Danger Zone",
                              "HAND_IN_DANGER"))
            else:
                fired.append((cfg.RISK_HAND_IN_DANGER,
                              "Worker hand is inside Danger Zone",
                              "HAND_IN_DANGER"))
                if machine_on:
                    fired.append((cfg.RISK_HAND_MACHINE_ON,
                                  "Machine is ON",
                                  "MACHINE_ON"))

        # -- RULE 8: proximity to the danger zone ----------------------------
        # A DELIBERATELY WEAK SIGNAL. This is an uncalibrated pixel distance,
        # not metres, so it must not be able to raise a critical alarm by
        # itself -- see the note on RISK_NEAR_* in config.py. It only applies
        # when the body is not already in the danger zone, where "distance 0"
        # would otherwise double-count what RULE 3 already said.
        if distance_to_danger is not None and not in_danger:
            if distance_to_danger <= cfg.DANGER_DISTANCE_PX:
                fired.append((
                    cfg.RISK_NEAR_DANGER,
                    "Worker is very close to the Danger Zone "
                    "({:.0f} px, image distance)".format(distance_to_danger),
                    "NEAR_DANGER",
                ))
            elif distance_to_danger <= cfg.WARNING_DISTANCE_PX:
                fired.append((
                    cfg.RISK_NEAR_WARNING,
                    "Worker is approaching the Danger Zone "
                    "({:.0f} px, image distance)".format(distance_to_danger),
                    "NEAR_WARNING",
                ))

        # -- combine ---------------------------------------------------------
        risk = max((score for score, _, _ in fired), default=0)

        return {
            "risk": int(risk),
            "level": self.risk_level(risk),
            "reasons": _dedupe([reason for _, reason, _ in fired]),
            "conditions": _dedupe([cond for _, _, cond in fired]),
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

    def evaluate_person(self, person, machine_on):
        """
        Convenience wrapper: read the fields a tracker.py person dict already
        carries and return the verdict. Keeps main.py's loop to one line.
        """
        return self.calculate_risk(
            zone=person.get("zone"),
            danger_duration=person.get("danger_duration", 0.0),
            machine_on=machine_on,
            hand_in_danger=person.get("hand_in_danger", False),
            distance_to_danger=person.get("distance_to_danger"),
            hand_zone=person.get("hand_zone"),
        )


def _dedupe(items):
    """Drop duplicates but keep the original order (reasons read as a list)."""
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# module-level convenience
# ---------------------------------------------------------------------------

_default_engine = None


def calculate_risk(zone, danger_duration=0.0, machine_on=False,
                   hand_in_danger=False, distance_to_danger=None,
                   hand_zone=None):
    """
    Same as RiskEngine().calculate_risk, using the project's config.py.

    Handy for a quick check in the REPL. The application builds one RiskEngine
    and reuses it, so its thresholds can be swapped in tests.
    """
    global _default_engine
    if _default_engine is None:
        _default_engine = RiskEngine()
    return _default_engine.calculate_risk(
        zone, danger_duration, machine_on, hand_in_danger, distance_to_danger,
        hand_zone,
    )
