#!/usr/bin/env python3
"""
Generate parameter space partition figures from experiment results
"""

import json
import sys
import os
import re
from pathlib import Path

# Add src directory to path for imports
SCRIPT_DIR = Path(__file__).parent.absolute()
SRC_DIR = SCRIPT_DIR.parent / "src"
sys.path.insert(0, str(SRC_DIR))

PLOTTER_ERROR = None
try:
    import matplotlib
    matplotlib.use('Agg')  # Use non-interactive backend for Docker/headless
    from parameter_space_plotter_1d import plot_parameter_space_1d, parse_logfile_entry as parse_logfile_entry_1d, load_config
    from parameter_space_plotter import plot_parameter_space as plot_parameter_space_2d, parse_logfile_entry as parse_logfile_entry_2d
    HAS_PLOTTER = True
except ImportError as e:
    HAS_PLOTTER = False
    PLOTTER_ERROR = str(e)


def find_config_for_logfile(log_path, search_dirs):
    """Find the config file that corresponds to a log file"""
    log_name = log_path.stem

    # Try to infer config name from log file name
    # Common patterns: results_smoke_test_z3.log -> LRW_1d_add_z3_smoke.json
    #                  LRW_1d_add_z3.log -> LRW_1d_add_z3.json
    #                  results_add_z3.log -> LRW_1d_add_z3.json

    possible_configs = []

    # Direct match: log name + .json
    possible_configs.append(f"{log_name}.json")

    # Smoke test pattern: results_smoke_test_z3 -> LRW_1d_add_z3_smoke
    if 'smoke_test' in log_name:
        if 'z3' in log_name:
            possible_configs.append("LRW_1d_add_z3_smoke.json")
        elif 'mathsat' in log_name:
            possible_configs.append("LRW_1d_add_mathsat_smoke.json")

    # Results pattern: results_add_z3 -> LRW_1d_add_z3
    # results_add_mul_mathsat -> LRW_1d_add_mul_mathsat
    if log_name.startswith('results_') and 'smoke_test' not in log_name:
        # Remove 'results_' prefix and add 'LRW_1d_' prefix
        base_name = log_name.replace('results_', '', 1)
        possible_configs.append(f"LRW_1d_{base_name}.json")

    # Partial run pattern: LRW_1d_add_z3_partial -> LRW_1d_add_z3
    if '_partial' in log_name:
        base_name = log_name.replace('_partial', '')
        possible_configs.append(f"{base_name}.json")

    # Search for configs in search directories
    for search_dir in search_dirs:
        for config_name in possible_configs:
            config_path = search_dir / config_name
            if config_path.exists():
                return config_path

    return None


def get_experiment_numbers_from_log(logfile_path):
    """Extract all experiment numbers from a log file"""
    try:
        with open(logfile_path, 'r') as f:
            content = f.read()

        matches = re.findall(r'EXPERIMENT #(\d+)', content)
        return [int(m) for m in matches] if matches else []
    except Exception:
        return []


def generate_png_figure(config_path, experiment_num, output_dir, actual_log_path=None):
    """Generate a PNG figure using the plotter module

    Args:
        config_path: Path to config file (used for system parameters)
        experiment_num: Which experiment entry to plot
        output_dir: Where to save the PNG
        actual_log_path: The actual log file to parse (if None, uses config's logfile)
    """
    if not HAS_PLOTTER:
        print(f"  Skipping PNG generation (plotter module not available)")
        return False

    try:
        # Load configuration
        config = load_config(str(config_path))

        # Check parameter space dimensions
        param_vars = config['system'].get('param_vars', [])
        num_params = len(param_vars)

        if num_params not in (1, 2):
            print(f"  Skipping: unsupported parameter space ({num_params} params, only 1D and 2D supported)")
            return False

        # Use actual log file if provided, otherwise fall back to config's logfile
        logfile = str(actual_log_path) if actual_log_path else config.get('logfile')
        if not logfile:
            print(f"  Skipping: no logfile available")
            return False

        # Generate output filename
        config_name = os.path.splitext(os.path.basename(config_path))[0]
        output_path = output_dir / f"experiment_{experiment_num}_{config_name}.png"

        # Parse the logfile entry and create the plot based on dimensions
        if num_params == 1:
            log_entry = parse_logfile_entry_1d(logfile, experiment_num)
            plot_parameter_space_1d(config, log_entry, experiment_num, str(output_path))
        else:  # num_params == 2
            log_entry = parse_logfile_entry_2d(logfile, experiment_num)
            plot_parameter_space_2d(config, log_entry, experiment_num, str(output_path))

        print(f"  Generated: {output_path}")
        return True

    except Exception as e:
        print(f"  Error generating figure: {e}")
        return False


