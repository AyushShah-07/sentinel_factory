"""
src/ui.py
---------
All on-screen drawing except the zone polygon itself (which lives in zone.py).
 
Responsibilities:
  * bounding boxes + labels for each person
  * the bottom-center "feet" marker that the decision is actually based on
  * the HUD: branding, people counts, status banner, FPS
  * the flashing red alert border
  * the keyboard help bar
  * full-screen error / message overlays
 
Everything here is a plain function taking a frame and drawing on it in place.
No state, no classes -- easy to read and easy to change.
 
NOTE ON TEXT: OpenCV's putText only supports its own ASCII Hershey fonts.
Emoji such as the warning sign or check mark render as empty boxes, so the
status strings in config.py use ASCII markers like [!] and [OK] instead.
"""
 
import math
import time
 
import cv2
 
FONT = cv2.FONT_HERSHEY_SIMPLEX
FONT_BOLD = cv2.FONT_HERSHEY_DUPLEX
 
# Geometry of the top-left HUD panel, shared so that other overlays (notably
# the big alert banner) can position themselves clear of it instead of
# guessing and overlapping.
# Phase 2 grew the panel: machine state, per-zone counts, and the global
# HIGHEST RISK / CRITICAL summary.
HUD_X, HUD_Y, HUD_W, HUD_H = 12, 12, 340, 268
 
# The panel is sized for a 1280-wide feed. On a 640x480 webcam a fixed 340 px
# panel would swallow half the picture, so everything in the HUD is multiplied
# by this factor. It never grows above 1.0 and never shrinks below 0.62, which
# keeps the text legible.
HUD_REFERENCE_WIDTH = 1280.0
HUD_MIN_SCALE = 0.62
 
 
def hud_scale(frame_width):
    """How much to shrink the HUD for this frame width."""
    return max(HUD_MIN_SCALE, min(1.0, frame_width / HUD_REFERENCE_WIDTH))
 
 
def hud_bottom(frame_width):
    """Y coordinate just under the HUD panel, so overlays can avoid it."""
    return HUD_Y + int(HUD_H * hud_scale(frame_width))
 
 
def person_tag(person):
    """
    How a person is named on screen: "P1".
 
    THE ONE PLACE the display name is resolved, so the boxes, the cards, the
    worker panel, the reason panel and the ticker can never disagree. Accepts a
    tracker person dict, an event's person block, a bare "P1" string, or a raw
    integer track id.
 
    When face recognition arrives it fills identity["person_name"], and this is
    the single function that has to start preferring it.
    """
    if isinstance(person, dict):
        identity = person.get("identity") or {}
        tag = identity.get("person_id") or person.get("person_id")
        if tag:
            return str(tag)
        raw = person.get("id")
        return "P{}".format(raw) if raw is not None else "?"
    if isinstance(person, str):
        return person
    if person is None:
        return "?"
    return "P{}".format(person)
 
 
# ---------------------------------------------------------------------------
# small drawing primitives
# ---------------------------------------------------------------------------
 
def draw_translucent_rect(frame, top_left, bottom_right, color, alpha):
    """
    Blend a filled rectangle onto the frame.
 
    OpenCV frames have no alpha channel, so transparency is done by drawing on
    a copy and then mixing the copy back in with cv2.addWeighted.
    """
    x1, y1 = top_left
    x2, y2 = bottom_right
    height, width = frame.shape[:2]
 
    x1 = max(0, min(int(x1), width))
    y1 = max(0, min(int(y1), height))
    x2 = max(0, min(int(x2), width))
    y2 = max(0, min(int(y2), height))
    if x2 <= x1 or y2 <= y1:
        return
 
    region = frame[y1:y2, x1:x2]
    patch = region.copy()
    patch[:] = color
    cv2.addWeighted(patch, alpha, region, 1.0 - alpha, 0, region)
 
 
def draw_text(frame, text, origin, scale=0.55, color=(255, 255, 255), thickness=1,
              font=FONT, shadow=True):
    """Draw text with a subtle dark shadow so it stays legible on any background."""
    if shadow:
        cv2.putText(frame, text, (origin[0] + 1, origin[1] + 1), font, scale,
                    (0, 0, 0), thickness + 1, cv2.LINE_AA)
    cv2.putText(frame, text, origin, font, scale, color, thickness, cv2.LINE_AA)
 
 
def draw_label_box(frame, text, anchor, color, scale=0.5, thickness=1, above=True):
    """
    Draw a filled colour tag with dark text, anchored to a bounding box corner.
    Flips below the anchor automatically when there is no room above it.
    """
    (text_w, text_h), baseline = cv2.getTextSize(text, FONT, scale, thickness)
    pad_x, pad_y = 6, 5
    box_w = text_w + pad_x * 2
    box_h = text_h + baseline + pad_y
 
    x = anchor[0]
    y = anchor[1] - box_h if above else anchor[1]
    if y < 0:                       # not enough room above -> put it below
        y = anchor[1]
    if x + box_w > frame.shape[1]:  # keep it inside the right edge
        x = max(0, frame.shape[1] - box_w)
 
    cv2.rectangle(frame, (x, y), (x + box_w, y + box_h), color, -1)
    cv2.putText(frame, text, (x + pad_x, y + box_h - baseline - 1),
                FONT, scale, (20, 20, 20), thickness, cv2.LINE_AA)
 
 
