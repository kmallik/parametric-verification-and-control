#!/usr/bin/env python3
"""
1D Parameter Space Plotter

Plots the results of parameter synthesis experiments from the logfile.
Visualizes angelic winning regions (green) and demonic winning regions (red)
on a 1D parameter space represented as a horizontal rectangle.

Usage:
    python parameter_space_plotter_1d.py <config.json> <experiment_number> [x_label]
"""

import json
import sys
import os
import re
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from typing import Dict, List, Tuple, Any


def load_config(filepath: str) -> Dict[str, Any]:
    """Load configuration from JSON file."""
    with open(filepath, 'r') as f:
        return json.load(f)


def parse_logfile_entry(logfile: str, experiment_num: int) -> Dict[str, Any]:
    """Parse the logfile and extract the entry for the given experiment number.

    Returns a dictionary with:
        - input_file: str
        - angelic_regions: List of param_bounds
        - demonic_regions: List of param_bounds
        - inconclusive_regions: List of param_bounds

    Only looks for new format entries ("EXPERIMENT #N"). Old format entries are ignored.
    """
    if not os.path.exists(logfile):
        raise FileNotFoundError(f"Logfile not found: {logfile}")

    with open(logfile, 'r') as f:
        content = f.read()

    # Find the specific experiment with "EXPERIMENT #N" format
    # Use flexible matching for the separator line (any number of = signs)
    pattern = rf'=+\nEXPERIMENT #{experiment_num}\n=+\n(.*?)(?=\n=+\nEXPERIMENT #|\Z)'
    match = re.search(pattern, content, re.DOTALL)

    if not match:
        # List available experiment numbers to help the user
        available = re.findall(r'EXPERIMENT #(\d+)', content)
        if available:
            available_nums = sorted(set(int(n) for n in available))
            raise ValueError(
                f"Experiment #{experiment_num} not found in logfile: {logfile}\n"
                f"Available experiment numbers: {available_nums}"
            )
        else:
            raise ValueError(
                f"Experiment #{experiment_num} not found in logfile: {logfile}\n"
                f"No numbered experiments found. The logfile may only contain old format entries."
            )

    entry_content = match.group(1)

    # Parse the entry
    result = {
        'input_file': None,
        'angelic_regions': [],
        'demonic_regions': [],
        'inconclusive_regions': []
    }

    # Extract input file
    input_file_match = re.search(r'Input file:\s*(.+)', entry_content)
    if input_file_match:
        result['input_file'] = input_file_match.group(1).strip()

    # Extract angelic regions
    angelic_section = re.search(
        r'--- ANGELIC WINNING REGIONS ---.*?Total:\s*(\d+)(.*?)(?=--- DEMONIC|$)',
        entry_content, re.DOTALL
    )
    if angelic_section:
        angelic_content = angelic_section.group(2)
        # Parse parameter bounds from each region
        bounds_matches = re.findall(r'Parameter bounds:\s*(\[.*?\])\s*\n', angelic_content)
        for bounds_str in bounds_matches:
            try:
                bounds = eval(bounds_str)  # Parse the list
                result['angelic_regions'].append(bounds)
            except:
                pass

    # Extract demonic regions
    demonic_section = re.search(
        r'--- DEMONIC WINNING REGIONS ---.*?Total:\s*(\d+)(.*?)(?=--- INCONCLUSIVE|$)',
        entry_content, re.DOTALL
    )
    if demonic_section:
        demonic_content = demonic_section.group(2)
        bounds_matches = re.findall(r'Parameter bounds:\s*(\[.*?\])\s*\n', demonic_content)
        for bounds_str in bounds_matches:
            try:
                bounds = eval(bounds_str)
                result['demonic_regions'].append(bounds)
            except:
                pass

    # Extract inconclusive regions
    inconclusive_section = re.search(
        r'--- INCONCLUSIVE REGIONS.*?Total:\s*(\d+)(.*?)(?=={80}|$)',
        entry_content, re.DOTALL
    )
    if inconclusive_section:
        inconclusive_content = inconclusive_section.group(2)
        # Inconclusive regions have a different format: "Region N: [bounds]"
        bounds_matches = re.findall(r'Region \d+:\s*(\[.*?\])', inconclusive_content)
        for bounds_str in bounds_matches:
            try:
                bounds = eval(bounds_str)
                result['inconclusive_regions'].append(bounds)
            except:
                pass

    return result


def format_latex_label(label: str) -> str:
    """Format label as LaTeX math expression if not already formatted."""
    if not label:
        return label
    # If already wrapped in $, return as is
    if label.startswith('$') and label.endswith('$'):
        return label
    # Otherwise, wrap in $ for LaTeX math mode
    return f'${label}$'


def validate_region_bounds(region_bounds: List, param_bounds: List, region_type: str) -> None:
    """Validate that region bounds are within the parameter bounds."""
    if len(region_bounds) != len(param_bounds):
        raise ValueError(
            f"{region_type} region has {len(region_bounds)} dimensions, "
            f"but parameter space has {len(param_bounds)} dimensions"
        )

    for i, (region, param) in enumerate(zip(region_bounds, param_bounds)):
        region_lo, region_hi = region
        param_lo, param_hi = param

        if region_lo < param_lo - 1e-9 or region_hi > param_hi + 1e-9:
            raise ValueError(
                f"{region_type} region bounds {region} in dimension {i} "
                f"exceed parameter bounds {param}"
            )


