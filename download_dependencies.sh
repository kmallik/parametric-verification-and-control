#!/bin/bash

# Download all dependencies for offline Docker build
# This script downloads everything needed to build the Docker image without internet

set -e

DEPS_DIR="offline_dependencies"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=========================================="
echo "Downloading Dependencies for Offline Build"
echo "=========================================="
echo ""
echo "This will download ~500MB of dependencies"
echo "Target directory: $DEPS_DIR"
echo ""

# Create directory structure
mkdir -p "$DEPS_DIR"/{pip,python-packages,mathsat,apt-packages}

cd "$DEPS_DIR"

# 1. Download pip installer
echo "----------------------------------------"
echo "[1/4] Downloading pip installer..."
echo "----------------------------------------"
curl -sS https://bootstrap.pypa.io/get-pip.py -o pip/get-pip.py
echo "✓ pip installer downloaded"
echo ""

# 2. Download Python packages (wheels)
echo "----------------------------------------"
echo "[2/4] Downloading Python packages..."
echo "----------------------------------------"

# Read requirements and download wheels
cd python-packages

# Download packages from requirements.txt
pip3 download -r ../../requirements.txt --dest .

# Download additional packages
pip3 download setuptools --dest .
pip3 download z3-solver --dest .
pip3 download numpy --dest .

echo "✓ Python packages downloaded"
echo ""
cd ..

# 3. Download MathSAT
echo "----------------------------------------"
echo "[3/4] Downloading MathSAT..."
echo "----------------------------------------"
cd mathsat
wget https://mathsat.fbk.eu/release/mathsat-5.6.17-linux-x86_64.tar.gz
echo "✓ MathSAT downloaded"
echo ""
cd ..

# 4. Download Ubuntu packages
echo "----------------------------------------"
echo "[4/4] Downloading Ubuntu packages..."
echo "----------------------------------------"
cd apt-packages

# Note: Python 3.12 requires deadsnakes PPA on Ubuntu 22.04
# We download the base packages that don't require the PPA first
echo "Downloading base Ubuntu packages..."
apt-get download \
    software-properties-common \
    git \
    wget \
    curl \
    build-essential \
    libgmp-dev \
    z3 \
    vim \
    2>/dev/null || echo "Some packages may already be downloaded"

# For Python 3.12, we note that it requires the deadsnakes PPA
# Users should add the PPA before building: add-apt-repository ppa:deadsnakes/ppa
echo ""
echo "Note: Python 3.12 packages require deadsnakes PPA to be added manually"
echo "The Dockerfile will add this PPA during the build process"

PKG_COUNT=$(ls *.deb 2>/dev/null | wc -l)
echo ""
echo "✓ $PKG_COUNT Ubuntu packages downloaded"
echo ""
cd ..

# Create a manifest file
cd "$SCRIPT_DIR/$DEPS_DIR"
cat > MANIFEST.txt << EOF
OFFLINE DEPENDENCIES MANIFEST
==============================

Generated: $(date)

Directory Structure:
-------------------
pip/
  - get-pip.py           : pip installer for Python 3.12

python-packages/
  - *.whl               : Python wheel files for all dependencies
                          (polyqent, pysmt, numpy, lark, z3-solver, etc.)

mathsat/
  - mathsat-5.6.17-linux-x86_64.tar.gz : MathSAT solver

apt-packages/
  - *.deb               : Ubuntu 22.04 packages
                          ($PKG_COUNT packages total)

Usage:
------
To build Docker image with offline dependencies, use:
  docker build -t atva2026-artifact .

Total Size: $(du -sh . | cut -f1)
EOF

cat MANIFEST.txt
echo ""
echo "=========================================="
echo "✓ All dependencies downloaded successfully!"
echo "=========================================="
echo ""
echo "Dependencies saved to: $DEPS_DIR/"
echo "Total size: $(du -sh . | cut -f1)"
echo ""
echo "To build the Docker image with offline dependencies:"
echo "  docker build -t atva2026-artifact ."
echo ""
echo "The entire project directory (including offline_dependencies/)"
echo "can be transferred to another system or included in a ZIP file."
echo "=========================================="
