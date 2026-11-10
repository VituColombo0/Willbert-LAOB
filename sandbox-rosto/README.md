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
    cp <este diretório>/{FaceImage.h,FaceImage.cpp,patch_*.py,rosto.py,sandbox.sh} .
    python3 patch_surface.py && python3 patch_sandbox.py
    make INSTALLDIR=$HOME/SARndbox-rosto

## Usar
    ./sandbox.sh rosto
    /opt/visao/venv/bin/python rosto.py foto.jpg
    ./sandbox.sh cmd "rosto /home/laob/SARndbox-rosto/fotos/rosto.png 0 0"
    ./sandbox.sh cmd "esculpir /home/laob/SARndbox-rosto/fotos/rosto.dem"
    ./sandbox.sh cmd "relevo"
    ./sandbox.sh original
