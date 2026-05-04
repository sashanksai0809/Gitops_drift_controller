FROM python:3.11-slim

ARG KUBECTL_VERSION=v1.29.3

# Install kubectl -- included so the image can apply manifests without kubectl on the host.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && \
    ARCH=$(dpkg --print-architecture) && \
    curl -sSLo /usr/local/bin/kubectl \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl" && \
    chmod +x /usr/local/bin/kubectl && \
    apt-get purge -y --auto-remove curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency spec first so source changes don't invalidate the pip layer.
COPY pyproject.toml ./
COPY src/ src/

RUN pip install --no-cache-dir -e .

# Mount the manifest directory at /manifests at runtime:
#   docker run -v $(pwd)/examples/desired:/manifests:ro gitops-drift --manifests /manifests ...
ENTRYPOINT ["python3", "-m", "gitops_drift.main"]
CMD ["--help"]
