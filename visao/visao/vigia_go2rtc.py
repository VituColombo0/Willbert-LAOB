"""Vigia do go2rtc: reinicia quando ele trava gastando um núcleo inteiro.

O go2rtc (até a versão de 14/11/2026) tem um defeito na leitura da câmera Tapo: depois de uma
queda de conexão, a rotina antiga pode ficar num laço sem fim (pkg/tapo/client.go, lê com um
espaço de tamanho zero) e gasta 100% de um núcleo para sempre. No laob isso ficou 2 dias assim,
tirando um dos 4 núcleos da caixa de areia. O normal é ~1%.

Aqui: a cada chamada mede a CPU do processo desde a chamada anterior; acima do limite várias
vezes seguidas, pede ao próprio go2rtc para reiniciar (POST /api/restart, só em 127.0.0.1). Quem
estava ligado nele (vídeo, microfone) reconecta sozinho em poucos segundos.
"""
import logging
import os
import time
import urllib.request

log = logging.getLogger("visao.go2rtc")


def cpu_do_processo(pid):
    """Segundos de CPU (usuário + sistema) que o processo já gastou, ou None."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            campos = f.read().rsplit(")", 1)[1].split()
        return (int(campos[11]) + int(campos[12])) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None


def acha_pid(nome="go2rtc"):
    try:
        for d in os.listdir("/proc"):
            if d.isdigit():
                try:
                    with open(f"/proc/{d}/comm") as f:
                        if f.read().strip() == nome:
                            return int(d)
                except OSError:
                    pass
    except OSError:
        pass
    return None


class VigiaGo2rtc:
    def __init__(self, url, limite=0.6, vezes=3, cpu=cpu_do_processo, pid=acha_pid, reinicia=None):
        self.url, self.limite, self.vezes = url.rstrip("/"), limite, vezes
        self._cpu, self._acha, self._reinicia = cpu, pid, reinicia or self._pede_reinicio
        self.pid, self.antes, self.altas, self.reinicios, self.ultimo_uso = None, None, 0, 0, None

    def _pede_reinicio(self):
        req = urllib.request.Request(self.url + "/api/restart", data=b"", method="POST")
        urllib.request.urlopen(req, timeout=5).close()

    def confere(self, agora=None, ocupado=False):
        """Chamar a cada ~1 min. Devolve True se pediu o reinício. `ocupado` (a câmera está
        falando) adia o reinício, mas a contagem continua."""
        agora = time.time() if agora is None else agora
        if self.pid is None or self._cpu(self.pid) is None:
            self.pid, self.antes, self.altas = self._acha(), None, 0
            if self.pid is None:
                return False
        gasto = self._cpu(self.pid)
        if gasto is None:
            return False
        antes, self.antes = self.antes, (agora, gasto)
        if antes is None or agora - antes[0] < 10:
            return False
        uso = (gasto - antes[1]) / (agora - antes[0])
        if uso < 0:   # o processo reiniciou (contador voltou)
            self.altas = 0
            return False
        self.ultimo_uso = uso
        self.altas = self.altas + 1 if uso >= self.limite else 0
        if self.altas < self.vezes or ocupado:
            return False
        log.warning("go2rtc preso gastando %.0f%% de um núcleo há %d medições; reiniciando", uso * 100, self.altas)
        self.altas, self.antes = 0, None
        try:
            self._reinicia()
            self.reinicios += 1
            return True
        except Exception as e:
            log.warning("não consegui reiniciar o go2rtc: %s", e)
            return False
