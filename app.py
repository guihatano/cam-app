import cv2
import hashlib
import hmac
import os
import secrets
import subprocess
import tempfile
import threading
import time
from datetime import datetime, date
from urllib.parse import urlparse

from flask import (
    Flask, Response, jsonify, render_template, request, send_file, abort,
    session, redirect, url_for,
)
from dotenv import load_dotenv

from xmeye import XMEyeClient, StreamDemux
from onvif_ptz import OnvifPTZ

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)

RTSP_URL = os.getenv("RTSP_URL", "rtsp://admin:admin@192.168.1.100:554/")
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "80"))
PORT = int(os.getenv("PORT", "5000"))

AUTH_USER = os.getenv("AUTH_USER", "admin")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "")

_parsed = urlparse(RTSP_URL)
CAMERA_HOST = _parsed.hostname
CAMERA_USER = _parsed.username or "admin"
CAMERA_PASS = _parsed.password or ""

ONVIF_PORT = int(os.getenv("ONVIF_PORT", "8899"))
PTZ_SPEED = float(os.getenv("PTZ_SPEED", "0.5"))
ptz = OnvifPTZ(CAMERA_HOST, CAMERA_USER, CAMERA_PASS, port=ONVIF_PORT)


# Stream usado no "Ao vivo". Recomendado apontar para o substream (resolução menor):
# decodificar o stream principal em tempo real custa muita CPU em máquinas ARM.
LIVE_RTSP_URL = os.getenv("LIVE_RTSP_URL") or RTSP_URL
# Segundos que a captura continua ativa depois que o último espectador sai
LIVE_IDLE_TIMEOUT = 10


class CameraStream:
    """Captura RTSP sob demanda: só decodifica enquanto houver alguém assistindo.

    Cada quadro é convertido para JPEG uma única vez e compartilhado entre todos
    os espectadores. A captura para LIVE_IDLE_TIMEOUT segundos após o último sair.
    """

    def __init__(self, url):
        self.url = url
        self.cond = threading.Condition()
        self.frame = None
        self.jpeg = None
        self.seq = 0
        self.viewers = 0
        self.last_used = 0.0
        self._thread = None

    def acquire(self):
        with self.cond:
            self.viewers += 1
            self.last_used = time.monotonic()
            if self._thread is None:
                self._thread = threading.Thread(target=self._capture_loop, daemon=True)
                self._thread.start()

    def release(self):
        with self.cond:
            self.viewers -= 1
            self.last_used = time.monotonic()

    def _should_stop(self):
        return self.viewers <= 0 and time.monotonic() - self.last_used > LIVE_IDLE_TIMEOUT

    def _capture_loop(self):
        cap = None
        encode_params = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        try:
            while True:
                with self.cond:
                    if self._should_stop():
                        self._finish()
                        return

                if cap is None:
                    cap = cv2.VideoCapture(self.url)
                    if not cap.isOpened():
                        cap.release()
                        cap = None
                        time.sleep(3)
                        continue

                ret, frame = cap.read()
                if not ret:
                    cap.release()
                    cap = None
                    time.sleep(1)
                    continue

                _, buffer = cv2.imencode(".jpg", frame, encode_params)
                with self.cond:
                    self.frame = frame
                    self.jpeg = buffer.tobytes()
                    self.seq += 1
                    self.cond.notify_all()
        finally:
            if cap is not None:
                cap.release()
            with self.cond:
                if self._thread is threading.current_thread():  # saída por exceção
                    self._finish()

    def _finish(self):
        """Marca a captura como parada. Deve ser chamado com self.cond adquirido,
        na mesma seção crítica da decisão de parar, para acquire() nunca ver uma
        thread que já está saindo."""
        # descarta o último quadro para não servir imagem velha na próxima conexão
        self.frame = self.jpeg = None
        self._thread = None
        self.cond.notify_all()

    def wait_jpeg(self, last_seq, timeout=5):
        """Espera um JPEG mais novo que last_seq. Retorna (seq, jpeg), jpeg=None se expirar."""
        with self.cond:
            self.cond.wait_for(lambda: self.seq != last_seq and self.jpeg is not None, timeout)
            if self.seq == last_seq or self.jpeg is None:
                return last_seq, None
            return self.seq, self.jpeg

    def wait_frame(self, timeout=10):
        with self.cond:
            self.cond.wait_for(lambda: self.frame is not None, timeout)
            return self.frame.copy() if self.frame is not None else None


camera = CameraStream(LIVE_RTSP_URL)


def generate_mjpeg(cam):
    cam.acquire()
    try:
        seq = 0
        while True:
            seq, jpeg = cam.wait_jpeg(seq)
            if jpeg is None:
                continue
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
            )
    finally:
        # chamado quando o cliente desconecta (o servidor fecha o gerador)
        cam.release()


def capture_snapshot():
    """Quadro para o snapshot, sempre do stream principal (resolução máxima)."""
    if LIVE_RTSP_URL == RTSP_URL:
        camera.acquire()
        try:
            return camera.wait_frame()
        finally:
            camera.release()

    cap = cv2.VideoCapture(RTSP_URL)
    try:
        for _ in range(30):
            ret, frame = cap.read()
            if ret:
                return frame
        return None
    finally:
        cap.release()


