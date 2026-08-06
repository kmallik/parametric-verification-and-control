#!/bin/bash

# ATVA 2026 Artifact: Full run
# This script runs all 6 benchmark configurations with full epsilon thresholds

set -e  # Exit on error

# Color codes for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Determine script directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXAMPLES="$SCRIPT_DIR/examples/stable"
RESULTS_DIR="$SCRIPT_DIR/full_run_results"

# Create results directory
echo -e "${BLUE}Creating results directory: $RESULTS_DIR${NC}"
mkdir -p "$RESULTS_DIR"

# Function to create modified config with cutoff_time_per_smt_query = 15
create_modified_config() {
    local base_config=$1
    local output_config=$2

    python3 -c "
import json

with open('$base_config', 'r') as f:
    config = json.load(f)

# Update cutoff_time_per_smt_query to 15 seconds
config['cutoff_time_per_smt_query'] = 15

with open('$output_config', 'w') as f:
    json.dump(config, f, indent=4)
"
}

# Print header
echo "========================================"
echo "ATVA 2026 Artifact Evaluation"
echo "Supermartingale Certificates for Parametric MDPs"
echo "========================================"
echo ""
echo "Running all experiments from Table 1 (Section 7)"
echo "This will run 6 benchmarks with a 15-minute timeout each"
echo "cutoff_time_per_smt_query = 15s for all experiments"
echo ""

# Array of experiments: name, config file
declare -a experiments=(
    "M+ with Z3:LRW_1d_add_z3.json"
    "M+ with MathSAT5:LRW_1d_add_mathsat.json"
    "M× with Z3:LRW_1d_mul_z3.json"
    "M× with MathSAT5:LRW_1d_mul_mathsat.json"
    "M+,× with Z3:LRW_1d_add_mul_z3.json"
    "M+,× with MathSAT5:LRW_1d_add_mul_mathsat.json"
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

    # Parse experiment name and config file
    name="${exp%%:*}"
    config="${exp##*:}"
    config_path="$EXAMPLES/$config"

    # Create modified config with cutoff_time_per_smt_query = 15
    modified_config="$RESULTS_DIR/modified_${config}"
    create_modified_config "$config_path" "$modified_config"

    # Get the logfile path from the config
    log_file=$(python3 -c "import json; print(json.load(open('$config_path')).get('logfile', 'N/A'))")

    echo "========================================"
    echo -e "${BLUE}[$current/$total] Running: $name${NC}"
    echo "Config: $config"
    echo "Log: $log_file"
    echo "Started: $(date)"
    echo ""

    # Run the experiment
    # Note: Don't use tee - write_to_logfile handles structured output with EXPERIMENT headers
    # The config's logfile setting determines where results are saved
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

# End time and summary
end_time=$(date +%s)
elapsed=$((end_time - start_time))
minutes=$((elapsed / 60))
seconds=$((elapsed % 60))

echo "========================================"
echo "GENERATING RESULTS SUMMARY"
echo "========================================"

# Generate results table and figures
if [ -f "$SCRIPT_DIR/scripts/generate_results_table.py" ]; then
    echo "Generating results table..."
    python3 "$SCRIPT_DIR/scripts/generate_results_table.py" "$RESULTS_DIR"
fi

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
echo "To view individual results:"
echo "  ls -lh $RESULTS_DIR"
echo ""
echo "To view a specific log:"
echo "  cat $RESULTS_DIR/<benchmark>.log"
echo "========================================"