def generate_ascii_figure_1d(safe_regions, unsafe_regions, param_bounds, title="Parameter Space"):
    """Generate ASCII visualization for 1D parameter space"""
    width = 60
    lower, upper = param_bounds
    range_size = upper - lower

    # Create visualization string
    viz = [' '] * width

    # Mark safe regions (green in concept, 'S' in ASCII)
    for region in safe_regions:
        r_lower, r_upper = region
        start_idx = int(((r_lower - lower) / range_size) * (width - 1))
        end_idx = int(((r_upper - lower) / range_size) * (width - 1))
        for i in range(max(0, start_idx), min(width, end_idx + 1)):
            viz[i] = 'S'

    # Mark unsafe regions (red in concept, 'U' in ASCII)
    for region in unsafe_regions:
        r_lower, r_upper = region
        start_idx = int(((r_lower - lower) / range_size) * (width - 1))
        end_idx = int(((r_upper - lower) / range_size) * (width - 1))
        for i in range(max(0, start_idx), min(width, end_idx + 1)):
            if viz[i] == ' ':
                viz[i] = 'U'

    # Mark inconclusive regions (gray in concept, '?' in ASCII)
    for i in range(width):
        if viz[i] == ' ':
            viz[i] = '?'

    print(f"\n{title}")
    print("-" * width)
    print(f"|{''.join(viz)}|")
    print("-" * width)
    print(f"{lower:<{width//2}}{upper:>{width//2}}")
    print()
    print("Legend: S = Safe (angelic), U = Unsafe (demonic), ? = Inconclusive")
    print()


def generate_figures(results_dir):
    """Generate all figures from results directory"""
    results_dir = Path(results_dir)

    if not results_dir.exists():
        print(f"Results directory {results_dir} does not exist")
        return

    # Find log files
    log_files = list(results_dir.glob("*.log"))

    # Also check for log files in parent directory (for smoke tests)
    if results_dir.parent.exists():
        log_files.extend(results_dir.parent.glob("*.log"))

    # Remove duplicates
    log_files = list(set(log_files))

    if not log_files:
        print(f"No log files found in {results_dir}")
        return

    print("\n" + "="*80)
    print("GENERATING PARAMETER SPACE FIGURES")
    print("="*80)

    if not HAS_PLOTTER:
        print(f"\nWARNING: PNG generation not available - {PLOTTER_ERROR}")
        print("To enable PNG generation, install matplotlib: pip install matplotlib")
        print("Continuing with log file processing...\n")

    # Directories to search for config files
    search_dirs = [
        results_dir,
        results_dir.parent,
        results_dir.parent / "examples" / "stable",
        Path("examples/stable"),
        Path(".")
    ]

    figures_generated = 0

    for log_file in sorted(log_files):
        print(f"\nProcessing: {log_file.name}")

        # Find corresponding config file
        config_path = find_config_for_logfile(log_file, search_dirs)

        if not config_path:
            print(f"  Could not find config file for {log_file.name}")
            continue

        print(f"  Config: {config_path}")

        # Get experiment numbers from log
        exp_nums = get_experiment_numbers_from_log(log_file)

        if not exp_nums:
            print(f"  No experiments found in log file")
            continue

        # Generate figure for the latest experiment
        latest_exp = max(exp_nums)
        print(f"  Generating figure for experiment #{latest_exp}")

        if generate_png_figure(config_path, latest_exp, results_dir, actual_log_path=log_file):
            figures_generated += 1

    print("\n" + "="*80)
    print(f"SUMMARY: Generated {figures_generated} PNG figure(s)")
    print("="*80)
    print()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        results_dir = sys.argv[1]
    else:
        # Default to current directory or typical results directory
        results_dir = "./artifact_results" if os.path.exists("./artifact_results") else "."

    generate_figures(results_dir)
