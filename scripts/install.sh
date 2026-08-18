#!/usr/bin/env bash
# Kurulum yardımcısı (spec §9 Faz 7 "README + kurulum scripti").
#
# Ne yapar: `uv sync`, `.env` yoksa `.env.example`den oluşturur,
# `systemd/*.service`/`*.target` dosyalarındaki `__ULL_BOT_DIR__`
# yer tutucusunu gerçek repo yoluyla değiştirip `~/.config/systemd/user/`e
# kopyalar, `systemctl --user daemon-reload` çalıştırır.
#
# Ne YAPMAZ: servisleri kendiliğinden `enable`/`start` etmez, `.env`i
# doldurmaz (API anahtarları), sudo İSTEMEZ ve hiçbir yerde çalıştırmaz —
# bunların hepsi kullanıcının kendi kararı olmalı (spec §12 ruhu: bu script
# de "arkasında ne olduğunu bilmeden bir şey açma" ilkesine uyuyor). Ollama
# kurulumu da bu scriptin dışında — Faz 5'te ayrı, sudo gerektiren bir adım
# olarak elle yapıldı (bkz. DECISIONS.md "Ollama GPU backend'i").

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

echo "== ULL-Bot kurulumu =="
echo "Repo: $REPO_DIR"

cd "$REPO_DIR"

echo
echo "-- Python bağımlılıkları (uv sync) --"
uv sync

echo
echo "-- .env --"
if [ -f .env ]; then
    echo ".env zaten var, dokunulmadı."
else
    cp .env.example .env
    echo ".env.example'dan .env oluşturuldu. En az bir sağlayıcı API anahtarını"
    echo "doldurmadan sohbet çalışmaz — bkz. README.md 'Setup'."
fi

echo
echo "-- systemd (--user) birimleri --"
mkdir -p "$SYSTEMD_USER_DIR"
for unit in ull-bot-litellm.service ull-bot-api.service ull-bot.target; do
    sed "s|__ULL_BOT_DIR__|$REPO_DIR|g" "systemd/$unit" > "$SYSTEMD_USER_DIR/$unit"
    echo "yazıldı: $SYSTEMD_USER_DIR/$unit"
done
systemctl --user daemon-reload

echo
echo "-- masaüstü kısayolu (Faz 8) --"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$DESKTOP_DIR"
sed "s|^Exec=.*|Exec=$REPO_DIR/scripts/ull-bot|" "scripts/ull-bot.desktop" \
    > "$DESKTOP_DIR/ull-bot.desktop"
chmod +x "$REPO_DIR/scripts/ull-bot"
echo "yazıldı: $DESKTOP_DIR/ull-bot.desktop"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

echo
echo "== Bitti =="
echo
echo "İKİ ÇALIŞTIRMA YOLU VAR, ikisi de kuruldu ama HİÇBİRİ başlatılmadı:"
echo
echo "  1) Masaüstü uygulaması (servisler uygulamayla birlikte açılır/kapanır):"
echo "         ./scripts/ull-bot"
echo "     ya da uygulama menüsünden 'ULL-Bot'."
echo
echo "  2) Servisler arka planda sürekli açık kalsın istiyorsan (systemd):"
echo
echo "         systemctl --user enable --now ull-bot.target"
echo
echo "     Bu ikisi çakışmaz: systemd servisleri açıksa uygulama onları"
echo "     benimser ve kapanırken durdurmaz."
echo
echo "Laptop profilindeysen önce systemd/ull-bot-litellm.service'teki"
echo "--config satırını 'litellm.laptop.yaml' yapıp $SYSTEMD_USER_DIR'a"
echo "tekrar kopyala, sonra daemon-reload çalıştır."
echo
echo "Durum kontrolü: systemctl --user status ull-bot.target"
echo "Loglar:         journalctl --user -u ull-bot-api -u ull-bot-litellm -f"
