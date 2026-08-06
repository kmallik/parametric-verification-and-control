# ATVA 2026 Artifact: Supermartingale Certificates for Parametric MDPs

This artifact accompanies the paper "Supermartingale Certificates for Parametric MDPs" accepted at ATVA 2026.

## Overview

This artifact contains:
- The parametric MDP verification tool implementation (Python 3.12+)
- All benchmark instances from Section 7 of the paper
- Scripts to reproduce the experimental results (Table 1 and Figure 1)
- Pre-downloaded dependencies for offline Docker build

The tool synthesizes parametric supermartingale certificates for continuous-space parametric Markov Decision Processes (pMDPs) using SMT-based abstraction-refinement.

## Getting Started

## Hardware and Software Requirements

### Requirements for the smoke test and a partial run
- **OS**: Linux (Ubuntu 22.04 or compatible)
- **Memory**: 16 GB RAM (recommended: 32 GB)
- **CPU Cores**: 8 (recommended: 14)
- **Disk Space**: 2 GB
- **Software**: Docker

### Recommended for the full run 
- **Memory**: 128 GB RAM
- **CPU Cores**: 72
- **Everything else**: as above

### Software Dependencies (included in Docker image)
- Python 3.12+
- PolyQEnt >= 0.0.15 (SMT solver interface)
- PySMT >= 0.9.6
- NumPy >= 2.0.0
- Lark >= 1.3.0
- Z3 and MathSAT5 solvers

All dependencies are pre-downloaded and included in the `offline_dependencies/` directory for reproducibility.

### Installation

The artifact includes pre-downloaded dependencies in the `offline_dependencies/` directory for reproducible builds.

**Default Build (Recommended)**

Build using the pre-downloaded offline dependencies (you may need to add "sudo" before docker commands):

```bash
# Pull the base Python image (one-time, requires internet)
docker pull python:3.12-slim-bookworm

# Build the Docker image (uses offline dependencies, no internet needed)
docker build -t atva2026-artifact .

# Run the container interactively (no internet needed)
docker run -it atva2026-artifact bash
```

**Internet requirements:**
- **One-time**: Pull base Docker image (`docker pull python:3.12-slim-bookworm`)
- **During build**: None - Python packages and MathSAT solver are installed from included offline files
- **Once built**: Container requires no internet to run experiments

If the build fails, the offline dependencies may be missing or corrupted. Run `./download_dependencies.sh` to re-download them (requires internet).

**Fallback: Online Build (Troubleshooting)**

If the default build fails or your offline dependencies are corrupted, you can build using internet downloads:

```bash
# Build using internet (downloads all dependencies during build)
docker build -f Dockerfile.online -t atva2026-artifact .

# Run the container interactively
docker run -it atva2026-artifact bash
```

This downloads all dependencies from their original sources during the build. Use this option only if:
- The default build fails
- You need to rebuild without running `./download_dependencies.sh`
- You want to use the latest package versions (less reproducible)

**Note:** The offline dependencies (~60MB) are included in the artifact ZIP file for reproducible builds.

### Smoke Test

To verify the installation, run the smoke test which tests both Z3 and MathSAT solvers:

```bash
# Using the automated script (recommended)
./run_smoke_test.sh
```

The automated script:
- Tests both Z3 and MathSAT solvers separately
- Generates a formatted results table
- Provides a summary of working solvers
- **Expected runtime**: Under 5 minutes

If one solver fails, you can still proceed with experiments using the working solver.

**Expected Output**:
- Initial and refined parameter space partitions
- SAT/UNSAT/INCONCLUSIVE region counts after each refinement round
- Final parameter regions with synthesized control strategies
- Summary statistics including total SMT calls and execution time
- A formatted table showing results for both solvers

**Note**: If you see "ERROR: Solver mathsat is not installed" during the MathSAT test, this is expected if the MathSAT download failed. The smoke test will report which solvers are working. At minimum, Z3 should always be available.

### Partial Run (Ideal for Personal Computers)

For faster validation between the smoke test and the full run:

```bash
./run_partial.sh
```

