"""
tests/measure_frame_source.py
-----------------------------
Measures what the latest-frame reader actually buys you, on whatever source
config.py is currently pointing at. No YOLO, no window -- it simulates a pipeline
that takes TARGET_PROCESSING_FPS frames per second and compares two ways of
feeding it:

    BEFORE   read one frame per loop pass, straight from VideoCapture
             (what the app did before the reader thread existed)
    AFTER    FrameSource with its reader thread + latest-frame buffer

Run it with the source you care about selected in config.py:

    python tests/measure_frame_source.py
    python tests/measure_frame_source.py --seconds 20

THE KEY NUMBER IS "buffered frames on exit", FOR LIVE SOURCES ONLY.
After the measurement each mode stops consuming and then reads as fast as it can,
counting the frames that come back instantly. On a camera or a network stream those
frames were already sitting in a buffer waiting for us -- they ARE the latency our
pipeline added, and dividing by the source frame rate turns them into seconds of lag.

That test is meaningless for a video FILE: the rest of the recording is always
available instantly, so reading ahead measures nothing. For a file the honest
number is how far playback fell behind real time, which is printed instead.

WHAT THIS DOES NOT MEASURE
True glass-to-screen latency. That includes the camera sensor, the phone's encoder,
wifi and the decoder, none of which we can see from here. To measure it properly,
point the camera at a running stopwatch on screen and photograph both together.
"""

import argparse
import os
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from src.camera.frame_source import FrameSource, FrameSourceError, ProcessingScheduler

# A read that returns faster than this was served from a buffer, not from the wire.
INSTANT_READ_SEC = 0.005


def drain(capture, limit=300):
    """Count the frames already waiting in the buffer, by reading until one blocks."""
    buffered = 0
    while buffered < limit:
        started = time.perf_counter()
        ok, frame = capture.read()
        elapsed = time.perf_counter() - started
        if not ok or frame is None:
            break
        if elapsed >= INSTANT_READ_SEC:
            break          # this one came off the wire: we have caught up
        buffered += 1
    return buffered


def measure_before(seconds, target_fps):
    """The old arrangement: one read per pass, the pipeline blocking in between."""
    source = FrameSource.from_config(config)
    source._validate()

    if source.source_type == "webcam":
        capture = cv2.VideoCapture(source.camera_index, cv2.CAP_DSHOW)
    elif source.source_type == "video":
        capture = cv2.VideoCapture(source.video_path)
    else:
        capture = cv2.VideoCapture(source.source_url)

    if not capture.isOpened():
        capture.release()
        raise FrameSourceError("could not open the source for the BEFORE run")
    if config.FRAME_WIDTH:
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, config.FRAME_WIDTH)
    if config.FRAME_HEIGHT:
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FRAME_HEIGHT)

    interval = 1.0 / target_fps
    read_times = []
    processed = 0
    resolution = ""
    start = time.monotonic()

    while (time.monotonic() - start) < seconds:
        began = time.perf_counter()
        ok, frame = capture.read()
        read_times.append(time.perf_counter() - began)
        if not ok or frame is None:
            continue
        processed += 1
        if not resolution:
            resolution = "{}x{}".format(frame.shape[1], frame.shape[0])
        # Stand in for YOLO + pose + drawing.
        time.sleep(interval)

    elapsed = time.monotonic() - start
    is_file = source.source_type == "video"
    buffered = None if is_file else drain(capture)
    capture.release()

    mean_read = sum(read_times) / len(read_times) if read_times else 0.0
    return {
        "mode": "BEFORE (read one frame per pass)",
        "resolution": resolution,
        "seconds": elapsed,
        "received": processed,      # every frame read was processed
        "processed": processed,
        "dropped": 0,
        "process_fps": processed / elapsed if elapsed else 0.0,
        "source_fps": None,         # cannot be seen: we never read faster than we processed
        "mean_read_ms": mean_read * 1000.0,
        "buffered_on_exit": buffered,
        "is_file": is_file,
    }


