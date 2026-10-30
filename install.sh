#!/bin/sh
# Instala o Bergamota no usuário atual: dependências (apt), app, ícone, serviço DBus e extensão do GNOME Shell.
# Uso: ./install.sh [--sem-dependencias] [--sem-libreoffice]
#   --sem-dependencias  não instala pacotes; só copia os arquivos (bom para atualizar)
#   --sem-libreoffice   não instala o LibreOffice (cerca de 1 GB); conversões de documento ficam indisponíveis
set -e
cd "$(dirname "$0")"

DEPS=yes
LIBREOFFICE=yes
for arg in "$@"; do
    case "$arg" in
        --sem-dependencias) DEPS=no ;;
        --sem-libreoffice) LIBREOFFICE=no ;;
        *) echo "Opção desconhecida: $arg" >&2; exit 2 ;;
    esac
done

[ "$(id -u)" -ne 0 ] || { echo "Rode sem sudo: a instalação é do seu usuário; o script pede a senha quando precisar." >&2; exit 1; }
command -v gnome-shell >/dev/null || { echo "O Bergamota precisa do GNOME Shell." >&2; exit 1; }

# ---- dependências ----
REQUIRED="python3-gi python3-gi-cairo python3-pil python3-numpy gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gdkpixbuf-2.0
gir1.2-gstreamer-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-libav ffmpeg poppler-utils
ghostscript libimage-exiftool-perl libnotify-bin libglib2.0-bin desktop-file-utils gtk-update-icon-cache"
# Sem estes o app abre, mas perde formatos: HEIC, SVG e gravar AVIF
OPTIONAL="heif-gdk-pixbuf librsvg2-common glycin-loaders"

installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "ok installed"; }

if [ "$DEPS" = yes ]; then
    command -v apt-get >/dev/null || { echo "Sem apt: instale as dependências à mão (lista no README) e use --sem-dependencias." >&2; exit 1; }
    missing=""
    for p in $REQUIRED; do installed "$p" || missing="$missing $p"; done
    for p in $OPTIONAL; do
        installed "$p" && continue
        if apt-cache show "$p" >/dev/null 2>&1; then
            missing="$missing $p"
        else
            echo "Aviso: $p não existe nesta versão do sistema; alguns formatos de imagem podem não abrir."
        fi
    done
    # no snap o pacote apt não aparece, mas o comando sim
    if [ "$LIBREOFFICE" = yes ] && ! command -v libreoffice >/dev/null; then
        missing="$missing libreoffice"
    fi
    if [ -n "$missing" ]; then
        echo "Instalando:$missing"
        sudo apt-get update
        # shellcheck disable=SC2086
        sudo apt-get install -y $missing
    fi
fi

# ---- arquivos ----
ICONS=~/.local/share/icons/hicolor
EXT=~/.local/share/gnome-shell/extensions
APPS=~/.local/share/applications
mkdir -p "$ICONS/512x512/apps" ~/.local/bin "$APPS" ~/.local/share/dbus-1/services "$EXT"
install -m644 icon.png "$ICONS/512x512/apps/io.github.bergamota.png"
gtk-update-icon-cache -q -t "$ICONS" 2>/dev/null || true
install -m755 bergamota.py ~/.local/bin/bergamota
sed "s|@HOME@|$HOME|" io.github.bergamota.service > ~/.local/share/dbus-1/services/io.github.bergamota.service
rm -rf "$EXT/bergamota@local"
cp -r bergamota@local "$EXT/"
cat > "$APPS/io.github.bergamota.desktop" <<DESKTOP
[Desktop Entry]
Name=Bergamota
Comment=Conversor de arquivos offline
Exec=$HOME/.local/bin/bergamota %F
Icon=io.github.bergamota
Type=Application
Categories=Utility;
Actions=settings;

[Desktop Action settings]
Name=Ajustes
Exec=gapplication action io.github.bergamota settings
DESKTOP
update-desktop-database "$APPS" 2>/dev/null || true

# ---- extensão: fica ligada no próximo login, mesmo que o GNOME ainda não a conheça ----
python3 - <<'PY'
import ast, subprocess
get = subprocess.run(["gsettings", "get", "org.gnome.shell", "enabled-extensions"], capture_output=True, text=True)
text = get.stdout.strip().removeprefix("@as ")
on = ast.literal_eval(text) if text else []
if "bergamota@local" not in on:
    subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", str(on + ["bergamota@local"])], check=True)
PY

# ---- reinicia o serviço para valer a versão nova ----
for p in $(pgrep -af "gapplication-servic[e]" | awk '/bergamota/{print $1}'); do kill "$p"; done

# ---- confere ----
echo "Conferindo a instalação (leva alguns segundos)..."
if python3 ~/.local/bin/bergamota --selftest; then
    echo "Testes passaram."
else
    echo "Aviso: o teste do app falhou (veja acima). Falta alguma dependência ou o sistema é antigo demais." >&2
fi

echo
echo "Pronto. Saia e entre de novo na sessão: o GNOME só carrega a extensão num novo login."
echo "Depois arraste um arquivo e toque Shift, Ctrl ou Alt."