# ---------------------------------------------------------------------------
# people
# ---------------------------------------------------------------------------
 
def draw_people(frame, people, cfg, risks=None):
    """
    Draw every detected person, coloured by the zone their FEET are in.
 
    SAFE / outside -> thin green (or grey) box, "ID 1  0.87"
    WARNING        -> amber box,               "ID 2  WARNING"
    DANGER         -> thick red box + corner brackets, "ID 3  DANGER 6.2s"
    Reach-in       -> "+HAND" appended, and the wrist marker turns red
 
    The small filled circle at the bottom-center of each box is the point the
    body-zone decision is actually made on -- see utils/geometry.py. The wrist
    markers are a completely separate signal; a person can be green-boxed with a
    red wrist, which is exactly the reach-in case.
    """
    for person in people:
        x1, y1, x2, y2 = person["bbox"]
        zone = person.get("zone")
        in_danger = person.get("is_inside", False)
        hand_violation = person.get("hand_in_danger", False)
 
        # Fall back to the Phase 1 colours if a caller hands us a person dict
        # without a zone (e.g. tracking disabled before zones are drawn).
        default_color = cfg.COLOR_ALERT if in_danger else cfg.COLOR_SAFE
        color = cfg.PERSON_COLORS.get(zone, default_color)
 
        # Anything that needs the operator's attention gets the heavy treatment.
        alarmed = in_danger or hand_violation
        thickness = cfg.BOX_THICKNESS_ALERT if alarmed else cfg.BOX_THICKNESS_SAFE
 
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
        if alarmed:
            _draw_corner_brackets(frame, (x1, y1, x2, y2), color)
 
        label = _person_label(person, cfg, zone, in_danger, hand_violation)
        draw_label_box(frame, label, (x1, y1), color)
 
        # The feet / ground-contact marker.
        # Handle both old format (single point) and new format (list of points)
        feet = person.get("feet", [])
        if feet:
            if isinstance(feet[0], (list, tuple)):
                # New format: list of (x,y) tuples - use center of both feet
                feet_x = int((feet[0][0] + feet[-1][0]) / 2)
                feet_y = int((feet[0][1] + feet[-1][1]) / 2)
            else:
                # Old format: single point
                feet_x, feet_y = int(feet[0]), int(feet[1])
        else:
            # Fallback: bottom center of bbox
            feet_x = int((x1 + x2) / 2)
            feet_y = y2
 
        if cfg.SHOW_FEET_GUIDE:
            center_x = (x1 + x2) // 2
            cv2.line(frame, (center_x, y2 - 18), (feet_x, feet_y),
                     color, 1, cv2.LINE_AA)
        cv2.circle(frame, (feet_x, feet_y), cfg.FEET_MARKER_RADIUS + 2,
                   (20, 20, 20), -1, cv2.LINE_AA)
        cv2.circle(frame, (feet_x, feet_y), cfg.FEET_MARKER_RADIUS,
                   color if in_danger else cfg.COLOR_POINT, -1, cv2.LINE_AA)
 
        draw_wrists(frame, person, cfg)
 
    # The per-person cards are drawn only when `risks` is supplied, so a single
    # draw_people() call still produces a complete picture for tests and for
    # anyone reusing this function. main.py passes None here and calls
    # draw_person_cards() last instead, so the cards land on TOP of the HUD and
    # the side panels -- a worker standing in a corner must never have their own
    # risk hidden behind a panel.
    if risks is not None:
        draw_person_cards(frame, people, cfg, risks)
 
 
def draw_person_cards(frame, people, cfg, risks):
    """
    Draw every person's info card. Separate from draw_people() purely so the
    cards can be layered last; see the note above.
    """
    if not cfg.SHOW_PERSON_CARD:
        return
    for person in people:
        zone = person.get("zone")
        default_color = (cfg.COLOR_ALERT if person.get("is_inside")
                         else cfg.COLOR_SAFE)
        accent = cfg.PERSON_COLORS.get(zone, default_color)
        draw_person_card(frame, cfg, person,
                         (risks or {}).get(person["id"], {}), accent)
 
 
def _person_label(person, cfg, zone, in_danger, hand_violation):

    """The short tag above a person's box."""

    tag = person_tag(person)

    if in_danger:

        duration = person.get("danger_duration", 0.0) or 0.0

        text = "{}  DANGER".format(tag)

        if duration > 0:

            text += " {:.1f}s".format(duration)

    elif zone == cfg.ZONE_WARNING:

        # ========== ADD THIS: Show warning duration ==========

        warning_duration = person.get("warning_duration", 0.0) or 0.0

        text = "{}  WARNING".format(tag)

        if warning_duration > 0:

            text += " {:.1f}s".format(warning_duration)

            if warning_duration >= cfg.WARNING_ALARM_THRESHOLD_SEC:

                text += " ⚠️"  # Show alarm indicator

    elif zone == cfg.ZONE_SAFE:

        text = "{}  SAFE".format(tag)

    else:

        text = "{}  {:.2f}".format(tag, person.get("confidence", 0.0))
 
    if hand_violation:

        text += "  +HAND"

    return text
 
 
