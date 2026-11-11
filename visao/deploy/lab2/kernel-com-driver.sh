#!/bin/bash
# Faz o lab2 iniciar no kernel mais novo que TEM o driver NVIDIA instalado.
#   sudo bash kernel-com-driver.sh            escolhe e grava (/etc/default/grub.d/99-willbert-kernel.cfg)
#   sudo bash kernel-com-driver.sh --desfazer volta ao padrão do Ubuntu (o kernel mais novo)
# Por quê: em 2026-11-11 as atualizações automáticas instalaram o kernel 6.8, mas o driver NVIDIA
# para ele não veio (o driver está fora das atualizações automáticas). O PC reiniciou no 6.8 sem
# placa de vídeo: a areia caiu para 1 quadro/s e o projetor sumiu. Não instala nem remove pacotes.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "rode com sudo"; exit 1; }
CFG=/etc/default/grub.d/99-willbert-kernel.cfg
if [ "${1:-}" = --desfazer ]; then
  rm -f "$CFG"; update-grub >/dev/null 2>&1; echo "kernel: padrão do Ubuntu (o mais novo)"; exit 0
fi
SERIE=$(dpkg-query -W -f='${Status} ${Package}\n' 'nvidia-driver-*' 2>/dev/null | awk '/^install ok installed/{sub("nvidia-driver-","",$4); print $4; exit}')
[ -n "$SERIE" ] || { echo "nenhum nvidia-driver instalado: nada a fazer"; exit 0; }
ESCOLHIDO=""
for k in $(ls /boot/vmlinuz-* | sed 's#/boot/vmlinuz-##' | sort -V -r); do
  if find "/lib/modules/$k" \( -path "*nvidia-$SERIE*" -o -path "*/dkms/*" -o -path "*/updates/*" \) \
       -name "nvidia.ko*" -not -path "*/bits/*" 2>/dev/null | grep -q .; then ESCOLHIDO=$k; break; fi
done
[ -n "$ESCOLHIDO" ] || { echo "nenhum kernel com o driver nvidia-$SERIE: não mudei nada"; exit 1; }
MAIS_NOVO=$(ls /boot/vmlinuz-* | sed 's#/boot/vmlinuz-##' | sort -V | tail -1)
if [ "$ESCOLHIDO" = "$MAIS_NOVO" ]; then
  rm -f "$CFG"; echo "kernel: o mais novo ($ESCOLHIDO) já tem o driver nvidia-$SERIE"
else
  UUID=$(findmnt -no UUID /)
  printf '# willbert-otimizacao: kernel com o driver nvidia-%s (o %s não tem). Desfazer: kernel-com-driver.sh --desfazer\nGRUB_DEFAULT="gnulinux-advanced-%s>gnulinux-%s-advanced-%s"\n' \
    "$SERIE" "$MAIS_NOVO" "$UUID" "$ESCOLHIDO" "$UUID" > "$CFG"
  echo "kernel: inicia no $ESCOLHIDO (o $MAIS_NOVO ainda não tem o driver nvidia-$SERIE)"
fi
update-grub >/dev/null 2>&1
grep -q "gnulinux-$ESCOLHIDO-advanced" /boot/grub/grub.cfg || { echo "ERRO: entrada do GRUB não encontrada; desfazendo"; rm -f "$CFG"; update-grub >/dev/null 2>&1; exit 1; }
echo "  vale a partir do próximo reinício"
