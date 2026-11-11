"""Tratamento da foto que vai para a caixa de areia (gesto das mãos), para qualquer pessoa.

Passos (foto_para_areia no detector):
  1. rajada: entre os quadros do último meio segundo, o de rosto mais nítido;
  2. recorte de cabeça e ombros, nivelado pelos olhos;
  3. luz e cor: exposição acertada pelo próprio rosto (vale para pele clara ou escura),
     balanço de branco pelo cinza médio (só metade da correção), ruído e nitidez;
  4. fundo: o U²-Net de pessoas diz "o que parece gente" e o FUNDO VAZIO da cena (a câmera
     é fixa: o Willbert guarda fotos do laboratório sem ninguém) diz "o que já estava lá".
     O que já estava lá sai, mesmo encostado na cabeça (o extintor de 2026-11-11), exceto no
     miolo do corpo (rosto e tronco), onde a roupa pode ter a cor da parede;
  5. borda: só a maior região, buracos tapados, contorno suavizado pela própria imagem
     (filtro guiado: acompanha o cabelo) e a base dos ombros em degradê.
Sai um PNG com transparência: o Projetor-LAOB usa esse alfa e não recorta de novo.
"""
import logging
import os
from collections import deque

import cv2
import numpy as np

log = logging.getLogger("visao.foto")