def draw_person_card(frame, cfg, person, verdict, accent):
    """
    Small information card pinned to one person's bounding box:
 
        P1
        Zone: DANGER_1
        Danger: 3.7 sec
        Risk: 70
 
    When the hand is in a different zone from the body, the card splits the two
    apart rather than collapsing them, because that difference IS the reach-in
    case and the operator needs to see both:
 
        P4
        Body: SAFE_1
        Hand: DANGER_1
        Risk: 90
 
    Anchored to the right of the box, flipping to the left when there is no room,
    so a worker at the frame edge never has their card clipped off-screen.
    """
    zone = person.get("zone") or cfg.ZONE_OUTSIDE
    hand_zone = person.get("hand_zone")
    duration = person.get("danger_duration", 0.0) or 0.0
    risk = verdict.get("risk", 0)
    level = verdict.get("level", "")
 
    # The zone TAG when we know which polygon it was ("DANGER_1"), otherwise the
    # bare type. The tag carries the type as its prefix, so nothing is lost.
    zone_text = person.get("zone_id") or zone
    hand_text = person.get("hand_zone_id") or hand_zone
 
    lines = [(person_tag(person), cfg.COLOR_TEXT)]
 
    # Only split body/hand when they actually differ -- otherwise "Zone:" is
    # less noisy and reads faster.
    if hand_zone is not None and hand_zone != zone:
        lines.append(("Body: {}".format(zone_text),
                      cfg.PERSON_COLORS.get(zone, cfg.COLOR_TEXT)))
        lines.append(("Hand: {}".format(hand_text),
                      cfg.PERSON_COLORS.get(hand_zone, cfg.COLOR_MUTED)))
    else:
        lines.append(("Zone: {}".format(zone_text),
                      cfg.PERSON_COLORS.get(zone, cfg.COLOR_TEXT)))
 
    # The live danger timer, shown for every person who has one running.
    if duration > 0:
        over = duration >= cfg.DANGER_TIME_THRESHOLD_SEC
        lines.append(("Danger: {:.1f} sec".format(duration),
                      cfg.COLOR_ALERT if over else cfg.COLOR_ZONE_WARNING))
 
    risk_color = (cfg.COLOR_ALERT if risk >= cfg.RISK_CRITICAL_THRESHOLD
                  else cfg.PERSON_COLORS.get(zone, cfg.COLOR_TEXT) if risk
                  else cfg.COLOR_SAFE)
    risk_text = "Risk: {}".format(risk)
    if level and cfg.SHOW_LEVEL_ON_CARD:
        risk_text += "  {}".format(level)
    lines.append((risk_text, risk_color))
 
    scale = cfg.PERSON_CARD_SCALE
    line_h = int(18 * scale / 0.45)
    pad = 7
 
    widths = [cv2.getTextSize(text, FONT, scale, 1)[0][0] for text, _ in lines]
    box_w = max(widths) + pad * 2
    box_h = len(lines) * line_h + pad
 
    x1, y1, x2, y2 = person["bbox"]
    x = x2 + 8
    if x + box_w > frame.shape[1]:      # no room on the right -> go left
        x = x1 - box_w - 8
    x = max(0, min(x, frame.shape[1] - box_w))
    y = max(0, min(y1, frame.shape[0] - box_h))
 
    draw_translucent_rect(frame, (x, y), (x + box_w, y + box_h),
                          cfg.COLOR_PANEL, cfg.PERSON_CARD_ALPHA)
    cv2.rectangle(frame, (x, y), (x + box_w, y + box_h), accent, 1)
    cv2.rectangle(frame, (x, y), (x + 3, y + box_h), accent, -1)
 
    for index, (text, color) in enumerate(lines):
        draw_text(frame, text, (x + pad, y + pad + line_h * index + line_h - 6),
                  scale, color, 1)
 
 
def draw_wrists(frame, person, cfg):
    """
    Draw the wrist keypoints for one person.
 
    Pale marker normally, red ring when this person has a CONFIRMED reach-in
    violation. Drawn even when there is no violation, because seeing where the
    model thinks the wrists are is the fastest way to debug a missed alarm.
    """
    wrists = person.get("wrists") or []
    if not wrists:
        return
 
    violation = person.get("hand_in_danger", False)
    # Highlight only the wrist that is actually in the danger zone. Reddening
    # both hands when one is safely at the worker's side would point the
    # operator at the wrong thing.
    flags = person.get("wrist_flags") or []
    radius = cfg.WRIST_MARKER_RADIUS
 
    for index, wrist in enumerate(wrists):
        offending = flags[index] if index < len(flags) else violation
        color = cfg.COLOR_WRIST_ALERT if offending else cfg.COLOR_WRIST
        wx, wy = int(wrist[0]), int(wrist[1])
        cv2.circle(frame, (wx, wy), radius + 2, (20, 20, 20), -1, cv2.LINE_AA)
        cv2.circle(frame, (wx, wy), radius, color, -1, cv2.LINE_AA)
        if offending:
            # Extra ring + cross so a red wrist is unmistakable at a glance.
            cv2.circle(frame, (wx, wy), radius + 6, color, 2, cv2.LINE_AA)
            cv2.line(frame, (wx - radius - 9, wy), (wx + radius + 9, wy),
                     color, 1, cv2.LINE_AA)
            cv2.line(frame, (wx, wy - radius - 9), (wx, wy + radius + 9),
                     color, 1, cv2.LINE_AA)
 
 
