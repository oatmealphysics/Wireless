# Wave Raider — recorded Remote ID analysis and RF inspection, local web UI.
FROM python:3.11-slim-bookworm

# Native bladeRF API and CLI; USB access and model-specific FPGA images are host setup.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libbladerf2 bladerf openssl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY waveraider ./waveraider
RUN pip install --no-cache-dir .

# Show output as it happens, and stop on Ctrl+C or docker stop right away (as a Ctrl+C
# would) instead of waiting for Docker's 10-second kill.
ENV PYTHONUNBUFFERED=1
STOPSIGNAL SIGINT
EXPOSE 8899
# The watchlist, monitoring settings and session history live in /root/.waveraider (mount a volume to keep them).
CMD ["wr", "serve", "--port", "8899"]
