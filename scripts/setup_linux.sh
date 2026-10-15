#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

INSTALLER="${1:-}"
STUDIO="${DJSP_CPLEX_STUDIO:-/opt/ibm/ILOG/CPLEX_Studio222}"
SWAP_GB="${SWAP_GB:-8}"
CPOPTIMIZER="$STUDIO/cpoptimizer/bin/x86-64_linux/cpoptimizer"

sudo apt-get update -y
sudo apt-get install -y python3-venv python3-dev build-essential git

if [ "$SWAP_GB" -gt 0 ] && [ -z "$(swapon --show=NAME --noheadings)" ]; then
    sudo fallocate -l "${SWAP_GB}G" /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-djsp-swap.conf
    sudo sysctl -p /etc/sysctl.d/99-djsp-swap.conf
fi

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

make

if [ -n "$INSTALLER" ] && [ ! -x "$CPOPTIMIZER" ]; then
    sudo apt-get install -y --no-install-recommends openjdk-17-jre-headless
    PROPERTIES="$(mktemp)"
    cat > "$PROPERTIES" <<EOF
INSTALLER_UI=silent
LICENSE_ACCEPTED=TRUE
USER_INSTALL_DIR=$STUDIO
USER_INPUT_SEGMENT_TRUE=0
USER_INPUT_SEGMENT_FALSE=1
CPLEX_STUDIO_IDE=0
CPLEX_STUDIO_README=0
EOF
    chmod +x "$INSTALLER"
    sudo "$INSTALLER" -i silent -f "$PROPERTIES"
    rm -f "$PROPERTIES"
fi

if [ -x "$CPOPTIMIZER" ]; then
    echo "CP Optimizer: $CPOPTIMIZER"
else
    echo "CP Optimizer not installed; the cp engine is unavailable."
fi
