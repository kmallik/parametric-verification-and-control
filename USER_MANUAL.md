# Parametric Verification and Control

A tool for synthesizing controllers and verifying stochastic systems with parametric uncertainty. The tool finds **angelic winning regions** (parameter values for which a controller exists satisfying the specification) and **demonic winning regions** (parameter values for which no controller can satisfy the specification).

## Installation

See [README.md](README.md) for Docker-based installation (recommended).

For local installation:

```bash
# Install dependencies
pip install -r requirements.txt
```

Requires:
- Python 3.12+
- Z3 SMT solver
- MathSAT5 solver (optional, for MathSAT benchmarks)
- PolyQEnt (polynomial quantifier elimination)
- PySMT (SMT solver interface)
- NumPy, Lark

## Usage

```bash
# Run a benchmark
python3 src/param_synthesis.py examples/stable/LRW_1d_add_z3.json

# Run smoke test
./run_smoke_test.sh

# Run full experiments
./run_full.sh
```

## Input Configuration Format

The input is a JSON file specifying the system, specification, and solver options.

### Complete Example

```json
{
  "system": {
    "type": "cartesian",
    "state_vars": ["S1", "S2"],
    "param_vars": ["P1"],
    "noise_vars": ["W1", "W2"],
    "control_vars": ["U1"],
    "state_bounds": [[-2, 150], [0, 100]],
    "param_bounds": [[0.0, 3.0]],
    "control_bounds": [[-1, 1]],
    "initial_region": {
      "bounds": [[2, 3], [0, 5]]
    },
    "dynamics": [
      {
        "condition": "0 <= S1 <= 100 and 0 <= S2 <= 50",
        "transforms": {
          "S1": "(+ S1 (+ U1 (+ W1 P1)))",
          "S2": "(+ S2 W2)"
        }
      },
      {
        "condition": "S1 >= 100",
        "transforms": {
          "S1": "(+ S1 0)",
          "S2": "(+ S2 0)"
        }
      }
    ],
    "noise_distribution": {
      "type": "uniform",
      "params": {"lower": [-1, -0.5], "upper": [1, 0.5]}
    }
  },
  "target_region": {
    "bounds": [[90, 150], [40, 100]]
  },
  "enable_param_refinement": true,
  "param_refinement_threshold": 0.1,
  "cutoff_time_per_smt_query": 10,
  "parallel_refinement": true,
  "target_probability": 0.95,
  "degree": 1,
  "smt_solver": "z3",
  "entailment_solver": "farkas",
  "output_smt_path": "./tmp/temporary_polyhorn_input.smt2"
}
```

### System Configuration

#### Variables

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Must be `"cartesian"` |
| `state_vars` | array of strings | State variable names (e.g., `["S1", "S2"]`) |
| `param_vars` | array of strings | Parameter variable names (e.g., `["P1", "P2"]`) |
| `noise_vars` | array of strings | Noise/disturbance variable names (e.g., `["W1", "W2"]`) |
| `control_vars` | array of strings | Control input variable names (e.g., `["U1"]`) |

#### Bounds Format

All bounds are specified as **arrays of `[lower, upper]` pairs**, one pair per variable in the corresponding variable list.

| Field | Type | Description |
|-------|------|-------------|
| `state_bounds` | array of `[lower, upper]` | Domain bounds for each state variable |
| `param_bounds` | array of `[lower, upper]` | Range for each parameter variable |
| `control_bounds` | array of `[lower, upper]` | Bounds for each control input |

**Example (2D system):**
```json
"state_vars": ["S1", "S2"],
"state_bounds": [[-2, 150], [0, 100]]
```
This means:
- `bounds[0] = [-2, 150]` applies to `state_vars[0] = "S1"` → `-2 <= S1 <= 150`
- `bounds[1] = [0, 100]` applies to `state_vars[1] = "S2"` → `0 <= S2 <= 100`

**Example (1D system with parameter):**
```json
"param_vars": ["P1"],
"param_bounds": [[0.0, 3.0]]
```
This means: `0.0 <= P1 <= 3.0`

