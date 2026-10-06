FROM python:3.12-slim

LABEL org.opencontainers.image.title="DELL Server Panel" \
      org.opencontainers.image.description="DELL服务器监控面板 - 温度/风扇/硬盘/GPU/网络, 支持 Dell IPMI 风扇调速" \
      org.opencontainers.image.licenses="MIT"

# 传感器工具: ipmitool(风扇/机箱温度) smartmontools(硬盘温度) nvme-cli(NVMe) curl(健康检查)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ipmitool smartmontools nvme-cli curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY src/ /app/

ENV PORT=8080 \
    BIND=0.0.0.0 \
    REFRESH=5 \
    TITLE="DELL服务器监控面板" \
    PYTHONUNBUFFERED=1

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/status" >/dev/null || exit 1

CMD ["python3", "-u", "/app/app.py"]
