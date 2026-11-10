"""Prepara uma foto para o modo Rosto do SARndbox.

  python rosto.py <foto.jpg> [--giro 0] [--espelho] [--sem-recorte]

Gera em fotos/:
  rosto.png  o rosto recortado (retrato 3:4), contraste reforçado para projetar na areia,
             bordas escurecendo em elipse
  rosto.dem  relevo-alvo para o modo Esculpir (altura em cm sobre a caixa de areia)
  teste.png  (com --teste) uma imagem de orientação: seta para CIMA e cantos coloridos
Usa o detector de rosto do Willbert (YuNet). Sem rosto achado, usa o centro da foto.
"""
import argparse
import os
import struct
import sys

import cv2
import numpy as np

AQUI = os.path.dirname(os.path.abspath(__file__))
FOTOS = os.path.join(AQUI, "fotos")
BOX = "/root/etc/SARndbox-2.8/BoxLayout.txt"
YUNET = "/opt/visao/models/face_detection_yunet_2023mar.onnx"
SAIDA_W, SAIDA_H = 600, 800


def caixa_cm():
    """Extensão da caixa de areia (cm) no espaço da caixa, igual ao SARndbox."""
    linhas = [l.strip() for l in open(BOX) if l.strip()]
    nums = [float(x) for x in linhas[0].replace("(", " ").replace(")", " ").replace(",", " ").split()]
    n = np.array(nums[:3]); n /= np.linalg.norm(n); off = nums[3]
    cs = []
    for l in linhas[1:5]:
        p = np.array([float(x) for x in l.replace("(", " ").replace(")", " ").replace(",", " ").split()])
        cs.append(p - (p @ n - off) * n)  # projeta no plano base
    x = (cs[1] - cs[0]) + (cs[3] - cs[2]); x -= (x @ n) * n; x /= np.linalg.norm(x)
    y = np.cross(n, x)
    c = ((cs[0] + cs[1]) / 2 + (cs[2] + cs[3]) / 2) / 2
    b = np.array([[(p - c) @ x, (p - c) @ y] for p in cs])
    bmin, bmax = b.min(0), b.max(0)
    # só a parte que o projetor ilumina (igual ao Sandbox.cpp): cantos da imagem até o plano base
    pm = os.path.join(os.path.dirname(BOX), "ProjectorMatrix.dat")
    if os.path.exists(pm):
        inv = np.linalg.inv(np.array(struct.unpack("<16d", open(pm, "rb").read()[:128])).reshape(4, 4))
        pts = []
        for nx, ny in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            p0, p1 = (inv @ np.array([nx, ny, z, 1.0]) for z in (-1.0, 1.0))
            p0, p1 = p0[:3] / p0[3], p1[:3] / p1[3]
            d0, d1 = p0 @ n - off, p1 @ n - off
            p = p0 + (p1 - p0) * (d0 / (d0 - d1))
            pts.append([(p - c) @ x, (p - c) @ y])
        xs, ys = np.sort(np.array(pts), axis=0).T
        bmin = np.maximum(bmin, [xs[1], ys[1]]); bmax = np.minimum(bmax, [xs[2], ys[2]])
    return bmin, bmax


def acha_rosto(img):
    if not os.path.exists(YUNET):
        return None
    h, w = img.shape[:2]
    esc = 640 / max(h, w)
    peq = cv2.resize(img, (int(w * esc), int(h * esc))) if esc < 1 else img
    esc = min(esc, 1.0)
    det = cv2.FaceDetectorYN.create(YUNET, "", (peq.shape[1], peq.shape[0]), 0.6, 0.3, 50)
    _, faces = det.detect(peq)
    if faces is None or len(faces) == 0:
        return None
    f = max(faces, key=lambda r: r[2] * r[3])  # o maior rosto
    return [v / esc for v in f[:4]]


def recorta(img, rosto):
    h, w = img.shape[:2]
    if rosto is None:
        cx, cy, ch = w / 2, h / 2, min(h, w * 4 / 3)
    else:
        x, y, fw, fh = rosto
        cx, cy = x + fw / 2, y + fh * 0.45  # um pouco acima do centro: cabelo cabe
        ch = fh * 2.1                       # rosto + cabelo + queixo
    cw = ch * 3 / 4
    M = np.float32([[SAIDA_W / cw, 0, SAIDA_W / 2 - cx * SAIDA_W / cw],
                    [0, SAIDA_H / ch, SAIDA_H / 2 - cy * SAIDA_H / ch]])
    return cv2.warpAffine(img, M, (SAIDA_W, SAIDA_H), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE)


