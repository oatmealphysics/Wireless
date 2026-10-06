These offline bundles passed the full Python/JavaScript suite (including the pinned
recorded DroneID fixture), native AMD64 and ARM64 installed-runtime checks,
network-disabled startup checks, and launcher/persistence checks for the same source
revision. See `release.json` for source and image identities and `SHA256SUMS` for
download integrity.

Download **waveraider-amd64.zip** for Intel/AMD or **waveraider-arm64.zip** for ARM64.
Extract the entire ZIP. Windows: open `start.cmd`. macOS/Linux: run `sh start.sh`
(use `sudo` on Linux if Docker requires it). Docker is a host prerequisite.

Monitor observations still use recorded sample data. Optional node health is
separate from detection. Physical receiver setup and field validation remain
required; these builds do not establish live detection or TDOA performance.
