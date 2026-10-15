#!/usr/bin/env python3
"""
tello-video-capture.py — Tello drone video stream capture for CTF proof-of-control

Connects to a Tello drone's UDP video port, decodes the H.264 stream, and
saves frames or video to disk. Optionally displays a live preview window.
Designed to run after WiFi takeover (e.g., via tello-takeover.py) as evidence
of commandeered control.

Tello SDK Protocol Overview:
  - Command port:  UDP 8889  (send SDK text commands, receive "ok"/"error")
  - State port:    UDP 8890  (drone pushes telemetry every ~100ms)
  - Video port:    UDP 11111 (drone pushes H.264 stream after "streamon")

Flow:
  1. Send "command" to UDP 8889 to enter SDK mode
  2. Send "streamon" to start the video feed on UDP 11111
  3. Receive H.264 packets, decode with OpenCV, save frames/video
  4. Send "streamoff" + "command" exit on shutdown

Requirements:
  pip install opencv-python

Usage:
  # Headless capture — save 30 seconds of video (CTF proof-of-capture):
  python3 tello-video-capture.py --duration 30 --output capture.avi

  # Live preview with frame saving:
  python3 tello-video-capture.py --preview --save-frames --output-dir frames/

  # Just preview, no saving:
  python3 tello-video-capture.py --preview

  # Custom drone IP (if on a different subnet):
  python3 tello-video-capture.py --drone-ip 192.168.1.100 --preview

Author: CTF attack team
"""

import argparse
import datetime
import os
import signal
import socket
import struct
import sys
import threading
import time


TELLO_CMD_PORT = 8889
TELLO_STATE_PORT = 8890
TELLO_VIDEO_PORT = 11111
TELLO_DEFAULT_IP = "192.168.10.1"

LOCAL_CMD_PORT = 9000
LOCAL_VIDEO_PORT = 11111


def log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] {msg}", file=sys.stderr)


