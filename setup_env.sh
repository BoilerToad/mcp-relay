#!/usr/bin/env bash
# =============================================================================
# mcp-relay environment setup
# Run from the project root:  bash setup_env.sh
# =============================================================================

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
PYTHON_MIN="3.13"

# -----------------------------------------------------------------------------
# Colors
# -----------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[mcp-relay]${NC} $1"; }
success() { echo -e "${GREEN}[mcp-relay]${NC} $1"; }
warn()    { echo -e "${YELLOW}[mcp-relay]${NC} $1"; }
error()   { echo -e "${RED}[mcp-relay]${NC} $1"; exit 1; }

echo ""
echo "============================================="
echo "  mcp-relay — environment setup"
echo "============================================="
echo ""

# -----------------------------------------------------------------------------
# 1. Find Python 3.13+
# -----------------------------------------------------------------------------
info "Looking for Python ${PYTHON_MIN}+..."

PYTHON=""
for candidate in python3.14 python3.13 python3; do
    if command -v "$candidate" &>/dev/null; then
        VERSION=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        MAJOR=$(echo "$VERSION" | cut -d. -f1)
        MINOR=$(echo "$VERSION" | cut -d. -f2)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 13 ]; then
            PYTHON="$candidate"
            success "Found $candidate (Python $VERSION)"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    error "Python 3.13+ not found. Install via: brew install python@3.13"
fi

# -----------------------------------------------------------------------------
# 2. Create virtual environment
# -----------------------------------------------------------------------------
if [ -d "$VENV_DIR" ]; then
    warn "Virtual environment already exists at .venv — recreating..."
    rm -rf "$VENV_DIR"
fi

info "Creating virtual environment at .venv ..."
"$PYTHON" -m venv "$VENV_DIR"
success "Virtual environment created"

# Activate
source "$VENV_DIR/bin/activate"
success "Virtual environment activated"

# -----------------------------------------------------------------------------
# 3. Upgrade pip + install build tools
# -----------------------------------------------------------------------------
info "Upgrading pip..."
pip install --upgrade pip --quiet

info "Installing build tools..."
pip install --upgrade setuptools wheel hatchling --quiet

# -----------------------------------------------------------------------------
# 4. Install mcp-relay in editable mode with dev deps
# -----------------------------------------------------------------------------
info "Installing mcp-relay[dev] in editable mode..."
pip install -e ".[dev]"

# -----------------------------------------------------------------------------
# 5. Install uvx / mcp-server-fetch for integration tests
# -----------------------------------------------------------------------------
info "Installing uv (for uvx mcp-server-fetch)..."
pip install uv --quiet

info "Pre-fetching mcp-server-fetch via uvx (first run downloads it)..."
uvx mcp-server-fetch --help &>/dev/null || warn "mcp-server-fetch pre-fetch skipped (will download on first test run)"

# -----------------------------------------------------------------------------
# 6. Install httpx for integration test Ollama detection
# -----------------------------------------------------------------------------
info "Installing httpx (for integration test Ollama detection)..."
pip install httpx --quiet

# -----------------------------------------------------------------------------
# 7. Verify installation
# -----------------------------------------------------------------------------
echo ""
info "Verifying installation..."

python -c "import mcp_relay; print(f'  mcp_relay version : {mcp_relay.__version__}')"
python -c "import mcp; print(f'  mcp SDK           : OK')"
python -c "import yaml; print(f'  pyyaml            : OK')"
python -c "import pytest; print(f'  pytest            : OK')"
python -c "import httpx; print(f'  httpx             : OK')"

# -----------------------------------------------------------------------------
# 8. Run unit tests to confirm everything works
# -----------------------------------------------------------------------------
echo ""
info "Running unit tests (excluding integration)..."
echo ""

cd "$PROJECT_DIR"
python -m pytest -m "not integration" -v --tb=short

# -----------------------------------------------------------------------------
# Done
# -----------------------------------------------------------------------------
echo ""
echo "============================================="
success "Setup complete!"
echo "============================================="
echo ""
echo "  Activate env  :  source .venv/bin/activate"
echo "  Run tests      :  pytest -m 'not integration' -v"
echo "  Integration    :  pytest -m integration -v   (requires Ollama)"
echo "  Demo           :  python demo/run_demo.py"
echo ""
