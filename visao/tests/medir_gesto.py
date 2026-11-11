"""Mede o detector de palmas com quadros reais da câmera (go2rtc local) e mostra quando o
gesto (mãos abertas ao lado do rosto) aparece. Não toca no Willbert em execução.
    python -m tests.medir_gesto <modelo palm.onnx> [segundos]"""
import sys
import time
import urllib.request

import cv2 as cv
import numpy as np

from visao.gesto import Palmas, maos_ao_lado

cv.setNumThreads(1)
pal = Palmas(sys.argv[1])
face = cv.FaceDetectorYN.create("/opt/visao/models/face_detection_yunet_2023mar.onnx", "", (320, 320), 0.7)
fim = time.time() + float(sys.argv[2] if len(sys.argv) > 2 else 5)
while time.time() < fim:
    with urllib.request.urlopen("http://127.0.0.1:1984/api/frame.jpeg?src=camera", timeout=10) as r:
        img = cv.imdecode(np.frombuffer(r.read(), np.uint8), cv.IMREAD_COLOR)
    h, w = img.shape[:2]
    t0 = time.perf_counter()
    face.setInputSize((w, h))
    _, fs = face.detect(img)
    t1 = time.perf_counter()
    ps = pal.detecta(img)
    t2 = time.perf_counter()
    rostos = [] if fs is None else [tuple(map(float, f[:4])) for f in fs]
    gesto = any(maos_ao_lado(r, ps) for r in rostos if r[2] >= 50)
    print(f"{time.strftime('%H:%M:%S')} rosto {(t1 - t0) * 1000:.0f} ms ({len(rostos)}) palmas {(t2 - t1) * 1000:.0f} ms "
          f"{[(int(p[0]), int(p[1]), round(p[4], 2)) for p in ps]}{'  <<< GESTO' if gesto else ''}", flush=True)
    time.sleep(0.25)