class TelloVideoCapture:
    def __init__(self, drone_ip: str = TELLO_DEFAULT_IP, local_video_port: int = LOCAL_VIDEO_PORT):
        self.drone_ip = drone_ip
        self.local_video_port = local_video_port
        self.cmd_sock: socket.socket | None = None
        self.running = False
        self.frame_count = 0
        self.sdk_active = False

    def send_command(self, cmd: str, timeout: float = 7.0) -> str | None:
        if self.cmd_sock is None:
            self.cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.cmd_sock.bind(("", LOCAL_CMD_PORT))
            self.cmd_sock.settimeout(timeout)

        log(f"TX >> {cmd}")
        self.cmd_sock.sendto(cmd.encode("utf-8"), (self.drone_ip, TELLO_CMD_PORT))

        try:
            data, _ = self.cmd_sock.recvfrom(1024)
            resp = data.decode("utf-8", errors="replace").strip()
            log(f"RX << {resp}")
            return resp
        except socket.timeout:
            log(f"TIMEOUT waiting for response to '{cmd}'")
            return None

    def enter_sdk_mode(self) -> bool:
        resp = self.send_command("command")
        if resp and "ok" in resp.lower():
            self.sdk_active = True
            log("SDK mode active")
            return True
        log("Failed to enter SDK mode — is the drone powered on and WiFi connected?")
        return False

    def start_video_stream(self) -> bool:
        if not self.sdk_active:
            if not self.enter_sdk_mode():
                return False
        resp = self.send_command("streamon")
        if resp and "ok" in resp.lower():
            log("Video stream started on UDP 11111")
            return True
        log("Failed to start video stream")
        return False

    def stop_video_stream(self) -> None:
        if self.sdk_active:
            self.send_command("streamoff", timeout=3.0)
            log("Video stream stopped")

    def query_battery(self) -> str | None:
        if self.sdk_active:
            return self.send_command("battery?", timeout=3.0)
        return None

    def capture(
        self,
        duration: float | None = None,
        max_frames: int | None = None,
        output: str | None = None,
        output_dir: str | None = None,
        save_frames: bool = False,
        preview: bool = False,
        fps: float = 30.0,
        frame_interval: int = 1,
    ) -> None:
        try:
            import cv2
        except ImportError:
            log("ERROR: opencv-python is required. Install with: pip install opencv-python")
            sys.exit(1)

        if not self.start_video_stream():
            sys.exit(1)

        time.sleep(1.0)

        bat = self.query_battery()
        if bat:
            log(f"Battery: {bat}%")

        video_url = f"udp://@0.0.0.0:{self.local_video_port}"
        log(f"Opening video stream: {video_url}")

        cap = cv2.VideoCapture(video_url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            log("ERROR: Could not open video stream. Check that the drone is streaming.")
            self.stop_video_stream()
            sys.exit(1)

        log("Video stream connected — decoding frames")

        writer: cv2.VideoWriter | None = None
        if output:
            os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
            fourcc = cv2.VideoWriter_fourcc(*"XVID")
            writer = cv2.VideoWriter(output, fourcc, fps, (960, 720))
            log(f"Recording to: {output}")

        if output_dir and save_frames:
            os.makedirs(output_dir, exist_ok=True)
            log(f"Saving frames to: {output_dir}/")

        self.running = True
        self.frame_count = 0
        start_time = time.monotonic()

        try:
            while self.running:
                ret, frame = cap.read()
                if not ret:
                    time.sleep(0.01)
                    continue

                self.frame_count += 1
                elapsed = time.monotonic() - start_time

                if duration and elapsed >= duration:
                    log(f"Duration limit reached ({duration}s)")
                    break

                if max_frames and self.frame_count >= max_frames:
                    log(f"Frame limit reached ({max_frames})")
                    break

                if writer:
                    writer.write(frame)

                if save_frames and output_dir and (self.frame_count % frame_interval == 0):
                    fname = os.path.join(output_dir, f"frame_{self.frame_count:06d}.jpg")
                    cv2.imwrite(fname, frame)

                if preview:
                    cv2.imshow("Tello Capture [q=quit]", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        log("Preview closed by user")
                        break

                if self.frame_count % 150 == 0:
                    log(f"Captured {self.frame_count} frames ({elapsed:.1f}s)")

        except KeyboardInterrupt:
            log("Interrupted by user")

        finally:
            self.running = False
            elapsed = time.monotonic() - start_time
            log(f"Capture complete: {self.frame_count} frames in {elapsed:.1f}s "
                f"({self.frame_count / max(elapsed, 0.001):.1f} fps)")

            if writer:
                writer.release()
                log(f"Video saved: {output}")

            cap.release()
            if preview:
                cv2.destroyAllWindows()

            self.stop_video_stream()

    def shutdown(self) -> None:
        self.running = False
        if self.cmd_sock:
            try:
                self.cmd_sock.close()
            except OSError:
                pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Capture video from a Tello drone after WiFi takeover (CTF proof-of-control)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Tello SDK Protocol:
  Command port:  UDP 8889  (send text commands like 'command', 'streamon')
  State port:    UDP 8890  (telemetry push from drone)
  Video port:    UDP 11111 (H.264 stream after 'streamon')

Examples:
  # Headless 30-second capture (CTF flag evidence):
  %(prog)s --duration 30 --output capture.avi

  # Live preview + save individual frames:
  %(prog)s --preview --save-frames --output-dir frames/

  # Preview only:
  %(prog)s --preview

  # Custom drone IP:
  %(prog)s --drone-ip 192.168.1.100 --preview
""",
    )

    p.add_argument("--drone-ip", default=TELLO_DEFAULT_IP,
                    help=f"Tello drone IP (default: {TELLO_DEFAULT_IP})")
    p.add_argument("--video-port", type=int, default=LOCAL_VIDEO_PORT,
                    help=f"Local UDP port for video (default: {LOCAL_VIDEO_PORT})")

    capture = p.add_argument_group("capture options")
    capture.add_argument("--duration", type=float, default=None,
                         help="Capture duration in seconds (default: until Ctrl-C)")
    capture.add_argument("--max-frames", type=int, default=None,
                         help="Stop after N frames")
    capture.add_argument("--fps", type=float, default=30.0,
                         help="Video file FPS (default: 30)")

    output = p.add_argument_group("output options")
    output.add_argument("--output", "-o", default=None,
                        help="Save video to file (e.g., capture.avi)")
    output.add_argument("--output-dir", default=None,
                        help="Directory for saved frames")
    output.add_argument("--save-frames", action="store_true",
                        help="Save individual frames as JPEG")
    output.add_argument("--frame-interval", type=int, default=1,
                        help="Save every Nth frame (default: 1 = every frame)")

    display = p.add_argument_group("display options")
    display.add_argument("--preview", action="store_true",
                         help="Show live preview window (press 'q' to quit)")

    return p


def main() -> None:
    args = build_parser().parse_args()

    if not args.output and not args.save_frames and not args.preview:
        log("WARNING: No output specified. Use --output, --save-frames, or --preview.")
        log("Defaulting to --preview mode.")
        args.preview = True

    tello = TelloVideoCapture(drone_ip=args.drone_ip, local_video_port=args.video_port)

    def handle_signal(signum, frame):
        log("Signal received, shutting down...")
        tello.shutdown()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log(f"Tello Video Capture — target: {args.drone_ip}:{TELLO_CMD_PORT}")
    log(f"Mode: {'preview' if args.preview else 'headless'} | "
        f"Record: {args.output or 'no'} | "
        f"Frames: {args.output_dir or 'no'}")

    try:
        tello.capture(
            duration=args.duration,
            max_frames=args.max_frames,
            output=args.output,
            output_dir=args.output_dir,
            save_frames=args.save_frames,
            preview=args.preview,
            fps=args.fps,
            frame_interval=args.frame_interval,
        )
    finally:
        tello.shutdown()


if __name__ == "__main__":
    main()
