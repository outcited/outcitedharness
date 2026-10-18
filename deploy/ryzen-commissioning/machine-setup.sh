#!/bin/bash
# RYZEN EXPANSION commissioning — Machine A/B setup
# Tested on Ubuntu 24.04.3 LTS, Ryzen 9, 32GB RAM, 2TB NVMe
# Usage: bash machine-setup.sh [machine-id] [role]
set -e

MACHINE_ID="${1:?Usage: machine-setup.sh <machine-id> <role>}"
ROLE="${2:?Usage: machine-setup.sh <machine-id> <role>}"

echo "=== Commissioning $MACHINE_ID as $ROLE ==="

# 1. System packages
sudo apt-get update
sudo apt-get install -y build-essential python3.12-venv python3-pip sqlite3 curl git

# 2. Rust toolchain
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source "$HOME/.cargo/env"
rustc --version
cargo --version

# 3. Python environment
python3 -m venv "$HOME/Harnessv1/.venv"
"$HOME/Harnessv1/.venv/bin/pip" install --upgrade pip
"$HOME/Harnessv1/.venv/bin/pip" install pymupdf requests pydantic

# 4. Directory layout
mkdir -p "$HOME/Harnessv1"/{scripts,harness,config,results,docs}
mkdir -p "$HOME/Harnessv1-powerdata01"/{designwins,scripts,scratch}
mkdir -p "$HOME/lane-out" "$HOME/corpus-vault" "$HOME/extract-results"

# 5. Copy code from M5 (run from M5 or happy)
# rsync -avz M5:~/Harnessv1/scripts/ "$HOME/Harnessv1/scripts/"
# rsync -avz M5:~/Harnessv1/harness/ "$HOME/Harnessv1/harness/"
# rsync -avz M5:~/Harnessv1-powerdata01/designwins/ "$HOME/Harnessv1-powerdata01/designwins/"

# 6. Build Rust
cd "$HOME/Harnessv1-powerdata01/designwins"
cargo build --release

# 7. Machine identity
echo "$MACHINE_ID" > "$HOME/.machine-id"
echo "$ROLE" > "$HOME/.machine-role"

echo "=== Commissioning complete for $MACHINE_ID ==="
echo "Next steps:"
echo "  1. Copy code from M5 or happy"
echo "  2. Copy queue files to /tmp/"
echo "  3. Run canary: $HOME/Harnessv1-powerdata01/designwins/target/release/dw-bridge < sample.jsonl"
echo "  4. Enable service: sudo systemctl enable --now extract-mode-currents@0"
echo "  5. Verify: ps aux | grep extract | grep -v grep | wc -l"
