"""
src/video_server.py

FastAPI server responsible for exposing the latest processed
AI frame to the web dashboard.
"""

import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

from src.camera.frame_broadcaster import FrameBroadcaster


def create_video_app(broadcaster: FrameBroadcaster):
    """
    Create the FastAPI application using the shared broadcaster.
    """

    app = FastAPI(
        title="Safety Monitoring Video API",
        version="1.0.0",
    )

    # React development server usually runs on localhost:5173.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health():
        return JSONResponse(
            {
                "status": "ok",
                "video_available": broadcaster.has_frame,
            }
        )

    def generate_frames():
        """
        Generate an MJPEG stream.

        Browser receives:

            JPEG
            JPEG
            JPEG
            JPEG
            ...

        Only the newest frame is sent.
        """

        last_frame = None

        while True:
            frame = broadcaster.get()

            if frame is None:
                time.sleep(0.01)
                continue

            # Don't repeatedly send the exact same frame.
            if frame == last_frame:
                time.sleep(0.005)
                continue

            last_frame = frame

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: "
                + str(len(frame)).encode()
                + b"\r\n\r\n"
                + frame
                + b"\r\n"
            )

    @app.get("/video")
    def video():
        """
        Live MJPEG video endpoint.
        """

        return StreamingResponse(
            generate_frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )

    return app