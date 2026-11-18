# GTA Clipper

Watches GTA 6 livestreams, detects hype moments from chat floods and the streamer's voice, and turns them into captioned 9:16 shorts. You review and approve them from a web dashboard on your PC or your phone.

- **Web app** with onboarding, a guided tour, a live dashboard (real-time hype charts and an activity feed), clip review, and settings.
- **Hot-swappable streamers.** Add, pause, resume and remove streamers while the app runs.
- **Phone control** through Tailscale (private to your devices) plus a password. You can add the app to your home screen.
- **Telegram alerts and webhooks.** n8n is optional; there's a workflow in `n8n/`.
- **Installers and auto-update:**
  - Windows: a setup `.exe` that updates silently in the app.
  - Fedora and Ubuntu: dnf/apt repositories built by GitHub Actions.

For how everything works, see `docs/GTA-Clipper-Guide.pdf`, also available in the app under Help. For releases, see `docs/GTA-Clipper-CI-CD.pdf`.

## Install

**Windows:** download `GTA-Clipper-x.y.z-setup.exe` from the latest GitHub Release and run it. You don't need admin rights. The installer sets up a private Python, the dependencies and FFmpeg, then opens the app.

**Fedora:**
```bash
sudo curl -fsSL -o /etc/yum.repos.d/gta-clipper.repo https://<owner>.github.io/<repo>/rpm/gta-clipper.repo
sudo dnf install gta-clipper && systemctl --user enable --now gta-clipper
```
FFmpeg with NVENC comes from RPM Fusion: `sudo dnf swap ffmpeg-free ffmpeg --allowerasing`.

**Ubuntu / Debian:** see `https://<owner>.github.io/<repo>/`.

**Also install:**
- **Claude Code**, logged in with your Pro account. It judges each moment.
  - Windows: `irm https://claude.ai/install.ps1 | iex`
  - Linux: `curl -fsSL https://claude.ai/install.sh | bash`
  - Then run `claude` once to log in.
- **Tailscale**, for phone access.

The in-app System check tells you if anything is missing.

## Run from source (development)

```bash
uv venv -p 3.12 .venv && uv pip install -r requirements.txt   # Windows: same commands in PowerShell
./run.sh            # Windows: .\run.ps1
```
Open http://127.0.0.1:8787. First run starts onboarding: password, language, system check, first streamer, phone and alerts, then the tour. Pick the **Demo** streamer to see the pipeline work without a live stream.

Settings are saved to `%APPDATA%\gta-clipper\config.yaml` on Windows and `~/.config/gta-clipper/config.yaml` on Linux. A `config.yaml` next to the code takes precedence. Data goes to `~/gta-clipper-data`.

**Tests:** `pytest -q tests`

## Release a new version

```bash
git tag v0.3.0 && git push origin v0.3.0
```
GitHub Actions then:
1. Runs the tests.
2. Builds the Windows installer, `.deb` and `.rpm`.
3. Creates the GitHub Release.
4. Updates the signed apt and dnf repositories on GitHub Pages.

Installed copies pick the update up on their own. One-time setup (signing key, secrets, Pages) is in `docs/GTA-Clipper-CI-CD.pdf`.

## Layout
```
web/        HTTP server, API, live events (SSE), static UI (web/static)
watcher/    recording, chat readers, audio features, hype detector, hot-swap manager
clipper/    transcription, LLM plan, rendering, worker queue, notifications, DB
common/     config store, event bus, updater, system checks
packaging/  nfpm (deb/rpm), Inno Setup (Windows), launchers, systemd unit
scripts/    CI helpers: build prep, apt/dnf repo publishing
tools/      replay (tune on recorded data), test_candidate, frame
```
