FROM ghcr.io/home-assistant/base-python:3.13-alpine3.22

ARG BUILD_VERSION="0.3.0"
ARG BUILD_ARCH

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}" \
  org.opencontainers.image.title="Irrigation Manager Multi-Impianto" \
  org.opencontainers.image.description="Portale multiutente per irrigazione basato sulle entità Home Assistant" \
  org.opencontainers.image.licenses="MIT"

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt
COPY app /app
COPY run.sh /run.sh
RUN chmod a+x /run.sh
CMD ["/run.sh"]