**Example (2D parameters):**
```json
"param_vars": ["P1", "P2"],
"param_bounds": [[-1.0, 1.0], [-1.0, 1.0]]
```
This means: `-1.0 <= P1 <= 1.0` and `-1.0 <= P2 <= 1.0`

#### Initial Region

```json
"initial_region": {
  "bounds": [[2, 3], [0, 5]]
}
```
Specifies the set of initial states as a hyperrectangle. Format is the same as `state_bounds` - an array of `[lower, upper]` pairs, one per state variable.

#### Dynamics

Piecewise dynamics with conditions and transforms:

```json
"dynamics": [
  {
    "condition": "0 <= S1 <= 100",
    "transforms": {"S1": "(+ S1 (+ U1 W1))"}
  },
  {
    "condition": "S1 >= 100",
    "transforms": {"S1": "(+ S1 0)"}
  }
]
```

**Condition Format:**
- Simple inequalities: `"S1 >= 0"`, `"S1 <= 100"`, `"S1 > 0"`, `"S1 < 100"`
- Chained inequalities: `"0 <= S1 <= 100"`
- Conjunctions (use ` and ` with spaces): `"0 <= S1 <= 100 and 0 <= S2 <= 50"`

**Transform Format (S-expressions):**
- Addition: `(+ a b)`
- Multiplication: `(* a b)`
- Subtraction: `(- a b)` (binary only)

Examples:
- Identity: `"(+ S1 0)"`
- Linear: `"(+ S1 (+ U1 W1))"` (S1 + U1 + W1)
- With parameter: `"(+ S1 (+ U1 (+ W1 P1)))"` (S1 + U1 + W1 + P1)
- Scaled: `"(+ (* 0.5 S1) W1)"` (0.5*S1 + W1)
- Negative coefficient: `"(+ (* -1 S1) (* 2 S2))"` (-S1 + 2*S2)

#### Noise Distribution

```json
"noise_distribution": {
  "type": "uniform",
  "params": {"lower": [-1, -0.5], "upper": [1, 0.5]}
}
```

Currently supported: `"uniform"` distribution with `lower` and `upper` bound arrays (one value per noise variable).

### Specification

Specify **either** `target_region` (reachability) **or** `unsafe_region` (safety), not both.

#### Reachability Specification

```json
"target_region": {
  "bounds": [[90, 150]]
},
"target_probability": 0.95
```

Synthesizes a controller that reaches the target region with probability >= `target_probability`.

#### Safety Specification

```json
"unsafe_region": {
  "bounds": [[90, 150]]
},
"target_probability": 0.95
```

Synthesizes a controller that avoids the unsafe region with probability >= `target_probability`.

### Solver Options

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `degree` | int | required | Polynomial degree for certificate/controller templates |
| `smt_solver` | string | required | SMT solver (`"z3"`) |
| `entailment_solver` | string | required | Entailment solver (`"farkas"`) |
| `enable_param_refinement` | bool | `false` | Enable parameter space refinement |
| `param_refinement_threshold` | float | `0.01` | Minimum region size for refinement |
| `cutoff_time_per_smt_query` | float | none | Timeout per SMT query (seconds) |
| `parallel_refinement` | bool | `false` | Use parallel exploration of parameter space |
| `output_smt_path` | string | required | Path for generated SMT file |
| `verbose` | bool | `false` | Enable detailed output (region bounds, certificates). When `false`, only shows progress updates. |

### Simplified Examples

#### 1D Reachability with Parameters

```json
{
  "system": {
    "type": "cartesian",
    "state_vars": ["S1"],
    "param_vars": ["P1"],
    "noise_vars": ["W1"],
    "control_vars": ["U1"],
    "state_bounds": [[-2, 150]],
    "param_bounds": [[-1.0, 1.0]],
    "control_bounds": [[0, 0.6]],
    "initial_region": {"bounds": [[2, 3]]},
    "dynamics": [
      {"condition": "-2 <= S1 <= 0", "transforms": {"S1": "(+ S1 0)"}},
      {"condition": "0 <= S1 <= 100", "transforms": {"S1": "(+ S1 (+ U1 (+ W1 P1)))"}},
      {"condition": "S1 >= 100", "transforms": {"S1": "(+ S1 0)"}}
    ],
    "noise_distribution": {"type": "uniform", "params": {"lower": [-0.75], "upper": [-0.25]}}
  },
  "target_region": {"bounds": [[90, 150]]},
  "enable_param_refinement": true,
  "param_refinement_threshold": 0.1,
  "cutoff_time_per_smt_query": 10,
  "parallel_refinement": true,
  "target_probability": 0.9,
  "degree": 1,
  "smt_solver": "z3",
  "entailment_solver": "farkas",
  "output_smt_path": "./tmp/temporary_polyhorn_input.smt2"
}
```

