#!/bin/bash

# ATVA 2026 Artifact: Quick smoke test
# Runs benchmarks with both Z3 and MathSAT to verify installation

# Color codes
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Determine script directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULTS_DIR="$SCRIPT_DIR/smoke_test_results"

# Create results directory
mkdir -p "$RESULTS_DIR"

echo "========================================"
echo "ATVA 2026 Artifact - Smoke Test"
echo "========================================"
echo ""
echo "Testing both Z3 and MathSAT solvers"
echo ""

# Track success/failure
z3_status=0
mathsat_status=0

# Test 1: Z3
echo "----------------------------------------"
echo -e "${BLUE}[1/2] Testing Z3 solver${NC}"
echo "Config: examples/stable/LRW_1d_add_z3_smoke.json"
echo "Started: $(date)"
echo ""

if python3 "$SCRIPT_DIR/src/param_synthesis.py" "$SCRIPT_DIR/examples/stable/LRW_1d_add_z3_smoke.json"; then
    echo ""
    echo -e "${GREEN}✓ Z3 test PASSED${NC}"
    z3_status=0
else
    echo ""
    echo -e "${YELLOW}✗ Z3 test FAILED${NC}"
    z3_status=1
fi

echo ""

# Test 2: MathSAT
echo "----------------------------------------"
echo -e "${BLUE}[2/2] Testing MathSAT solver${NC}"
echo "Config: examples/stable/LRW_1d_add_mathsat_smoke.json"
echo "Started: $(date)"
echo ""

if python3 "$SCRIPT_DIR/src/param_synthesis.py" "$SCRIPT_DIR/examples/stable/LRW_1d_add_mathsat_smoke.json"; then
    echo ""
    echo -e "${GREEN}✓ MathSAT test PASSED${NC}"
    mathsat_status=0
else
    echo ""
    echo -e "${YELLOW}✗ MathSAT test FAILED${NC}"
    mathsat_status=1
fi

echo ""
echo "========================================"
echo "GENERATING RESULTS SUMMARY"
echo "========================================"

# Generate results table and figures if scripts exist
if [ -f "$SCRIPT_DIR/scripts/generate_results_table.py" ]; then
    echo "Generating results table..."
    python3 "$SCRIPT_DIR/scripts/generate_results_table.py" "$RESULTS_DIR"
fi

if [ -f "$SCRIPT_DIR/scripts/generate_figures.py" ]; then
    echo "Generating parameter space figures..."
    python3 "$SCRIPT_DIR/scripts/generate_figures.py" "$RESULTS_DIR"
fi

echo ""
echo "Results saved in: $RESULTS_DIR"

echo ""
echo "========================================"
echo "SUMMARY"
echo "========================================"

if [ $z3_status -eq 0 ] && [ $mathsat_status -eq 0 ]; then
    echo -e "${GREEN}✓ Both solvers working${NC}"
    echo ""
    echo "Installation verified successfully!"
    echo ""
    echo "Next steps:"
    echo "  - Partial run (~30-40 min): ./run_partial.sh"
    echo "  - Full run (~30-60 min): ./run_full.sh"
    echo "  - See README.md for more details"
    exit 0
elif [ $z3_status -eq 0 ]; then
    echo -e "${YELLOW}⚠ Z3 working, MathSAT failed${NC}"
    echo ""
    echo "Z3 is installed correctly. You can run Z3-based experiments."
    echo ""
    echo "To install MathSAT:"
    echo "  python3 -m pysmt install --msat --confirm-agreement"
    echo ""
    echo "Or run Z3-only experiments:"
    echo "  python3 src/param_synthesis.py examples/stable/LRW_1d_add_z3.json"
    exit 0
elif [ $mathsat_status -eq 0 ]; then
    echo -e "${YELLOW}⚠ MathSAT working, Z3 failed${NC}"
    echo ""
    echo "MathSAT is installed correctly. You can run MathSAT-based experiments."
    echo ""
    echo "Z3 should be available. Please check installation."
    exit 0
else
    echo "✗ Both solvers failed"
    echo ""
    echo "Please check:"
    echo "  - Python dependencies are installed (pip3 install -r requirements.txt)"
    echo "  - SMT solvers are properly installed"
    echo ""
    echo "See README.md for troubleshooting steps."
    exit 1
fi
