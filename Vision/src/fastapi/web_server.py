import threading
import time

import uvicorn

from fastapi import FastAPI
from fastapi.responses import StreamingResponse


class WebServer:

    def __init__(
        self,
        broadcaster,
        host="0.0.0.0",
        port=8000,
    ):

        self.broadcaster = broadcaster
        self.host = host
        self.port = port

        self.app = FastAPI(
            title="ZONE X Web Server"
        )

        self.server = None
        self.thread = None

        self._setup_routes()

    def _setup_routes(self):

        @self.app.get("/")
        def root():

            return {
                "status": "running",
                "video": "/video",
            }

        @self.app.get("/health")
        def health():

            return {
                "status": "ok"
            }

        @self.app.get("/video")
        def video():

            return StreamingResponse(
                self._generate_frames(),
                media_type=(
                    "multipart/x-mixed-replace; "
                    "boundary=frame"
                ),
            )

    def _generate_frames(self):

        while True:

            frame = self.broadcaster.get_frame()

            if frame is not None:

                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + frame
                    + b"\r\n"
                )

            time.sleep(0.03)

    def start(self):

        def run_server():

            config = uvicorn.Config(
                self.app,
                host=self.host,
                port=self.port,
                log_level="info",
            )

            self.server = uvicorn.Server(
                config
            )

            self.server.run()

        self.thread = threading.Thread(
            target=run_server,
            daemon=True,
        )

        self.thread.start()

        print(
            "[WEB] Server started: "
            "http://{}:{}/video".format(
                self.host,
                self.port,
            )
        )

    def stop(self):

        if self.server is not None:

            self.server.should_exit = True