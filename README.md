# DataNode Web

Paste a DataNode share link in a mobile-friendly web page. A server browser prepares the download, then your device downloads the file. Includes MoonDownloader's automatic verification attempt and a touchable browser screenshot for manual fallback.

## Install on Ubuntu / Debian

Requires a **64-bit** x86_64 or ARM64 OS, internet access, and preferably 2 GB free RAM. Raspberry Pi 400 with a 64-bit OS is supported by the build design, but has not been hardware-tested. The installer installs Docker, Compose, and all application/browser dependencies. It does not change your firewall.

From the extracted project folder:

```bash
sudo bash install.sh
```

Open `http://YOUR-SERVER-IP:3923`. Sign in as `admin` with the generated password printed by the installer. It is also stored in `.env`, readable only by the installing user/root. Use HTTPS via a reverse proxy before exposing this password-protected app on the internet. The app is intended for one owner, not unrelated public users; browser state and jobs are shared.

Install directly from the public GitHub repository:

```bash
curl -fsSL https://raw.githubusercontent.com/mohamedt1996/datanode-web/main/install.sh -o /tmp/datanode-install.sh && sudo bash /tmp/datanode-install.sh mohamedt1996/datanode-web
```

The command downloads the installer, installs its prerequisites, clones the public repository to `/opt/datanode-web`, and starts the app. It preserves an existing installation instead of overwriting it.

## Use

1. Paste `https://datanodes.to/FILECODE/optional-filename` and press **Prepare download**.
2. Keep the page open. Verification is attempted automatically.
3. If it needs help, expand **Browser help** and tap the checkbox in the server browser image.
4. The download starts automatically when ready; a visible button remains if the device blocks it.

The default relay streams from DataNode through your server to the device, with bounded memory and no file stored on server disk. It supports a single HTTP byte range for resume while the job and upstream link are valid. It uses server bandwidth. **Direct from DataNode** redirects the device instead: this may fail if DataNode binds links to the server IP, verification cookies, or headers. A direct redirect cannot transfer the server's browser cookies to your device.

Jobs are in memory and expire after 30 minutes; upstream links can expire sooner. Restarting loses jobs but retains the browser profile. One extraction runs at a time, up to five can queue. Only submit links you are permitted to download.

## Settings / management

Edit `.env`, then run `sudo docker compose up -d`:

- `PORT=3923`: host port.
- `BIND_IP=0.0.0.0`: use `127.0.0.1` when a reverse proxy on the same host handles access.
- `APP_PASSWORD`: at least 20 characters. Never commit `.env`.
- `DATANODES_API_KEY`: optional DataNode key. Uses the upstream API when your account permits direct links; otherwise falls back to the browser. No key is bundled.

```bash
sudo docker compose logs --tail=80
sudo docker compose restart
sudo docker compose down
# After intentionally updating project source:
sudo docker compose up -d --build
```

Example Caddy reverse proxy (install/manage Caddy separately, point your domain at your server):

```caddy
files.example.com {
    reverse_proxy 127.0.0.1:3923
}
```

## Limits and security

Automatic Turnstile success is **not guaranteed**. The container uses Debian Chromium for both x86_64 and ARM64; it may be rejected where desktop Chrome succeeds. DataNode layout changes can break extraction. A live DataNode download and Docker build must be validated on the target server; this delivery does not claim they were tested here.

The app rejects arbitrary source domains, authenticates UI/API/downloads, checks POST origins, limits jobs, blocks private destinations in relay DNS resolution, filters redirected cookies by destination, and exposes no Chrome debugging/VNC port. Browser requests receive a private-network check, but browser DNS resolution is separate from that check: use a firewall-isolated container/network if protection against malicious DNS rebinding is required. Do not grant the browser access to trusted internal services. Browser upstream automation uses Chromium without its sandbox inside a non-root, capability-dropped container. Keep it dedicated to downloads.

Run local checks:

```bash
python3 -m pip install aiohttp==3.13.5
python3 -m unittest discover -s tests -v
bash -n install.sh entrypoint.sh
```

## Attribution

`vendor/moon_extract.py` is MoonDownloader's extraction module, MIT, copyright LeyckerS. See `vendor/LICENSE` and `vendor/README.md`. The wrapper is MIT licensed; see `LICENSE`.
