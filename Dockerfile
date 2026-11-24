# Headless MeMyselfAI for a VPS. See docs/vps.md.
# Debian's Python packages, the same ones deploy/install-vps.sh uses. The prebuilt PyPI wheel of
# cryptography can require CPU features that some virtual machines lack.
FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git nodejs npm openssl \
        python3 python3-cryptography \
 && rm -rf /var/lib/apt/lists/*

# Everything that must survive a container rebuild lives under /data: the workspace,
# agent CLIs installed with npm, and their sign-ins in the service user's home.
RUN useradd --uid 10001 --home-dir /data/home --no-create-home --shell /bin/bash memyselfai \
 && mkdir -p /data && chown memyselfai /data
ENV HOME=/data/home \
    NPM_CONFIG_PREFIX=/data/home/.local \
    PATH=/data/home/.local/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --chown=root:root *.py /app/
COPY --chown=root:root ui /app/ui
COPY --chown=root:root vendor /app/vendor
COPY --chown=root:root deploy/container-start.sh /app/container-start.sh

USER memyselfai
VOLUME /data
EXPOSE 8801 8802
CMD ["/bin/sh", "/app/container-start.sh"]