def realca(img):
    """Projetado na areia o contraste cai muito: reforça contraste e cor, escurece as bordas."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    lab[..., 0] = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(6, 6)).apply(lab[..., 0])
    img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] = np.clip(hsv[..., 1] * 1.25, 0, 255)
    img = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    yy, xx = np.mgrid[0:SAIDA_H, 0:SAIDA_W]
    r = np.sqrt(((xx - SAIDA_W / 2) / (SAIDA_W * 0.5)) ** 2 + ((yy - SAIDA_H / 2) / (SAIDA_H * 0.5)) ** 2)
    vinheta = np.clip(1.25 - r, 0, 1) ** 0.7
    return (img * vinheta[..., None]).astype(np.uint8)


def relevo(img):
    """Altura (cm) para esculpir: cúpula da cabeça + detalhes pela luminosidade
    (olhos e boca afundam, nariz e testa sobem). Sem câmera 3D é uma aproximação."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255
    g = cv2.GaussianBlur(g, (0, 0), 6)
    yy, xx = np.mgrid[0:SAIDA_H, 0:SAIDA_W]
    r2 = ((xx - SAIDA_W / 2) / (SAIDA_W * 0.42)) ** 2 + ((yy - SAIDA_H * 0.48) / (SAIDA_H * 0.46)) ** 2
    cupula = np.sqrt(np.clip(1 - r2, 0, 1))
    h = 9.0 * cupula + 4.0 * (g - g[cupula > 0.3].mean()) * (cupula > 0)
    return np.clip(h, 0, 14)


def grava_dem(h, giro, espelho, caminho):
    """DEM no espaço da caixa (como o SARndbox): mesma posição/giro que a foto projetada."""
    bmin, bmax = caixa_cm()
    bw, bh = bmax - bmin
    r = giro % 360
    ext_x, ext_y = (bh, bw) if r in (90, 270) else (bw, bh)
    aspect = SAIDA_W / SAIDA_H
    H = min(ext_y, ext_x / aspect); W = H * aspect
    nx, ny = 200, int(200 * bh / bw)
    xs = np.linspace(bmin[0], bmax[0], nx); ys = np.linspace(bmin[1], bmax[1], ny)
    bx, by = np.meshgrid(xs - (bmin[0] + bmax[0]) / 2, ys - (bmin[1] + bmax[1]) / 2)
    c = {0: 1, 90: 0, 180: -1, 270: 0}[r]; s = {0: 0, 90: 1, 180: 0, 270: -1}[r]
    px, py = c * bx - s * by, s * bx + c * by
    if espelho:
        px = -px
    u, v = px / W + 0.5, py / H + 0.5
    dentro = (u >= 0) & (u <= 1) & (v >= 0) & (v <= 1)
    col = np.clip(u * (SAIDA_W - 1), 0, SAIDA_W - 1).astype(int)
    lin = np.clip((1 - v) * (SAIDA_H - 1), 0, SAIDA_H - 1).astype(int)  # v para cima, linha 0 em cima
    dem = np.where(dentro, h[lin, col], 0).astype(np.float32)
    with open(caminho, "wb") as f:
        f.write(struct.pack("<2i", nx, ny))
        f.write(struct.pack("<4f", bmin[0], bmin[1], bmax[0], bmax[1]))
        f.write(dem.astype("<f4").tobytes())


def imagem_teste():
    img = np.full((SAIDA_H, SAIDA_W, 3), 40, np.uint8)
    cv2.arrowedLine(img, (300, 650), (300, 150), (255, 255, 255), 40, tipLength=0.35)
    cv2.putText(img, "CIMA", (190, 760), cv2.FONT_HERSHEY_SIMPLEX, 3, (255, 255, 255), 8)
    for (x, y), cor in (((0, 0), (0, 0, 255)), ((SAIDA_W - 150, 0), (0, 255, 0)),
                        ((0, SAIDA_H - 150), (255, 0, 0)), ((SAIDA_W - 150, SAIDA_H - 150), (0, 255, 255))):
        img[y:y + 150, x:x + 150] = cor
    return img  # vermelho em cima à esquerda, verde em cima à direita, azul embaixo à esquerda, amarelo embaixo à direita


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("foto", nargs="?")
    ap.add_argument("--giro", type=int, default=0)
    ap.add_argument("--espelho", action="store_true")
    ap.add_argument("--sem-recorte", action="store_true")
    ap.add_argument("--teste", action="store_true")
    a = ap.parse_args()
    os.makedirs(FOTOS, exist_ok=True)
    if a.teste:
        img = imagem_teste()
        cv2.imwrite(os.path.join(FOTOS, "teste.png"), img)
        grava_dem(np.zeros((SAIDA_H, SAIDA_W), np.float32), a.giro, a.espelho, os.path.join(FOTOS, "teste.dem"))
        print(os.path.join(FOTOS, "teste.png"))
        return
    img = cv2.imread(a.foto)
    if img is None:
        sys.exit(f"não consegui abrir {a.foto}")
    rosto = None if a.sem_recorte else acha_rosto(img)
    if not a.sem_recorte and rosto is None:
        print("aviso: não achei um rosto; usando o centro da foto", file=sys.stderr)
    out = realca(recorta(img, rosto))
    cv2.imwrite(os.path.join(FOTOS, "rosto.png"), out)
    grava_dem(relevo(out), a.giro, a.espelho, os.path.join(FOTOS, "rosto.dem"))
    print("rosto achado" if rosto is not None else "sem rosto", os.path.join(FOTOS, "rosto.png"))


if __name__ == "__main__":
    main()
