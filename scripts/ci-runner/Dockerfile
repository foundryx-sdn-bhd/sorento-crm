# Self-hosted Actions runner for the Mac mini (label sorento-mac, linux arm64).
# The official runner image (git, jq, python3, docker CLI) plus what the test
# jobs call directly on a GitHub-hosted runner: gh (Changed areas, PR gate),
# psql/pg_isready (Postgres setup steps), zstd (actions/cache), and the
# pango/cairo libraries WeasyPrint loads (same list as the backend image).
FROM ghcr.io/actions/actions-runner:2.337.0

ARG GH_VERSION=2.102.0

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
      postgresql-client \
      zstd \
      xz-utils \
      libglib2.0-0 \
      libpango-1.0-0 \
      libpangocairo-1.0-0 \
      libgdk-pixbuf-2.0-0 \
      libcairo2 \
      libffi8 \
      shared-mime-info \
      fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && cd /tmp \
    && arch="$(dpkg --print-architecture)" \
    && curl -sSfLO "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${arch}.tar.gz" \
    && curl -sSfLO "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_checksums.txt" \
    && grep " gh_${GH_VERSION}_linux_${arch}.tar.gz\$" "gh_${GH_VERSION}_checksums.txt" | sha256sum -c - \
    && tar -xzf "gh_${GH_VERSION}_linux_${arch}.tar.gz" \
    && install -m 0755 "gh_${GH_VERSION}_linux_${arch}/bin/gh" /usr/local/bin/gh \
    && rm -rf /tmp/gh_*

COPY entrypoint.sh job-started.sh /usr/local/bin/
# Registration survives a container re-create in the runner-state volume.
RUN mkdir /runner-state && chown runner:runner /runner-state
USER runner
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
