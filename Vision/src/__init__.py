"""
ZONE X - core modules.

    frame_source   where frames come from (webcam / ip / cctv / video)
    detector       YOLOv8 person detection
    pose_detector  optional wrist keypoints, for reach-in detection
    tracker        person identity (P1, P2, ...), hysteresis, dwell timers
    zone           polygons and their unique tags (SAFE_1, DANGER_2, ...)
    risk_engine    the safety rules
    events         which state changes are worth recording
    event_logger   writing those records as JSON Lines
    ui             everything drawn on screen
"""
