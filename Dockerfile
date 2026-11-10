# Debian 13 : ffmpeg 7 (lecture d'un fichier déjà ouvert, pour le bac à sable) et setpriv (util-linux).
FROM python:3.12-slim-trixie
RUN apt-get update && apt-get install -y --no-install-recommends util-linux ffmpeg i965-va-driver intel-media-va-driver mesa-va-drivers \
    && (command -v setpriv || apt-get install -y --no-install-recommends setpriv) \
    && rm -rf /var/lib/apt/lists/*
# Le bac à sable est obligatoire : sans setpriv ou sans lecture par descripteur, la construction échoue
# (et l'appli déjà installée continue de tourner).
RUN command -v setpriv && ffmpeg -hide_banner -protocols | grep -qw fd
WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ .
# Lisible par tous : ffmpeg et Pillow tournent sous « nobody » (bac_a_sable.py), la porte du Wi-Fi sous le compte du serveur.
RUN chmod -R a+rX /app
# L'appli complète (127.0.0.1:8090, publiée seulement par Tailscale). La porte du Wi-Fi
# (page invités) est un autre service de compose.yaml, dans son propre conteneur sans pouvoirs.
CMD ["uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8090", "--proxy-headers", "--no-server-header"]
