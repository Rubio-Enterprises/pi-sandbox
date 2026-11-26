FROM ghcr.io/rubio-enterprises/base:main@sha256:273f3fcaeaca36f6b60aaddaa96a710fb8de122533a13e6c49b153075809c1f7 AS dev
ENV UV_PYTHON=3.15.2

USER root
# Mise 2026.11.1 reads the system config's lock at /etc/mise/mise.lock
# Native dependency sidecars under image/.mise/ are part of the lock
COPY image/ /etc/mise/
RUN --mount=type=secret,id=GITHUB_TOKEN /opt/standards/image/install.sh

FROM dev AS ci
COPY --link --from=ghcr.io/rubio-enterprises/runner:main@sha256:44b3f58d3ad97a08b550c96042ce2a6505dec684ec119c62362a2beb6b9766ca --chown=1000:1000 /home/runner /opt/actions-runner
WORKDIR /opt/actions-runner

# Same home and cache paths as the eventual runner, not root's throwaway caches
COPY --chown=1000:1000 . /workspaces/warmup
RUN /opt/standards/image/warmup.sh
USER vscode
