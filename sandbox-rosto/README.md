# SARndbox com modo Rosto (lab2)

Cópia do AR Sandbox 2.8 (`~/SARndbox-rosto` no lab2) com dois modos novos. O original
(`~/SARndbox-2.8`) não é alterado e continua sendo o que abre no login.

- **Rosto**: a foto é projetada sobre a areia; onde a areia está alta a foto amplia (efeito
  lente) e o relevo faz sombra, então o rosto se deforma enquanto mexem na areia.
- **Esculpir**: o rosto vira um relevo-alvo; o SARndbox pinta onde falta areia (vermelho) e
  onde sobra (azul) para formar o rosto.

## Arquivos
- `FaceImage.h/.cpp`: a foto como textura e a matriz câmera -> foto (giro, espelho, proporção).
- `patch_surface.py`, `patch_sandbox.py`: alterações no SurfaceRenderer e no Sandbox (shader e
  comandos `rosto`, `rostoAjuste`, `esculpir`, `relevo` no cano de controle `-cp`).
- `rosto.py`: recorta o rosto (YuNet do Willbert), reforça o contraste e gera `rosto.dem`.
- `sandbox.sh`: troca entre o original e esta versão (volta ao original se ela falhar).

## Montar no lab2
    cp -a ~/SARndbox-2.8 ~/SARndbox-rosto && cd ~/SARndbox-rosto
    cp /root/etc/SARndbox-2.8/* etc/SARndbox-2.8/
    cp <este diretório>/{FaceImage.h,FaceImage.cpp,patch_*.py,rosto.py,sandbox.sh,projetor.cfg} .
    python3 patch_surface.py && python3 patch_sandbox.py
    make INSTALLDIR=$HOME/SARndbox-rosto

## Painel do celular
`painel.py` (serviço de usuário `sandbox-painel`) em http://100.114.194.28:8090 (só Tailscale e
127.0.0.1): tirar selfie, escolher foto ou usar a câmera do laboratório, trocar de modo,
girar/espelhar e ajustar deformação, sombra e mistura com as cores de relevo.

    cp sandbox-painel.service ~/.config/systemd/user/ && systemctl --user enable --now sandbox-painel

## Usar
    ./sandbox.sh rosto
    /opt/visao/venv/bin/python rosto.py foto.jpg
    ./sandbox.sh cmd "rosto /home/laob/SARndbox-rosto/fotos/rosto.png 0 0"
    ./sandbox.sh cmd "esculpir /home/laob/SARndbox-rosto/fotos/rosto.dem"
    ./sandbox.sh cmd "relevo"
    ./sandbox.sh original

## Projetor

- `sandbox.sh` abre o SARndbox (as duas versões) com `-mergeConfig projetor.cfg`: a janela já sai
  em tela cheia no projetor (HDMI-0). Sem isso ela abre no monitor.
- A calibração do lab2 marca uma caixa maior que a imagem do projetor; a foto é encaixada só na
  parte iluminada (cantos da imagem do projetor levados ao plano base, em `Sandbox.cpp` e `rosto.py`).
  Hoje: x -34,5..24,8 cm, y -28,8..13,1 cm (cerca de 59 × 42 cm).

## Topografia ao ligar, foto por comando

- Ao ligar o lab2, o autostart (`~/.config/autostart/SARndbox.desktop`) roda `areia-topografia`:
  o SARndbox original em tela cheia no projetor. O atalho antigo ficou em
  `~/SARndbox-rosto/SARndbox.desktop.original` (para desfazer, copie-o de volta).
- **Pasta das imagens:** `~/Pictures/Areia` (atalho "Areia" na barra lateral do Arquivos). Coloque
  ali as fotos (.jpg, .png, .webp, .bmp). No terminal (comandos em `~/.local/bin`):
  - `areia-foto`: lista as imagens da pasta e pergunta o número (Enter = a última usada).
  - `areia-foto 3` / `areia-foto gato`: escolhe direto pelo número ou por parte do nome.
  - `areia-foto ~/Downloads/x.jpg`: qualquer arquivo; ele é copiado para a pasta Areia.
  - `areia-foto lista`, `areia-foto pasta` (abre a pasta), `areia-foto girar`, `areia-foto espelhar`.
  - `areia-foto camera`: foto pela câmera do laboratório (só essa opção depende do Willbert).
  - `areia-topografia`: volta à topografia.
- Com rosto na imagem: recorte retrato em volta do rosto, sem fundo. Sem rosto: a imagem inteira,
  com fundo (`--sem-fundo` recorta a pessoa mesmo assim; `--com-fundo` nunca recorta). PNG já
  transparente usa a própria transparência. A preparação (~5 s) fica guardada em `fotos/cache/`
  (40 últimas): trocar para uma imagem já usada leva ~1 s.
- Tudo local nesta pasta: `venv/` (OpenCV, NumPy, Flask, waitress), `modelos/` (YuNet e U²-Net) e a
  calibração de `etc/`. Recriar o venv:
  `python3 -m venv venv && venv/bin/pip install numpy==2.2.6 opencv-python-headless==5.0.0.93 Flask==3.1.3 waitress==3.0.2`
  As fotos enviadas pelo painel do celular também vão para a pasta Areia.
- No modo foto não aparecem cores de relevo, curvas de nível nem água: só a foto, que amplia onde a
  areia está mais alta e ganha sombra do relevo. No painel, "mistura" traz o relevo de volta, e
  deformação 0 + sombra 0 projeta a foto sem efeito nenhum.
- Recorte do fundo: U²-Net de pessoas (`modelos/u2net_human_seg.onnx`, 176 MB, release v0.0.0 do
  projeto rembg) rodando no OpenCV do venv local, ~3 s. Baixar com:
  `wget -P modelos https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net_human_seg.onnx`
