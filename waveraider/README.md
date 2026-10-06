<p align="center">
  <img src="waveraider/brand/logo.png" alt="WaveRaider — official logo" width="720">
</p>

# WaveRaider

**Offline RF inspection today. A field-ready UAS detection workflow in development.**

WaveRaider brings recorded Remote ID decoding, radio-frequency inspection, and
sample-based monitoring into one local application. The goal is an offline field
computer that monitors supported drone-link families and presents the evidence
behind each observation, including signals from aircraft that do not broadcast
Remote ID.

**Raw-IQ classification, trained models, and live RF alerts are planned. None is
implemented yet.** Live Remote ID reception also remains unvalidated and unsupported.

[Install](#install--copy--paste) · [Latest release](https://github.com/skippydinglechalk420/WaveRaider/releases/latest) · [Usage guide](docs/usage.md) · [Roadmap and validation gates](docs/roadmap.md)

## What you can use today

The latest numbered release is **[v0.6.3](https://github.com/skippydinglechalk420/WaveRaider/releases/tag/v0.6.3)**.

| Capability | Available behavior | Boundary |
| --- | --- | --- |
| Recorded Remote ID | Decode supported Wi-Fi beacon and Bluetooth legacy captures; inspect reported identifiers and positions | Broadcast claims are unauthenticated; this is not live reception |
| Monitor and Settings | Explore a bundled sample, evidence details, watchlists, receiver information and exports; stopped sessions are saved locally when the save succeeds, and saved sessions can be exported or deleted later | Monitor replays sample data, not nearby live traffic; the server has no login, so anyone who can reach it can view, export or delete saved sessions |
| RF inspection | Inspect supported drone-band spectrum with an SDR, or use a simulated spectrum without one | Frequency, power and profile hints do not identify a drone |
| Experimental DroneID scan | Report ordered pilot-pair candidates | No payload validation, aircraft identity or GPS extraction |
| Experimental node tools (current build) | Authenticated heartbeats and recording summaries over an IP network; opt-in bladeRF micro or simulated IQ recording | CLI capture/reporting and opt-in GUI node health; physical capture and network validation pending; no TDOA or automatic detection reporting. [Setup](docs/node-quickstart.md) |
| Offline deployment | Build Docker images for `linux/amd64` and `linux/arm64`, or use a native Python install | Build checks do not establish physical field-hardware or live-RF performance |

No receiver is needed to try the application. Start a sample session to explore the
interface. With suitable hardware, Inspect RF provides a separate spectrum view
across the supported 868/900 MHz, 1.3, 2.4, 5.2 and 5.8 GHz reference bands.

Wi-Fi NAN and Bluetooth 5 Long Range are not supported. A quiet receiver or short
negative scan does not prove that no drone is present.

## Where WaveRaider is headed

1. **Recorded IQ before live inference.** Develop and evaluate an interpretable
   baseline using rights-compatible recordings with defensible labels and linked
   provenance. Add a shared model, and specialist models only if measured results
   justify them. No model or raw-IQ inference module ships today.
2. **An offline field computer.** Target a native Ubuntu installation on a small
   computer such as an Intel NUC or Jetson, with attached SDR receivers and a locally
   stored, versioned model. The intended operator client is a Samsung S20 5G accessing
   the web GUI over local Wi-Fi; IQ processing stays on the field computer. No specific
   NUC/Jetson model has been validated. Training stays on a separate machine; model updates are offline.
   The accepted distributed design adds two or three operator-carried sensor nodes
   reporting to one operator node, with local GPSD position reporting at each receiver.
   GPSD does not by itself provide TDOA sample synchronization. See the
   [node architecture and delivery gates](docs/distributed-nodes.md); these features
   include an initial CLI implementation; moving-node localization remains planned. Field observations are not used for automatic
   learning or retraining.
3. **Evidence-based monitoring and alerts.** Aim to scan supported bands repeatedly,
   classify supported link families, and show evidence, uncertainty and receiver
   coverage. Sequential scanning has blind intervals; it is not continuous coverage
   of every band. Alert delivery and retention are still design decisions.
4. **Validate before relying on it.** Keep recorded and live integration behind
   separate gates, including independent known-positive and long representative
   negative sessions, whole-pipeline false-event measurements, and agreed budgets
   on the target hardware. A successful known-transmitter check alone is not enough.

FPV control links such as **ExpressLRS and Crossfire**, followed by **DJI links**, are
research priorities, not supported detection claims. Qualified protocol-labeled IQ
coverage remains incomplete; generic LoRa or a device brand does not establish an
air protocol. A September 2026 search of public sources found no raw IQ recordings
with both usable license terms and protocol evidence for ExpressLRS, Crossfire,
HDZero, Walksnail, the DJI O3/O4 air units, AFHDS 3, OpenIPC-class links or Wi-Fi
Remote ID. Those link families therefore depend on the project's own recordings of
known transmitters, which have not started. Live Remote ID is a separate
receive-path milestone.

A future classifier result would describe evidence consistent with a supported
**link family**, not identify a specific aircraft or establish its presence with
certainty. Its score must be validated for the stated data and conditions; it must
be able to report **unknown**. See [how to interpret evidence](#how-to-interpret-evidence)
and the [full roadmap](docs/roadmap.md) for the acceptance gates.

There are no promised release dates or established field-performance guarantees.
The earlier general-purpose protocol analyzers were retired in v0.6.0;
[see what changed](docs/usage.md#retired-after-v051).

## Install — copy & paste

**Three steps. No coding needed.** Connect to the internet for the first setup.
Docker runs WR on your computer; install it once. Already have Docker? Skip step 1.

### 1. Install Docker — choose your computer

<details>
<summary><b>🪟 Windows — click to expand</b></summary>

[**⬇ Download Docker for Windows (Intel/AMD)**](https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe)

Open the downloaded installer and follow its prompts. Restart if asked, then open
**Docker Desktop** from Start. Wait for **Engine running** and leave it open.

Prefer a command? Open **PowerShell** from Start and paste:

```powershell
winget install -e --id Docker.DockerDesktop
```

[Windows setup help / ARM computers](https://docs.docker.com/desktop/setup/install/windows-install/)

</details>

<details>
<summary><b>🍎 macOS — click to expand</b></summary>

[**⬇ Docker for Apple Silicon**](https://desktop.docker.com/mac/main/arm64/Docker.dmg)
· [**⬇ Docker for Intel Mac**](https://desktop.docker.com/mac/main/amd64/Docker.dmg)

Not sure? **Apple menu → About This Mac** shows your chip.
Open `Docker.dmg`, drag **Docker** to **Applications**, then open it.
Finish setup, wait for **Engine running**, and leave it open.

Already use Homebrew? Open **Terminal** and paste:

```bash
brew install --cask docker-desktop
open -a Docker
```

</details>

<details>
<summary><b>🐧 Ubuntu 24.04 — click to expand</b></summary>

Press **Ctrl+Alt+T**, then paste this block. Enter your computer password if asked
(it will not appear while you type).

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
```

Uses [Ubuntu's Docker packages](https://packages.ubuntu.com/noble/docker-compose-v2).
For other Linux versions, use [Docker's install guide](https://docs.docker.com/engine/install/).

</details>

### 2. Download WR

[![Download WaveRaider](docs/assets/download-wr.svg)](https://github.com/skippydinglechalk420/WaveRaider/releases/latest)

Sign in to GitHub → open the latest validated offline release → scroll to **Assets**
→ download the ZIP for your computer. These bundles do not expire after 30 days:

| Your computer | Download this file |
| --- | --- |
| Intel/AMD NUC or PC; Intel Mac | **waveraider-amd64.zip** |
| Apple Silicon Mac; Jetson; Raspberry Pi with 64-bit OS | **waveraider-arm64.zip** |

**Extract the entire ZIP** into a folder called **WaveRaider** in **Downloads**.
On Windows: right-click the ZIP → **Extract All**. On Mac: double-click the ZIP,
then rename the extracted folder. On Linux: right-click → **Extract Here**, then
rename the folder. `start.sh` and `start.cmd` should be directly
inside **Downloads → WaveRaider**.

### 3. Start WR

**Windows:** open **Downloads → WaveRaider** and double-click **start.cmd**.

**Mac:** open **Terminal** and paste:

```bash
cd "$HOME/Downloads/WaveRaider" && sh start.sh
```

**Linux:** open **Terminal** and paste:

```bash
cd "$HOME/Downloads/WaveRaider" && sudo sh start.sh
```

Leave that window open. When it shows the server address:

[**▶ OPEN WAVERAIDER — http://localhost:8899**](http://localhost:8899)

Click **Start sample session** to try WR without a radio. This replays recorded
examples; live radio use needs [receiver setup](docs/install-advanced.md#receiver-setup).

**Stop:** press **Ctrl+C** in the running terminal. **Restart:** repeat step 3.
**Update:** stop WR, download a fresh ZIP into a new folder, and run its launcher.
Saved settings and sessions stay on your computer.

<details>
<summary><b>Need help?</b></summary>

| What you see | What to do |
| --- | --- |
| GitHub says 404 / no download | Sign in with an account that has access to this private repository. |
| No ZIPs under Assets | Check that you opened a validated offline release and are signed in. |
| “Start Docker” | Open Docker Desktop, wait for the engine, then try again. |
| “No such file or directory” | Check that Downloads → WaveRaider contains `start.sh`; avoid a second nested folder. |
| Wrong CPU / checksum error | Download the matching ZIP again and extract all files. |
| Port 8899 already in use | Stop the other copy of WR, then try again. |

[Phone over Wi-Fi, another port, or radio setup](docs/install-advanced.md)
· [Build from source or use Python](docs/install-advanced.md#build-from-source)
· [Prepare an offline install](docs/install-advanced.md#offline-install)

</details>

## How to interpret evidence

| Evidence | What it supports |
| --- | --- |
| Decoded Remote ID report | Supported fields reported by a transmitter; identity and location are not independently authenticated |
| Experimental DroneID candidate | An ordered DJI pilot pair, searched over carrier offset; no decoded identity or location |
| Unidentified RF activity | Energy on a frequency; a matching reference profile is not UAS identification |

A planned RF classifier would add a separate evidence type, not a rung on a ranking:
a model result consistent with a supported kind of link, reported with the model
version and a score whose meaning is established only by validation on stated data.
It is not a probability that a drone is present: the same kinds of links (Wi-Fi,
analog video, LoRa, an idle controller) also have non-drone uses. It can say
"unknown", and is never an identity, aircraft count or location.

A transmitter is not an aircraft count. Wi-Fi and Bluetooth addresses are separate
namespaces, and multiple transmitters or changing addresses can describe the same
aircraft. Receive timestamps differ from timestamps claimed in a message. A
watchlist match is an identifier match, not evidence of a threat.

## Documentation

- [Setup, recorded captures, and current UI](docs/usage.md)
- [Remote ID support and evidence limits](docs/remote-id.md)
- [Spectrum and experimental DroneID](docs/spectrum.md)
- [Product roadmap](docs/roadmap.md)
- [Development and validation](docs/development.md)
- [Changelog](CHANGELOG.md)

## License

MIT — see [LICENSE](LICENSE). Vendored Open Drone ID reference fixtures carry
[their own attribution and license](tests/fixtures/opendroneid/PROVENANCE.md).
