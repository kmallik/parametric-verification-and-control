#!/bin/bash

# ATVA 2026 Artifact: Partial run (faster validation)
# This script runs all benchmarks with reduced epsilon thresholds
# Runtime: ~30-40 minutes (faster than full run)

set -e  # Exit on error

# Color codes for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Determine script directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXAMPLES="$SCRIPT_DIR/examples/stable"
RESULTS_DIR="$SCRIPT_DIR/partial_run_results"

# Create results directory
echo -e "${BLUE}Creating results directory: $RESULTS_DIR${NC}"
mkdir -p "$RESULTS_DIR"

# Print header
echo "========================================"
echo "ATVA 2026 Artifact - Partial Run"
echo "========================================"
echo ""
echo "Running all benchmarks with reduced epsilon thresholds"
echo "This provides faster validation than the full run"
echo "Expected total runtime: 30-40 minutes"
echo ""

# Create temporary modified config files
TMP_DIR="$SCRIPT_DIR/tmp_partial_configs"
mkdir -p "$TMP_DIR"

# Function to create modified config with custom max_inconclusive
create_modified_config() {
    local base_config=$1
    local output_config=$2
    local max_inconc_values=$3
    local logfile=$4

    # Read the base config and modify max_inconclusive, cutoff_time, and logfile
    python3 -c "
import json
import sys

with open('$base_config', 'r') as f:
    config = json.load(f)

# Update max_inconclusive values
config['max_inconclusive'] = $max_inconc_values

# Update cutoff_time_per_smt_query to 20 seconds for intermediate experiments
config['cutoff_time_per_smt_query'] = 20

# Update logfile
config['logfile'] = '$logfile'

with open('$output_config', 'w') as f:
    json.dump(config, f, indent=4)
"
}

# Array of experiments: name, base config, max_inconclusive values, logfile
declare -a experiments=(
    "M+ (add) with Z3:LRW_1d_add_z3.json:[0.4, 0.3]:LRW_1d_add_z3_partial.log"
    "M+ (add) with MathSAT:LRW_1d_add_mathsat.json:[0.4, 0.3]:LRW_1d_add_mathsat_partial.log"
    "M× (mul) with Z3:LRW_1d_mul_z3.json:[0.4, 0.3]:LRW_1d_mul_z3_partial.log"
    "M× (mul) with MathSAT:LRW_1d_mul_mathsat.json:[0.4, 0.3]:LRW_1d_mul_mathsat_partial.log"
    "M+,× (add+mul) with Z3:LRW_1d_add_mul_z3.json:[0.4, 0.3]:LRW_1d_add_mul_z3_partial.log"
    "M+,× (add+mul) with MathSAT:LRW_1d_add_mul_mathsat.json:[0.4, 0.3]:LRW_1d_add_mul_mathsat_partial.log"
)

# Track experiment count
total=${#experiments[@]}
current=0
successful=0
failed=0

# Start time
start_time=$(date +%s)

# Run each experiment
for exp in "${experiments[@]}"; do
    current=$((current + 1))

    # Parse experiment components (split by colon)
    IFS=':' read -r name base_config max_inconc logfile <<< "$exp"

    base_config_path="$EXAMPLES/$base_config"
    modified_config="$TMP_DIR/$(basename $base_config .json)_partial.json"
    log_path="$RESULTS_DIR/$logfile"

    # Create modified config
    echo "========================================"
    echo -e "${BLUE}[$current/$total] Preparing: $name${NC}"
    echo "Base config: $base_config"
    echo "Max inconclusive: $max_inconc"
    echo "Log: $log_path"
    echo ""

    create_modified_config "$base_config_path" "$modified_config" "$max_inconc" "$log_path"

    # Run the experiment
    # Note: Don't use tee to the same file as the config's logfile
    # write_to_logfile handles structured output with EXPERIMENT headers
    echo "Started: $(date)"
    echo ""

    if python3 "$SCRIPT_DIR/src/param_synthesis.py" "$modified_config"; then
        echo -e "${GREEN}✓ Completed successfully${NC}"
        successful=$((successful + 1))
    else
        echo -e "${YELLOW}✗ Failed or timed out${NC}"
        failed=$((failed + 1))
    fi

    echo "Finished: $(date)"
    echo ""
done

# Clean up temporary configs
rm -rf "$TMP_DIR"

# End time and summary
end_time=$(date +%s)
elapsed=$((end_time - start_time))
minutes=$((elapsed / 60))
seconds=$((elapsed % 60))

echo "========================================"
echo "GENERATING RESULTS SUMMARY"
echo "========================================"

# Generate results table
if [ -f "$SCRIPT_DIR/scripts/generate_results_table.py" ]; then
    echo "Generating results table..."
    python3 "$SCRIPT_DIR/scripts/generate_results_table.py" "$RESULTS_DIR"
fi

# Generate parameter space figures
if [ -f "$SCRIPT_DIR/scripts/generate_figures.py" ]; then
    echo "Generating parameter space figures..."
    python3 "$SCRIPT_DIR/scripts/generate_figures.py" "$RESULTS_DIR"
fi

echo ""
echo "========================================"
echo "SUMMARY"
echo "========================================"
echo "Total experiments: $total"
echo -e "${GREEN}Successful: $successful${NC}"
if [ $failed -gt 0 ]; then
    echo -e "${YELLOW}Failed/Timeout: $failed${NC}"
fi
echo "Total runtime: ${minutes}m ${seconds}s"
echo ""
echo "Results saved in: $RESULTS_DIR"
echo ""
echo "Partial run with reduced epsilon thresholds:"
echo "  M+ (additive):       c = [0.4, 0.3]"
echo "  M× (multiplicative): c = [0.4, 0.3]"
echo "  M+,× (combined):     c = [0.4, 0.3]"
echo "  cutoff_time_per_smt_query = 20s for all"
echo ""
echo "For full reproducibility, run: ./run_full.sh"
echo "========================================"
