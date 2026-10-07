#!/usr/bin/env bash
set -Eeuo pipefail
# Run from a checkout: sudo bash install.sh
# Or pass a PUBLIC GitHub repo: sudo bash install.sh owner/repository
if [[ $EUID -ne 0 ]]; then echo 'Run with sudo bash install.sh [owner/repository]'; exit 1; fi
source /etc/os-release
case "$ID" in ubuntu|debian) ;; *) echo 'Supported systems: Debian and Ubuntu (64-bit x86 or ARM).'; exit 1;; esac
case "$(uname -m)" in x86_64|aarch64) ;; *) echo 'A 64-bit OS is required.'; exit 1;; esac
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' "$(dpkg --print-architecture)" "$ID" "${VERSION_CODENAME}" > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
elif ! docker compose version >/dev/null 2>&1; then
  apt-get install -y docker-compose-plugin || { echo 'Install the Docker Compose v2 plugin, then rerun.'; exit 1; }
fi
systemctl enable --now docker
if [[ -n ${1:-} ]]; then
  [[ $1 =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo 'Use owner/repository'; exit 1; }
  DEST=/opt/datanode-web
  if [[ -e $DEST ]]; then echo '/opt/datanode-web already exists. Update your existing checkout deliberately, then run its install.sh.'; exit 1; fi
  git clone --depth 1 "https://github.com/$1.git" "$DEST"
  cd "$DEST"
else
  cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
fi
[[ -f compose.yaml && -f app.py ]] || { echo 'Run the installer from the extracted project folder.'; exit 1; }
umask 077
if [[ ! -f .env ]]; then
  printf 'PORT=3923\nBIND_IP=0.0.0.0\nDATANODES_API_KEY=\n' > .env
fi
# Older installs had a password; it is no longer used.
sed -i '/^APP_PASSWORD=/d' .env
chmod 600 .env
docker compose up -d --build
healthy=0
for attempt in $(seq 1 60); do
  state=$(docker inspect --format '{{.State.Health.Status}}' "$(docker compose ps -q datanode-web)" 2>/dev/null || true)
  if [[ $state == healthy ]]; then healthy=1; break; fi
  sleep 2
done
if [[ $healthy != 1 ]]; then echo 'The app did not become healthy. Run: docker compose logs --tail=80'; exit 1; fi
PORT=$(sed -n 's/^PORT=//p' .env); IP=$(hostname -I 2>/dev/null | awk '{print $1}')
printf '\nDataNode Web is ready. Open http://%s:%s on any device on your network (no login).\n' "${IP:-YOUR-SERVER-IP}" "${PORT:-3923}"
printf 'There is no password, so do not forward this port to the internet. Settings: %s/.env\n' "$PWD"
