#!/usr/bin/env python3
"""
Generate results table from experiment log files matching Table 1 format from the paper
"""

import sys
import os
import re
import numpy as np
from pathlib import Path
from collections import defaultdict

def extract_solver(log_path):
    """Extract solver type from log filename."""
    log_str = str(log_path).lower()
    if 'mathsat' in log_str:
        return 'mathsat'
    elif 'z3' in log_str:
        return 'z3'
    return 'unknown'

def extract_benchmark_name(log_filename):
    """Extract benchmark name from log filename."""
    name = log_filename.replace('.log', '')

    if 'smoke_test' in name:
        return 'smoke_test'

    name = re.sub(r'^results_', '', name)
    name = re.sub(r'^LRW_1d_', '', name)
    name = re.sub(r'_partial$', '', name)
    name = re.sub(r'_(z3|mathsat)$', '', name)

    name_map = {
        'add': 'M+ (additive)',
        'mul': 'M× (multiplicative)',
        'add_mul': 'M+,× (combined)'
    }

    return name_map.get(name, name) if name else 'unknown'

def compute_statistics(times):
    """Compute mean and standard deviation."""
    if not times:
        return None, None
    mean = np.mean(times)
    std = 0.0 if len(times) == 1 else np.std(times, ddof=1)
    return mean, std

def parse_experiment_block(block):
    """Parse a single EXPERIMENT block and extract all data."""
    result = {}

    # Extract total runtime
    runtime_match = re.search(r'Total runtime:\s*([0-9.]+)\s*seconds', block)
    result['total_time'] = float(runtime_match.group(1)) if runtime_match else None

    # Extract overall timeout threshold from log
    timeout_match = re.search(r'Overall timeout:\s*([0-9.]+)\s*seconds', block)
    timeout_threshold = float(timeout_match.group(1)) if timeout_match else None

    # Extract epsilon
    eps_match = re.search(r'Epsilon \(max_inconclusive\):\s*([0-9.]+)', block)
    result['epsilon'] = float(eps_match.group(1)) if eps_match else None

    # Check for timeout: explicit OVERALL TIMEOUT or runtime exceeds threshold from config
    is_explicit_timeout = 'OVERALL TIMEOUT' in block
    is_runtime_timeout = (timeout_threshold is not None and
                          result['total_time'] is not None and
                          result['total_time'] >= timeout_threshold)
    result['is_timeout'] = is_explicit_timeout or is_runtime_timeout

    # Extract angelic computation times
    angelic_times = []
    angelic_match = re.search(r'--- ANGELIC WINNING REGIONS ---(.+?)--- DEMONIC WINNING REGIONS ---',
                              block, re.DOTALL)
    if angelic_match:
        angelic_section = angelic_match.group(1)
        angelic_comp_times = re.findall(r'Computation time \(s\):\s+([\d.]+)', angelic_section)
        angelic_times = [float(t) for t in angelic_comp_times]

    # Extract demonic computation times
    demonic_times = []
    demonic_match = re.search(r'--- DEMONIC WINNING REGIONS ---(.+?)--- INCONCLUSIVE REGIONS',
                              block, re.DOTALL)
    if demonic_match:
        demonic_section = demonic_match.group(1)
        demonic_comp_times = re.findall(r'Computation time \(s\):\s+([\d.]+)', demonic_section)
        demonic_times = [float(t) for t in demonic_comp_times]

    result['angelic_times'] = angelic_times
    result['demonic_times'] = demonic_times

    return result

def parse_log_file(log_path):
    """Parse a log file and extract experiment data."""
    results = []
    solver = extract_solver(log_path)

    try:
        with open(log_path, 'r') as f:
            content = f.read()

        # Extract epsilon thresholds from config section (for filling in missing epsilons)
        eps_thresholds = []
        eps_match = re.search(r'Max inconclusive fractions:\s*\[([^\]]+)\]', content)
        if eps_match:
            eps_thresholds = sorted([float(x.strip()) for x in eps_match.group(1).split(',')], reverse=True)

        # Split by EXPERIMENT headers
        parts = re.split(r'(={80,}\nEXPERIMENT #\d+\n={80,})', content)

        # Find experiment blocks
        exp_index = 0
        for i, part in enumerate(parts):
            if 'EXPERIMENT #' in part and i + 1 < len(parts):
                block = parts[i + 1]
                result = parse_experiment_block(block)
                result['solver'] = solver

                # If epsilon is missing, try to infer from thresholds
                # The snapshots come in order of epsilon thresholds, final has min epsilon
                if result.get('epsilon') is None and eps_thresholds:
                    if exp_index < len(eps_thresholds):
                        result['epsilon'] = eps_thresholds[exp_index]
                    else:
                        result['epsilon'] = min(eps_thresholds)

                if result.get('epsilon') is not None:
                    results.append(result)
                exp_index += 1

        # If no EXPERIMENT entries found, try to parse from stdout format
        if not results:
            # Find FINAL SUMMARY
            final_runtime_match = re.search(
                r'FINAL SUMMARY.*?Total runtime:\s*([0-9.]+)\s*seconds',
                content, re.DOTALL
            )

            if final_runtime_match and eps_thresholds:
                total_time = float(final_runtime_match.group(1))
                epsilon = min(eps_thresholds)  # Use the final (minimum) epsilon

                results.append({
                    'total_time': total_time,
                    'epsilon': epsilon,
                    'solver': solver,
                    'is_timeout': 'OVERALL TIMEOUT' in content,
                    'angelic_times': [],
                    'demonic_times': [],
                })

        return results

    except Exception as e:
        print(f"Error parsing {log_path}: {e}", file=sys.stderr)
        return []

