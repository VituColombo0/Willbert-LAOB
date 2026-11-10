"""Detecção (YuNet), alinhamento, qualidade e reconhecimento (SFace) de rostos."""
import os
from dataclasses import dataclass

import cv2
import numpy as np

from . import db

DET_MODEL = "face_detection_yunet_2023mar.onnx"
REC_MODEL = "face_recognition_sface_2021dec.onnx"


@dataclass
class Face:
    row: np.ndarray  # 15 valores do YuNet, já no referencial do quadro original

    @property
    def box(self):
        x, y, w, h = self.row[:4]
        return int(x), int(y), int(w), int(h)

    @property
    def w(self):
        return float(self.row[2])

    @property
    def score(self):
        return float(self.row[14])

    @property
    def center(self):
        x, y, w, h = self.row[:4]
        return float(x + w / 2), float(y + h / 2)

    def yaw(self):
        """Aproximação de quão virado o rosto está (0 = de frente)."""
        lm = self.row[4:14].reshape(5, 2)
        eye_mid = (lm[0] + lm[1]) / 2
        eye_dist = float(np.linalg.norm(lm[0] - lm[1])) + 1e-6
        return float((lm[2][0] - eye_mid[0]) / eye_dist)

    def pitch(self):
        """Posição vertical do nariz entre os olhos e a boca (~0,45 de frente; perto de 1 =
        olhando para baixo; perto de 0 = olhando para cima)."""
        lm = self.row[4:14].reshape(5, 2)
        eye_y = (lm[0][1] + lm[1][1]) / 2
        mouth_y = (lm[3][1] + lm[4][1]) / 2
        return float((lm[2][1] - eye_y) / (mouth_y - eye_y + 1e-6))


