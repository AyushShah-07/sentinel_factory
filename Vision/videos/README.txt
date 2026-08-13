Put recorded factory footage here, then point config.py at it:

    SOURCE_TYPE = "video"
    VIDEO_PATH  = "videos/factory_test.mp4"
    MIRROR_PREVIEW = False      # do not mirror real footage

This lets the whole safety system be tested -- detection, zones, the danger
timer, the risk rules and the event log -- with no camera plugged in.

H.264 mp4 is the safest format for OpenCV. Playback is paced to the file's own
frame rate (config.VIDEO_REALTIME) so the 5-second danger dwell rule behaves the
same as it does on a live camera.
