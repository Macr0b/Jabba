FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends util-linux && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ .
# Deux portes : l'appli complète (127.0.0.1:8090, publiée seulement par Tailscale)
# et la page invités du Wi-Fi (port 80, réseau local seulement).
CMD ["sh", "-c", "uvicorn invites:app --host 0.0.0.0 --port ${PORT_WIFI:-80} --no-server-header --log-level warning & exec uvicorn main:app --host 127.0.0.1 --port 8090 --proxy-headers --no-server-header"]
