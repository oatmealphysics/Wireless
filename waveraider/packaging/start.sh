#!/bin/sh
set -eu
cd "$(dirname "$0")"
command -v docker >/dev/null 2>&1 || { echo 'Install Docker first (see README.txt).'; exit 1; }
docker info >/dev/null 2>&1 || { echo 'Start Docker, or use sudo sh start.sh on Linux.'; exit 1; }
docker compose version >/dev/null 2>&1 || { echo 'Install the Docker Compose plugin.'; exit 1; }
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c SHA256SUMS
else
    shasum -a 256 -c SHA256SUMS
fi
expected=$(cat architecture.txt)
actual=$(docker info --format '{{.Architecture}}')
case "$actual" in x86_64) actual=amd64;; aarch64) actual=arm64;; esac
[ "$actual" = "$expected" ] || { echo "This bundle is $expected; Docker is $actual. Download the matching bundle."; exit 1; }
docker load -i waveraider.tar
printf '\nOpen http://localhost:8899 after the server starts. Stop with Ctrl+C.\n'
exec docker compose up --pull never
