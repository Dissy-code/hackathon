#!/usr/bin/env bash
# Шрифты, которые используют шаблоны, но не встраивают в .pptx. Без них LibreOffice подменяет
# их на другие, и рендер превью / проверки переполнения текста расходятся с PowerPoint.
# Все шрифты — SIL Open Font License.
#
#   scripts/install_fonts.sh                 # в ~/.local/share/fonts/prism
#   FONT_DIR=/usr/share/fonts/prism scripts/install_fonts.sh   # в Docker-образе
set -euo pipefail

FONT_DIR="${FONT_DIR:-$HOME/.local/share/fonts/prism}"
GF="https://raw.githubusercontent.com/google/fonts/main/ofl"
MONT="https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf"

FONTS=(
  "$GF/play/Play-Regular.ttf"
  "$GF/play/Play-Bold.ttf"
  "$GF/lato/Lato-Light.ttf"
  "$GF/lato/Lato-Regular.ttf"
  "$GF/lato/Lato-Bold.ttf"
  "$GF/poppins/Poppins-Light.ttf"
  "$GF/poppins/Poppins-Regular.ttf"
  "$GF/poppins/Poppins-Medium.ttf"
  "$GF/poppins/Poppins-Bold.ttf"
  # Carlito — метрически совместим с Calibri (замена без сдвига вёрстки)
  "$GF/carlito/Carlito-Regular.ttf"
  "$GF/carlito/Carlito-Bold.ttf"
  "$GF/carlito/Carlito-Italic.ttf"
  "$GF/carlito/Carlito-BoldItalic.ttf"
)
for w in Thin ExtraLight Light Regular Medium SemiBold Bold ExtraBold Black; do
  FONTS+=("$MONT/Montserrat-$w.ttf")
done
FONTS+=("$MONT/Montserrat-Italic.ttf" "$MONT/Montserrat-BoldItalic.ttf")

mkdir -p "$FONT_DIR"
for url in "${FONTS[@]}"; do
  name="$(basename "$url")"
  [[ -s "$FONT_DIR/$name" ]] && continue
  curl -fsSL --retry 3 -o "$FONT_DIR/$name" "$url"
  echo "  + $name"
done
fc-cache -f "$FONT_DIR" >/dev/null
echo "шрифты в $FONT_DIR"
