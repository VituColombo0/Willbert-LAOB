"""Volta de fotos com a câmera (para montar a planta do laboratório): gira a Tapo por ONVIF,
guarda um quadro em cada posição e SEMPRE devolve a câmera para onde estava.

  visao-cli panorama --saida DIR [--passos 10] [--inclinacoes 2]

Em cada posição espera a câmera parar, pede um quadro ao go2rtc (que já está ligado nela: não
abre outra conexão) e grava DIR/pan-NN.jpg; DIR/panorama.json lista a posição de cada foto.
As posições usam a escala do ONVIF (-1 a 1 no giro e na inclinação). Enquanto a câmera gira o
detector não reconhece ninguém e joga fora o "fundo vazio" (ele refaz sozinho depois).
"""
import json
import logging
import os
import time
import urllib.parse
import urllib.request

log = logging.getLogger("visao.panorama")


class Ptz:
    """A parte do ONVIF que a volta de fotos usa (separada para poder testar sem câmera)."""

    def __init__(self, cam_cfg, wsdl_dir):
        from onvif import ONVIFCamera
        cam = ONVIFCamera(cam_cfg.host, cam_cfg.onvif_port, cam_cfg.user, cam_cfg.password, wsdl_dir)
        self.token = cam.create_media_service().GetProfiles()[0].token
        self.ptz = cam.create_ptz_service()

    def posicao(self):
        p = self.ptz.GetStatus({"ProfileToken": self.token}).Position.PanTilt
        return float(p.x), float(p.y)

    def vai(self, pan, tilt):
        self.ptz.AbsoluteMove({"ProfileToken": self.token,
                               "Position": {"PanTilt": {"x": float(pan), "y": float(tilt)}}})


def quadro_go2rtc(url, stream):
    with urllib.request.urlopen(f"{url.rstrip('/')}/api/frame.jpeg?src={urllib.parse.quote(stream)}", timeout=20) as r:
        return r.read()


def posicoes(passos, inclinacoes, tilt0):
    """Giros igualmente espaçados de -0,9 a 0,9 (as pontas da Tapo às vezes não respondem), em
    zigue-zague entre as inclinações (a atual e, se pedido, uma mais para baixo)."""
    passos = max(2, min(24, int(passos)))
    pans = [-0.9 + 1.8 * k / (passos - 1) for k in range(passos)]
    tilts = [tilt0] + ([max(-1.0, tilt0 - 0.45)] if int(inclinacoes) >= 2 else [])
    lista = []
    for j, t in enumerate(tilts):
        for p in (pans if j % 2 == 0 else pans[::-1]):
            lista.append((round(p, 3), round(t, 3)))
    return lista


def espera_parar(ptz, alvo, limite_s=14.0, dorme=time.sleep, agora=time.time):
    """Espera a câmera chegar (ou parar de se mexer). Devolve a posição final."""
    fim = agora() + limite_s
    antes = None
    while agora() < fim:
        dorme(0.6)
        pos = ptz.posicao()
        if abs(pos[0] - alvo[0]) < 0.02 and abs(pos[1] - alvo[1]) < 0.03:
            break
        if antes is not None and abs(pos[0] - antes[0]) < 0.003 and abs(pos[1] - antes[1]) < 0.003:
            break  # parou sem chegar (fim de curso): fica com onde está
        antes = pos
    dorme(1.2)  # a imagem assenta (foco e exposição)
    return ptz.posicao()


def volta(ptz, pega_quadro, saida, passos=10, inclinacoes=2, dorme=time.sleep, agora=time.time):
    """Faz a volta de fotos. Devolve a lista [{arquivo, pan, tilt}]. A câmera volta ao lugar no fim,
    aconteça o que acontecer."""
    os.makedirs(saida, exist_ok=True)
    origem = ptz.posicao()
    fotos = []
    try:
        for n, alvo in enumerate(posicoes(passos, inclinacoes, origem[1])):
            ptz.vai(*alvo)
            pos = espera_parar(ptz, alvo, dorme=dorme, agora=agora)
            try:
                dados = pega_quadro()
            except Exception as e:  # um quadro que falhou não estraga a volta
                log.warning("posição %d: sem quadro (%s)", n, e)
                continue
            nome = f"pan-{n:02d}.jpg"
            with open(os.path.join(saida, nome), "wb") as f:
                f.write(dados)
            fotos.append({"arquivo": nome, "pan": round(pos[0], 3), "tilt": round(pos[1], 3)})
    finally:
        try:
            ptz.vai(*origem)
            espera_parar(ptz, origem, dorme=dorme, agora=agora)
        except Exception as e:
            log.error("NÃO consegui devolver a câmera para a posição original %s: %s", origem, e)
        with open(os.path.join(saida, "panorama.json"), "w") as f:
            json.dump({"origem": {"pan": origem[0], "tilt": origem[1]}, "fotos": fotos}, f, indent=1)
    return fotos
