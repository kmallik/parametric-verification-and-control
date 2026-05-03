#!/usr/bin/env python3
"""
Statistics calculator for experiment log files.
Computes mean and standard deviation of computation times for ANGELIC and DEMONIC winning regions.
"""

import re
import sys
import numpy as np
from typing import List, Tuple


def parse_log_file(logfile_path: str, experiment_numbers: List[int]) -> Tuple[List[float], List[float]]:
    """
    Parse the log file and extract computation times for ANGELIC and DEMONIC winning regions.

    Args:
        logfile_path: Path to the log file
        experiment_numbers: List of experiment numbers to analyze

    Returns:
        Tuple of (angelic_times, demonic_times) - lists of computation times
    """
    angelic_times = []
    demonic_times = []

    with open(logfile_path, 'r') as f:
        content = f.read()

    # Split by experiment separators
    experiments = re.split(r'={80,}\nEXPERIMENT #(\d+)\n={80,}', content)

    # The split creates a list like: ['', '1', '<exp1 content>', '2', '<exp2 content>', ...]
    # So we iterate in pairs: (experiment_number, experiment_content)
    for i in range(1, len(experiments), 2):
        exp_num = int(experiments[i])
        exp_content = experiments[i + 1]

        if exp_num in experiment_numbers:
            # Extract ANGELIC WINNING REGIONS section
            angelic_match = re.search(r'--- ANGELIC WINNING REGIONS ---(.+?)--- DEMONIC WINNING REGIONS ---',
                                     exp_content, re.DOTALL)
            if angelic_match:
                angelic_section = angelic_match.group(1)
                # Find all computation times in angelic section
                angelic_comp_times = re.findall(r'Computation time \(s\):\s+([\d.]+)', angelic_section)
                angelic_times.extend([float(t) for t in angelic_comp_times])

            # Extract DEMONIC WINNING REGIONS section
            demonic_match = re.search(r'--- DEMONIC WINNING REGIONS ---(.+?)--- INCONCLUSIVE REGIONS',
                                     exp_content, re.DOTALL)
            if demonic_match:
                demonic_section = demonic_match.group(1)
                # Find all computation times in demonic section
                demonic_comp_times = re.findall(r'Computation time \(s\):\s+([\d.]+)', demonic_section)
                demonic_times.extend([float(t) for t in demonic_comp_times])

    return angelic_times, demonic_times


def compute_statistics(runtimes: List[float]) -> tuple:
    """
    Compute mean and standard deviation of runtimes.

    Args:
        runtimes: List of runtime values

    Returns:
        Tuple of (mean, std_deviation)
    """
    if not runtimes:
        raise ValueError("No runtime data available")

    mean = np.mean(runtimes)

    # For a single data point, standard deviation is 0
    if len(runtimes) == 1:
        std_dev = 0.0
    else:
        std_dev = np.std(runtimes, ddof=1)  # Sample standard deviation (ddof=1)

    return mean, std_dev


def main():
    if len(sys.argv) < 3:
        print("Usage: python logfile_stats.py <logfile_path> <exp_num1> [<exp_num2> ...]", file=sys.stderr)
        print("Example: python logfile_stats.py final_results/results_add_mathsat.log 1 2 3", file=sys.stderr)
        sys.exit(1)

    logfile_path = sys.argv[1]
    experiment_numbers = [int(x) for x in sys.argv[2:]]

    # Parse log file and extract computation times for angelic and demonic regions
    angelic_times, demonic_times = parse_log_file(logfile_path, experiment_numbers)

    if not angelic_times and not demonic_times:
        print("Error: No computation time data found for the specified experiments", file=sys.stderr)
        sys.exit(1)

    # Compute and output statistics for angelic winning regions
    if angelic_times:
        angelic_mean, angelic_std = compute_statistics(angelic_times)
        print(f"angelic: $ {angelic_mean:.2f} \\pm {angelic_std:.2f} $")
    else:
        print("angelic: No data")

    # Compute and output statistics for demonic winning regions
    if demonic_times:
        demonic_mean, demonic_std = compute_statistics(demonic_times)
        print(f"demonic: $ {demonic_mean:.2f} \\pm {demonic_std:.2f} $")
    else:
        print("demonic: No data")


if __name__ == "__main__":
    main()
