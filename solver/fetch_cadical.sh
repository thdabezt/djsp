#!/usr/bin/env bash
set -euo pipefail
VERSION="$1"
cd "$(dirname "$0")"
if [ ! -d "cadical-$VERSION" ]; then
    git clone --quiet --depth 1 --branch "rel-$VERSION" https://github.com/arminbiere/cadical.git "cadical-$VERSION"
fi
cd "cadical-$VERSION"
./configure
make -j"$(nproc)"