def generate_table(results_dir):
    """Generate results table from log files in results directory"""
    results_dir = Path(results_dir)

    if not results_dir.exists():
        print(f"Results directory {results_dir} does not exist")
        return

    log_files = list(results_dir.glob("*.log"))

    if not log_files:
        print(f"No log files found in {results_dir}")
        return

    # Parse all log files and group by (benchmark, epsilon)
    # Structure: {(benchmark, epsilon): {'z3': result, 'mathsat': result}}
    experiments = defaultdict(lambda: {'z3': None, 'mathsat': None})

    for log_file in log_files:
        benchmark_name = extract_benchmark_name(log_file.name)
        entries = parse_log_file(log_file)

        for entry in entries:
            if entry:
                epsilon = entry.get('epsilon')
                solver = entry.get('solver', 'unknown')

                if epsilon is not None and solver in ['z3', 'mathsat']:
                    eps_key = f"{epsilon:.2f}"
                    key = (benchmark_name, eps_key)

                    # Keep the entry with the most data (prefer entries with computation times)
                    existing = experiments[key][solver]
                    if existing is None:
                        experiments[key][solver] = entry
                    elif len(entry.get('angelic_times', [])) > len(existing.get('angelic_times', [])):
                        experiments[key][solver] = entry

    if not experiments:
        print("No valid results found in log files")
        return

    # Print table
    print("\n" + "="*120)
    print("EXPERIMENTAL RESULTS TABLE")
    print("="*120)
    print()

    # Table header
    header = f"{'Benchmark':<20} {'ε':<6} {'Z3 (s)':<10} {'MS (s)':<10} {'Z3 Ang':<14} {'MS Ang':<14} {'Z3 Dem':<14} {'MS Dem':<14}"
    print(header)
    print("-"*120)

    # Sort by benchmark name, then by epsilon (descending)
    def sort_key(item):
        benchmark, eps = item[0]
        try:
            eps_val = float(eps)
        except:
            eps_val = 0.0
        return (benchmark, -eps_val)

    for (benchmark, epsilon), data in sorted(experiments.items(), key=sort_key):
        # Z3 data
        z3 = data['z3']
        if z3 and z3.get('is_timeout'):
            z3_total = "TO"
            z3_ang = "-"
            z3_dem = "-"
        elif z3 and z3.get('total_time') is not None:
            z3_total = f"{z3['total_time']:.2f}"
            # Angelic stats
            ang_times = z3.get('angelic_times', [])
            if ang_times:
                mean, std = compute_statistics(ang_times)
                z3_ang = f"{mean:.2f}±{std:.2f}"
            else:
                z3_ang = "-"
            # Demonic stats
            dem_times = z3.get('demonic_times', [])
            if dem_times:
                mean, std = compute_statistics(dem_times)
                z3_dem = f"{mean:.2f}±{std:.2f}"
            else:
                z3_dem = "-"
        else:
            z3_total = "-"
            z3_ang = "-"
            z3_dem = "-"

        # MathSAT data
        ms = data['mathsat']
        if ms and ms.get('is_timeout'):
            ms_total = "TO"
            ms_ang = "-"
            ms_dem = "-"
        elif ms and ms.get('total_time') is not None:
            ms_total = f"{ms['total_time']:.2f}"
            # Angelic stats
            ang_times = ms.get('angelic_times', [])
            if ang_times:
                mean, std = compute_statistics(ang_times)
                ms_ang = f"{mean:.2f}±{std:.2f}"
            else:
                ms_ang = "-"
            # Demonic stats
            dem_times = ms.get('demonic_times', [])
            if dem_times:
                mean, std = compute_statistics(dem_times)
                ms_dem = f"{mean:.2f}±{std:.2f}"
            else:
                ms_dem = "-"
        else:
            ms_total = "-"
            ms_ang = "-"
            ms_dem = "-"

        print(f"{benchmark:<20} {epsilon:<6} {z3_total:<10} {ms_total:<10} {z3_ang:<14} {ms_ang:<14} {z3_dem:<14} {ms_dem:<14}")

    print("-"*120)
    print()
    print("Legend:")
    print("  ε = Epsilon (max_inconclusive threshold)")
    print("  Z3/MS (s) = Total runtime in seconds")
    print("  Ang/Dem = Angelic/Demonic computation times: mean±std")
    print("  TO = Timeout, - = Data not available")
    print()
    print("="*120)
    print()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        results_dir = sys.argv[1]
    else:
        results_dir = "./partial_run_results" if os.path.exists("./partial_run_results") else "."

    generate_table(results_dir)
