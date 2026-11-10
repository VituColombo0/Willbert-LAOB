"""Acesso à câmera: quadros via RTSP/ffmpeg, gravação em anel e eventos ONVIF."""
import glob
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta

import numpy as np

log = logging.getLogger("visao.camera")


def _probe_size(url, timeout=25):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", url],
        capture_output=True, text=True, timeout=timeout)
    parts = out.stdout.strip().splitlines()[0].split(",") if out.stdout.strip() else []
    if len(parts) < 2:
        raise RuntimeError("ffprobe não retornou o tamanho do vídeo")
    return int(parts[0]), int(parts[1])


class FrameSource:
    """Mantém sempre o quadro mais recente do stream (decodificado por um ffmpeg separado)."""

    def start(self):
        threading.Thread(target=self.run, daemon=True, name=self.__class__.__name__).start()

    def __init__(self, url, every=4):
        self.url, self.every = url, max(1, int(every))
        self._lock = threading.Lock()
        self._frame, self._ts, self._seq = None, 0.0, 0
        self.online, self.fps, self.size = False, 0.0, None
        self.proc = None
        self._halt = threading.Event()

    def latest(self):
        with self._lock:
            return self._frame, self._ts, self._seq

    def stop(self):
        self._halt.set()
        self._kill()

    def _kill(self):
        p = self.proc
        if p and p.poll() is None:
            p.kill()

    def run(self):
        backoff = 2
        while not self._halt.is_set():
            try:
                w, h = _probe_size(self.url)
            except Exception as e:  # câmera fora do ar, senha errada, rede...
                self.online = False
                log.warning("câmera indisponível (%s); nova tentativa em %ss", e, backoff)
                self._halt.wait(backoff)
                backoff = min(backoff * 2, 60)
                continue
            self.size = (w, h)
            # prioridade acima do reconhecimento: com os 4 núcleos ocupados reconhecendo, a
            # decodificação ficava para trás e o detector analisava imagens de até 7 s atrás.
            cmd = ["nice", "-n", "-10",
                   "ffmpeg", "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp",
                   "-timeout", "10000000", "-threads", "1",
                   # sem buffer de entrada: o quadro analisado é o mais recente possível
                   "-fflags", "nobuffer", "-flags", "low_delay", "-i", self.url, "-an",
                   "-vf", f"select=not(mod(n\\,{self.every}))", "-fps_mode", "passthrough",
                   "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         stdin=subprocess.DEVNULL, bufsize=0)
            log.info("lendo stream %dx%d (1 a cada %d quadros)", w, h, self.every)
            started = time.time()
            threading.Thread(target=self._watchdog, args=(self.proc, started), daemon=True).start()
            n, t_fps = 0, time.time()
            try:
                while not self._halt.is_set():
                    frame = np.empty((h, w, 3), np.uint8)
                    if not self._read_into(memoryview(frame).cast("B")):
                        break
                    now = time.time()
                    with self._lock:
                        self._frame, self._ts, self._seq = frame, now, self._seq + 1
                    self.online, backoff = True, 2
                    n += 1
                    if now - t_fps >= 5:
                        self.fps, n, t_fps = n / (now - t_fps), 0, now
            finally:
                self._kill()
                self.online, self.fps = False, 0.0
            if not self._halt.is_set():
                log.warning("stream encerrado; reconectando")
                self._halt.wait(2)

    def _read_into(self, mv):
        got, total = 0, len(mv)
        while got < total:
            r = self.proc.stdout.readinto(mv[got:])
            if not r:
                return False
            got += r
        return True

    def _watchdog(self, proc, started):
        while proc.poll() is None and not self._halt.is_set():
            time.sleep(5)
            last = max(self._ts, started)
            if time.time() - last > 25:
                log.warning("sem quadros há 25 s; reiniciando o ffmpeg")
                proc.kill()
                return


class Recorder:
    """Grava o stream principal em segmentos curtos na RAM, para montar clipes de eventos."""

    SEG = 10

    def start(self):
        threading.Thread(target=self.run, daemon=True, name=self.__class__.__name__).start()

    def __init__(self, url, seg_dir, keep_s=180):
        self.url, self.dir, self.keep = url, seg_dir, keep_s
        self.proc, self.online = None, False
        self._halt = threading.Event()
        os.makedirs(seg_dir, exist_ok=True)

    def stop(self):
        self._halt.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def run(self):
        while not self._halt.is_set():
            cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp",
                   "-timeout", "10000000", "-i", self.url, "-map", "0:v:0", "-c", "copy",
                   "-f", "segment", "-segment_time", str(self.SEG), "-reset_timestamps", "1",
                   "-strftime", "1", os.path.join(self.dir, "seg_%Y%m%d-%H%M%S.ts")]
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
            self.online = True
            while self.proc.poll() is None and not self._halt.is_set():
                self._cleanup()
                self._halt.wait(5)
            self.online = False
            if not self._halt.is_set():
                log.warning("gravador parou (código %s); reiniciando", self.proc.returncode)
                self._halt.wait(5)

    def _cleanup(self):
        limit = time.time() - self.keep
        for f in glob.glob(os.path.join(self.dir, "seg_*.ts")):
            try:
                if os.path.getmtime(f) < limit:
                    os.remove(f)
            except FileNotFoundError:
                pass

    def make_clip(self, t0, t1, out):
        segs = []
        for f in sorted(glob.glob(os.path.join(self.dir, "seg_*.ts"))):
            try:
                start = datetime.strptime(os.path.basename(f)[4:-3], "%Y%m%d-%H%M%S").timestamp()
                end = os.path.getmtime(f)
            except (ValueError, FileNotFoundError):
                continue
            if start <= t1 and end >= t0:
                segs.append(f)
        if not segs:
            return False
        lst = os.path.join(self.dir, f"concat_{os.getpid()}_{threading.get_ident()}.txt")
        with open(lst, "w") as fh:
            for s in segs:
                fh.write(f"file '{s}'\n")
        try:
            r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "concat",
                                "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart", out],
                               timeout=180)
        finally:
            os.remove(lst)
        return r.returncode == 0 and os.path.exists(out)