# ------------------------------------------------------------------ nitidez e rajada
def nitidez(img):
    """Variância do laplaciano (maior = mais nítido); em cinza, no tamanho dado."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def mais_nitido(quadros, caixa=None):
    """O quadro com o rosto (ou a imagem toda) mais nítido."""
    if not quadros:
        return None

    def nota(q):
        if caixa is None:
            return nitidez(q)
        x, y, w, h = (int(v) for v in caixa)
        r = q[max(0, y):y + h, max(0, x):x + w]
        return nitidez(r) if r.size else 0.0
    return max(quadros, key=nota)


# ------------------------------------------------------------------ enquadramento
def enquadra(img, rosto, olhos=None, saida=(600, 800)):
    """Retrato 3:4 de cabeça e ombros, com os olhos nivelados. rosto = (x, y, l, a);
    olhos = ((xe, ye), (xd, yd)) do YuNet. Devolve (imagem, matriz 2x3 usada)."""
    W, H = saida
    x, y, fw, fh = rosto
    cx, cy = x + fw / 2, y + fh * 0.62     # centro um pouco abaixo do rosto: cabe o ombro
    ang = 0.0
    if olhos is not None:
        (x1, y1), (x2, y2) = olhos
        ang = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        if abs(ang) > 25:                  # leitura estranha dos olhos: não gira
            ang = 0.0
    altura = fh * 3.0                      # cabeça + pescoço + ombros
    esc = H / altura
    M = cv2.getRotationMatrix2D((cx, cy), ang, esc)
    M[0, 2] += W / 2 - cx
    M[1, 2] += H / 2 - cy
    return cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE), M


def caixa_transformada(caixa, M):
    x, y, w, h = caixa
    pts = np.array([[x, y, 1], [x + w, y, 1], [x, y + h, 1], [x + w, y + h, 1]], np.float32) @ M.T
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    return float(x0), float(y0), float(x1 - x0), float(y1 - y0)


# ------------------------------------------------------------------ luz e cor
def ajusta_luz(img, rosto):
    """Exposição pelo rosto (mediana da luminância no miolo do rosto -> ~150 de 255, com
    gama para não estourar), meio balanço de branco pelo cinza médio, ruído e nitidez."""
    out = img.astype(np.float32)
    # balanço de branco: puxa metade do caminho até o cinza médio (luz amarela/azul do lab)
    medias = out.reshape(-1, 3).mean(axis=0)
    ganho = np.clip(medias.mean() / np.maximum(medias, 1.0), 0.8, 1.25)
    out *= (1.0 + (ganho - 1.0) * 0.5)
    out = np.clip(out, 0, 255).astype(np.uint8)
    # exposição pelo rosto
    x, y, w, h = (int(v) for v in rosto)
    miolo = out[max(0, y + h // 4):y + 3 * h // 4, max(0, x + w // 4):x + 3 * w // 4]
    if miolo.size:
        L = cv2.cvtColor(miolo, cv2.COLOR_BGR2LAB)[..., 0]
        med = float(np.median(L))
        if 15 < med < 240:
            gama = np.clip(np.log(150 / 255) / np.log(med / 255), 0.4, 1.6)
            tabela = (np.clip((np.arange(256) / 255.0) ** gama, 0, 1) * 255).astype(np.uint8)
            lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
            lab[..., 0] = cv2.LUT(lab[..., 0], tabela)
            out = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    out = cv2.fastNlMeansDenoisingColored(out, None, 4, 4, 7, 21)
    borrada = cv2.GaussianBlur(out, (0, 0), 1.2)
    return cv2.addWeighted(out, 1.35, borrada, -0.35, 0)


# ------------------------------------------------------------------ fundo
class FundoCena:
    """Fotos do laboratório SEM ninguém, para saber o que é fundo. A câmera é fixa: guarda
    algumas (a cada `intervalo_s`, só quando não há rosto nem movimento) e usa a mediana
    (tira quem passou de relance). Se a câmera girar (PTZ), começa de novo."""

    def __init__(self, n=5, intervalo_s=300.0, arquivo=None):
        self.fotos = deque(maxlen=n)
        self.intervalo_s, self.ultima, self.arquivo = intervalo_s, float("-inf"), arquivo
        self._mediana = None
        if arquivo and os.path.exists(arquivo):
            img = cv2.imread(arquivo)
            if img is not None:
                self.fotos.append(img)
                self._mediana = img

    def precisa(self, agora):
        return agora - self.ultima >= (60.0 if len(self.fotos) < 2 else self.intervalo_s)

    def adiciona(self, img, agora):
        if self.fotos and self.fotos[-1].shape != img.shape:
            self.fotos.clear()
        self.fotos.append(img)
        self.ultima = agora
        self._mediana = None
        if self.arquivo:
            try:
                cv2.imwrite(self.arquivo, self.imagem())
            except Exception:
                pass

    def esquece(self):
        self.fotos.clear()
        self._mediana = None

    def imagem(self):
        if not self.fotos:
            return None
        if self._mediana is None:
            self._mediana = self.fotos[0] if len(self.fotos) == 1 else \
                np.median(np.stack(self.fotos), axis=0).astype(np.uint8)
        return self._mediana


def diferenca_do_fundo(img, fundo):
    """0..1: quanto cada ponto difere do fundo vazio (Lab, com menos peso na luz: sombra da
    própria pessoa não vira "gente")."""
    a = cv2.cvtColor(cv2.GaussianBlur(img, (0, 0), 2.0), cv2.COLOR_BGR2LAB).astype(np.float32)
    b = cv2.cvtColor(cv2.GaussianBlur(fundo, (0, 0), 2.0), cv2.COLOR_BGR2LAB).astype(np.float32)
    d = np.sqrt(0.35 * (a[..., 0] - b[..., 0]) ** 2 + (a[..., 1] - b[..., 1]) ** 2 + (a[..., 2] - b[..., 2]) ** 2)
    return np.clip((d - 8.0) / 14.0, 0, 1)


def miolo_do_corpo(shape, rosto):
    """0..1: rosto (elipse um pouco maior que a caixa) e tronco abaixo dele. Ali a pessoa
    fica mesmo que a cor bata com o fundo (camisa branca na parede branca)."""
    H, W = shape[:2]
    x, y, w, h = rosto
    cx, cy = x + w / 2, y + h / 2
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    cabeca = ((xx - cx) / (0.62 * w)) ** 2 + ((yy - cy) / (0.72 * h)) ** 2 <= 1.0
    queixo = y + h * 1.05
    larg = np.clip(0.35 * w + (yy - queixo) * 0.9, 0, None)   # pescoço que alarga nos ombros
    tronco = (yy >= queixo) & (np.abs(xx - cx) <= larg)
    m = (cabeca | tronco).astype(np.float32)
    return cv2.GaussianBlur(m, (0, 0), max(2.0, w * 0.04))


def mascara_unet(img, modelo):
    """0..1 onde parece gente (U²-Net de pessoas, ~2-3 s na CPU do lab2)."""
    net = cv2.dnn.readNetFromONNX(modelo)
    rgb = cv2.cvtColor(cv2.resize(img, (320, 320), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
    rgb = rgb.astype(np.float32) / max(float(rgb.max()), 1.0)
    rgb = (rgb - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
    net.setInput(rgb.transpose(2, 0, 1)[None].astype(np.float32))
    m = net.forward()[0, 0]
    m = (m - m.min()) / max(float(m.max() - m.min()), 1e-6)
    return cv2.resize(m, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)


def junta_mascaras(gente, diff, miolo):
    """Gente pelo U²-Net, MENOS o que já estava no fundo vazio, exceto no miolo do corpo.
    Sem fundo vazio (diff None), fica só o U²-Net."""
    if diff is None:
        return gente
    return gente * np.clip(np.maximum(diff * 1.3, miolo), 0, 1)


# ------------------------------------------------------------------ borda
def so_a_maior(m):
    """Só a maior região (> 0,5), com os buracos tapados; a borda suave original fica perto dela."""
    b = (m > 0.5).astype(np.uint8)
    n, rot, st, _ = cv2.connectedComponentsWithStats(b, 8)
    if n > 2:
        b = (rot == 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
    cheia = np.zeros_like(b)
    cont, _ = cv2.findContours(b, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    cv2.drawContours(cheia, cont, -1, 1, thickness=cv2.FILLED)
    perto = cv2.dilate(cheia, np.ones((9, 9), np.uint8)).astype(np.float32)
    return np.maximum(m * perto, cheia.astype(np.float32))


def filtro_guiado(guia, p, raio=8, eps=1e-3):
    """Suaviza a máscara p seguindo as bordas da imagem guia (He et al.): o contorno acompanha
    o cabelo em vez de ficar "recortado a tesoura"."""
    g = cv2.cvtColor(guia, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    k = (2 * raio + 1, 2 * raio + 1)
    mg, mp = cv2.boxFilter(g, -1, k), cv2.boxFilter(p, -1, k)
    cov = cv2.boxFilter(g * p, -1, k) - mg * mp
    var = cv2.boxFilter(g * g, -1, k) - mg * mg
    a = cov / (var + eps)
    b = mp - a * mg
    return np.clip(cv2.boxFilter(a, -1, k) * g + cv2.boxFilter(b, -1, k), 0, 1)


def base_em_degrade(m, fracao=0.14):
    h = m.shape[0]
    y = np.arange(h, dtype=np.float32)[:, None]
    return m * np.clip((h - 1 - y) / (fracao * h), 0, 1) ** 1.5


def alfa_final(img, m):
    """Máscara final: maior região, encolhida uns pixels (a borda do U²-Net costuma pegar um
    fio da parede), suavizada seguindo a imagem, SEM passar do contorno original (senão a
    parede clara vira um halo em volta da pessoa) e com a base em degradê."""
    m = so_a_maior(np.clip(m, 0, 1).astype(np.float32))
    dentro = (m > 0.5).astype(np.uint8)
    limite = cv2.dilate(dentro, np.ones((3, 3), np.uint8)).astype(np.float32)
    encolhida = cv2.erode(dentro, np.ones((5, 5), np.uint8)).astype(np.float32)
    m = filtro_guiado(img, encolhida, raio=4, eps=1e-2)
    m = np.clip((m - 0.15) / 0.7, 0, 1) * cv2.GaussianBlur(limite, (0, 0), 1.0)
    return base_em_degrade(m)


def png_com_alfa(img, alfa):
    return np.dstack([img, (np.clip(alfa, 0, 1) * 255).astype(np.uint8)])