def plot_parameter_space_1d(config: Dict[str, Any], log_entry: Dict[str, Any],
                            experiment_num: int, output_path: str, x_label: str = None) -> None:
    """Create and save the 1D parameter space plot as a thin horizontal rectangle."""
    param_bounds = config['system']['param_bounds']
    param_vars = config['system']['param_vars']

    # Create figure and axis
    fig, ax = plt.subplots(1, 1, figsize=(12, 2))

    # Set axis limits based on parameter bounds
    x_bounds = param_bounds[0]

    # Rectangle height and y-position (centered at y=0.5)
    rect_height = 0.5
    rect_y = 0.25

    ax.set_xlim(x_bounds[0], x_bounds[1])
    ax.set_ylim(0, 1)

    # Plot angelic regions (green)
    for region in log_entry['angelic_regions']:
        validate_region_bounds(region, param_bounds, "Angelic")

        x_lo, x_hi = region[0]

        rect = patches.Rectangle(
            (x_lo, rect_y),
            x_hi - x_lo,
            rect_height,
            linewidth=1,
            edgecolor='darkgreen',
            facecolor='green',
            alpha=0.5,
            label='Angelic' if region == log_entry['angelic_regions'][0] else None
        )
        ax.add_patch(rect)

    # Plot demonic regions (red)
    for region in log_entry['demonic_regions']:
        validate_region_bounds(region, param_bounds, "Demonic")

        x_lo, x_hi = region[0]

        rect = patches.Rectangle(
            (x_lo, rect_y),
            x_hi - x_lo,
            rect_height,
            linewidth=1,
            edgecolor='darkred',
            facecolor='red',
            alpha=0.5,
            label='Demonic' if region == log_entry['demonic_regions'][0] else None
        )
        ax.add_patch(rect)

    # Plot inconclusive regions (gray)
    for region in log_entry['inconclusive_regions']:
        validate_region_bounds(region, param_bounds, "Inconclusive")

        x_lo, x_hi = region[0]

        rect = patches.Rectangle(
            (x_lo, rect_y),
            x_hi - x_lo,
            rect_height,
            linewidth=1,
            edgecolor='gray',
            facecolor='gray',
            alpha=0.3,
            label='Inconclusive' if region == log_entry['inconclusive_regions'][0] else None
        )
        ax.add_patch(rect)

    # Set labels
    xlabel = format_latex_label(x_label if x_label else param_vars[0])
    ax.set_xlabel(xlabel, fontsize=12)
    ax.set_yticks([])  # Remove y-axis ticks for cleaner look
    # ax.set_title(f'Parameter Space - Experiment #{experiment_num}', fontsize=14)

    # # Add legend
    # ax.legend(loc='upper right')

    # Add grid on x-axis only
    ax.grid(True, axis='x', linestyle='--', alpha=0.3)

    # Save the plot
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()

    print(f"Plot saved to: {output_path}")


def main():
    """Main entry point."""
    if len(sys.argv) < 3 or len(sys.argv) > 4:
        print("Usage: python parameter_space_plotter_1d.py <config.json> <experiment_number> [x_label]")
        sys.exit(1)

    config_file = sys.argv[1]
    try:
        experiment_num = int(sys.argv[2])
    except ValueError:
        print(f"Error: Experiment number must be an integer, got: {sys.argv[2]}")
        sys.exit(1)

    # Optional x-axis label
    x_label = sys.argv[3] if len(sys.argv) == 4 else None

    # Load configuration
    print(f"Loading configuration from: {config_file}")
    config = load_config(config_file)

    # Check if parameter space is 1D
    param_vars = config['system'].get('param_vars', [])
    if len(param_vars) != 1:
        raise ValueError(
            f"This plotter is only for 1D parameter spaces. "
            f"Found {len(param_vars)} parameter(s): {param_vars}"
        )

    param_bounds = config['system'].get('param_bounds', [])
    if len(param_bounds) != 1:
        raise ValueError(
            f"Parameter bounds must have 1 dimension for 1D plotting. "
            f"Found {len(param_bounds)} dimension(s)."
        )

    # Get logfile path
    logfile = config.get('logfile')
    if not logfile:
        raise ValueError("No 'logfile' specified in the configuration file.")

    print(f"Reading logfile: {logfile}")

    # Parse the logfile entry
    log_entry = parse_logfile_entry(logfile, experiment_num)

    # # Verify input file matches
    # logged_input_file = log_entry['input_file']
    # # Compare basenames to handle relative vs absolute paths
    # config_basename = os.path.basename(config_file)
    # logged_basename = os.path.basename(logged_input_file) if logged_input_file else None

    # if logged_basename != config_basename:
    #     raise ValueError(
    #         f"Input file mismatch!\n"
    #         f"  Provided config: {config_file} (basename: {config_basename})\n"
    #         f"  Logged input:    {logged_input_file} (basename: {logged_basename})\n"
    #         f"The experiment #{experiment_num} was run with a different input file."
    #     )

    print(f"Found experiment #{experiment_num}")
    print(f"  Angelic regions: {len(log_entry['angelic_regions'])}")
    print(f"  Demonic regions: {len(log_entry['demonic_regions'])}")
    print(f"  Inconclusive regions: {len(log_entry['inconclusive_regions'])}")

    # Generate output filename
    config_name = os.path.splitext(os.path.basename(config_file))[0]
    output_path = f"./experiment_{experiment_num}_{config_name}.png"

    # Create the plot
    plot_parameter_space_1d(config, log_entry, experiment_num, output_path, x_label)


if __name__ == "__main__":
    main()
