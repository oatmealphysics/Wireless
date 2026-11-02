# Installation reference

[Back to the quick install](../README.md#install--copy--paste)

Source builds, offline preparation, phone access and hardware details.

**Recommended: download a tested runtime bundle.** It contains Python, all core
Python dependencies (including NumPy for RF processing), GUI assets, sample data,
and the native bladeRF library/CLI. No Git, host Python, or build step is needed.

1. Install and start [Docker Desktop](https://docs.docker.com/desktop/) on Windows
   or macOS. On Linux install [Docker Engine and the Compose plugin](https://docs.docker.com/engine/install/).
   Do this while connected to the internet; Docker itself is not inside the bundle.
2. Sign into GitHub with access to this private repository. Open
   [Multi-arch build](https://github.com/skippydinglechalk420/WaveRaider/actions/workflows/multiarch.yml),
   select the latest **successful `master` run**, then download its artifact:
   **waveraider-amd64** for Intel/AMD NUCs or **waveraider-arm64** for 64-bit ARM
   (Jetson, Raspberry Pi OS 64-bit, Apple Silicon). These are runtime downloads;
   GitHub's **Source code ZIP** and older releases do not include dependencies.
3. Extract the whole ZIP into a folder. Windows: double-click **start.cmd**
   (Docker Desktop must use Linux containers). macOS: open Terminal in the folder
   and run `sh start.sh`. Linux: run `sudo sh start.sh` in that folder.
4. Wait for the server address, then open **http://localhost:8899** and select
   **Start sample session**. Stop with **Ctrl+C**; use the same launcher to restart.

The launcher checks checksums and CPU architecture, loads the included image, and
starts without pulling or building anything. Keep the extracted files together.
Artifacts expire after 30 days; save a verified bundle before going offline. A new
successful build produces new downloads. For updates, stop the old bundle and launch
the new one; both use the `waveraider_wr_targets` Docker volume for saved data. Do
not run `docker compose down -v` unless you intend to delete that data.

## Phone over Wi-Fi

 bundles initially accept connections from this computer only.
To use your S20, create a `.env` file in the extracted folder containing
`WR_BIND=0.0.0.0`, restart, and open `http://<NUC-IP>:8899` on the same private
network. Restrict port 8899 to that subnet in the host firewall. The GUI has no
login: anyone allowed to connect can change settings and export/delete saved data.
Do not forward it to the internet. If port 8899 is busy, add `WR_PORT=8900` to `.env`
and open port 8900 instead. These variables apply to the runtime bundle.

## Receiver setup

 the bundle is ready for the GUI, recorded analysis and sample
replay. For physical bladeRF use, native Linux is the deployment target; configure
USB access and the correct FPGA image/firmware for your exact bladeRF model using
[Nuand's setup guide](https://github.com/Nuand/bladeRF/wiki#Getting-Started).
Containers need explicit USB passthrough. Docker Desktop does not automatically
expose an attached USB radio. RTL-SDR/HackRF/Soapy tools are optional backends and
are not included. Experimental CLI node reporting, GPSD snapshots and recording
are documented in the [node quickstart](node-quickstart.md). A trained classifier
and TDOA remain planned. Intel/ARM smoke tests do not establish
real-time performance on every NUC, Jetson or Raspberry Pi.

## Build from source

 the OS instructions below need internet for the first
build. The repository is private: authenticate Git using a credential manager,
GitHub CLI (`gh auth login` then `gh auth setup-git`), or an authorized SSH key;
a GitHub account password cannot authenticate an HTTPS clone. Alternatively download
and extract the repository ZIP while signed in, then open its folder in a terminal.
Docker Desktop must be running before `docker compose up`.

### 🪟 Windows

Open **PowerShell** (click Start, type *PowerShell*, press Enter) and paste:

```powershell
winget install -e --id Git.Git
winget install -e --id Docker.DockerDesktop
```

Then:

1. **Restart the computer** if the Docker Desktop installer asks you to. It may
   also ask you to turn on WSL 2 or virtualization; follow its prompts (see
   [Docker's Windows install guide](https://docs.docker.com/desktop/setup/install/windows-install/)).
2. Open **Docker Desktop**, finish its first-run setup, and wait until it says the
   engine is running. Leave it open.
3. Close PowerShell (and Windows Terminal, if you use it) and open it again, so it
   knows about Git and Docker. A new tab in the old window may not. Then paste:

```powershell
git clone https://github.com/skippydinglechalk420/WaveRaider.git
cd WaveRaider
docker compose up
```

*(No `winget`? Install [Git](https://git-scm.com/download/win) and
[Docker Desktop](https://www.docker.com/products/docker-desktop/) from those
links, then follow steps 1–3.)*

### 🍎 macOS

Open **Terminal** (press `Cmd`+`Space`, type *Terminal*, press Enter) and paste:

```bash
brew install git
brew install --cask docker-desktop
```

Open **Docker Desktop**, finish its first-run setup, and wait until it says the
engine is running. Leave it open, then back in Terminal paste:

```bash
git clone https://github.com/skippydinglechalk420/WaveRaider.git
cd WaveRaider
docker compose up
```

*(No `brew`? Install it from <https://brew.sh>, or download
[Docker Desktop](https://www.docker.com/products/docker-desktop/) directly. The
first time you run `git`, macOS may offer to install the Command Line Tools, which
include it — accept, then run the lines again.)*

### 🐧 Linux

Install Docker Engine and its Compose plugin from the official instructions for
[Ubuntu](https://docs.docker.com/engine/install/ubuntu/) or
[Debian](https://docs.docker.com/engine/install/debian/), then:

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/skippydinglechalk420/WaveRaider.git
cd WaveRaider
sudo docker compose up
```

For other distributions use [Docker's installation guide](https://docs.docker.com/engine/install/).

### Open the app

When the terminal shows a line mentioning `http://localhost:8899`, open your web
browser and go to:

**<http://localhost:8899>**

Wave Raider opens on **Monitor**. Select **Start sample session**, then select an
observation to inspect its reported fields and evidence. These are recorded
examples, not live detections. **Settings** has receivers, monitoring, the
watchlist, export, and activity.

### Stopping and restarting

Stop it: click the terminal and press **`Ctrl`+`C`**. Start it again later:

```bash
cd WaveRaider
docker compose up          # Linux: sudo docker compose up
```

Your watchlist, monitoring settings and saved session history are kept between runs.

### Updating

Get the latest version and rebuild (without `--build`, Docker keeps running the
version it built first):

```bash
cd WaveRaider
git pull
docker compose up --build  # Linux: sudo docker compose up --build
```

<details>
<summary><b>Prefer Python instead of Docker?</b></summary>

After cloning (`git clone …` then `cd WaveRaider` as above), create a private
environment and run it. This installs into a `.venv` folder so it never touches
your system Python. Install Python 3.9 or newer first (3.11 recommended). On
Debian/Ubuntu, install `python3`, `python3-venv` and `python3-pip`. Pip installs
NumPy automatically; native radio libraries and USB drivers remain separate.

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install .
.venv\Scripts\python -m waveraider.cli serve
```

**macOS / Linux:**

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m waveraider.cli serve
```

Then open <http://localhost:8899>. Stop it with `Ctrl`+`C`. For recorded captures,
reports, and the command line, see [setup and usage](usage.md).

</details>

<a id="offline-install"></a>

<details>
<summary><b>Advanced: installing on a computer with no internet (field use)</b></summary>

The runtime bundle above is the easiest offline path: install Docker first, then
carry the entire extracted bundle across. To build your own artifacts:

**Docker image (self-contained).** Build it for the offline computer's CPU type:
`linux/amd64` for Intel or AMD computers such as most NUCs, `linux/arm64` for ARM
computers such as Apple Silicon Macs or a Raspberry Pi 5. Docker Desktop can build
either type on any computer; on Linux, first set up emulation as described in
[Docker's multi-platform guide](https://docs.docker.com/build/building/multi-platform/).

    # on a connected machine (use linux/arm64 for an ARM computer)
    docker buildx build --platform linux/amd64 -t waveraider:dev --load .
    docker image inspect waveraider:dev --format '{{.Architecture}}'   # amd64
    docker save waveraider:dev -o waveraider.tar
    # move waveraider.tar to the offline machine, then:
    docker load -i waveraider.tar
    docker run -p 8899:8899 -v waveraider-data:/root/.waveraider waveraider:dev

*(No `docker buildx`? `docker build --platform linux/amd64 -t waveraider:dev .`
also works, but it relabels Docker's cached `python:3.11-slim-bookworm` base image as the
other CPU type. Before building for your own computer again, run
`docker pull python:3.11-slim-bookworm`.)*

**Python wheelhouse (no Docker).** Build it on a connected computer with the **same
Python version as the offline computer** (for example both 3.11; check with
`python3 --version`). Pip picks dependency versions for the Python that builds the
wheelhouse, and some of them require a newer Python than Wave Raider itself does, so a
wheelhouse from a newer Python can fail to install on an older one. The OS and CPU type
must also match the destination: NumPy is a compiled core dependency. Use the same
OS family, CPU architecture and Python major/minor version.
Inside the `WaveRaider` folder (macOS / Linux shown; on Windows use `python` and
`.venv\Scripts\python`):

    # on the connected machine, with the offline computer's Python version
    python3 -m pip wheel . -w wheelhouse     # builds WaveRaider and all core dependency wheels
    # before going offline, check the wheelhouse installs with no network:
    python3 -m venv /tmp/wr-check
    /tmp/wr-check/bin/python -m pip install --no-index --find-links wheelhouse waveraider
    # copy the wheelhouse/ folder to the offline machine, then, in the folder that holds it:
    python3 -m venv .venv
    .venv/bin/python -m pip install --no-index --find-links wheelhouse waveraider
    .venv/bin/python -m waveraider.cli serve

Neither path contacts the network at run time. Docker runs the web UI and sample
data; it does not set up USB passthrough, host drivers, or live Remote ID reception.

</details>