def _draw_corner_brackets(frame, bbox, color, length_ratio=0.22, thickness=4):
    """Thick L-shaped corners: reads as 'targeted / alarmed' at a glance."""
    x1, y1, x2, y2 = bbox
    length = max(10, int(min(x2 - x1, y2 - y1) * length_ratio))
 
    for cx, cy, dx, dy in (
        (x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)
    ):
        cv2.line(frame, (cx, cy), (cx + dx * length, cy), color, thickness)
        cv2.line(frame, (cx, cy), (cx, cy + dy * length), color, thickness)
 
 
# ---------------------------------------------------------------------------
# HUD
# ---------------------------------------------------------------------------
 
def draw_hud(frame, cfg, people_count, inside_count, fps, status, zone_info,
             paused=False, machine_on=None, zone_counts=None, banner_text=None,
             highest_risk=None, critical=False):
    """
    Top-left information panel:
 
        SENTINEL FACTORY
        Smart Safety Perimeter
        ------------------------------
        Machine:                   ON
        People Detected:            3
        SAFE 1     WARN 1   DANGER 1
        FPS: 24         Zones: S W D
        [ STATUS BANNER ]
 
    `status` is one of "CRITICAL", "DANGER", "WARNING", "CLEAR", "NO_ZONE",
    "DEFINING", "PAUSED" (plus Phase 1's "ALERT", still accepted).
 
    machine_on / zone_counts are optional so the Phase 1 call signature keeps
    working; pass them to get the two new rows populated.
    """
    s = hud_scale(frame.shape[1])
    counts = zone_counts or {}
 
    panel_x, panel_y = HUD_X, HUD_Y
    panel_w, panel_h = int(HUD_W * s), int(HUD_H * s)
 
    def sy(offset):
        """Scale a vertical offset inside the panel."""
        return panel_y + int(offset * s)
 
    draw_translucent_rect(frame, (panel_x, panel_y),
                          (panel_x + panel_w, panel_y + panel_h),
                          cfg.COLOR_PANEL, cfg.PANEL_ALPHA)
 
    # Accent stripe down the left edge of the panel.
    stripe = (cfg.COLOR_ALERT if status in ("ALERT", "CRITICAL", "DANGER")
              else cfg.COLOR_ACCENT)
    cv2.rectangle(frame, (panel_x, panel_y), (panel_x + 4, panel_y + panel_h),
                  stripe, -1)
 
    text_x = panel_x + int(16 * s)
    value_x = panel_x + panel_w - int(42 * s)
 
    draw_text(frame, cfg.APP_TITLE, (text_x, sy(30)), 0.66 * s,
              cfg.COLOR_TEXT, 1, FONT_BOLD)
    draw_text(frame, cfg.APP_SUBTITLE, (text_x, sy(52)), 0.45 * s,
              cfg.COLOR_MUTED, 1)
 
    cv2.line(frame, (text_x, sy(62)), (panel_x + panel_w - int(16 * s), sy(62)),
             (90, 90, 90), 1)
 
    # -- machine state ------------------------------------------------------
    # Deliberately the first row: it is the difference between "a worker is
    # somewhere they should not be" and "a worker is about to be injured".
    draw_text(frame, "Machine:", (text_x, sy(86)), 0.5 * s, cfg.COLOR_MUTED)
    if machine_on is None:
        draw_text(frame, "--", (value_x - int(10 * s), sy(86)), 0.6 * s,
                  cfg.COLOR_MUTED, 1, FONT_BOLD)
    else:
        draw_text(frame, "ON" if machine_on else "OFF",
                  (value_x - int(10 * s), sy(86)), 0.6 * s,
                  cfg.COLOR_ALERT if machine_on else cfg.COLOR_SAFE, 1, FONT_BOLD)
 
    draw_text(frame, "People Detected:", (text_x, sy(110)), 0.5 * s, cfg.COLOR_MUTED)
    draw_text(frame, str(people_count), (value_x, sy(110)),
              0.6 * s, cfg.COLOR_TEXT, 1, FONT_BOLD)
 
    # -- per-zone tallies ---------------------------------------------------
    safe_n = counts.get(cfg.ZONE_SAFE, 0)
    warn_n = counts.get(cfg.ZONE_WARNING, 0)
    danger_n = counts.get(cfg.ZONE_DANGER, inside_count)
 
    column = panel_w - int(32 * s)
    for index, (label, value, color) in enumerate((
        ("SAFE", safe_n, cfg.COLOR_ZONE_SAFE),
        ("WARN", warn_n, cfg.COLOR_ZONE_WARNING),
        ("DANGER", danger_n, cfg.COLOR_ZONE_DANGER),
    )):
        cx = text_x + int(index * column / 3.0)
        draw_text(frame, label, (cx, sy(134)), 0.42 * s,
                  color if value else cfg.COLOR_MUTED)
        draw_text(frame, str(value), (cx + int(46 * s), sy(134)), 0.52 * s,
                  color if value else cfg.COLOR_MUTED, 1, FONT_BOLD)
 
    # -- global summary ------------------------------------------------------
    # HIGHEST RISK is the MAX across every tracked worker, never an arbitrary
    # one. Individual risks stay on each person's own card; this line answers
    # "does anything on this screen need me right now?".
    divider_y = sy(150)
    cv2.line(frame, (text_x, divider_y),
             (panel_x + panel_w - int(16 * s), divider_y), (90, 90, 90), 1)
 
    top_risk = 0 if highest_risk is None else int(highest_risk)
    risk_color = (cfg.COLOR_ALERT if top_risk >= cfg.RISK_CRITICAL_THRESHOLD
                  else cfg.COLOR_ZONE_WARNING if top_risk
                  else cfg.COLOR_SAFE)
 
    draw_text(frame, "HIGHEST RISK:", (text_x, sy(172)), 0.5 * s, cfg.COLOR_MUTED)
    draw_text(frame, str(top_risk), (value_x, sy(172)), 0.62 * s,
              risk_color, 1, FONT_BOLD)
 
    draw_text(frame, "CRITICAL:", (text_x, sy(194)), 0.5 * s, cfg.COLOR_MUTED)
    draw_text(frame, "YES" if critical else "NO",
              (value_x - int(12 * s), sy(194)), 0.6 * s,
              cfg.COLOR_ALERT if critical else cfg.COLOR_SAFE, 1, FONT_BOLD)
 
    draw_text(frame, "FPS: {:.0f}".format(fps), (text_x, sy(218)), 0.5 * s,
              cfg.COLOR_ACCENT)
    draw_text(frame, zone_info, (text_x + int(96 * s), sy(218)), 0.45 * s,
              cfg.COLOR_MUTED)
 
    _draw_status_banner(frame, cfg, status,
                        (text_x, sy(230)),
                        panel_w - int(32 * s), s, banner_text)
 
 
