"""Tratamento da foto da areia (visao/foto.py) e a rajada em 1080p:
    python -m tests.test_foto"""
import os
import shutil
import subprocess
import tempfile

import cv2
import numpy as np

from visao import foto as F

ok = True


def check(label, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("OK   " if cond else "FALHA"), label, extra)


rng = np.random.default_rng(3)
H, W = 400, 300
parede = np.full((H, W, 3), (200, 205, 210), np.uint8)
parede[60:180, 230:270] = (40, 40, 200)            # o "extintor" vermelho, no fundo
cena = parede.copy()
rosto = (110, 90, 80, 100)                          # x, y, l, a
cv2.ellipse(cena, (150, 140), (45, 58), 0, 0, 360, (120, 150, 190), -1)   # cabeça
cv2.rectangle(cena, (80, 200), (220, 400), (205, 205, 210), -1)           # camisa da cor da parede

# --- fundo vazio
fc = F.FundoCena(n=3, intervalo_s=300)
check("sem fotos do fundo: precisa de uma", fc.precisa(0) and fc.imagem() is None)
passante = parede.copy(); passante[0:100, 0:100] = 0
for i, img in enumerate((parede, passante, parede)):
    fc.adiciona(img, 100 + i)
check("mediana tira quem passou de relance", np.array_equal(fc.imagem(), parede))
fc2 = F.FundoCena(n=5)
fc2.adiciona(passante, 1); fc2.adiciona(parede, 2)
check("com 2 fotos usa a mais nova (sem misturar)", np.array_equal(fc2.imagem(), parede))
check("com 2+ fotos, a próxima só em 5 min", not fc.precisa(200) and fc.precisa(500))
fc.esquece()
check("câmera girou: esquece o fundo", fc.imagem() is None)

# --- máscara: o que já estava no fundo sai, mesmo encostado; o miolo do corpo fica
diff = F.diferenca_do_fundo(cena, parede)
check("o extintor não difere do fundo vazio", diff[100:150, 235:265].mean() < 0.05)
check("o rosto difere do fundo vazio", diff[120:160, 130:170].mean() > 0.8)
gente = np.zeros((H, W), np.float32)
gente[80:400, 70:275] = 1.0                         # o U²-Net "achou" a pessoa e o extintor junto
miolo = F.miolo_do_corpo(cena.shape, rosto)
m = F.junta_mascaras(gente, diff, miolo)
check("extintor encostado sai", m[100:150, 240:262].mean() < 0.1, f"{m[100:150, 240:262].mean():.2f}")
check("camisa da cor da parede fica (miolo)", m[260:380, 130:170].mean() > 0.9)
check("sem fundo vazio: fica o U²-Net", np.array_equal(F.junta_mascaras(gente, None, miolo), gente))
a = F.alfa_final(cena, m)
check("borda: base em degradê", a[-1].max() < 0.05 and a[250, 150] > 0.9)
png = F.png_com_alfa(cena, a)
check("PNG com transparência (4 canais)", png.shape == (H, W, 4) and png[5, 5, 3] < 10)

# --- luz: rosto escuro fica claro; o claro não estoura
escuro = (cena * 0.35).astype(np.uint8)
claro = F.ajusta_luz(escuro, rosto)
Lr = cv2.cvtColor(claro, cv2.COLOR_BGR2LAB)[110:170, 130:170, 0].mean()
check("rosto escuro (pouca luz) fica com luz boa", 120 < Lr < 185, f"L={Lr:.0f}")
estourado = F.ajusta_luz(np.clip(cena * 1.25, 0, 255).astype(np.uint8), rosto)
Le = cv2.cvtColor(estourado, cv2.COLOR_BGR2LAB)[110:170, 130:170, 0].mean()
check("rosto claro demais escurece um pouco", Le < 200, f"L={Le:.0f}")

# --- enquadramento: olhos nivelados e cabeça centrada
M = cv2.getRotationMatrix2D((150, 140), 12, 1.0)
torta = cv2.warpAffine(cena, M, (W, H), borderMode=cv2.BORDER_REPLICATE)
olho_e = M @ np.array([130, 130, 1.0]); olho_d = M @ np.array([170, 130, 1.0])
ret, M2 = F.enquadra(torta, rosto, (tuple(olho_e), tuple(olho_d)))
e2 = M2 @ np.array([*olho_e, 1.0]); d2 = M2 @ np.array([*olho_d, 1.0])
check("olhos nivelados", abs(e2[1] - d2[1]) < 1.0, f"{e2[1]:.1f} x {d2[1]:.1f}")
check("retrato 3:4 (600x800)", ret.shape[:2] == (800, 600))
cx = (e2[0] + d2[0]) / 2
check("rosto no meio", abs(cx - 300) < 40, f"x={cx:.0f}")

# --- rajada: o quadro mais nítido
borrado = cv2.GaussianBlur(cena, (0, 0), 3)
check("mais nítido entre borrados", F.mais_nitido([borrado, cena, borrado], rosto) is cena)

# --- rajada em 1080p de um .ts gravado (como o gravador do Willbert)
if shutil.which("ffmpeg"):
    import visao.detector as det
    d = tempfile.mkdtemp()
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30", "-t", "3",
                    "-c:v", "libx264", "-g", "60", "-pix_fmt", "yuv420p", os.path.join(d, "seg_20261111-190000.ts")],
                   check=True)
    q = det.Detector.__new__(det.Detector)
    q.rec = type("R", (), {"dir": d})()
    t = q.quadros_rajada((600, 300, 400, 500))
    check("rajada: ~0,6 s de quadros recortados", 10 <= len(t) <= 25 and t[0].shape == (500, 400, 3), f"{len(t)}")
    check("tamanho do vídeo gravado", q.tamanho_alta() == (1920, 1080))
    shutil.rmtree(d)

# --- rosto pequeno não é ampliado demais
_, Mp = F.enquadra(cena, (140, 120, 20, 25))
check("rosto pequeno (longe): zoom no máximo 1,6x", abs(Mp[0, 0]) <= 1.61, f"{Mp[0, 0]:.2f}")

# --- rajada sem rosto de frente: não monta foto (o Willbert pede o gesto de novo)
import visao.detector as det2
from visao.config import load as _load
q2 = det2.Detector.__new__(det2.Detector)
q2.cfg = _load("/nonexistent.toml", detect_hardware=False)
q2.fundo = F.FundoCena()
q2.tamanho_alta = lambda: (1920, 1080)
q2.quadros_rajada = lambda regiao: [cena.copy(), cena.copy()]
q2.rosto_perto = lambda img, esperado, olhos=False: None
quem = type("T", (), {"face": type("Fa", (), {"box": (600, 300, 100, 120)})()})()
r = q2.monta_retrato(np.zeros((720, 1280, 3), np.uint8), quem)
check("ninguém de frente na rajada: sem foto", r[0] is None, f"{r[2]}")
print("TUDO OK" if ok else "HÁ FALHAS")
