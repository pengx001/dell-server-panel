# DELL Server Panel

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-v1.1.0-green.svg)](https://github.com/pengx001/dell-server-panel/releases)
[![Docker](https://img.shields.io/badge/docker-ghcr.io-2496ED?logo=docker&logoColor=white)](https://github.com/pengx001/dell-server-panel/pkgs/container/dell-server-panel)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20fnOS%20%7C%20NAS-lightgrey.svg)](#)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](#)

[中文](README.md) | **English**

A single-container, zero-dependency web dashboard for server hardware monitoring:
CPU temperature/load, GPU temperature·fan·VRAM·power, chassis & disk temperatures, fan speeds,
NIC status and real-time traffic — plus **IPMI fan control on Dell PowerEdge servers**
(quiet / standard / full / automatic, with over-temperature fallback).

> Backend is pure Python standard library; frontend is one HTML file (no framework, no CDN).
> **No data ever leaves your machine.**

## Features

### Monitoring
- **CPU**: package temperature per socket, per-core temperatures, usage %
- **GPU**: NVIDIA (nvidia-smi) / AMD (amdgpu) / Intel — temperature, fan %, util, VRAM, power
- **Chassis**: inlet / exhaust / CPU temps via IPMI
- **Disks**: RAID controller (perccli/storcli), NVMe (nvme-cli), SATA (smartctl)
- **Fans**: server (IPMI, incl. redundancy) or consumer boards (hwmon)
- **Network**: link state, speed, real-time up/down rate, cumulative traffic, throughput sparkline
- **System**: load average, memory usage, uptime

### Fan control (Dell PowerEdge)
- Presets: **Quiet (10%) / Standard (20%) / Auto (thermostatic) / Full (100%)**
- Custom duty (10-100%, clamped & validated)
- **Safety**: ≥78 °C hands control back to iDRAC · floor 10 % · auto-restore after reboot

### UI
- **Auto hardware model detection**: reads DMI at startup and shows e.g. `Dell Inc. · PowerEdge R730XD`; switches automatically on other machines (e.g. R740)
- Dark / light theme toggle (`?theme=light` also works)
- Per-card visibility settings (⚙️, remembered locally)
- Ring gauges, animated number rolling, subtle flash on temperature change
- Card fade-in, mobile-friendly, real-time (1-2 s refresh, 0.07 s sensor reads via IPMI SDR cache)

## Quick Start

```bash
git clone https://github.com/pengx001/dell-server-panel.git
cd dell-server-panel
docker compose up -d --build
# open http://<your-nas-ip>:18080
```

Or with plain docker:

```bash
docker build -t dell-server-panel .
docker run -d --name dell-server-panel --network host --restart unless-stopped \
  -e IPMI_HOST=192.168.1.120 -e IPMI_USER=root -e IPMI_PASS=calvin \
  -e TITLE="DELL Server Panel" -v /sys:/sys:ro dell-server-panel
```

## Configuration (env vars)

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8080` | Listening port |
| `BIND` | `0.0.0.0` | Bind address |
| `REFRESH` | `5` | Frontend refresh interval (s) |
| `TITLE` | `DELL Server Panel` | Page title |
| `IPMI_HOST` | — | BMC/iDRAC address (remote IPMI; no privileges needed) |
| `IPMI_USER` / `IPMI_PASS` | — | IPMI credentials |
| `ENABLE_FAN_CONTROL` | `auto` | `true`/`false`/`auto` |
| `AUTH_USER` / `AUTH_PASS` | — | Optional HTTP Basic auth |

## Notes

- **Disk temps behind a RAID controller** require the host's `perccli` mounted into the container.
- **Local IPMI** needs `--device /dev/ipmi0` (or `privileged: true`); remote IPMI needs nothing.
- **NVIDIA GPU** requires the NVIDIA Container Toolkit (`--gpus all`).
- IPMI **SDR cache** is used to cut fan reads from seconds to ~0.07 s.

## License

[MIT](LICENSE)

## ⭐ Support

If this helps you, a **Star** is appreciated — it helps others find it too.
- **Auto hardware title**: the title is generated from the detected model (e.g. "Dell PowerEdge R740 监控面板"); override with `TITLE="{model} Panel"`