This runs all 6 benchmarks with reduced epsilon thresholds (amounting to 2/5-th of rows of Table 1):
- **M+ (additive)**: c = [0.4, 0.3]
- **M× (multiplicative)**: c = [0.4, 0.3]
- **M+,× (combined)**: c = [0.4, 0.3]
- All with `cutoff_time_per_smt_query = 20s`

Afterwards, this script will:
- Save results to the `partial_run_results/` directory
- Generate a formatted results table matching Table 1
- Generate parameter space visualization figures
- Display progress and timing for each experiment
- Show a summary at the end

**Expected runtime**: 30-40 minutes

This provides a good balance between validation depth and runtime, allowing you to verify the tool works correctly before committing to the full run.

### Full Run

**IMPORTANT**: The full run was tested on a large server with 128 GB RAM, and it
may crash personal computers with smaller RAM sizes. This is because our algorithm
uses a partition refinement over the parameter space, with the full run
pushing the number of partition elements to an enormous value. Our current
implementation keeps track of the partition elements using a simple dynamic
array. More space-efficient solutions are underway.

To reproduce all results from Table 1 with a single command:

```bash
./run_full.sh
```

This script will:
- Run all benchmark configurations automatically
- Save results to the `full_run_results/` directory
- Generate a formatted results table matching Table 1
- Generate parameter space visualization figures
- Display progress and timing for each experiment
- Show a summary at the end

**Expected runtime**: 6 hours 

## Step-by-Step Instructions

### Running Individual Benchmarks

The benchmarks are located in `examples/stable/`:

| Benchmark | Configuration File |
|-----------|-------------------|
| M+ with Z3 | `LRW_1d_add_z3.json` |
| M+ with MathSAT5 | `LRW_1d_add_mathsat.json` |
| M× with Z3 | `LRW_1d_mul_z3.json` |
| M× with MathSAT5 | `LRW_1d_mul_mathsat.json` |
| M+,× with Z3 | `LRW_1d_add_mul_z3.json` |
| M+,× with MathSAT5 | `LRW_1d_add_mul_mathsat.json` |


To run a specific benchmark:

```bash
python3 src/param_synthesis.py <path-to-json-config>
```

For example:
```bash
python3 src/param_synthesis.py examples/stable/LRW_1d_add_z3.json
python3 src/param_synthesis.py examples/stable/LRW_1d_mul_mathsat.json
```

### Understanding the Output


After running experiments, automated scripts generate:
- **Results table (part of AE)**: Formatted table matching Table 1 from the paper (with mean
  ± std statistics)
- **Log file**: Detailed execution trace which contains:
   - **Parameter regions**: Partitions of the parameter space labeled as SAT (safe), UNSAT (unsafe), or INCONCLUSIVE
   - **Control strategy**: Synthesized controllers for each safe region
   - **Statistics**: SMT solver calls, refinement iterations, and timing information
- **Parameter space figures (part of AE)**: PNG visualizations of
  safe/unsafe/inconclusive regions (Figure 1)

### Viewing Generated Figures

The experiments generate PNG figure files inside the Docker container. To view these figures on your host machine, you need to copy them out of the container.

**Step 1: Find your container ID**

After running experiments, your container is still running (if you used `docker
run -it`). Open a **new terminal** on your host machine and run (you may need `sudo`):

```bash
# List all running containers
docker ps
```

This shows output like:
```
CONTAINER ID   IMAGE              COMMAND   CREATED          STATUS          NAMES
a1b2c3d4e5f6   atva2026-artifact  "bash"    10 minutes ago   Up 10 minutes   eager_newton
```

The **CONTAINER ID** is the first column (e.g., `a1b2c3d4e5f6`). You can use either the full ID or just the first few characters.

If you already exited the container, use `docker ps -a` to see all containers (including stopped ones).

**Step 2: Copy PNG files to your host machine**

```bash
# Copy all PNG files from smoke test results
docker cp a1b2c3d4e5f6:/artifact/smoke_test_results ./smoke_test_results

# Copy all PNG files from partial run
docker cp a1b2c3d4e5f6:/artifact/partial_run_results ./partial_run_results

# Copy all PNG files from full run
docker cp a1b2c3d4e5f6:/artifact/full_run_results ./full_run_results
```