@app.before_request
def require_login():
    if request.endpoint in ("login", "static"):
        return
    if session.get("auth"):
        return
    if request.method == "GET" and "text/html" in request.headers.get("Accept", ""):
        return redirect(url_for("login", next=request.path))
    return "Unauthorized", 401


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        user = request.form.get("user", "")
        pw = request.form.get("password", "")
        ok_user = hmac.compare_digest(user, AUTH_USER)
        ok_pw = bool(AUTH_PASSWORD) and hmac.compare_digest(pw, AUTH_PASSWORD)
        if ok_user and ok_pw:
            session["auth"] = True
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") else url_for("index"))
        error = "Usuário ou senha inválidos."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/stream")
def stream():
    return Response(
        generate_mjpeg(camera),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/snapshot")
def snapshot():
    frame = capture_snapshot()
    if frame is None:
        return "Camera not ready", 503
    _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return Response(buffer.tobytes(), mimetype="image/jpeg")


_PTZ_DIRS = {
    "up":    (0,  1), "down":  (0, -1),
    "left":  (-1, 0), "right": (1,  0),
    "upleft":   (-1,  1), "upright":   (1,  1),
    "downleft": (-1, -1), "downright": (1, -1),
}


@app.route("/ptz/move")
def ptz_move():
    direction = request.args.get("dir", "")
    if direction in _PTZ_DIRS:
        px, py = _PTZ_DIRS[direction]
        # Esta câmera tem o eixo de pan invertido em relação ao ONVIF padrão
        pan, tilt, zoom = -px * PTZ_SPEED, py * PTZ_SPEED, 0.0
    elif direction == "zoomin":
        pan, tilt, zoom = 0.0, 0.0, PTZ_SPEED
    elif direction == "zoomout":
        pan, tilt, zoom = 0.0, 0.0, -PTZ_SPEED
    else:
        return "Invalid dir", 400
    try:
        ptz.move(pan=pan, tilt=tilt, zoom=zoom)
    except Exception as e:
        return f"PTZ error: {e}", 502
    return "", 204


@app.route("/ptz/stop")
def ptz_stop():
    try:
        ptz.stop()
    except Exception as e:
        return f"PTZ error: {e}", 502
    return "", 204


@app.route("/api/recordings")
def api_recordings():
    date_str = request.args.get("date", date.today().isoformat())
    try:
        query_date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Invalid date format"}), 400

    try:
        with XMEyeClient(CAMERA_HOST) as client:
            if not client.login(CAMERA_USER, CAMERA_PASS):
                return jsonify({"error": "Camera login failed"}), 401
            files = client.search_recordings(query_date)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    recordings = []
    for f in files:
        start = f.get("StartTime") or f.get("BeginTime") or f.get("STIME", "")
        end   = f.get("EndTime")   or f.get("ETIME", "")
        name  = f.get("FileName", "")
        recordings.append({"start": start, "end": end, "filename": name})

    return jsonify({"date": date_str, "recordings": recordings})


RECORDINGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")
os.makedirs(RECORDINGS_DIR, exist_ok=True)
_playback_locks = {}
_playback_locks_guard = threading.Lock()


def _output_name(start):
    """Nome de arquivo legível derivado do horário de início: 2026-06-27_00-00-00.mp4"""
    try:
        dt = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d_%H-%M-%S") + ".mp4"
    except ValueError:
        return hashlib.md5(start.encode()).hexdigest() + ".mp4"


def _prepare_playback(filename, start, end):
    """Baixa o segmento e transcoda para MP4 (H.264), baixando e convertendo em paralelo.

    Os dados da câmera são demuxados conforme chegam e enviados ao ffmpeg via pipe,
    sobrepondo download e conversão. Resultado fica cacheado em RECORDINGS_DIR.
    """
    name = _output_name(start)
    mp4_path = os.path.join(RECORDINGS_DIR, name)
    if os.path.exists(mp4_path):
        return mp4_path

    with _playback_locks_guard:
        lock = _playback_locks.setdefault(name, threading.Lock())
    with lock:
        if os.path.exists(mp4_path):
            return mp4_path

        tmp_path = mp4_path + ".part"
        err_log = tempfile.TemporaryFile()
        proc = subprocess.Popen(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-r", "12", "-i", "pipe:0",
                "-vf", "scale=-2:720",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                "-movflags", "+faststart", "-f", "mp4",
                tmp_path,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=err_log,
        )

        demux = StreamDemux()

        def on_data(chunk):
            data = demux.feed(chunk)
            if data:
                proc.stdin.write(data)  # bloqueia se ffmpeg ficar para trás (backpressure)

        try:
            with XMEyeClient(CAMERA_HOST) as client:
                if not client.login(CAMERA_USER, CAMERA_PASS):
                    raise RuntimeError("Camera login failed")
                client.download_recording(filename, start, end, on_data)
            proc.stdin.close()
            ret = proc.wait()
        except Exception as e:
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.wait()
            err_log.seek(0)
            detail = err_log.read().decode(errors="replace")[:300]
            err_log.close()
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise RuntimeError(f"{e}; ffmpeg: {detail}".strip("; "))

        err_log.seek(0)
        detail = err_log.read().decode(errors="replace")[:300]
        err_log.close()
        if ret != 0 or not os.path.exists(tmp_path):
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise RuntimeError("ffmpeg falhou: " + detail)

        os.replace(tmp_path, mp4_path)
        return mp4_path


@app.route("/playback")
def playback():
    filename = request.args.get("filename", "")
    start    = request.args.get("start", "")
    end      = request.args.get("end", "")
    if not filename or not start or not end:
        return "Missing filename/start/end", 400

    try:
        mp4_path = _prepare_playback(filename, start, end)
    except Exception as e:
        return f"Playback error: {e}", 500

    return send_file(mp4_path, mimetype="video/mp4", conditional=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, threaded=True)
