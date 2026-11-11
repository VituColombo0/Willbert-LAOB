"""Gesto para tirar a foto da areia: as duas mãos abertas, uma de cada lado do rosto.

Detector de palmas: o modelo palm_detection_mediapipe_2023feb.onnx da coleção do OpenCV
(opencv_zoo, Apache 2.0), rodando no cv.dnn. Entrada 192x192 RGB 0..1 (imagem com borda
para ficar quadrada); saídas: 2016 caixas (cx, cy, w, h + 7 pontos, em px de 192) relativas
a âncoras fixas e 2016 notas (logit). A decodificação aqui é nossa.

O gesto vale quando, com um rosto grande o bastante, há uma palma à esquerda e outra à
direita dele, na altura do rosto, por `segurar_s` segundos (evita disparar quando alguém só
passa a mão no cabelo).
"""
import time

import cv2 as cv
import numpy as np

ENTRADA = 192


def ancoras():
    """Âncoras do detector de palmas (SSD do MediaPipe, 192 px): passo 8 -> grade 24x24 com 2
    âncoras; passo 16 (3 camadas juntas) -> grade 12x12 com 6. Centros em fração da imagem."""
    pts = []
    for grade, por_lugar in ((24, 2), (12, 6)):
        for y in range(grade):
            for x in range(grade):
                pts.extend([((x + 0.5) / grade, (y + 0.5) / grade)] * por_lugar)
    return np.array(pts, np.float32)


class Palmas:
    def __init__(self, modelo, nota_min=0.6, nms=0.3):
        self.net = cv.dnn.readNet(modelo)
        self.nota_min, self.nms = nota_min, nms
        self.anc = ancoras()

    def detecta(self, img):
        """Lista de (x1, y1, x2, y2, nota) em px da imagem."""
        h, w = img.shape[:2]
        lado = max(h, w)
        r = ENTRADA / lado
        pw, ph = int(round(w * r)), int(round(h * r))
        esq, topo = (ENTRADA - pw) // 2, (ENTRADA - ph) // 2
        peq = cv.resize(img, (pw, ph))
        peq = cv.copyMakeBorder(peq, topo, ENTRADA - ph - topo, esq, ENTRADA - pw - esq,
                                cv.BORDER_CONSTANT, value=(0, 0, 0))
        blob = cv.cvtColor(peq, cv.COLOR_BGR2RGB).astype(np.float32)[None] / 255.0
        self.net.setInput(blob)
        saidas = self.net.forward(self.net.getUnconnectedOutLayersNames())
        caixas, notas = (saidas[0], saidas[1]) if saidas[0].shape[-1] == 18 else (saidas[1], saidas[0])
        notas = 1.0 / (1.0 + np.exp(-notas[0, :, 0].astype(np.float64)))
        d = caixas[0, :, :4] / ENTRADA
        centro = d[:, :2] + self.anc
        x1y1 = (centro - d[:, 2:] / 2) * lado
        x2y2 = (centro + d[:, 2:] / 2) * lado
        desloc = np.array([esq, topo], np.float32) / r
        x1y1 -= desloc
        x2y2 -= desloc
        cx = np.concatenate([x1y1, x2y2 - x1y1], axis=1)  # x, y, largura, altura para o NMS
        manter = cv.dnn.NMSBoxes(cx.tolist(), notas.tolist(), self.nota_min, self.nms)
        manter = np.array(manter).reshape(-1)
        return [(float(x1y1[i, 0]), float(x1y1[i, 1]), float(x2y2[i, 0]), float(x2y2[i, 1]), float(notas[i]))
                for i in manter]


def maos_ao_lado(rosto, palmas):
    """rosto = (x, y, largura, altura). True se há uma palma de cada lado do rosto, na altura
    dele (do queixo a um pouco acima da testa) e perto (até ~2 rostos de distância)."""
    x, y, fw, fh = rosto
    cx = x + fw / 2
    esq = dir_ = False
    for x1, y1, x2, y2, _ in palmas:
        px, py = (x1 + x2) / 2, (y1 + y2) / 2
        if not (y - 0.6 * fh <= py <= y + 1.3 * fh):
            continue
        if cx - 2.6 * fw <= px <= x - 0.05 * fw:
            esq = True
        elif x + fw + 0.05 * fw <= px <= cx + 2.6 * fw:
            dir_ = True
    return esq and dir_


class Gesto:
    """Junta as leituras: dispara uma vez quando as mãos ficam ao lado do rosto por segurar_s."""

    def __init__(self, segurar_s=1.5, pausa_s=20.0, folga_s=1.0):
        self.segurar_s, self.pausa_s, self.folga_s = segurar_s, pausa_s, folga_s
        self.desde = None      # início do gesto atual
        self.ultimo_ok = 0.0   # última leitura com o gesto (aceita uma leitura falhada no meio: folga_s)
        self.disparou = float("-inf")  # nenhuma foto ainda

    def leitura(self, tem_gesto, agora=None):
        agora = time.time() if agora is None else agora
        if tem_gesto:
            if self.desde is None or agora - self.ultimo_ok > self.folga_s:
                self.desde = agora
            self.ultimo_ok = agora
            if agora - self.desde >= self.segurar_s and agora - self.disparou >= self.pausa_s:
                self.disparou, self.desde = agora, None
                return True
        elif self.desde is not None and agora - self.ultimo_ok > self.folga_s:
            self.desde = None
        return False
