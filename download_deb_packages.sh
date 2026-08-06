#!/bin/bash
# Download all Debian packages needed for MathSAT compilation
# Must be run on a Debian bookworm system or in a Docker container

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEB_DIR="$SCRIPT_DIR/offline_dependencies/deb-packages"

echo "=============================================="
echo "Downloading Debian packages for offline build"
echo "=============================================="
echo ""
echo "This script downloads build-essential, libgmp-dev, and all dependencies."
echo "Must be run on Debian bookworm (or in python:3.12-slim-bookworm container)."
echo ""

mkdir -p "$DEB_DIR"
cd "$DEB_DIR"

# Download packages with all dependencies
echo "Downloading packages..."
apt-get update

# Use apt-get download with dependencies
# We need to download recursively
apt-get download $(apt-cache depends --recurse --no-recommends --no-suggests \
    --no-conflicts --no-breaks --no-replaces --no-enhances \
    build-essential libgmp-dev vim 2>/dev/null | grep "^\w" | sort -u)

echo ""
echo "Downloaded packages:"
ls -lh *.deb | wc -l
echo "files"
du -sh .

echo ""
echo "Done! Packages saved to: $DEB_DIR"