def sharpness(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


class YuNetClassic:
    """YuNet rodando no motor clássico do OpenCV 5 (~30% mais rápido na TV Box que o motor
    novo usado pelo FaceDetectorYN). Mesma saída do FaceDetectorYN: x, y, w, h, 5 pontos, score."""

    STRIDES = (8, 16, 32)

    def __init__(self, path, score=0.6, nms=0.3, top_k=50):
        self.net = cv2.dnn.readNetFromONNX(path, engine=cv2.dnn.ENGINE_CLASSIC)
        self.names = self.net.getUnconnectedOutLayersNames()
        self.score, self.nms, self.top_k = float(score), float(nms), int(top_k)
        self._grid = {}

    def _priors(self, H, W):
        key = (H, W)
        if key not in self._grid:
            g = []
            for st in self.STRIDES:
                rows, cols = H // st, W // st
                r, c = np.divmod(np.arange(rows * cols, dtype=np.float32), cols)
                g.append((c, r, float(st)))
            self._grid[key] = g
        return self._grid[key]

    def detect(self, img):
        h, w = img.shape[:2]
        H, W = -(-h // 32) * 32, -(-w // 32) * 32  # a rede pede múltiplos de 32: completa com preto
        if (H, W) != (h, w):
            img = cv2.copyMakeBorder(img, 0, H - h, 0, W - w, cv2.BORDER_CONSTANT)
        self.net.setInput(cv2.dnn.blobFromImage(img))
        outs = dict(zip(self.names, self.net.forward(self.names)))
        rows = []
        for (c, r, st), stv in zip(self._priors(H, W), self.STRIDES):
            cls = np.clip(outs[f"cls_{stv}"].reshape(-1), 0, 1)
            obj = np.clip(outs[f"obj_{stv}"].reshape(-1), 0, 1)
            score = np.sqrt(cls * obj)
            keep = score >= self.score
            if not keep.any():
                continue
            bb = outs[f"bbox_{stv}"].reshape(-1, 4)[keep]
            kp = outs[f"kps_{stv}"].reshape(-1, 10)[keep]
            cc, rr = c[keep], r[keep]
            cx, cy = (cc + bb[:, 0]) * st, (rr + bb[:, 1]) * st
            bw, bh = np.exp(bb[:, 2]) * st, np.exp(bb[:, 3]) * st
            out = np.zeros((len(cc), 15), np.float32)
            out[:, 0], out[:, 1], out[:, 2], out[:, 3] = cx - bw / 2, cy - bh / 2, bw, bh
            out[:, 4:14:2] = (kp[:, 0::2] + cc[:, None]) * st
            out[:, 5:14:2] = (kp[:, 1::2] + rr[:, None]) * st
            out[:, 14] = score[keep]
            rows.append(out)
        if not rows:
            return None, None
        allf = np.concatenate(rows)
        idx = cv2.dnn.NMSBoxes(allf[:, :4].tolist(), allf[:, 14].tolist(), self.score, self.nms,
                               top_k=self.top_k)
        idx = np.array(idx).reshape(-1)
        return 1, (allf[idx] if len(idx) else None)


class FaceEngine:
    def __init__(self, models_dir, det_width=640, det_score=0.6, threads=4, classic=True):
        cv2.setNumThreads(int(threads))
        det_path, rec_path = os.path.join(models_dir, DET_MODEL), os.path.join(models_dir, REC_MODEL)
        self.rec = cv2.FaceRecognizerSF.create(rec_path, "")  # alinhamento (e reserva)
        self.rec_net = None
        self.classic = False
        if classic:
            try:  # motor clássico: mesmos resultados, bem mais rápido nesta CPU
                self.det = YuNetClassic(det_path, det_score)
                self.rec_net = cv2.dnn.readNetFromONNX(rec_path, engine=cv2.dnn.ENGINE_CLASSIC)
                self.classic = True
            except Exception:
                self.rec_net = None
        if not self.classic:
            self.det = cv2.FaceDetectorYN.create(det_path, "", (320, 320), float(det_score), 0.3, 50)
        self.det_width = int(det_width)
        self._size = None

    def detect(self, frame, max_width=None):
        max_width = max_width or self.det_width
        h, w = frame.shape[:2]
        s = min(1.0, max_width / w)
        img = frame
        if s < 1.0:
            img = cv2.resize(frame, (int(round(w * s)), int(round(h * s))), interpolation=cv2.INTER_AREA)
        size = (img.shape[1], img.shape[0])
        if not self.classic and size != self._size:
            self.det.setInputSize(size)
            self._size = size
        _, faces = self.det.detect(img)
        if faces is None:
            return []
        faces = faces.astype(np.float32).copy()
        faces[:, :14] /= s
        return [Face(r) for r in faces]

    def align(self, frame, face):
        return self.rec.alignCrop(frame, face.row)

    def embed(self, aligned):
        if self.rec_net is not None:
            self.rec_net.setInput(cv2.dnn.blobFromImage(aligned, 1.0, (112, 112), (0, 0, 0), True, False))
            f = self.rec_net.forward().reshape(-1).astype(np.float32)
        else:
            f = self.rec.feature(aligned).reshape(-1).astype(np.float32)
        n = float(np.linalg.norm(f))
        return f / n if n > 0 else f

    def assess(self, frame, face, dc):
        """Alinha o rosto e mede a qualidade. Retorna (recorte, info, apto_para_reconhecer)."""
        aligned = self.align(frame, face)
        sh = sharpness(aligned)
        yaw = face.yaw()
        # de frente (yaw <= yaw_max) ou de lado/perfil (até yaw_side_max, aceito com mais rigor)
        side = abs(yaw) > dc.yaw_max
        ok = (face.w >= dc.min_face_px and abs(yaw) <= dc.yaw_side_max
              and sh >= dc.sharp_min and face.score >= dc.rec_det_score)
        q = (min(face.w / 80.0, 1.5) * max(0.0, 1 - abs(yaw))
             * min(sh / 150.0, 1.5) * face.score)
        pitch = face.pitch()
        # serve de AMOSTRA (aprender) só rosto bom de verdade: de frente, sem olhar para baixo,
        # nítido e grande. Reconhecer aceita bem mais que isso.
        learn = (face.w >= dc.learn_min_px and abs(yaw) <= 0.30 and 0.30 <= pitch <= 0.70
                 and sh >= dc.learn_sharp and face.score >= 0.8)
        return aligned, {"w": face.w, "yaw": yaw, "sharp": sh, "score": face.score, "q": q,
                         "pitch": pitch, "learn": learn,
                         "side": side}, ok

    def enroll_image(self, img):
        """Para fotos de cadastro: escolhe o maior rosto. Levanta ValueError se não servir."""
        h, w = img.shape[:2]
        s = min(1.0, 1280 / max(h, w))
        if s < 1.0:
            img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        faces = self.detect(img, max_width=img.shape[1])
        if not faces:
            raise ValueError("nenhum rosto encontrado na foto")
        faces.sort(key=lambda f: -f.w)
        if len(faces) > 1 and faces[1].w > 0.7 * faces[0].w:
            raise ValueError("mais de um rosto na foto; envie uma foto só da pessoa")
        f = faces[0]
        if f.w < 40:
            raise ValueError("rosto muito pequeno na foto")
        aligned = self.align(img, f)
        emb = self.embed(aligned)
        info = {"w": f.w, "yaw": f.yaw(), "sharp": sharpness(aligned), "score": f.score}
        return aligned, emb, info


class Gallery:
    """Embeddings de todos os membros ativos, para comparação rápida."""

    def __init__(self):
        self.matrix = np.zeros((0, 128), np.float32)
        self.labels = np.zeros(0, np.int64)
        self.names = {}

    def __len__(self):
        return len(self.labels)

    def load(self, con):
        rows = con.execute(
            "SELECT s.member_id, s.embedding FROM samples s JOIN members m ON m.id = s.member_id "
            "WHERE s.status = 'ok' AND s.embedding IS NOT NULL AND m.active = 1").fetchall()
        embs = [db.blob_to_emb(r["embedding"]) for r in rows]
        self.matrix = np.stack(embs).astype(np.float32) if embs else np.zeros((0, 128), np.float32)
        self.labels = np.array([r["member_id"] for r in rows], np.int64)
        self.names = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM members")}

    def match(self, emb):
        """Lista (member_id, similaridade) do mais parecido para o menos parecido."""
        if not len(self.labels):
            return []
        sims = self.matrix @ emb
        best = {}
        for label, s in zip(self.labels.tolist(), sims.tolist()):
            if s > best.get(label, -2.0):
                best[label] = s
        return sorted(best.items(), key=lambda kv: -kv[1])

    def count(self, member_id):
        return int((self.labels == member_id).sum())

    def member_sims(self, member_id, emb):
        mask = self.labels == member_id
        return self.matrix[mask] @ emb if mask.any() else np.zeros(0, np.float32)
