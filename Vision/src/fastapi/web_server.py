import threading
import time

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


class ZoneRequest(BaseModel):
    zone_type: str
    points: list[dict[str, float]]


class ZonePointRequest(BaseModel):
    x: float
    y: float


class ControlRequest(BaseModel):
    key: str


class WebServer:

    def __init__(
        self,
        broadcaster,
        zone_manager=None,
        command_handler=None,
        host="0.0.0.0",
        port=8000,
    ):
        self.broadcaster = broadcaster
        self.zone_manager = zone_manager
        self.command_handler = command_handler
        self.host = host
        self.port = port

        self.app = FastAPI(
            title="ZONE X Web Server"
        )

        # --------------------------------------------------------------
        # CORS
        # --------------------------------------------------------------
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            ],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
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
                "zones": "/zones",
                "control": "/control/key",
            }

        @self.app.get("/health")
        def health():
            return {"status": "ok"}

        @self.app.get("/video")
        def video():
            return StreamingResponse(
                self._generate_frames(),
                media_type=(
                    "multipart/x-mixed-replace; "
                    "boundary=frame"
                ),
            )

        @self.app.get("/zones")
        def get_zones():

            if self.zone_manager is None:
                raise HTTPException(
                    status_code=500,
                    detail="ZoneManager is not connected",
                )

            return {
                "status": "success",
                "zones": self.zone_manager.describe(),
                "is_defining": self.zone_manager.is_defining,
                "defining_type": self.zone_manager.defining_type,
                "defining_id": self.zone_manager.defining_id,
                "message": self.zone_manager.last_message,
            }

        @self.app.post("/control/key")
        def control_key(request: ControlRequest):

            if self.command_handler is None:
                raise HTTPException(
                    status_code=500,
                    detail="Command handler is not connected",
                )

            key = (request.key or "").strip()

            if not key:
                raise HTTPException(
                    status_code=400,
                    detail="Key cannot be empty",
                )

            try:
                result = self.command_handler(
                    "key",
                    key=key,
                )

                return {
                    "status": "success",
                    "key": key,
                    "result": result,
                }

            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=str(exc),
                )

        @self.app.post("/zones/start")
        def start_zone(request: ZoneRequest):

            if self.command_handler is None:
                raise HTTPException(
                    status_code=500,
                    detail="Command handler is not connected",
                )

            zone_type = request.zone_type.upper()

            if zone_type not in {
                "SAFE",
                "WARNING",
                "DANGER",
            }:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid zone type",
                )

            try:
                result = self.command_handler(
                    "start_zone",
                    zone_type=zone_type,
                )

                return {
                    "status": "success",
                    "zone_type": zone_type,
                    "result": result,
                }

            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=str(exc),
                )

        @self.app.post("/zones/point")
        def add_zone_point(request: ZonePointRequest):

            if self.command_handler is None:
                raise HTTPException(
                    status_code=500,
                    detail="Command handler is not connected",
                )

            try:
                result = self.command_handler(
                    "zone_point",
                    x=request.x,
                    y=request.y,
                )

                return {
                    "status": "success",
                    "result": result,
                }

            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=str(exc),
                )

        @self.app.post("/zones/hover")
        def zone_hover(request: ZonePointRequest):

            if self.command_handler is None:
                raise HTTPException(
                    status_code=500,
                    detail="Command handler is not connected",
                )

            try:
                result = self.command_handler(
                    "zone_hover",
                    x=request.x,
                    y=request.y,
                )

                return {
                    "status": "success",
                    "result": result,
                }

            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=str(exc),
                )

        @self.app.post("/zones/undo")
        def undo_zone_point():

            if self.command_handler is None:
                raise HTTPException(
                    status_code=500,
                    detail="Command handler is not connected",
                )

            try:
                result = self.command_handler(
                    "zone_undo"
                )

                return {
                    "status": "success",
                    "result": result,
                }

            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=str(exc),
                )

        @self.app.post("/zones/finish")
        def finish_zone():

            if self.command_handler is None:
                raise HTTPException(
                    status_code=500,
                    detail="Command handler is not connected",
                )

            try:
                result = self.command_handler(
                    "zone_finish"
                )

                return {
                    "status": "success",
                    "result": result,
                }

            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=str(exc),
                )

        @self.app.post("/zones/cancel")
        def cancel_zone():

            if self.command_handler is None:
                raise HTTPException(
                    status_code=500,
                    detail="Command handler is not connected",
                )

            try:
                result = self.command_handler(
                    "zone_cancel"
                )

                return {
                    "status": "success",
                    "result": result,
                }

            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=str(exc),
                )

        @self.app.delete("/zones")
        def clear_zones():

            if self.command_handler is None:
                raise HTTPException(
                    status_code=500,
                    detail="Command handler is not connected",
                )

            try:
                result = self.command_handler(
                    "clear"
                )

                return {
                    "status": "success",
                    "result": result,
                }

            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=str(exc),
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

            self.server = uvicorn.Server(config)
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