def measure_after(seconds, target_fps):
    """The new arrangement: reader thread + latest-frame buffer + time-based gate."""
    source = FrameSource.from_config(config).open()
    schedule = ProcessingScheduler(target_fps)
    interval = 1.0 / target_fps
    resolution = ""
    start = time.monotonic()

    try:
        while (time.monotonic() - start) < seconds:
            if schedule.due():
                ok, frame = source.read()
                if ok:
                    schedule.mark()
                    if not resolution:
                        resolution = "{}x{}".format(frame.shape[1], frame.shape[0])
                    time.sleep(interval)     # stand in for the AI pipeline
                elif source.ended:
                    break
            else:
                time.sleep(0.002)

        elapsed = time.monotonic() - start
        stats = source.stats.snapshot()
        is_file = source.is_file
        buffered = None
        if not is_file and source._capture is not None:
            source._stop.set()          # stop the reader so the drain is ours alone
            buffered = drain(source._capture)
    finally:
        source.release()

    return {
        "mode": "AFTER (reader thread + latest frame)",
        "resolution": resolution or stats["resolution"],
        "seconds": elapsed,
        "received": stats["frames_received"],
        "processed": stats["frames_processed"],
        "dropped": stats["frames_dropped"],
        "process_fps": stats["process_fps"],
        "source_fps": stats["source_fps"],
        "mean_read_ms": None,       # the reader absorbs the blocking, not the loop
        "buffered_on_exit": buffered,
        "buffer_age_ms": stats["buffer_age_ms"],
        "is_file": is_file,
    }


def show(result, source_fps_hint=None):
    print("  {}".format(result["mode"]))
    print("    resolution           {}".format(result["resolution"]))
    print("    ran for              {:.1f} s".format(result["seconds"]))
    if result["source_fps"] is not None:
        print("    source FPS           {:.1f}".format(result["source_fps"]))
    else:
        print("    source FPS           not observable in this mode")
    print("    process FPS          {:.1f}".format(result["process_fps"]))
    print("    frames received      {}".format(result["received"]))
    print("    frames processed     {}".format(result["processed"]))
    print("    frames dropped       {}".format(result["dropped"]))
    if result.get("mean_read_ms") is not None:
        print("    mean read() time     {:.1f} ms   "
              "(near 0 = frames were already queued)"
              .format(result["mean_read_ms"]))
    if result.get("buffer_age_ms") is not None:
        print("    buffer age           {:.1f} ms  (our own queueing delay)"
              .format(result["buffer_age_ms"]))
    rate = result["source_fps"] or source_fps_hint
    buffered = result["buffered_on_exit"]

    if buffered is None:
        # A file source: reading ahead proves nothing, so report how far behind
        # real time the playback actually fell instead.
        if rate:
            # How much of the recording was CONSUMED (received), not how much was
            # analysed: the reader advancing the file is what keeps playback in
            # step with the clock, whether or not the pipeline looked at a frame.
            played = result["received"] / rate
            behind = max(0.0, result["seconds"] - played)
            print("    playback behind      {:.1f} s   "
                  "({:.1f} s of video consumed in {:.1f} s of wall clock)"
                  .format(behind, played, result["seconds"]))
        print("    buffered on exit     n/a for a file source")
    else:
        line = "    buffered on exit     {} frames".format(buffered)
        if rate:
            line += "   (~{:.2f} s of lag)".format(buffered / rate)
        print(line)
    print("")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=12.0,
                        help="how long to run each mode (default 12)")
    parser.add_argument("--fps", type=float, default=None,
                        help="pipeline rate to simulate (default TARGET_PROCESSING_FPS)")
    args = parser.parse_args()

    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    target_fps = args.fps or config.TARGET_PROCESSING_FPS

    print("")
    print("Frame source measurement")
    print("  SOURCE_TYPE            {}".format(config.SOURCE_TYPE))
    print("  simulated pipeline     {:.0f} FPS".format(target_fps))
    print("  duration per mode      {:.0f} s".format(args.seconds))
    print("")

    after = measure_after(args.seconds, target_fps)
    before = measure_before(args.seconds, target_fps)

    show(before, source_fps_hint=after["source_fps"])
    show(after)

    print("  Lower 'buffered on exit' is the win: those frames were stale video")
    print("  waiting in a queue. True end-to-end latency is NOT measured here --")
    print("  see the note at the top of this file.")
    print("")


if __name__ == "__main__":
    main()