def _draw_status_banner(frame, cfg, status, top_left, width, s=1.0,
                        text_override=None):
    """The single most important line on screen: current safety state."""
    text, color = {
        # -- Phase 2 --
        "CRITICAL": (cfg.CRITICAL_BANNER_TEXT, cfg.COLOR_ALERT),
        "DANGER":   (cfg.DANGER_BANNER_TEXT,   cfg.COLOR_ZONE_DANGER),
        "WARNING":  (cfg.WARNING_BANNER_TEXT,  cfg.COLOR_ZONE_WARNING),
        "NO_ZONE":  (cfg.NO_ZONES_BANNER_TEXT, cfg.COLOR_ZONE),
        # -- Phase 1, still accepted --
        "ALERT":    (cfg.ALERT_BANNER_TEXT,    cfg.COLOR_ALERT),
        "CLEAR":    (cfg.CLEAR_BANNER_TEXT,    cfg.COLOR_SAFE),
        "DEFINING": (cfg.DEFINING_BANNER_TEXT, cfg.COLOR_ACCENT),
        "PAUSED":   (cfg.PAUSED_BANNER_TEXT,   cfg.COLOR_MUTED),
    }.get(status, ("UNKNOWN", cfg.COLOR_MUTED))
 
    if text_override:
        text = text_override
 
    x, y = top_left
    height = max(16, int(28 * s))
    cv2.rectangle(frame, (x, y), (x + width, y + height), color, -1)
 
    scale = 0.46 * s
    (text_w, _), _ = cv2.getTextSize(text, FONT_BOLD, scale, 1)
    # Shrink the text if the banner string is too long for the panel.
    while text_w > width - 12 and scale > 0.24:
        scale -= 0.02
        (text_w, _), _ = cv2.getTextSize(text, FONT_BOLD, scale, 1)
 
    cv2.putText(frame, text, (x + max(4, (width - text_w) // 2), y + int(height * 0.7)),
                FONT_BOLD, scale, (20, 20, 20), 1, cv2.LINE_AA)
 
 
def draw_alert_border(frame, cfg, text=None):
    """
    Flashing red frame border + a big banner across the top.
    This is the "impossible to miss" part of the warning.
    """
    # Pulse using wall-clock time, so the flash rate is independent of FPS.
    phase = math.sin(time.time() * cfg.ALERT_FLASH_HZ * 2 * math.pi)
    if phase < 0:
        return  # off half of the blink
 
    height, width = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (width - 1, height - 1), cfg.COLOR_ALERT, 14)
 
    banner = text or cfg.ALERT_TEXT
    thickness = 3
    scale = 1.5
 
    # Shrink the text until it fits the frame, so the banner is never clipped
    # on a small camera resolution.
    max_text_w = max(40, width - 80)
    (text_w, text_h), _ = cv2.getTextSize(banner, FONT_BOLD, scale, thickness)
    while text_w > max_text_w and scale > 0.4:
        scale -= 0.05
        (text_w, text_h), _ = cv2.getTextSize(banner, FONT_BOLD, scale, thickness)
 
    # Sit just below the HUD panel rather than on top of it.
    x = (width - text_w) // 2
    y = hud_bottom(width) + 24 + text_h
    y = min(y, height - 16)  # never fall off the bottom of a short frame
 
    draw_translucent_rect(frame, (x - 26, y - text_h - 18), (x + text_w + 26, y + 18),
                          cfg.COLOR_ALERT, 0.85)
    cv2.putText(frame, banner, (x, y), FONT_BOLD, scale, (255, 255, 255),
                thickness, cv2.LINE_AA)
 
 
def draw_help_bar(frame, cfg, defining=False):
    """Keyboard shortcuts along the bottom of the window."""
    if defining:
        text = ("LEFT CLICK add point   RIGHT CLICK undo   "
                "ENTER finish   C cancel   Q quit")
    else:
        text = ("S safe zone   W warning zone   D danger zone   "
                "M machine   C clear all   P pause   Q quit")
 
    height, width = frame.shape[:2]
    bar_h = 30
    draw_translucent_rect(frame, (0, height - bar_h), (width, height),
                          cfg.COLOR_PANEL, 0.72)
    draw_text(frame, text, (14, height - 10), 0.46, cfg.COLOR_MUTED, 1, shadow=False)
 
 
def draw_zone_message(frame, cfg, message):
    """Transient feedback line from the zone editor, drawn near the top-right."""
    if not message:
        return
    scale = 0.5
    (text_w, text_h), _ = cv2.getTextSize(message, FONT, scale, 1)
    width = frame.shape[1]
    x = max(12, width - text_w - 28)
    y = 34
 
    draw_translucent_rect(frame, (x - 10, y - text_h - 10), (x + text_w + 10, y + 10),
                          cfg.COLOR_PANEL, 0.7)
    draw_text(frame, message, (x, y), scale, cfg.COLOR_ACCENT, 1, shadow=False)
 
 
def draw_center_message(frame, cfg, lines, color=None):
    """
    Big centred message block, used for fatal errors and the paused state, so
    the user is never left staring at a frozen window with no explanation.
    """
    color = color or cfg.COLOR_TEXT
    height, width = frame.shape[:2]
    scale = 0.7
 
    sizes = [cv2.getTextSize(line, FONT_BOLD, scale, 2)[0] for line in lines]
    block_w = max(size[0] for size in sizes) if sizes else 0
    line_h = 38
    block_h = line_h * len(lines)
 
    x = (width - block_w) // 2
    y = (height - block_h) // 2
 
    draw_translucent_rect(frame, (x - 30, y - 34), (x + block_w + 30, y + block_h + 12),
                          (0, 0, 0), 0.75)
    for index, line in enumerate(lines):
        line_w = sizes[index][0]
        draw_text(frame, line,
                  ((width - line_w) // 2, y + index * line_h),
                  scale, color, 2, FONT_BOLD, shadow=False)
 
 
# ===========================================================================
# PHASE 2 PANELS
# Per-worker breakdown, the deterministic reason list, and the reach-in alert.
# Everything above is unchanged apart from the two new HUD rows.
# ===========================================================================
 
def draw_worker_panel(frame, cfg, people, risks):
    """
    Right-hand panel, one block per worker:
 
        P3
        Body: DANGER_1
        Hand: DANGER_1
        Danger Time: 6.2 sec
        Risk: 100  CRITICAL
 
    Sorted worst-risk-first, so with a crowd on screen the person who needs
    attention is always at the top rather than wherever their track ID landed.
    """
    if not people:
        return
 
    ordered = sorted(
        people,
        key=lambda p: (-(risks.get(p["id"], {}).get("risk", 0)), p["id"]),
    )
    shown = ordered[:cfg.WORKER_PANEL_MAX_ROWS]
    hidden = len(ordered) - len(shown)
 
    s = hud_scale(frame.shape[1])
    width = int(cfg.WORKER_PANEL_WIDTH * s)
    line_h = int(19 * s)
 
    # Measure first so the panel is exactly as tall as its contents.
    blocks = []
    for person in shown:
        blocks.append(_worker_lines(person, cfg, risks.get(person["id"], {})))
    total_lines = sum(len(b[0]) for b in blocks)
 
    pad = int(12 * s)
    height = pad * 2 + total_lines * line_h + len(blocks) * int(8 * s)
    if hidden > 0:
        height += line_h
 
    x = frame.shape[1] - width - int(12 * s)
    y = 56  # clear of the top-right zone-message line
 
    draw_translucent_rect(frame, (x, y), (x + width, y + height),
                          cfg.COLOR_PANEL, cfg.PANEL_ALPHA)
 
    cursor = y + pad + int(6 * s)
    for lines, accent in blocks:
        # Colour bar so the worst worker is identifiable without reading.
        cv2.rectangle(frame, (x, cursor - int(12 * s)),
                      (x + 3, cursor + (len(lines) - 1) * line_h + int(4 * s)),
                      accent, -1)
        for index, (text, color, bold) in enumerate(lines):
            draw_text(frame, text, (x + int(12 * s), cursor + index * line_h),
                      (0.5 if bold else 0.44) * s, color, 1,
                      FONT_BOLD if bold else FONT)
        cursor += len(lines) * line_h + int(8 * s)
 
    if hidden > 0:
        draw_text(frame, "+{} more".format(hidden), (x + int(12 * s), cursor),
                  0.42 * s, cfg.COLOR_MUTED)
 
 
def _worker_lines(person, cfg, verdict):
    """Build the text lines for one worker block. Returns (lines, accent)."""
    zone = person.get("zone") or cfg.ZONE_OUTSIDE
    accent = cfg.PERSON_COLORS.get(zone, cfg.COLOR_MUTED)
    risk = verdict.get("risk", 0)
    level = verdict.get("level", "SAFE")
 
    hand_zone = person.get("hand_zone")
 
    lines = [
        (person_tag(person), cfg.COLOR_TEXT, True),
        ("Body: {}".format(person.get("zone_id") or zone), accent, False),
    ]
 
    # Hand zone is always shown when known, and always as its own line: body and
    # hand are independent states and collapsing them hides reach-in.
    if hand_zone is not None:
        hand_color = cfg.PERSON_COLORS.get(hand_zone, cfg.COLOR_MUTED)
        lines.append(("Hand: {}".format(person.get("hand_zone_id") or hand_zone),
                      hand_color, person.get("hand_in_danger", False)))
 
    # The live per-person danger timer.
    duration = person.get("danger_duration", 0.0) or 0.0
    if duration > 0:
        over = duration >= cfg.DANGER_TIME_THRESHOLD_SEC
        lines.append((
            "Danger Time: {:.1f} sec".format(duration),
            cfg.COLOR_ALERT if over else cfg.COLOR_ZONE_WARNING,
            False,
        ))
 
    # Image distance, explicitly labelled px -- never presented as metres.
    distance = person.get("distance_to_danger")
    if distance is not None and zone != cfg.ZONE_DANGER:
        lines.append(("Dist to Danger: {:.0f} px".format(distance),
                      cfg.COLOR_MUTED, False))
 
    risk_color = (cfg.COLOR_ALERT if risk >= cfg.RISK_CRITICAL_THRESHOLD
                  else accent if risk else cfg.COLOR_SAFE)
    lines.append(("Risk: {}  {}".format(risk, level), risk_color, True))
 
    return lines, accent
 
 
def draw_reason_panel(frame, cfg, verdict, person=None):
    """
    Bottom-left panel listing WHY the system is alarming:
 
        RISK 100  CRITICAL   P3
        - Worker is inside Danger Zone
        - Worker has remained in Danger Zone for 7.2 seconds
        - Worker hand is inside Danger Zone
        - Machine is ON
 
    These strings come straight from the deterministic rules in risk_engine.py.
    Nothing here is generated by a model, which is exactly why an operator can
    trust the list -- and why the Phase 3 dashboard can display it verbatim.
    """
    reasons = (verdict or {}).get("reasons") or []
    if not reasons:
        return
 
    reasons = reasons[:cfg.MAX_DISPLAYED_REASONS]
    level = verdict.get("level", "")
    risk = verdict.get("risk", 0)
    critical = risk >= cfg.RISK_CRITICAL_THRESHOLD
 
    s = hud_scale(frame.shape[1])
    line_h = int(20 * s)
    pad = int(12 * s)
 
    heading = "RISK {}  {}".format(risk, level)
    if person is not None:
        heading += "   {}".format(person_tag(person))
 
    texts = [heading] + ["- " + reason for reason in reasons]
    scale = 0.46 * s
    widths = [cv2.getTextSize(t, FONT, scale, 1)[0][0] for t in texts]
    width = max(widths) + pad * 2
 
    height = frame.shape[0]
    bar_h = 30 if cfg.SHOW_HELP_BAR else 0
    block_h = pad * 2 + len(texts) * line_h
 
    x = int(12 * s)
    y = height - bar_h - int(10 * s) - block_h
 
    draw_translucent_rect(frame, (x, y), (x + width, y + block_h),
                          cfg.COLOR_PANEL, 0.78)
    accent = cfg.COLOR_ALERT if critical else cfg.COLOR_ZONE_WARNING
    cv2.rectangle(frame, (x, y), (x + 4, y + block_h), accent, -1)
 
    cursor = y + pad + int(10 * s)
    draw_text(frame, heading, (x + pad, cursor), 0.5 * s, accent, 1, FONT_BOLD)
    for index, reason in enumerate(reasons, start=1):
        draw_text(frame, "- " + reason, (x + pad, cursor + index * line_h),
                  scale, cfg.COLOR_TEXT, 1)
 
 
def draw_hand_alert(frame, cfg, people):
    """
    Dedicated reach-in banner, top-centre.
 
    This is deliberately its OWN message rather than being folded into the
    danger-zone alarm. A worker whose feet are safe but whose hand is in the
    machine must not be reported as "worker in danger zone" -- that would send
    the operator looking at the wrong thing. The body zone stays whatever it
    actually is; this line says the hand crossed the line.
    """
    offenders = [p for p in people if p.get("hand_in_danger")]
    if not offenders:
        return
 
    ids = ", ".join(person_tag(p) for p in offenders)
    text = "{}  ({})".format(cfg.HAND_ALERT_TEXT, ids)
 
    scale = 0.62
    thickness = 2
    width = frame.shape[1]
    (text_w, text_h), _ = cv2.getTextSize(text, FONT_BOLD, scale, thickness)
    while text_w > width - 40 and scale > 0.32:
        scale -= 0.02
        (text_w, text_h), _ = cv2.getTextSize(text, FONT_BOLD, scale, thickness)
 
    x = (width - text_w) // 2
    # Sits a little below the very top, so it does not bury the label of a zone
    # whose top edge runs along the top of the frame (the usual SAFE zone).
    y = text_h + 62
 
    draw_translucent_rect(frame, (x - 20, y - text_h - 14),
                          (x + text_w + 20, y + 14), cfg.COLOR_ALERT, 0.88)
    cv2.putText(frame, text, (x, y), FONT_BOLD, scale, (255, 255, 255),
                thickness, cv2.LINE_AA)
 
 
def draw_source_stats(frame, cfg, stats):
    """
    Bottom-right diagnostics panel for the frame pipeline:
 
        SOURCE        IP 1280x720
        SOURCE FPS    28.7
        PROCESS FPS   9.8
        RECEIVED      1240
        PROCESSED     423
        DROPPED       817
        BUFFER AGE    33 ms
        PIPELINE      96 ms
 
    DROPPED counts frames the reader replaced before the pipeline could take them.
    A large number is NORMAL and expected: the camera runs faster than YOLO, and we
    deliberately keep only the newest frame. See src/frame_source.py.
 
    BUFFER AGE is how long the processed frame waited in OUR buffer -- the only part
    of the latency this program controls. It does NOT include camera, encoding or
    network delay, so it is not end-to-end glass-to-screen latency.
 
    These are diagnostics, never safety events, and never written to the JSON log.
    """
    if stats is None:
        return
 
    lines = stats.hud_lines()
    s = hud_scale(frame.shape[1])
    scale = 0.42 * s
    line_h = int(17 * s)
    pad = int(10 * s)
 
    label_w = max(cv2.getTextSize(label, FONT, scale, 1)[0][0] for label, _ in lines)
    value_w = max(cv2.getTextSize(value, FONT, scale, 1)[0][0] for _, value in lines)
    width = label_w + value_w + pad * 3
    height = len(lines) * line_h + pad
 
    bar_h = 30 if cfg.SHOW_HELP_BAR else 0
    x = frame.shape[1] - width - int(12 * s)
    y = frame.shape[0] - bar_h - int(10 * s) - height
 
    draw_translucent_rect(frame, (x, y), (x + width, y + height),
                          cfg.COLOR_PANEL, 0.66)
    cv2.rectangle(frame, (x, y), (x + 3, y + height), cfg.COLOR_ACCENT, -1)
 
    for index, (label, value) in enumerate(lines):
        row_y = y + pad + index * line_h
        draw_text(frame, label, (x + pad, row_y), scale, cfg.COLOR_MUTED, 1,
                  shadow=False)
        draw_text(frame, value, (x + pad * 2 + label_w, row_y), scale,
                  cfg.COLOR_TEXT, 1, shadow=False)
 
 
def draw_event_ticker(frame, cfg, events):
    """
    Small bottom-right list of the most recent safety events:
 
        P1 ZONE_ENTER
           MACHINE_TURNED_ON
 
    Purely a debugging aid for this phase -- it is the same data that Phase 3
    will POST to the backend, shown on screen so we can see it is correct
    without a database. Reads the event's own person_id field, so it displays
    exactly what was written to the log.
    """
    if not events:
        return
 
    s = hud_scale(frame.shape[1])
    scale = 0.4 * s
    line_h = int(16 * s)
    lines = ["{} {}".format(
        person_tag(e["person_id"]) if e.get("person_id") else "  ",
        e.get("event_type", "?"),
    ) for e in events]
 
    widths = [cv2.getTextSize(t, FONT, scale, 1)[0][0] for t in lines]
    width = max(widths) + int(20 * s)
    block_h = len(lines) * line_h + int(14 * s)
 
    bar_h = 30 if cfg.SHOW_HELP_BAR else 0
    x = frame.shape[1] - width - int(12 * s)
    y = frame.shape[0] - bar_h - int(10 * s) - block_h
 
    draw_translucent_rect(frame, (x, y), (x + width, y + block_h),
                          cfg.COLOR_PANEL, 0.66)
    for index, line in enumerate(lines):
        draw_text(frame, line, (x + int(10 * s), y + int(14 * s) + index * line_h),
                  scale, cfg.COLOR_MUTED, 1, shadow=False)
 