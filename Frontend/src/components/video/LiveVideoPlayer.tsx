import { useEffect, useRef, useState } from 'react'
import './LiveVideoPlayer.css'

interface Point {
  x: number
  y: number
}

interface LiveVideoPlayerProps {
  streamUrl?: string
  zoneApiUrl?: string
}

type ZoneType = 'SAFE' | 'WARNING' | 'DANGER'

const FRAME_WIDTH = 1280
const FRAME_HEIGHT = 720

export const LiveVideoPlayer = ({
  streamUrl = 'http://localhost:8000/video',
  zoneApiUrl = 'http://localhost:8000',
}: LiveVideoPlayerProps) => {
  const videoRef = useRef<HTMLImageElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  const [zoneType, setZoneType] =
    useState<ZoneType>('DANGER')

  const [points, setPoints] =
    useState<Point[]>([])

  const [drawing, setDrawing] =
    useState(false)

  const [fullscreen, setFullscreen] =
    useState(false)

  // ------------------------------------------------------------------
  // DISPLAYED VIDEO RECTANGLE
  //
  // The <img> can contain black letterbox areas because object-fit:
  // contain is used. We calculate the REAL displayed 1280x720 image area.
  // ------------------------------------------------------------------

  const getDisplayedVideoRect = () => {
    const image = videoRef.current

    if (!image) {
      return null
    }

    const rect = image.getBoundingClientRect()

    const sourceWidth =
      image.naturalWidth || FRAME_WIDTH

    const sourceHeight =
      image.naturalHeight || FRAME_HEIGHT

    const sourceRatio =
      sourceWidth / sourceHeight

    const containerRatio =
      rect.width / rect.height

    let width = rect.width
    let height = rect.height
    let left = 0
    let top = 0

    if (containerRatio > sourceRatio) {
      height = rect.height
      width = height * sourceRatio
      left = (rect.width - width) / 2
    } else {
      width = rect.width
      height = width / sourceRatio
      top = (rect.height - height) / 2
    }

    return {
      left,
      top,
      width,
      height,
    }
  }

  // ------------------------------------------------------------------
  // CANVAS
  // ------------------------------------------------------------------

  const redrawCanvas = () => {
    const canvas =
      canvasRef.current

    const image =
      videoRef.current

    if (!canvas || !image) {
      return
    }

    const rect =
      image.getBoundingClientRect()

    canvas.width = rect.width
    canvas.height = rect.height

    const ctx =
      canvas.getContext('2d')

    if (!ctx) {
      return
    }

    ctx.clearRect(
      0,
      0,
      canvas.width,
      canvas.height,
    )

    if (!drawing || points.length === 0) {
      return
    }

    const videoRect =
      getDisplayedVideoRect()

    if (!videoRect) {
      return
    }

    const scaleX =
      videoRect.width /
      FRAME_WIDTH

    const scaleY =
      videoRect.height /
      FRAME_HEIGHT

    ctx.beginPath()

    points.forEach(
      (point, index) => {
        const x =
          videoRect.left +
          point.x * scaleX

        const y =
          videoRect.top +
          point.y * scaleY

        if (index === 0) {
          ctx.moveTo(x, y)
        } else {
          ctx.lineTo(x, y)
        }
      },
    )

    if (points.length >= 3) {
      ctx.closePath()
    }

    ctx.lineWidth = 3

    if (zoneType === 'DANGER') {
      ctx.strokeStyle = '#ff3333'
      ctx.fillStyle =
        'rgba(255, 0, 0, 0.20)'
    } else if (zoneType === 'WARNING') {
      ctx.strokeStyle = '#ffaa00'
      ctx.fillStyle =
        'rgba(255, 170, 0, 0.20)'
    } else {
      ctx.strokeStyle = '#00ff66'
      ctx.fillStyle =
        'rgba(0, 255, 100, 0.20)'
    }

    if (points.length >= 3) {
      ctx.fill()
    }

    ctx.stroke()

    points.forEach(
      (point) => {
        const x =
          videoRect.left +
          point.x * scaleX

        const y =
          videoRect.top +
          point.y * scaleY

        ctx.beginPath()

        ctx.arc(
          x,
          y,
          5,
          0,
          Math.PI * 2,
        )

        ctx.fillStyle =
          ctx.strokeStyle

        ctx.fill()
      },
    )
  }

  // ------------------------------------------------------------------
  // RESIZE
  // ------------------------------------------------------------------

  useEffect(() => {
    redrawCanvas()

    const handleResize = () => {
      redrawCanvas()
    }

    window.addEventListener(
      'resize',
      handleResize,
    )

    return () => {
      window.removeEventListener(
        'resize',
        handleResize,
      )
    }
  }, [
    points,
    zoneType,
    drawing,
    fullscreen,
  ])

  // ------------------------------------------------------------------
  // FULLSCREEN
  // ------------------------------------------------------------------

  useEffect(() => {
    const handleFullscreenChange = () => {
      setFullscreen(
        Boolean(
          document.fullscreenElement,
        ),
      )

      requestAnimationFrame(
        redrawCanvas,
      )
    }

    document.addEventListener(
      'fullscreenchange',
      handleFullscreenChange,
    )

    return () => {
      document.removeEventListener(
        'fullscreenchange',
        handleFullscreenChange,
      )
    }
  }, [])

  // ------------------------------------------------------------------
  // SEND KEY TO PYTHON
  // ------------------------------------------------------------------

  const sendKey = async (
    key: string,
  ) => {
    try {
      const response =
        await fetch(
          `${zoneApiUrl}/control/key`,
          {
            method: 'POST',
            headers: {
              'Content-Type':
                'application/json',
            },
            body: JSON.stringify({
              key,
            }),
          },
        )

      if (!response.ok) {
        throw new Error(
          `Control failed: ${response.status}`,
        )
      }

      return await response.json()
    } catch (error) {
      console.error(
        'Failed to send keyboard command:',
        error,
      )

      return null
    }
  }

  // ------------------------------------------------------------------
  // BROWSER KEYBOARD
  //
  // This makes React behave like the OpenCV keyboard.
  // ------------------------------------------------------------------

  useEffect(() => {
    const handleKeyDown = (
      event: KeyboardEvent,
    ) => {
      const tag =
        (
          event.target as HTMLElement
        )?.tagName || ''

      if (
        tag === 'INPUT' ||
        tag === 'TEXTAREA' ||
        tag === 'SELECT'
      ) {
        return
      }

      let key: string | null = null

      switch (event.key) {
        case 's':
        case 'S':
          key = 's'
          break

        case 'w':
        case 'W':
          key = 'w'
          break

        case 'd':
        case 'D':
          key = 'd'
          break

        case 'r':
        case 'R':
          key = 'r'
          break

        case 'c':
        case 'C':
          key = 'c'
          break

        case 'm':
        case 'M':
          key = 'm'
          break

        case 'p':
        case 'P':
          key = 'p'
          break

        case 'q':
        case 'Q':
          key = 'q'
          break

        case 'Escape':
          key = 'escape'
          break

        case 'Enter':
          key = 'enter'
          break

        default:
          break
      }

      if (!key) {
        return
      }

      event.preventDefault()

      void sendKey(key)

      // React preview state follows the same commands.
      if (
        key === 's' ||
        key === 'w' ||
        key === 'd' ||
        key === 'r'
      ) {
        const selected: ZoneType =
          key === 's'
            ? 'SAFE'
            : key === 'w'
              ? 'WARNING'
              : 'DANGER'

        setZoneType(selected)
        setPoints([])
        setDrawing(true)
      }

      if (key === 'enter') {
        setPoints([])
        setDrawing(false)
      }

      if (key === 'c' || key === 'escape') {
        setPoints([])
        setDrawing(false)
      }
    }

    window.addEventListener(
      'keydown',
      handleKeyDown,
    )

    return () => {
      window.removeEventListener(
        'keydown',
        handleKeyDown,
      )
    }
  }, [zoneApiUrl])

  // ------------------------------------------------------------------
  // MOUSE CLICK
  // ------------------------------------------------------------------

  const handleCanvasClick = async (
    event: React.MouseEvent<HTMLCanvasElement>,
  ) => {
    if (!drawing) {
      return
    }

    const canvas =
      canvasRef.current

    if (!canvas) {
      return
    }

    const videoRect =
      getDisplayedVideoRect()

    if (!videoRect) {
      return
    }

    const canvasRect =
      canvas.getBoundingClientRect()

    const localX =
      event.clientX -
      canvasRect.left

    const localY =
      event.clientY -
      canvasRect.top

    const imageX =
      localX - videoRect.left

    const imageY =
      localY - videoRect.top

    if (
      imageX < 0 ||
      imageY < 0 ||
      imageX > videoRect.width ||
      imageY > videoRect.height
    ) {
      return
    }

    const frameX =
      Math.round(
        imageX /
          videoRect.width *
          FRAME_WIDTH,
      )

    const frameY =
      Math.round(
        imageY /
          videoRect.height *
          FRAME_HEIGHT,
      )

    setPoints(
      (previous) => [
        ...previous,
        {
          x: frameX,
          y: frameY,
        },
      ],
    )

    try {
      await fetch(
        `${zoneApiUrl}/zones/point`,
        {
          method: 'POST',
          headers: {
            'Content-Type':
              'application/json',
          },
          body: JSON.stringify({
            x: frameX,
            y: frameY,
          }),
        },
      )
    } catch (error) {
      console.error(
        'Failed to send zone point:',
        error,
      )
    }
  }

  // ------------------------------------------------------------------
  // MOUSE MOVE
  // ------------------------------------------------------------------

  const handleCanvasMouseMove = async (
    event: React.MouseEvent<HTMLCanvasElement>,
  ) => {
    if (!drawing) {
      return
    }

    const canvas =
      canvasRef.current

    if (!canvas) {
      return
    }

    const videoRect =
      getDisplayedVideoRect()

    if (!videoRect) {
      return
    }

    const canvasRect =
      canvas.getBoundingClientRect()

    const imageX =
      event.clientX -
      canvasRect.left -
      videoRect.left

    const imageY =
      event.clientY -
      canvasRect.top -
      videoRect.top

    if (
      imageX < 0 ||
      imageY < 0 ||
      imageX > videoRect.width ||
      imageY > videoRect.height
    ) {
      return
    }

    const frameX =
      Math.round(
        imageX /
          videoRect.width *
          FRAME_WIDTH,
      )

    const frameY =
      Math.round(
        imageY /
          videoRect.height *
          FRAME_HEIGHT,
      )

    try {
      await fetch(
        `${zoneApiUrl}/zones/hover`,
        {
          method: 'POST',
          headers: {
            'Content-Type':
              'application/json',
          },
          body: JSON.stringify({
            x: frameX,
            y: frameY,
          }),
        },
      )
    } catch {
      // Hover requests are best-effort.
    }
  }

  // ------------------------------------------------------------------
  // RIGHT CLICK = UNDO
  // ------------------------------------------------------------------

  const handleCanvasContextMenu = async (
    event: React.MouseEvent<HTMLCanvasElement>,
  ) => {
    event.preventDefault()

    if (!drawing) {
      return
    }

    setPoints(
      (previous) =>
        previous.slice(0, -1),
    )

    try {
      await fetch(
        `${zoneApiUrl}/zones/undo`,
        {
          method: 'POST',
        },
      )
    } catch (error) {
      console.error(
        'Failed to undo zone point:',
        error,
      )
    }
  }

  // ------------------------------------------------------------------
  // START DRAWING
  // ------------------------------------------------------------------

  const startDrawing = async () => {
    setPoints([])
    setDrawing(true)

    try {
      await fetch(
        `${zoneApiUrl}/zones/start`,
        {
          method: 'POST',
          headers: {
            'Content-Type':
              'application/json',
          },
          body: JSON.stringify({
            zone_type: zoneType,
            points: [],
          }),
        },
      )
    } catch (error) {
      console.error(
        'Failed to start zone:',
        error,
      )
    }
  }

  // ------------------------------------------------------------------
  // FINISH
  // ------------------------------------------------------------------

  const finishZone = async () => {
    if (points.length < 3) {
      alert(
        'A zone needs at least 3 points.',
      )
      return
    }

    try {
      const response =
        await fetch(
          `${zoneApiUrl}/zones/finish`,
          {
            method: 'POST',
          },
        )

      if (!response.ok) {
        const body =
          await response
            .json()
            .catch(
              () => null,
            )

        throw new Error(
          body?.detail ||
            `Zone finish failed: ${response.status}`,
        )
      }

      setPoints([])
      setDrawing(false)
    } catch (error) {
      console.error(
        'Failed to finish zone:',
        error,
      )

      alert(
        error instanceof Error
          ? error.message
          : 'Failed to finish zone.',
      )
    }
  }

  // ------------------------------------------------------------------
  // CANCEL
  // ------------------------------------------------------------------

  const cancelDrawing = async () => {
    try {
      await fetch(
        `${zoneApiUrl}/zones/cancel`,
        {
          method: 'POST',
        },
      )
    } catch (error) {
      console.error(
        'Failed to cancel zone:',
        error,
      )
    }

    setPoints([])
    setDrawing(false)
  }

  // ------------------------------------------------------------------
  // UNDO BUTTON
  // ------------------------------------------------------------------

  const undoPoint = async () => {
    setPoints(
      (previous) =>
        previous.slice(0, -1),
    )

    try {
      await fetch(
        `${zoneApiUrl}/zones/undo`,
        {
          method: 'POST',
        },
      )
    } catch (error) {
      console.error(
        'Failed to undo point:',
        error,
      )
    }
  }

  // ------------------------------------------------------------------
  // CLEAR
  // ------------------------------------------------------------------

  const clearZones = async () => {
    try {
      const response =
        await fetch(
          `${zoneApiUrl}/zones`,
          {
            method: 'DELETE',
          },
        )

      if (!response.ok) {
        throw new Error(
          `Clear zones failed: ${response.status}`,
        )
      }

      setPoints([])
      setDrawing(false)
    } catch (error) {
      console.error(
        'Failed to clear zones:',
        error,
      )
    }
  }

  // ------------------------------------------------------------------
  // ZONE TYPE BUTTONS
  //
  // These now behave like S/W/D: selecting a type immediately starts
  // that type of zone.
  // ------------------------------------------------------------------

  const selectZoneType = async (
    type: ZoneType,
  ) => {
    setZoneType(type)
    setPoints([])
    setDrawing(true)

    try {
      await fetch(
        `${zoneApiUrl}/zones/start`,
        {
          method: 'POST',
          headers: {
            'Content-Type':
              'application/json',
          },
          body: JSON.stringify({
            zone_type: type,
            points: [],
          }),
        },
      )
    } catch (error) {
      console.error(
        'Failed to start zone:',
        error,
      )
    }
  }

  // ------------------------------------------------------------------
  // FULLSCREEN
  // ------------------------------------------------------------------

  const toggleFullscreen = async () => {
    const container =
      document.querySelector(
        '.live-player',
      ) as HTMLElement | null

    if (!container) {
      return
    }

    try {
      if (
        !document.fullscreenElement
      ) {
        await container.requestFullscreen()
      } else {
        await document.exitFullscreen()
      }
    } catch (error) {
      console.error(
        'Fullscreen error:',
        error,
      )
    }
  }

  // ------------------------------------------------------------------
  // UI
  // ------------------------------------------------------------------

  return (
    <section className="live-player">

      <div className="live-player__header">

        <div>
          <span>
            Live camera stream
          </span>

          <p>
            Zone X real-time monitoring
          </p>
        </div>

        <span className="live-player__status">
          ● LIVE
        </span>

      </div>

      <div className="live-player__toolbar">

        <button
          className={
            zoneType === 'SAFE'
              ? 'active safe'
              : ''
          }
          onClick={() =>
            void selectZoneType('SAFE')
          }
        >
          SAFE
        </button>

        <button
          className={
            zoneType === 'WARNING'
              ? 'active warning'
              : ''
          }
          onClick={() =>
            void selectZoneType(
              'WARNING',
            )
          }
        >
          WARNING
        </button>

        <button
          className={
            zoneType === 'DANGER'
              ? 'active danger'
              : ''
          }
          onClick={() =>
            void selectZoneType(
              'DANGER',
            )
          }
        >
          DANGER
        </button>

        <button
          onClick={() =>
            void startDrawing()
          }
        >
          Draw Zone
        </button>

        <button
          onClick={() =>
            void undoPoint()
          }
          disabled={
            points.length === 0
          }
        >
          Undo
        </button>

        <button
          onClick={() =>
            void finishZone()
          }
          disabled={
            points.length < 3
          }
        >
          Finish
        </button>

        <button
          onClick={() =>
            void cancelDrawing()
          }
        >
          Cancel
        </button>

        <button
          onClick={() =>
            void clearZones()
          }
        >
          Clear Zones
        </button>

        <button
          onClick={() =>
            void toggleFullscreen()
          }
        >
          {fullscreen
            ? 'Exit Fullscreen'
            : 'Fullscreen'}
        </button>

      </div>

      <div className="live-player__body">

        <div className="live-video-wrapper">

          <img
            ref={videoRef}
            src={streamUrl}
            alt="Zone X live camera feed"
            className="live-player__video"
            onLoad={redrawCanvas}
          />

          <canvas
            ref={canvasRef}
            className="live-player__canvas"
            onClick={(event) =>
              void handleCanvasClick(
                event,
              )
            }
            onMouseMove={(event) =>
              void handleCanvasMouseMove(
                event,
              )
            }
            onContextMenu={(event) =>
              void handleCanvasContextMenu(
                event,
              )
            }
          />

        </div>

      </div>

    </section>
  )
}