def _wsdl_dir():
    import onvif
    cands = glob.glob(os.path.join(sys.prefix, "lib", "python*", "site-packages", "wsdl"))
    cands.append(os.path.join(os.path.dirname(os.path.dirname(onvif.__file__)), "wsdl"))
    for c in cands:
        if os.path.exists(os.path.join(c, "devicemgmt.wsdl")):
            return c
    raise RuntimeError("arquivos WSDL do ONVIF não encontrados")


MOTION_KEYS = {"IsMotion", "IsPeople", "IsPerson", "IsHuman"}


class OnvifWatcher:
    """Recebe os eventos de movimento da câmera e acompanha a posição do pan/tilt."""

    def start(self):
        threading.Thread(target=self.run, daemon=True, name=self.__class__.__name__).start()

    def __init__(self, cam):
        self.cam = cam
        self.state = "iniciando"
        self.motion_on, self.motion_ts = False, 0.0
        self.ptz_pos, self.ptz_ts = None, 0.0
        self.event_count = 0
        self._topics = set()
        self._halt = threading.Event()

    def motion_recent(self, s):
        now = time.time()
        return (self.motion_on and now - self.motion_ts < 120) or now - self.motion_ts < s

    def ptz_recent(self, s):
        return time.time() - self.ptz_ts < s

    def stop(self):
        self._halt.set()

    def run(self):
        from onvif import ONVIFCamera
        backoff = 5
        while not self._halt.is_set():
            pp = None
            try:
                c = self.cam
                cam = ONVIFCamera(c.host, c.onvif_port, c.user, c.password, _wsdl_dir())
                token = cam.create_media_service().GetProfiles()[0].token
                try:
                    ptz = cam.create_ptz_service()
                except Exception:
                    ptz = None
                pp = cam.create_pullpoint_service()
                self.state, self.motion_on, backoff = "ok", False, 5
                log.info("ONVIF conectado (eventos%s)", " + PTZ" if ptz else "")
                last_ptz = 0.0
                while not self._halt.is_set():
                    if ptz is not None and time.time() - last_ptz >= 1:
                        self._poll_ptz(ptz, token)
                        last_ptz = time.time()
                    msgs = pp.PullMessages({"Timeout": timedelta(seconds=1), "MessageLimit": 50})
                    for m in getattr(msgs, "NotificationMessage", None) or []:
                        self._on_message(m)
            except Exception as e:
                self.state = f"erro: {type(e).__name__}"
                log.warning("ONVIF: %s; reconectando em %ss", e, backoff)
                try:
                    if pp is not None:
                        pp.Unsubscribe()
                except Exception:
                    pass
                self._halt.wait(backoff)
                backoff = min(backoff * 2, 120)

    def _poll_ptz(self, ptz, token):
        st = ptz.GetStatus({"ProfileToken": token})
        p = st.Position.PanTilt
        pos = (float(p.x), float(p.y))
        if self.ptz_pos is not None and (abs(pos[0] - self.ptz_pos[0]) > 0.002
                                         or abs(pos[1] - self.ptz_pos[1]) > 0.002):
            self.ptz_ts = time.time()
        self.ptz_pos = pos

    def _on_message(self, m):
        try:
            topic = str(m.Topic._value_1)
        except Exception:
            topic = "?"
        items = {}
        el = getattr(m.Message, "_value_1", None)
        if el is not None and hasattr(el, "iter"):
            for it in el.iter():
                if isinstance(it.tag, str) and it.tag.endswith("SimpleItem"):
                    items[it.get("Name")] = it.get("Value")
        self.event_count += 1
        if topic not in self._topics:
            self._topics.add(topic)
            log.info("tópico ONVIF novo: %s %s", topic, items)
        for k, v in items.items():
            if k in MOTION_KEYS or (k == "State" and "Motion" in topic):
                self.motion_on = str(v).lower() in ("true", "1")
                self.motion_ts = time.time()
