"""Alertas opcionais pelo Telegram (desligado se token/chat_id estiverem vazios)."""
import logging
import queue
import threading
import urllib.parse
import urllib.request
import uuid

log = logging.getLogger("visao.notify")


class Notifier:
    def __init__(self, token, chat_id):
        self.token, self.chat_id = token, str(chat_id)
        self.enabled = bool(token and chat_id)
        self.q = queue.Queue(maxsize=50)

    def start(self):
        threading.Thread(target=self.run, daemon=True, name="notify").start()

    def send(self, text, photo_path=None):
        if not self.enabled:
            return
        try:
            self.q.put_nowait((text, photo_path))
        except queue.Full:
            log.warning("fila de notificações cheia; descartando")

    def run(self):
        while True:
            text, photo = self.q.get()
            try:
                if photo:
                    self._photo(text, photo)
                else:
                    self._message(text)
            except Exception as e:
                log.warning("falha ao enviar para o Telegram: %s", e)

    def _url(self, method):
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def _message(self, text):
        data = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text}).encode()
        urllib.request.urlopen(self._url("sendMessage"), data, timeout=20).read()

    def _photo(self, caption, path):
        boundary = uuid.uuid4().hex
        with open(path, "rb") as f:
            img = f.read()
        parts = []
        for name, value in (("chat_id", self.chat_id), ("caption", caption)):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                         f'{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; '
                     f'filename="foto.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode() + img + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(self._url("sendPhoto"), data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        urllib.request.urlopen(req, timeout=30).read()