Replace `a1b2c3d4e5f6` with your actual container ID.

**Step 3: View the figures**

The PNG files are now on your host machine. Open them with any image viewer:
- **Linux**: `eog smoke_test_results/*.png` or `xdg-open smoke_test_results/experiment_1_LRW_1d_add_z3_smoke.png`
- **macOS**: `open smoke_test_results/*.png`
- **Windows**: Double-click the PNG files in File Explorer

**Tip**: While still inside the container, you can list generated PNG files:
```bash
ls -la smoke_test_results/*.png
ls -la partial_run_results/*.png
ls -la full_run_results/*.png
```

## File Structure

```
.
├── Dockerfile                      # Docker image specification
├── LICENSE                         # MIT License
├── USER_MANUAL.md                  # Tool documentation (JSON format specification)
├── README.md                       # This file (artifact evaluation instructions)
├── requirements.txt                # Python dependencies
├── download_dependencies.sh        # Script to refresh offline dependencies (optional, requires internet connection)
├── run_smoke_test.sh               # Script for quick installation verification (<5 min)
├── run_partial.sh                  # Partial run (~30-40 min)
├── run_full.sh                     # Full run for Table 1 experiments
├── offline_dependencies/           # Pre-downloaded dependencies (~150MB)
│   ├── python-packages/            # Python wheel files
│   ├── mathsat/                    # MathSAT solver tarball
│   └── deb-packages/               # Debian packages for build-essential, libgmp-dev, vim
├── src/
│   └── param_synthesis.py          # Main synthesis tool
├── scripts/
│   ├── generate_results_table.py   # Generates formatted results table
│   └── generate_figures.py         # Generates parameter space visualizations
├── examples/
│   └── stable/                     # Benchmark instances from Table 1
│       ├── LRW_1d_add_z3.json
│       ├── LRW_1d_add_mathsat.json
│       ├── LRW_1d_mul_z3.json
│       ├── LRW_1d_mul_mathsat.json
│       ├── LRW_1d_add_mul_z3.json
│       ├── LRW_1d_add_mul_mathsat.json
│       ├── LRW_1d_add_z3_smoke.json         # Smoke test configs
│       └── LRW_1d_add_mathsat_smoke.json
├── smoke_test_results/             # Results from run_smoke_test.sh (created at runtime)
├── partial_run_results/            # Results from run_partial.sh (created at runtime)
├── full_run_results/               # Results from run_full.sh (created at runtime)
└── tmp/                            # Temporary SMT files (created at runtime)
```

## Troubleshooting

### Build Fails: Missing Offline Dependencies

**Problem**: Build fails with "No such file or directory" for `offline_dependencies/`

**Solution**: Ensure you extracted the complete artifact ZIP file which includes this directory.

### Build Fails: Corrupted Dependencies

**Problem**: Build fails during MathSAT or Python package installation

**Solutions** (choose one):

1. **Re-download dependencies** (requires internet):
   ```bash
   ./download_dependencies.sh && docker build -t atva2026-artifact .
   ```
   Downloads all dependencies (~85MB): pip installer, Python wheels, MathSAT tarball, Ubuntu packages.

   Requires: Internet access, `pip3`, `apt-get`

2. **Use online build fallback** (requires internet):
   ```bash
   docker build -f Dockerfile.online -t atva2026-artifact .
   ```
   Downloads dependencies directly during build.

3. **Run Z3-only benchmarks** (workaround):
   ```bash
   # Z3 is always available via system package
   python3 src/param_synthesis.py examples/stable/LRW_1d_add_z3.json
   python3 src/param_synthesis.py examples/stable/LRW_1d_mul_z3.json
   python3 src/param_synthesis.py examples/stable/LRW_1d_add_mul_z3.json
   ```
   Or modify MathSAT JSON files: change `"smt_solver": "mathsat"` to `"smt_solver": "z3"`



## Additional Documentation

- **USER_MANUAL.md**: Detailed documentation of the tool, JSON configuration format, and implementation details

