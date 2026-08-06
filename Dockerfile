FROM python:3.12-slim-bookworm

LABEL maintainer="Supermartingale Certificates for Parametric MDPs <atva2026-artifact>"
LABEL description="Docker image for reproducing experiments from ATVA 2026 paper"

# Avoid interactive prompts during build
ENV DEBIAN_FRONTEND=noninteractive

# Copy offline dependencies (REQUIRED - build will fail if missing)
COPY offline_dependencies/deb-packages /tmp/deb-packages/
COPY offline_dependencies/python-packages /tmp/python-packages/
COPY offline_dependencies/mathsat/mathsat-5.6.17-linux-x86_64.tar.gz /tmp/mathsat-5.6.17-linux-x86_64.tar.gz

# Verify offline dependencies are present
RUN if [ ! -d "/tmp/python-packages" ]; then \
        echo "ERROR: Offline dependencies not found!"; \
        echo "Please ensure offline_dependencies/ directory is present."; \
        exit 1; \
    fi

# Install system dependencies from offline .deb packages (no internet needed)
RUN dpkg -i /tmp/deb-packages/*.deb || true && \
    apt-get install -f -y --no-download && \
    rm -rf /tmp/deb-packages /var/lib/apt/lists/*

# Set working directory
WORKDIR /artifact

# Copy requirements first
COPY requirements.txt .

# Install Python packages from offline wheels (no internet needed)
RUN pip install --no-index --find-links=/tmp/python-packages -r requirements.txt

# Install additional packages from offline wheels
RUN pip install --no-index --find-links=/tmp/python-packages setuptools z3-solver

# Install MathSAT from offline tarball
RUN cd /tmp && \
    tar xzf mathsat-5.6.17-linux-x86_64.tar.gz && \
    cd mathsat-5.6.17-linux-x86_64 && \
    cp bin/mathsat /usr/local/bin/ && \
    cp lib/libmathsat.a /usr/local/lib/ && \
    cp include/*.h /usr/local/include/ && \
    cd python && \
    python3 setup.py build && \
    python3 setup.py install && \
    cd /tmp && \
    rm -rf mathsat-5.6.17-linux-x86_64* /tmp/python-packages

# Copy the source code and examples
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY examples/stable/ ./examples/stable/
COPY USER_MANUAL.md .
COPY README.md .
COPY LICENSE .
COPY run_smoke_test.sh .
COPY run_full.sh .
COPY run_partial.sh .
RUN chmod +x run_smoke_test.sh run_full.sh run_partial.sh scripts/*.py

# Create output directories
RUN mkdir -p tmp results

# Set Python path
ENV PYTHONPATH=/artifact

# Default command shows usage
CMD ["bash", "-c", "echo 'ATVA 2026 Artifact: Supermartingale Certificates for Parametric MDPs' && echo 'See README.md for evaluation instructions' && echo '' && echo 'Quick start:' && echo '  ./run_smoke_test.sh'"]