#### 1D Safety with Parameters

```json
{
  "system": {
    "type": "cartesian",
    "state_vars": ["S1"],
    "param_vars": ["P1"],
    "noise_vars": ["W1"],
    "control_vars": ["U1"],
    "state_bounds": [[-2, 150]],
    "param_bounds": [[0.0, 3.0]],
    "control_bounds": [[-1, 1]],
    "initial_region": {"bounds": [[2, 3]]},
    "dynamics": [
      {"condition": "-2 <= S1 <= 0", "transforms": {"S1": "(+ S1 0)"}},
      {"condition": "0 <= S1 <= 100", "transforms": {"S1": "(+ S1 (+ U1 (+ W1 P1)))"}},
      {"condition": "S1 >= 100", "transforms": {"S1": "(+ S1 0)"}}
    ],
    "noise_distribution": {"type": "uniform", "params": {"lower": [-1], "upper": [1]}}
  },
  "unsafe_region": {"bounds": [[90, 150]]},
  "enable_param_refinement": true,
  "param_refinement_threshold": 0.1,
  "cutoff_time_per_smt_query": 10,
  "target_probability": 0.95,
  "degree": 1,
  "smt_solver": "z3",
  "entailment_solver": "farkas",
  "output_smt_path": "./tmp/temporary_polyhorn_input.smt2"
}
```

#### 2D System with Conjunctive Conditions

```json
{
  "system": {
    "type": "cartesian",
    "state_vars": ["S1", "S2"],
    "param_vars": ["P1", "P2"],
    "noise_vars": ["W1", "W2"],
    "state_bounds": [[-2, 2], [-2, 2]],
    "param_bounds": [[-1.0, 1.0], [-1.0, 1.0]],
    "initial_region": {"bounds": [[-1.9, -1.8], [-1.9, -1.8]]},
    "dynamics": [
      {
        "condition": "-2 <= S1 <= 2 and -2 <= S2 <= 2",
        "transforms": {
          "S1": "(+ (* -1 S1) (+ (* -2 S2) (+ P1 W1)))",
          "S2": "(+ S1 (+ (* -1 S2) (+ P2 W2)))"
        }
      }
    ],
    "noise_distribution": {"type": "uniform", "params": {"lower": [-0.1, 0.0], "upper": [-0.1, 0.1]}}
  },
  "target_region": {"bounds": [[-0.1, 0.1], [-0.1, 0.1]]},
  "target_probability": 0.9,
  "degree": 1,
  "smt_solver": "z3",
  "entailment_solver": "farkas",
  "output_smt_path": "./tmp/temporary_polyhorn_input.smt2"
}
```

## Output

The tool outputs:

1. **Angelic Winning Regions**: Parameter regions where a controller exists satisfying the specification
2. **Demonic Winning Regions**: Parameter regions where no controller can satisfy the specification (the dual spec holds for all controllers)
3. **Inconclusive Regions**: Regions that timed out during analysis

For each winning region, the tool provides:
- Parameter bounds defining the region
- Synthesized certificate (V function) and controller (C function) coefficients

## Theory

The tool uses:
- **Ranking Supermartingales (RSM)** for reachability verification
- **Repulsing Supermartingales (RASM)** for safety verification
- **Farkas' Lemma** for polynomial constraint solving
- **Parametric refinement** to partition the parameter space into winning regions

### Certificate Functions

For a system with states X and parameters P:
- **V(X, P)**: Lyapunov-like certificate function (polynomial over states and parameters)
- **C(X, P)**: Controller function (polynomial over states and parameters)
- **I(X)**: Invariant function (polynomial over states only)

The degree of these polynomials is controlled by the `degree` parameter.
