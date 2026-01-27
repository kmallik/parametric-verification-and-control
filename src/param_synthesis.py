import json
import sys
import os
import time
import threading
import multiprocessing
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor
from polyqent.main import execute
from srsm_generator import SRSMGenerator

# Thread-safe counter for unique file naming
_file_counter = 0
_file_counter_lock = threading.Lock()

def load_config(filepath: str) -> Dict[str, Any]:
    """Load configuration from JSON file."""
    with open(filepath, 'r') as f:
        return json.load(f)


def _run_smt_query_worker(args, result_queue):
    """Run SMT query in a separate process and put result in queue.

    This function runs in a subprocess and puts the result in the queue.
    """
    (current_bounds, config, entailment_solver, degree, smt_solver, file_id) = args

    output_path = f"./tmp/temporary_polyhorn_input_id{file_id}.smt2"
    config_path = f"./tmp/temporary_polyhorn_config_id{file_id}.json"
    temp_output_path = f"./tmp/polyhorn_temp_id{file_id}.txt"

    try:
        generator = SRSMGenerator()

        has_target = 'target_region' in config
        has_unsafe = 'unsafe_region' in config
        target_probability = config.get('target_probability', 1.0)

        # Determine which type of specification
        if has_target and not has_unsafe:
            if target_probability < 1.0:
                generator.generate_smt_file_quantitative_reach_simplified(config, output_path, override_param_bounds=current_bounds)
            else:
                generator.generate_smt_file_qualitative_reach_simplified(config, output_path, override_param_bounds=current_bounds)
        elif has_unsafe and not has_target:
            if target_probability < 1.0:
                generator.generate_smt_file_quantitative_safety_simplified(config, output_path, override_param_bounds=current_bounds)
            else:
                raise NotImplementedError("Qualitative safety (target_probability = 1) not yet implemented")
        else:
            raise ValueError("Must specify either 'target_region' or 'unsafe_region', but not both")

        generator.generate_config_file(entailment_solver, degree, smt_solver, output_path, config_path, temp_output_path)

        is_sat, model = execute(formula=output_path, config=config_path)
        result_queue.put(('success', is_sat, model))
    except Exception as e:
        result_queue.put(('error', str(e), None))


def _run_dual_smt_query_worker(args, result_queue):
    """Run dual SMT query in a separate process and put result in queue.

    This function runs in a subprocess for the dual problem (demonic).
    """
    (current_bounds, config, entailment_solver, degree, smt_solver, file_id) = args

    output_path = f"./tmp/temporary_dual_polyhorn_input_id{file_id}.smt2"
    config_path = f"./tmp/temporary_dual_polyhorn_config_id{file_id}.json"
    temp_output_path = f"./tmp/dual_polyhorn_temp_id{file_id}.txt"

    try:
        generator = SRSMGenerator()

        has_target = 'target_region' in config
        has_unsafe = 'unsafe_region' in config
        original_target_probability = config.get('target_probability', 1.0)

        # For dual problem, use 1 - target_probability
        dual_target_probability = 1.0 - original_target_probability

        # Create a modified config with the dual probability
        dual_config = config.copy()
        dual_config['target_probability'] = dual_target_probability

        # Determine which type of dual specification
        # Dual of reachability (has target) -> safety (avoid target)
        # Dual of safety (has unsafe) -> reachability (reach unsafe)
        if has_target and not has_unsafe:
            if dual_target_probability > 0 and dual_target_probability < 1.0:
                generator.generate_smt_file_dual_reach_simplified(dual_config, output_path, override_param_bounds=current_bounds)
            else:
                raise NotImplementedError("Dual reachability requires 0 < 1-p < 1")
        elif has_unsafe and not has_target:
            if dual_target_probability > 0 and dual_target_probability < 1.0:
                generator.generate_smt_file_dual_safety_simplified(dual_config, output_path, override_param_bounds=current_bounds)
            else:
                raise NotImplementedError("Dual safety requires 0 < 1-p < 1")
        else:
            raise ValueError("Must specify either 'target_region' or 'unsafe_region', but not both")

        generator.generate_config_file(entailment_solver, degree, smt_solver, output_path, config_path, temp_output_path)

        is_sat, model = execute(formula=output_path, config=config_path)
        result_queue.put(('success', is_sat, model))
    except Exception as e:
        result_queue.put(('error', str(e), None))


def region_is_contained_in(inner_bounds: List, outer_bounds: List) -> bool:
    """Check if inner_bounds is completely contained within outer_bounds.

    Both bounds are lists of [lower, upper] pairs, one per dimension.
    Returns True if inner is a subset of outer in all dimensions.
    """
    for i in range(len(inner_bounds)):
        inner_lo, inner_hi = inner_bounds[i]
        outer_lo, outer_hi = outer_bounds[i]
        # Inner must be within outer in each dimension
        if inner_lo < outer_lo or inner_hi > outer_hi:
            return False
    return True


def filter_contained_regions(timed_out_regions: List[Dict], winning_regions: List[Dict]) -> List[Dict]:
    """Filter out timed-out regions that are contained in any winning region.

    Args:
        timed_out_regions: List of dicts with 'param_bounds' for regions that timed out
        winning_regions: List of dicts with 'param_bounds' for angelic or demonic winning regions

    Returns:
        List of timed-out regions that are NOT contained in any winning region
    """
    truly_inconclusive = []
    for timed_out in timed_out_regions:
        timed_out_bounds = timed_out['param_bounds']
        is_contained = False
        for winning in winning_regions:
            winning_bounds = winning['param_bounds']
            if region_is_contained_in(timed_out_bounds, winning_bounds):
                is_contained = True
                break
        if not is_contained:
            truly_inconclusive.append(timed_out)
    return truly_inconclusive


def merge_adjacent_regions(regions: List[Dict]) -> List[Dict]:
    """Merge adjacent regions that share a boundary.

    Two regions can be merged if:
    - They differ in exactly one dimension
    - In that dimension, one's upper bound equals the other's lower bound
    - In all other dimensions, they have identical bounds

    Returns a new list with merged regions.
    """
    if not regions:
        return []

    # Extract just the param_bounds for merging
    bounds_list = [r['param_bounds'] for r in regions]

    # Convert to list of tuples if needed
    bounds_list = [
        [tuple(b) if isinstance(b, list) else b for b in bounds]
        for bounds in bounds_list
    ]

    def can_merge(b1: List[Tuple[float, float]], b2: List[Tuple[float, float]]) -> int:
        """Check if two regions can be merged. Returns the dimension index if mergeable, -1 otherwise."""
        if len(b1) != len(b2):
            return -1

        differ_dim = -1
        for dim in range(len(b1)):
            if b1[dim] != b2[dim]:
                if differ_dim != -1:
                    # Already found a differing dimension, can't merge
                    return -1
                differ_dim = dim

        if differ_dim == -1:
            # Identical regions (shouldn't happen, but handle it)
            return -1

        # Check if they share a boundary in the differing dimension
        if b1[differ_dim][1] == b2[differ_dim][0] or b2[differ_dim][1] == b1[differ_dim][0]:
            return differ_dim

        return -1

    def merge_bounds(b1: List[Tuple[float, float]], b2: List[Tuple[float, float]], dim: int) -> List[Tuple[float, float]]:
        """Merge two regions along the specified dimension."""
        result = list(b1)
        result[dim] = (min(b1[dim][0], b2[dim][0]), max(b1[dim][1], b2[dim][1]))
        return result

    # Keep merging until no more merges are possible
    merged = bounds_list.copy()
    changed = True

    while changed:
        changed = False
        new_merged = []
        used = [False] * len(merged)

        for i in range(len(merged)):
            if used[i]:
                continue

            current = merged[i]

            for j in range(i + 1, len(merged)):
                if used[j]:
                    continue

                merge_dim = can_merge(current, merged[j])
                if merge_dim != -1:
                    current = merge_bounds(current, merged[j], merge_dim)
                    used[j] = True
                    changed = True

            new_merged.append(current)
            used[i] = True

        merged = new_merged

    # Convert back to the original format (list of dicts with param_bounds)
    return [{'param_bounds': bounds} for bounds in merged]


def write_to_logfile(logfile: str, config_file: str, angelic_regions: List[Dict],
                     demonic_regions: List[Dict], inconclusive_regions: List[Dict],
                     runtime: float, refinement_mode: Optional[int] = None,
                     epsilon: Optional[float] = None) -> None:
    """Write experimental results to a logfile.

    Appends the results to the logfile, creating it if it doesn't exist.
    Includes date, time, input filename, runtime, refinement mode, and experimental results.
    Experiment entries are numbered sequentially.
    """
    import re

    # Determine the next experiment number by reading existing entries
    experiment_num = 1
    if os.path.exists(logfile):
        try:
            with open(logfile, 'r') as f:
                content = f.read()
                # Find all experiment numbers in the file
                matches = re.findall(r'EXPERIMENT #(\d+)', content)
                if matches:
                    experiment_num = max(int(m) for m in matches) + 1
        except Exception:
            pass  # If we can't read the file, start at 1

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    mode_descriptions = {
        0: "Mode 0: Sequential (angelic first, then demonic on complement)",
        1: "Mode 1: Parallel children exploration (angelic first, then demonic)",
        2: "Mode 2: Parallel angelic/demonic per region",
        3: "Mode 3: Epsilon-based parallel angelic/demonic per region",
        None: "No refinement (single query)"
    }

    with open(logfile, 'a') as f:
        f.write(f"\n{'='*80}\n")
        f.write(f"EXPERIMENT #{experiment_num}\n")
        f.write(f"{'='*80}\n")
        f.write(f"Date/Time: {timestamp}\n")
        f.write(f"Input file: {config_file}\n")
        f.write(f"Refinement mode: {mode_descriptions.get(refinement_mode, f'Unknown ({refinement_mode})')}\n")
        if epsilon is not None:
            f.write(f"Epsilon (max_inconclusive): {epsilon}\n")
        f.write(f"Total runtime: {runtime:.2f} seconds\n")
        f.write(f"\n")

        # Angelic regions
        f.write(f"--- ANGELIC WINNING REGIONS ---\n")
        f.write(f"(Exists controller satisfying the specification)\n")
        f.write(f"Total: {len(angelic_regions)}\n")
        for i, model_info in enumerate(angelic_regions, 1):
            f.write(f"\n  Region {i}:\n")
            if 'computation_time' in model_info:
                f.write(f"    Computation time (s): {model_info['computation_time']:.2f}\n")
            f.write(f"    Parameter bounds: {model_info['param_bounds']}\n")
            f.write(f"    Certificate: {model_info['model']}\n")

        f.write(f"\n")

        # Demonic regions
        f.write(f"--- DEMONIC WINNING REGIONS ---\n")
        f.write(f"(For all controllers, the dual specification is satisfied)\n")
        f.write(f"Total: {len(demonic_regions)}\n")
        for i, model_info in enumerate(demonic_regions, 1):
            f.write(f"\n  Region {i}:\n")
            if 'computation_time' in model_info:
                f.write(f"    Computation time (s): {model_info['computation_time']:.2f}\n")
            f.write(f"    Parameter bounds: {model_info['param_bounds']}\n")
            f.write(f"    Certificate: {model_info['model']}\n")

        f.write(f"\n")

        # Inconclusive regions
        f.write(f"--- INCONCLUSIVE REGIONS (Merged) ---\n")
        f.write(f"(Neither angelic nor demonic winning determined)\n")
        f.write(f"Total: {len(inconclusive_regions)}\n")
        for i, region_info in enumerate(inconclusive_regions, 1):
            f.write(f"  Region {i}: {region_info['param_bounds']}\n")

        f.write(f"\n{'='*80}\n\n")

    print(f"Results appended to logfile: {logfile}")


def compute_complement_regions(angelic_regions: List[Dict], initial_bounds: List[Tuple[float, float]],
                               threshold: float) -> List[Tuple[List[Tuple[float, float]], int]]:
    """Compute the complement of angelic winning regions within the initial parameter bounds.

    Returns a list of (bounds, depth) tuples representing regions to explore for demonic winning.
    Uses a recursive subtraction approach.
    """
    def bounds_intersect(b1: List[Tuple[float, float]], b2: List[Tuple[float, float]]) -> bool:
        """Check if two rectangular bounds intersect."""
        for i in range(len(b1)):
            if b1[i][1] <= b2[i][0] or b2[i][1] <= b1[i][0]:
                return False
        return True

    def subtract_region(region: List[Tuple[float, float]], to_subtract: List[Tuple[float, float]]) -> List[List[Tuple[float, float]]]:
        """Subtract one rectangular region from another, returning list of resulting regions."""
        if not bounds_intersect(region, to_subtract):
            return [region]

        result = []
        remaining = region

        for dim in range(len(region)):
            # Lower slice in this dimension
            if remaining[dim][0] < to_subtract[dim][0]:
                lower_slice = remaining.copy()
                lower_slice[dim] = (remaining[dim][0], min(remaining[dim][1], to_subtract[dim][0]))
                result.append(lower_slice)

            # Upper slice in this dimension
            if remaining[dim][1] > to_subtract[dim][1]:
                upper_slice = remaining.copy()
                upper_slice[dim] = (max(remaining[dim][0], to_subtract[dim][1]), remaining[dim][1])
                result.append(upper_slice)

            # Update remaining to the intersection in this dimension for next iteration
            remaining = remaining.copy()
            remaining[dim] = (max(remaining[dim][0], to_subtract[dim][0]),
                             min(remaining[dim][1], to_subtract[dim][1]))

            # If remaining is empty in any dimension, we're done
            if remaining[dim][0] >= remaining[dim][1]:
                break

        return result

    # Start with the full initial bounds
    complement_regions = [initial_bounds]

    # Subtract each angelic region
    for angelic_info in angelic_regions:
        angelic_bounds = angelic_info['param_bounds']
        # Convert to list of tuples if needed
        if isinstance(angelic_bounds[0], list):
            angelic_bounds = [tuple(b) for b in angelic_bounds]

        new_complement = []
        for region in complement_regions:
            subtracted = subtract_region(region, angelic_bounds)
            new_complement.extend(subtracted)
        complement_regions = new_complement

    # Filter out regions that are too small (below threshold)
    def compute_width(bounds):
        return max(upper - lower for lower, upper in bounds)

    valid_regions = []
    for region in complement_regions:
        width = compute_width(region)
        if width > threshold:
            valid_regions.append((region, 0))  # depth 0 for initial complement regions

    return valid_regions


def refine_parameter_space(config: Dict[str, Any], entailment_solver: str,
                          degree: int, smt_solver: str, threshold: float = 0.01,
                          refinement_mode: int = 0,
                          cutoff_time: Optional[float] = None,
                          max_inconclusive=None,
                          overall_timeout: Optional[float] = None) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """Iteratively refine parameter space to find angelic and demonic winning regions.

    First finds angelic winning regions (exists controller satisfying spec).
    Then finds demonic winning regions in the complement (for all controllers, dual spec satisfied).

    Args:
        config: Configuration dictionary
        entailment_solver: Solver for entailment checking
        degree: Polynomial degree
        smt_solver: SMT solver to use
        threshold: Refinement threshold
        refinement_mode: Refinement mode:
            0 - Sequential: all angelic first, then all demonic on complement
            1 - Parallel children: parallel exploration of children regions, angelic first then demonic
            2 - Parallel angelic/demonic: run angelic and demonic per region simultaneously
            3 - Epsilon-based: like mode 2 but ignores threshold, stops when inconclusive fraction < max_inconclusive
        cutoff_time: Optional timeout in seconds for each SMT query. If None, no timeout.
        max_inconclusive: Maximum fraction of parameter space that can remain inconclusive (required for mode 3).
            Can be a single float or a list of floats. If a list, snapshots are taken at each threshold.
        overall_timeout: Optional overall timeout in seconds for the entire refinement (mode 3).

    Returns:
        Tuple of (angelic_regions, demonic_regions, timed_out_regions):
        - angelic_regions: List of dicts with 'param_bounds', 'model' for angelic winning regions
        - demonic_regions: List of dicts with 'param_bounds', 'model' for demonic winning regions
        - timed_out_regions: List of dicts with 'param_bounds' for regions that timed out (neither angelic nor demonic)
    """

    if 'param_bounds' not in config['system']:
        raise ValueError("Parameter space refinement requires 'param_bounds'")

    initial_param_bounds = config['system']['param_bounds']
    param_vars = config['system'].get('param_vars', [])

    if not param_vars:
        raise ValueError("No parameter variables specified")

    # Create tmp directory if it doesn't exist
    os.makedirs('./tmp', exist_ok=True)

    # Reset file counter for clean state
    global _file_counter
    _file_counter = 0

    print(f"\n{'='*80}")
    print(f"PARAMETER SPACE REFINEMENT")
    print(f"{'='*80}")
    print(f"Initial parameter bounds: {initial_param_bounds}")
    print(f"Refinement threshold: {threshold}")
    if cutoff_time is not None:
        print(f"Cutoff time per SMT query: {cutoff_time} seconds")
    print(f"{'='*80}\n")

    def compute_width(bounds: List[Tuple[float, float]]) -> float:
        """Compute maximum width across all parameter dimensions."""
        return max(upper - lower for lower, upper in bounds)

    def compute_region_volume(bounds: List[Tuple[float, float]]) -> float:
        """Compute the volume (product of widths) of a rectangular region."""
        volume = 1.0
        for lo, hi in bounds:
            volume *= (hi - lo)
        return volume

    def split_bounds(bounds: List[Tuple[float, float]], dim: int) -> Tuple[List, List]:
        """Split bounds along dimension at midpoint."""
        midpoint = (bounds[dim][0] + bounds[dim][1]) / 2.0

        left_bounds = bounds.copy()
        right_bounds = bounds.copy()

        left_bounds[dim] = (bounds[dim][0], midpoint)
        right_bounds[dim] = (midpoint, bounds[dim][1])

        return left_bounds, right_bounds

    def get_unique_file_id() -> int:
        """Get a unique file ID for thread-safe file naming."""
        global _file_counter
        with _file_counter_lock:
            _file_counter += 1
            return _file_counter

    # Mode 3: Epsilon-based parallel angelic/demonic with multi-region concurrency
    if refinement_mode == 3:
        from collections import deque

        if max_inconclusive is None:
            raise ValueError("'max_inconclusive' is required for refinement_mode 3")
        total_volume = compute_region_volume(initial_param_bounds)
        resolved_volume = 0.0
        refinement_start_time = time.time()
        # Normalize max_inconclusive to a sorted list (descending) of thresholds
        if isinstance(max_inconclusive, (list, tuple)):
            epsilon_thresholds = sorted([float(x) for x in max_inconclusive], reverse=True)
        else:
            epsilon_thresholds = [float(max_inconclusive)]
        final_epsilon = epsilon_thresholds[-1]
        pending_thresholds = list(epsilon_thresholds)
        snapshots = []

        # Determine max concurrent region pairs
        cpu_count = os.cpu_count() or 2
        max_concurrent = max(1, cpu_count // 2)

        print("REFINEMENT MODE 3: Epsilon-based parallel angelic/demonic per region")
        print(f"Epsilon thresholds: {epsilon_thresholds}")
        print(f"Max concurrent region pairs: {max_concurrent} (using up to {max_concurrent * 2} processes)")
        if overall_timeout is not None:
            print(f"Overall timeout: {overall_timeout} seconds")
        print("A region can be angelic OR demonic winning, but not both.")
        print(f"{'='*80}\n")

        queue = deque([(initial_param_bounds, 0)])
        angelic_regions = []
        demonic_regions = []
        timed_out_regions = []

        # Active jobs: list of dicts with keys: bounds, depth, angelic_proc, demonic_proc,
        # angelic_queue, demonic_queue, angelic_done, demonic_done, angelic_result,
        # demonic_result, start_time, found_sat
        active_jobs = []

        def _check_epsilon_and_snapshot():
            """Check epsilon thresholds and take snapshots. Returns True if final epsilon reached."""
            nonlocal resolved_volume
            inconclusive_fraction = (total_volume - resolved_volume) / total_volume
            while pending_thresholds and inconclusive_fraction <= pending_thresholds[0]:
                crossed = pending_thresholds.pop(0)
                snap_runtime = time.time() - refinement_start_time
                snap_inconclusive = list(timed_out_regions) + [{'param_bounds': b, 'depth': d} for b, d in queue]
                # Also include bounds from active jobs as inconclusive in snapshot
                for job in active_jobs:
                    snap_inconclusive.append({'param_bounds': job['bounds'], 'depth': job['depth']})
                snap_inconclusive_merged = merge_adjacent_regions(snap_inconclusive)
                snapshots.append((crossed, list(angelic_regions), list(demonic_regions), snap_inconclusive_merged, snap_runtime))
                print(f"\n*** SNAPSHOT at epsilon={crossed}: inconclusive fraction={inconclusive_fraction:.6f}, runtime={snap_runtime:.2f}s ***")
            return inconclusive_fraction <= final_epsilon

        def _terminate_all_active():
            """Terminate all active jobs and mark their regions as timed out."""
            for job in active_jobs:
                for proc in [job['angelic_proc'], job['demonic_proc']]:
                    if proc.is_alive():
                        proc.terminate()
                        proc.join(timeout=1)
                        if proc.is_alive():
                            proc.kill()
                timed_out_regions.append({'param_bounds': job['bounds'], 'depth': job['depth']})
            active_jobs.clear()

        def _launch_job(bounds, depth):
            """Launch an angelic/demonic pair for the given region."""
            fid_a = get_unique_file_id()
            fid_d = get_unique_file_id()
            aq = multiprocessing.Queue()
            dq = multiprocessing.Queue()
            a_args = (bounds, config, entailment_solver, degree, smt_solver, fid_a)
            d_args = (bounds, config, entailment_solver, degree, smt_solver, fid_d)
            a_proc = multiprocessing.Process(target=_run_smt_query_worker, args=(a_args, aq))
            d_proc = multiprocessing.Process(target=_run_dual_smt_query_worker, args=(d_args, dq))
            indent = "  " * depth
            print(f"{indent}Exploring region: {bounds} (launching angelic+demonic pair)")
            a_proc.start()
            d_proc.start()
            active_jobs.append({
                'bounds': bounds, 'depth': depth,
                'angelic_proc': a_proc, 'demonic_proc': d_proc,
                'angelic_queue': aq, 'demonic_queue': dq,
                'angelic_done': False, 'demonic_done': False,
                'angelic_result': None, 'demonic_result': None,
                'start_time': time.time(), 'found_sat': False
            })

        effective_timeout = cutoff_time if cutoff_time is not None else 600

        while queue or active_jobs:
            # Check overall timeout
            if overall_timeout is not None and time.time() - refinement_start_time > overall_timeout:
                print(f"\n⏱ OVERALL TIMEOUT ({overall_timeout}s) reached.")
                _terminate_all_active()
                while queue:
                    rb, rd = queue.popleft()
                    timed_out_regions.append({'param_bounds': rb, 'depth': rd})
                snap_runtime = time.time() - refinement_start_time
                snap_inconclusive_merged = merge_adjacent_regions(timed_out_regions)
                for crossed in pending_thresholds:
                    snapshots.append((crossed, list(angelic_regions), list(demonic_regions), list(snap_inconclusive_merged), snap_runtime))
                    print(f"*** SNAPSHOT at epsilon={crossed} (overall timeout): runtime={snap_runtime:.2f}s ***")
                pending_thresholds.clear()
                break

            # Check epsilon criterion
            if _check_epsilon_and_snapshot():
                print(f"\n✓ Final epsilon ({final_epsilon}) reached. Stopping.")
                _terminate_all_active()
                while queue:
                    rb, rd = queue.popleft()
                    timed_out_regions.append({'param_bounds': rb, 'depth': rd})
                break

            # Fill up to max_concurrent active jobs from the queue
            while len(active_jobs) < max_concurrent and queue:
                bounds, depth = queue.popleft()
                _launch_job(bounds, depth)

            if not active_jobs:
                break

            # Poll all active jobs
            completed_indices = []
            for idx, job in enumerate(active_jobs):
                indent = "  " * job['depth']
                elapsed = time.time() - job['start_time']

                # Per-query timeout
                if elapsed > effective_timeout and not job['found_sat']:
                    if not job['angelic_done'] or not job['demonic_done']:
                        print(f"{indent}⏱ TIMEOUT for region {job['bounds']}")
                        for proc in [job['angelic_proc'], job['demonic_proc']]:
                            if proc.is_alive():
                                proc.terminate()
                        job['angelic_done'] = True
                        job['demonic_done'] = True

                # Check angelic
                if not job['angelic_done'] and not job['angelic_proc'].is_alive():
                    job['angelic_done'] = True
                    try:
                        job['angelic_result'] = job['angelic_queue'].get_nowait()
                    except:
                        job['angelic_result'] = ('error', 'No result', None)

                    if job['angelic_result'][0] == 'success' and job['angelic_result'][1] == 'sat':
                        computation_time = time.time() - job['start_time']
                        print(f"{indent}✓ ANGELIC SAT for {job['bounds']} (time: {computation_time:.2f}s)")
                        if job['demonic_proc'].is_alive():
                            job['demonic_proc'].terminate()
                            job['demonic_proc'].join(timeout=1)
                            if job['demonic_proc'].is_alive():
                                job['demonic_proc'].kill()
                        angelic_regions.append({
                            'param_bounds': job['bounds'],
                            'model': job['angelic_result'][2],
                            'is_sat': 'sat',
                            'computation_time': computation_time
                        })
                        resolved_volume += compute_region_volume(job['bounds'])
                        job['found_sat'] = True
                        frac = (total_volume - resolved_volume) / total_volume
                        print(f"{indent}  [Inconclusive fraction: {frac:.6f}]")
                        completed_indices.append(idx)
                        continue

                # Check demonic
                if not job['demonic_done'] and not job['demonic_proc'].is_alive():
                    job['demonic_done'] = True
                    try:
                        job['demonic_result'] = job['demonic_queue'].get_nowait()
                    except:
                        job['demonic_result'] = ('error', 'No result', None)

                    if job['demonic_result'][0] == 'success' and job['demonic_result'][1] == 'sat':
                        computation_time = time.time() - job['start_time']
                        print(f"{indent}✓ DEMONIC SAT for {job['bounds']} (time: {computation_time:.2f}s)")
                        if job['angelic_proc'].is_alive():
                            job['angelic_proc'].terminate()
                            job['angelic_proc'].join(timeout=1)
                            if job['angelic_proc'].is_alive():
                                job['angelic_proc'].kill()
                        demonic_regions.append({
                            'param_bounds': job['bounds'],
                            'model': job['demonic_result'][2],
                            'is_sat': 'sat',
                            'computation_time': computation_time
                        })
                        resolved_volume += compute_region_volume(job['bounds'])
                        job['found_sat'] = True
                        frac = (total_volume - resolved_volume) / total_volume
                        print(f"{indent}  [Inconclusive fraction: {frac:.6f}]")
                        completed_indices.append(idx)
                        continue

                # Both done, no SAT found
                if job['angelic_done'] and job['demonic_done'] and not job['found_sat']:
                    # Clean up
                    job['angelic_proc'].join(timeout=1)
                    job['demonic_proc'].join(timeout=1)

                    # Split region (mode 3 never stops due to threshold)
                    max_dim = max(range(len(job['bounds'])),
                                 key=lambda d: job['bounds'][d][1] - job['bounds'][d][0])
                    left_bounds, right_bounds = split_bounds(job['bounds'], max_dim)
                    print(f"{indent}✗ Both UNSAT/timeout - splitting region {job['bounds']}...")
                    queue.append((left_bounds, job['depth'] + 1))
                    queue.append((right_bounds, job['depth'] + 1))
                    frac = (total_volume - resolved_volume) / total_volume
                    print(f"{indent}  [Inconclusive fraction: {frac:.6f}]")
                    completed_indices.append(idx)

            # Remove completed jobs (reverse order to preserve indices)
            for idx in sorted(completed_indices, reverse=True):
                job = active_jobs.pop(idx)
                job['angelic_proc'].join(timeout=1)
                job['demonic_proc'].join(timeout=1)

            if not completed_indices:
                time.sleep(0.1)  # Avoid busy waiting if nothing completed this cycle

        # Summary
        print(f"\n{'='*80}")
        print(f"PARALLEL REFINEMENT COMPLETE (MODE 3)")
        print(f"{'='*80}")
        print(f"Total angelic winning regions found: {len(angelic_regions)}")
        for i, model_info in enumerate(angelic_regions, 1):
            print(f"  Region {i}: {model_info['param_bounds']}")
        print(f"\nTotal demonic winning regions found: {len(demonic_regions)}")
        for i, model_info in enumerate(demonic_regions, 1):
            print(f"  Region {i}: {model_info['param_bounds']}")
        if timed_out_regions:
            print(f"\nInconclusive regions: {len(timed_out_regions)}")
            for i, region_info in enumerate(timed_out_regions, 1):
                print(f"  Region {i}: {region_info['param_bounds']}")
        print(f"{'='*80}\n")

        merged_inconclusive = merge_adjacent_regions(timed_out_regions)
        if len(timed_out_regions) != len(merged_inconclusive):
            print(f"Merged {len(timed_out_regions)} inconclusive regions into {len(merged_inconclusive)} regions.")

        return angelic_regions, demonic_regions, merged_inconclusive, snapshots

    # Mode 2: run angelic and demonic queries in parallel for each region (one at a time)
    if refinement_mode == 2:
        from collections import deque

        print("REFINEMENT MODE 2: Running angelic and demonic queries simultaneously per region")
        print("A region can be angelic OR demonic winning, but not both.")
        print(f"{'='*80}\n")

        queue = deque([(initial_param_bounds, 0)])
        angelic_regions = []
        demonic_regions = []
        timed_out_regions = []

        while queue:
            current_bounds, depth = queue.popleft()
            indent = "  " * depth
            width = compute_width(current_bounds)

            print(f"{indent}Exploring region: {current_bounds} (width: {width:.6f})")

            # Early threshold check
            if width <= threshold:
                print(f"{indent}✗ Below threshold - marking as inconclusive")
                timed_out_regions.append({'param_bounds': current_bounds, 'depth': depth})
                continue

            # Run angelic and demonic queries in parallel
            file_id_angelic = get_unique_file_id()
            file_id_demonic = get_unique_file_id()

            angelic_queue = multiprocessing.Queue()
            demonic_queue = multiprocessing.Queue()

            angelic_args = (current_bounds, config, entailment_solver, degree, smt_solver, file_id_angelic)
            demonic_args = (current_bounds, config, entailment_solver, degree, smt_solver, file_id_demonic)

            angelic_process = multiprocessing.Process(
                target=_run_smt_query_worker,
                args=(angelic_args, angelic_queue)
            )
            demonic_process = multiprocessing.Process(
                target=_run_dual_smt_query_worker,
                args=(demonic_args, demonic_queue)
            )

            print(f"{indent}Running angelic and demonic queries in parallel...")
            angelic_process.start()
            demonic_process.start()

            # Poll both processes until one returns SAT or both complete
            angelic_result = None
            demonic_result = None
            angelic_done = False
            demonic_done = False
            found_sat = False

            effective_timeout = cutoff_time if cutoff_time is not None else 600

            start_time = time.time()
            while not (angelic_done and demonic_done):
                elapsed = time.time() - start_time
                if elapsed > effective_timeout:
                    print(f"{indent}⏱ TIMEOUT")
                    if angelic_process.is_alive():
                        angelic_process.terminate()
                    if demonic_process.is_alive():
                        demonic_process.terminate()
                    break

                # Check angelic
                if not angelic_done and not angelic_process.is_alive():
                    angelic_done = True
                    try:
                        angelic_result = angelic_queue.get_nowait()
                    except:
                        angelic_result = ('error', 'No result', None)

                    if angelic_result[0] == 'success' and angelic_result[1] == 'sat':
                        computation_time = time.time() - start_time
                        print(f"{indent}✓ ANGELIC SAT - terminating demonic query (time: {computation_time:.2f}s)")
                        if demonic_process.is_alive():
                            demonic_process.terminate()
                            demonic_process.join(timeout=1)
                            if demonic_process.is_alive():
                                demonic_process.kill()
                        angelic_regions.append({
                            'param_bounds': current_bounds,
                            'model': angelic_result[2],
                            'is_sat': 'sat',
                            'computation_time': computation_time
                        })
                        found_sat = True
                        break

                # Check demonic
                if not demonic_done and not demonic_process.is_alive():
                    demonic_done = True
                    try:
                        demonic_result = demonic_queue.get_nowait()
                    except:
                        demonic_result = ('error', 'No result', None)

                    if demonic_result[0] == 'success' and demonic_result[1] == 'sat':
                        computation_time = time.time() - start_time
                        print(f"{indent}✓ DEMONIC SAT - terminating angelic query (time: {computation_time:.2f}s)")
                        if angelic_process.is_alive():
                            angelic_process.terminate()
                            angelic_process.join(timeout=1)
                            if angelic_process.is_alive():
                                angelic_process.kill()
                        demonic_regions.append({
                            'param_bounds': current_bounds,
                            'model': demonic_result[2],
                            'is_sat': 'sat',
                            'computation_time': computation_time
                        })
                        found_sat = True
                        break

                time.sleep(0.1)

            # Clean up processes
            angelic_process.join(timeout=1)
            demonic_process.join(timeout=1)

            if found_sat:
                continue

            # Check final state if both completed without SAT
            if angelic_done and demonic_done:
                angelic_sat = angelic_result and angelic_result[0] == 'success' and angelic_result[1] == 'sat'
                demonic_sat = demonic_result and demonic_result[0] == 'success' and demonic_result[1] == 'sat'

                if not angelic_sat and not demonic_sat:
                    max_dim = max(range(len(current_bounds)),
                                 key=lambda d: current_bounds[d][1] - current_bounds[d][0])
                    left_bounds, right_bounds = split_bounds(current_bounds, max_dim)
                    child_width = compute_width(left_bounds)

                    if child_width <= threshold:
                        print(f"{indent}✗ Both UNSAT - children would be below threshold, marking as inconclusive")
                        timed_out_regions.append({'param_bounds': current_bounds, 'depth': depth})
                    else:
                        print(f"{indent}✗ Both UNSAT - splitting region...")
                        queue.append((left_bounds, depth + 1))
                        queue.append((right_bounds, depth + 1))
            elif not angelic_done or not demonic_done:
                max_dim = max(range(len(current_bounds)),
                             key=lambda d: current_bounds[d][1] - current_bounds[d][0])
                left_bounds, right_bounds = split_bounds(current_bounds, max_dim)
                child_width = compute_width(left_bounds)

                if child_width <= threshold:
                    print(f"{indent}⏱ Timeout at minimum granularity - marking as inconclusive")
                    timed_out_regions.append({'param_bounds': current_bounds, 'depth': depth})
                else:
                    print(f"{indent}⏱ Timeout - splitting to continue exploration...")
                    queue.append((left_bounds, depth + 1))
                    queue.append((right_bounds, depth + 1))

        # Summary
        print(f"\n{'='*80}")
        print(f"PARALLEL REFINEMENT COMPLETE")
        print(f"{'='*80}")
        print(f"Total angelic winning regions found: {len(angelic_regions)}")
        for i, model_info in enumerate(angelic_regions, 1):
            print(f"  Region {i}: {model_info['param_bounds']}")
        print(f"\nTotal demonic winning regions found: {len(demonic_regions)}")
        for i, model_info in enumerate(demonic_regions, 1):
            print(f"  Region {i}: {model_info['param_bounds']}")
        if timed_out_regions:
            print(f"\nInconclusive regions: {len(timed_out_regions)}")
            for i, region_info in enumerate(timed_out_regions, 1):
                print(f"  Region {i}: {region_info['param_bounds']}")
        print(f"{'='*80}\n")

        merged_inconclusive = merge_adjacent_regions(timed_out_regions)
        if len(timed_out_regions) != len(merged_inconclusive):
            print(f"Merged {len(timed_out_regions)} inconclusive regions into {len(merged_inconclusive)} regions.")

        return angelic_regions, demonic_regions, merged_inconclusive

    # Mode 0 and Mode 1: Sequential phases (angelic first, then demonic on complement)
    # Mode 0: Serial exploration of children
    # Mode 1: Parallel exploration of children using multiprocessing
    mode_desc = "MODE 0 (Sequential)" if refinement_mode == 0 else "MODE 1 (Parallel children)"
    print(f"REFINEMENT {mode_desc}: Angelic phase first, then demonic on complement")
    print(f"{'='*80}\n")

    # If no cutoff time, use the recursive approach
    if cutoff_time is None:
        def explore_region(current_bounds: List[Tuple[float, float]], depth: int = 0) -> List[Dict]:
            """Recursively explore parameter region."""
            indent = "  " * depth
            width = compute_width(current_bounds)

            print(f"{indent}Exploring region: {current_bounds} (width: {width:.6f})")

            # Early threshold check - skip SMT generation if region is too small
            if width <= threshold:
                print(f"{indent}✗ Below threshold - skipping SMT generation")
                return []

            region_start_time = time.time()
            generator = SRSMGenerator()

            # Use unique file ID to avoid race conditions in parallel mode
            file_id = get_unique_file_id()
            output_path = f"./tmp/temporary_polyhorn_input_id{file_id}.smt2"
            config_path = f"./tmp/temporary_polyhorn_config_id{file_id}.json"
            temp_output_path = f"./tmp/polyhorn_temp_id{file_id}.txt"

            has_target = 'target_region' in config
            has_unsafe = 'unsafe_region' in config
            target_probability = config.get('target_probability', 1.0)

            # Determine which type of specification
            if has_target and not has_unsafe:
                # Reachability specification
                if target_probability < 1.0:
                    generator.generate_smt_file_quantitative_reach_simplified(config, output_path, override_param_bounds=current_bounds)
                else:
                    generator.generate_smt_file_qualitative_reach_simplified(config, output_path, override_param_bounds=current_bounds)
            elif has_unsafe and not has_target:
                # Safety specification
                if target_probability < 1.0:
                    generator.generate_smt_file_quantitative_safety_simplified(config, output_path, override_param_bounds=current_bounds)
                else:
                    raise NotImplementedError("Qualitative safety (target_probability = 1) not yet implemented")
            else:
                raise ValueError("Must specify either 'target_region' or 'unsafe_region', but not both")

            generator.generate_config_file(entailment_solver, degree, smt_solver, output_path, config_path, temp_output_path)

            print(f"{indent}Running PolyQnt solver...")
            try:
                is_sat, model = execute(formula=output_path, config=config_path)
            except Exception as e:
                print(f"{indent}Error: {e}")
                return []

            print(f"{indent}Result: {is_sat}")

            if is_sat == 'sat':
                computation_time = time.time() - region_start_time
                print(f"{indent}✓ SAT region found! (time: {computation_time:.2f}s)")
                return [{'param_bounds': current_bounds, 'model': model, 'is_sat': 'sat', 'computation_time': computation_time}]

            # UNSAT - check if children would be below threshold before splitting
            max_dim = max(range(len(current_bounds)),
                         key=lambda d: current_bounds[d][1] - current_bounds[d][0])
            left_bounds, right_bounds = split_bounds(current_bounds, max_dim)
            child_width = compute_width(left_bounds)

            if child_width <= threshold:
                print(f"{indent}✗ UNSAT - children would be below threshold ({child_width:.6f} <= {threshold})")
                return []

            print(f"{indent}Splitting region...")

            if refinement_mode == 1:
                # Mode 1: Parallel exploration of children
                print(f"{indent}Exploring both children in parallel...")
                with ThreadPoolExecutor(max_workers=2) as executor:
                    left_future = executor.submit(explore_region, left_bounds, depth + 1)
                    right_future = executor.submit(explore_region, right_bounds, depth + 1)
                    left_models = left_future.result()
                    right_models = right_future.result()
            else:
                # Mode 0: Serial exploration
                print(f"{indent}Exploring left child...")
                left_models = explore_region(left_bounds, depth + 1)
                print(f"{indent}Exploring right child...")
                right_models = explore_region(right_bounds, depth + 1)

            return left_models + right_models

        models = explore_region(initial_param_bounds)
        timed_out_regions = []

    else:
        # Anytime algorithm with timeout: use queue-based approach
        from collections import deque

        # Queue of regions to explore: (bounds, depth)
        queue = deque([(initial_param_bounds, 0)])
        sat_regions = []
        timed_out_regions = []

        while queue:
            current_bounds, depth = queue.popleft()
            indent = "  " * depth
            width = compute_width(current_bounds)

            print(f"{indent}Exploring region: {current_bounds} (width: {width:.6f})")

            file_id = get_unique_file_id()
            region_start_time = time.time()

            # Run SMT query with timeout using multiprocessing.Process
            print(f"{indent}Running PolyQnt solver (timeout: {cutoff_time}s)...")

            args = (current_bounds, config, entailment_solver, degree, smt_solver, file_id)

            # Create a queue to get results from the subprocess
            result_queue = multiprocessing.Queue()

            # Create and start the process
            process = multiprocessing.Process(
                target=_run_smt_query_worker,
                args=(args, result_queue)
            )
            process.start()

            # Wait for the process with timeout
            process.join(timeout=cutoff_time)

            if process.is_alive():
                # Process is still running after timeout - terminate it
                print(f"{indent}⏱ TIMEOUT - query exceeded {cutoff_time}s")
                process.terminate()
                process.join(timeout=1)  # Give it a moment to terminate
                if process.is_alive():
                    process.kill()  # Force kill if still alive
                    process.join()

                # If below threshold, mark as timed out; otherwise check if children would be below threshold
                if width <= threshold:
                    print(f"{indent}⏱ Region at minimum granularity - marking as timed out")
                    timed_out_regions.append({'param_bounds': current_bounds, 'depth': depth})
                else:
                    # Compute hypothetical child widths before splitting
                    max_dim = max(range(len(current_bounds)),
                                 key=lambda d: current_bounds[d][1] - current_bounds[d][0])
                    left_bounds, right_bounds = split_bounds(current_bounds, max_dim)
                    child_width = compute_width(left_bounds)  # Both children have same width after split

                    if child_width <= threshold:
                        # Children would be below threshold - mark current region as timed out instead
                        print(f"{indent}⏱ Children would be below threshold ({child_width:.6f} <= {threshold}) - marking as timed out")
                        timed_out_regions.append({'param_bounds': current_bounds, 'depth': depth})
                    else:
                        print(f"{indent}Splitting timed-out region to continue exploration...")
                        # Add children to queue
                        queue.append((left_bounds, depth + 1))
                        queue.append((right_bounds, depth + 1))
                continue

            # Process completed - get the result
            try:
                result = result_queue.get_nowait()
                if result[0] == 'success':
                    is_sat, model = result[1], result[2]
                else:
                    print(f"{indent}Error: {result[1]}")
                    continue
            except Exception as e:
                print(f"{indent}Error getting result: {e}")
                continue

            print(f"{indent}Result: {is_sat}")

            if is_sat == 'sat':
                computation_time = time.time() - region_start_time
                print(f"{indent}✓ SAT region found! (time: {computation_time:.2f}s)")
                sat_regions.append({'param_bounds': current_bounds, 'model': model, 'is_sat': 'sat', 'computation_time': computation_time})

            elif width <= threshold:
                print(f"{indent}✗ UNSAT (below threshold)")
                # Don't add children

            else:
                # Check if children would be below threshold before splitting
                max_dim = max(range(len(current_bounds)),
                             key=lambda d: current_bounds[d][1] - current_bounds[d][0])
                left_bounds, right_bounds = split_bounds(current_bounds, max_dim)
                child_width = compute_width(left_bounds)

                if child_width <= threshold:
                    print(f"{indent}✗ UNSAT - children would be below threshold ({child_width:.6f} <= {threshold})")
                    # Don't add children
                else:
                    print(f"{indent}Splitting region...")
                    # Add children to queue
                    queue.append((left_bounds, depth + 1))
                    queue.append((right_bounds, depth + 1))

        models = sat_regions

    angelic_regions = models
    angelic_timed_out = timed_out_regions

    print(f"\n{'='*80}")
    print(f"ANGELIC REFINEMENT COMPLETE")
    print(f"{'='*80}")
    print(f"Total angelic winning regions found: {len(angelic_regions)}")
    for i, model_info in enumerate(angelic_regions, 1):
        print(f"  Region {i}: {model_info['param_bounds']}")

    if angelic_timed_out:
        print(f"\nAngelic timed out regions: {len(angelic_timed_out)}")
        for i, region_info in enumerate(angelic_timed_out, 1):
            print(f"  Region {i}: {region_info['param_bounds']}")

    print(f"{'='*80}\n")

    # Now find demonic winning regions in the complement of angelic regions
    print(f"\n{'='*80}")
    print(f"DEMONIC REFINEMENT (DUAL PROBLEM)")
    print(f"{'='*80}")

    # Compute complement of angelic regions
    complement_regions = compute_complement_regions(angelic_regions, initial_param_bounds, threshold)

    if not complement_regions:
        print("No complement regions to explore (all parameters are angelic winning)")
        demonic_regions = []
        demonic_timed_out = []
    else:
        print(f"Complement regions to explore: {len(complement_regions)}")
        for i, (region, _) in enumerate(complement_regions, 1):
            print(f"  Region {i}: {region}")
        print(f"{'='*80}\n")

        # Explore complement regions for demonic winning using dual problem
        from collections import deque

        queue = deque(complement_regions)
        demonic_regions = []
        demonic_timed_out = []

        while queue:
            current_bounds, depth = queue.popleft()
            indent = "  " * depth
            width = compute_width(current_bounds)

            print(f"{indent}[Demonic] Exploring region: {current_bounds} (width: {width:.6f})")

            file_id = get_unique_file_id()

            if cutoff_time is None:
                # No timeout - run directly
                region_start_time = time.time()
                generator = SRSMGenerator()

                has_target = 'target_region' in config
                has_unsafe = 'unsafe_region' in config
                original_target_probability = config.get('target_probability', 1.0)
                dual_target_probability = 1.0 - original_target_probability

                dual_config = config.copy()
                dual_config['target_probability'] = dual_target_probability

                output_path = f"./tmp/temporary_dual_polyhorn_input_id{file_id}.smt2"
                config_path = f"./tmp/temporary_dual_polyhorn_config_id{file_id}.json"
                temp_output_path = f"./tmp/dual_polyhorn_temp_id{file_id}.txt"

                try:
                    if has_target and not has_unsafe:
                        generator.generate_smt_file_dual_reach_simplified(dual_config, output_path, override_param_bounds=current_bounds)
                    elif has_unsafe and not has_target:
                        generator.generate_smt_file_dual_safety_simplified(dual_config, output_path, override_param_bounds=current_bounds)

                    generator.generate_config_file(entailment_solver, degree, smt_solver, output_path, config_path, temp_output_path)

                    print(f"{indent}[Demonic] Running PolyQnt solver...")
                    is_sat, model = execute(formula=output_path, config=config_path)
                except Exception as e:
                    print(f"{indent}[Demonic] Error: {e}")
                    continue

                print(f"{indent}[Demonic] Result: {is_sat}")

                if is_sat == 'sat':
                    computation_time = time.time() - region_start_time
                    print(f"{indent}[Demonic] ✓ Demonic winning region found! ({computation_time:.2f}s)")
                    demonic_regions.append({'param_bounds': current_bounds, 'model': model, 'is_sat': 'sat', 'computation_time': computation_time})
                elif width <= threshold:
                    print(f"{indent}[Demonic] ✗ UNSAT (below threshold)")
                else:
                    # Check if children would be below threshold before splitting
                    max_dim = max(range(len(current_bounds)),
                                 key=lambda d: current_bounds[d][1] - current_bounds[d][0])
                    left_bounds, right_bounds = split_bounds(current_bounds, max_dim)
                    child_width = compute_width(left_bounds)

                    if child_width <= threshold:
                        print(f"{indent}[Demonic] ✗ UNSAT - children would be below threshold ({child_width:.6f} <= {threshold})")
                    else:
                        print(f"{indent}[Demonic] Splitting region...")
                        queue.append((left_bounds, depth + 1))
                        queue.append((right_bounds, depth + 1))
            else:
                # With timeout - use subprocess
                region_start_time = time.time()
                print(f"{indent}[Demonic] Running PolyQnt solver (timeout: {cutoff_time}s)...")

                args = (current_bounds, config, entailment_solver, degree, smt_solver, file_id)

                result_queue = multiprocessing.Queue()
                process = multiprocessing.Process(
                    target=_run_dual_smt_query_worker,
                    args=(args, result_queue)
                )
                process.start()
                process.join(timeout=cutoff_time)

                if process.is_alive():
                    print(f"{indent}[Demonic] ⏱ TIMEOUT - query exceeded {cutoff_time}s")
                    process.terminate()
                    process.join(timeout=1)
                    if process.is_alive():
                        process.kill()
                        process.join()

                    if width <= threshold:
                        print(f"{indent}[Demonic] ⏱ Region at minimum granularity - marking as timed out")
                        demonic_timed_out.append({'param_bounds': current_bounds, 'depth': depth})
                    else:
                        # Compute hypothetical child widths before splitting
                        max_dim = max(range(len(current_bounds)),
                                     key=lambda d: current_bounds[d][1] - current_bounds[d][0])
                        left_bounds, right_bounds = split_bounds(current_bounds, max_dim)
                        child_width = compute_width(left_bounds)

                        if child_width <= threshold:
                            # Children would be below threshold - mark current region as timed out instead
                            print(f"{indent}[Demonic] ⏱ Children would be below threshold ({child_width:.6f} <= {threshold}) - marking as timed out")
                            demonic_timed_out.append({'param_bounds': current_bounds, 'depth': depth})
                        else:
                            print(f"{indent}[Demonic] Splitting timed-out region...")
                            queue.append((left_bounds, depth + 1))
                            queue.append((right_bounds, depth + 1))
                    continue

                try:
                    result = result_queue.get_nowait()
                    if result[0] == 'success':
                        is_sat, model = result[1], result[2]
                    else:
                        print(f"{indent}[Demonic] Error: {result[1]}")
                        continue
                except Exception as e:
                    print(f"{indent}[Demonic] Error getting result: {e}")
                    continue

                print(f"{indent}[Demonic] Result: {is_sat}")

                if is_sat == 'sat':
                    computation_time = time.time() - region_start_time
                    print(f"{indent}[Demonic] ✓ Demonic winning region found! ({computation_time:.2f}s)")
                    demonic_regions.append({'param_bounds': current_bounds, 'model': model, 'is_sat': 'sat', 'computation_time': computation_time})
                elif width <= threshold:
                    print(f"{indent}[Demonic] ✗ UNSAT (below threshold)")
                else:
                    # Check if children would be below threshold before splitting
                    max_dim = max(range(len(current_bounds)),
                                 key=lambda d: current_bounds[d][1] - current_bounds[d][0])
                    left_bounds, right_bounds = split_bounds(current_bounds, max_dim)
                    child_width = compute_width(left_bounds)

                    if child_width <= threshold:
                        print(f"{indent}[Demonic] ✗ UNSAT - children would be below threshold ({child_width:.6f} <= {threshold})")
                    else:
                        print(f"{indent}[Demonic] Splitting region...")
                        queue.append((left_bounds, depth + 1))
                        queue.append((right_bounds, depth + 1))

    print(f"\n{'='*80}")
    print(f"DEMONIC REFINEMENT COMPLETE")
    print(f"{'='*80}")
    print(f"Total demonic winning regions found: {len(demonic_regions)}")
    for i, model_info in enumerate(demonic_regions, 1):
        print(f"  Region {i}: {model_info['param_bounds']}")

    if demonic_timed_out:
        print(f"\nDemonic timed out regions: {len(demonic_timed_out)}")
        for i, region_info in enumerate(demonic_timed_out, 1):
            print(f"  Region {i}: {region_info['param_bounds']}")

    print(f"{'='*80}\n")

    # Combine timed out regions from both angelic and demonic phases
    all_timed_out = angelic_timed_out + demonic_timed_out

    # Filter out timed-out regions that are contained in any winning region
    # (angelic or demonic). These are not truly inconclusive since we know
    # their status from the containing winning region.
    all_winning = angelic_regions + demonic_regions
    truly_inconclusive = filter_contained_regions(all_timed_out, all_winning)

    if len(all_timed_out) != len(truly_inconclusive):
        print(f"Filtered out {len(all_timed_out) - len(truly_inconclusive)} timed-out regions "
              f"that are contained in winning regions.")

    # Merge adjacent inconclusive regions
    merged_inconclusive = merge_adjacent_regions(truly_inconclusive)

    if len(truly_inconclusive) != len(merged_inconclusive):
        print(f"Merged {len(truly_inconclusive)} inconclusive regions into {len(merged_inconclusive)} regions.")

    return angelic_regions, demonic_regions, merged_inconclusive


def main():
    """Main entry point."""
    if len(sys.argv) != 2:
        print("Usage: python param_synthesis.py <config.json>")
        sys.exit(1)

    config_file = sys.argv[1]
    config = load_config(config_file)

    degree = config['degree']
    smt_solver = config['smt_solver']
    entailment_solver = config['entailment_solver']
    output_path = config.get('output_smt_path', './tmp/temporary_polyhorn_input.smt2')

    param_vars = config['system'].get('param_vars', [])
    refinement_mode_raw = config.get('refinement_mode', None)

    # Handle "none"/"None" string as no refinement
    if refinement_mode_raw is None or (isinstance(refinement_mode_raw, str) and refinement_mode_raw.lower() == 'none'):
        refinement_mode = None
    else:
        refinement_mode = int(refinement_mode_raw)

    # Run parameter space refinement if refinement_mode is specified (0, 1, or 2)
    if refinement_mode is not None and param_vars and 'param_bounds' in config['system']:
        print("Parameter space refinement enabled.")
        threshold = config.get('param_refinement_threshold', 0.01)
        cutoff_time = config.get('cutoff_time_per_smt_query', None)

        mode_descriptions = {
            0: "MODE 0: Sequential (angelic first, then demonic on complement)",
            1: "MODE 1: Parallel children exploration (angelic first, then demonic)",
            2: "MODE 2: Parallel angelic/demonic per region",
            3: "MODE 3: Epsilon-based parallel angelic/demonic per region"
        }
        print(mode_descriptions.get(refinement_mode, f"Unknown mode: {refinement_mode}"))

        if cutoff_time is not None:
            print(f"Cutoff time per SMT query: {cutoff_time} seconds")

        max_inconclusive = None
        overall_timeout = None
        if refinement_mode == 3:
            max_inconclusive_raw = config.get('max_inconclusive', None)
            if max_inconclusive_raw is None:
                raise ValueError("'max_inconclusive' is required when refinement_mode is 3")
            # Support both scalar and vector
            if isinstance(max_inconclusive_raw, (list, tuple)):
                max_inconclusive = [float(x) for x in max_inconclusive_raw]
                print(f"Max inconclusive fractions: {max_inconclusive}")
            else:
                max_inconclusive = float(max_inconclusive_raw)
                print(f"Max inconclusive fraction: {max_inconclusive}")
            overall_timeout_raw = config.get('overall_timeout', None)
            if overall_timeout_raw is not None:
                overall_timeout = float(overall_timeout_raw)
                print(f"Overall timeout: {overall_timeout} seconds")

        start_time = time.time()

        result = refine_parameter_space(
            config, entailment_solver, degree, smt_solver, threshold, refinement_mode, cutoff_time,
            max_inconclusive=max_inconclusive, overall_timeout=overall_timeout
        )

        if refinement_mode == 3:
            angelic_regions, demonic_regions, timed_out_regions, snapshots = result
        else:
            angelic_regions, demonic_regions, timed_out_regions = result
            snapshots = None

        runtime = time.time() - start_time

        print(f"\n{'='*80}")
        print("FINAL SUMMARY")
        print(f"{'='*80}")
        print(f"Total runtime: {runtime:.2f} seconds")

        print(f"\n--- ANGELIC WINNING REGIONS ---")
        print(f"(Exists controller satisfying the specification)")
        print(f"Total: {len(angelic_regions)}")

        for i, model_info in enumerate(angelic_regions, 1):
            print(f"\n  Region {i}:")
            print(f"    Parameter bounds: {model_info['param_bounds']}")
            print(f"    Certificate: {model_info['model']}")

        print(f"\n--- DEMONIC WINNING REGIONS ---")
        print(f"(For all controllers, the dual specification is satisfied)")
        print(f"Total: {len(demonic_regions)}")

        for i, model_info in enumerate(demonic_regions, 1):
            print(f"\n  Region {i}:")
            print(f"    Parameter bounds: {model_info['param_bounds']}")
            print(f"    Certificate: {model_info['model']}")

        if timed_out_regions:
            print(f"\n--- INCONCLUSIVE REGIONS (Merged) ---")
            print(f"(Neither angelic nor demonic winning determined)")
            print(f"Total: {len(timed_out_regions)}")
            for i, region_info in enumerate(timed_out_regions, 1):
                print(f"  Region {i}: {region_info['param_bounds']}")

        print(f"\n{'='*80}\n")

        # Write to logfile if specified
        logfile = config.get('logfile')
        if logfile:
            if refinement_mode == 3 and snapshots:
                # Write each snapshot as a separate experiment entry
                for epsilon, snap_angelic, snap_demonic, snap_inconclusive, snap_runtime in snapshots:
                    write_to_logfile(logfile, config_file, snap_angelic, snap_demonic,
                                   snap_inconclusive, snap_runtime, refinement_mode,
                                   epsilon=epsilon)
            else:
                write_to_logfile(logfile, config_file, angelic_regions, demonic_regions,
                               timed_out_regions, runtime, refinement_mode)

        return angelic_regions, demonic_regions, timed_out_regions

    else:
        # Create tmp directory if it doesn't exist
        os.makedirs('./tmp', exist_ok=True)

        # Check for cutoff time (even without refinement)
        cutoff_time_config = config.get('cutoff_time_per_smt_query', None)
        # Handle "none" string as no cutoff
        if cutoff_time_config == "none" or cutoff_time_config is None:
            cutoff_time = None
        else:
            cutoff_time = float(cutoff_time_config)

        generator = SRSMGenerator()

        has_target = 'target_region' in config
        has_unsafe = 'unsafe_region' in config
        target_probability = config.get('target_probability', 1.0)
        mode = config.get('mode', 'angelic')  # Default to angelic mode

        # Check for demonic mode
        if mode == 'demonic':
            print("Running in DEMONIC mode (dual problem).")
            # For demonic mode, use dual probability
            dual_target_probability = 1.0 - target_probability
            dual_config = config.copy()
            dual_config['target_probability'] = dual_target_probability

            if has_target and not has_unsafe:
                # Dual reachability specification
                print(f"Generating SMT file for dual reachability (probability: {dual_target_probability})...")
                generator.generate_smt_file_dual_reach_simplified(dual_config, output_path)
            elif has_unsafe and not has_target:
                # Dual safety specification
                print(f"Generating SMT file for dual safety (probability: {dual_target_probability})...")
                generator.generate_smt_file_dual_safety_simplified(dual_config, output_path)
            else:
                raise ValueError("Must specify either 'target_region' or 'unsafe_region', but not both")
        else:
            # Angelic mode (default)
            # Determine which type of specification
            if has_target and not has_unsafe:
                # Reachability specification
                if target_probability < 1.0:
                    print(f"Generating SMT file for quantitative reachability (probability: {target_probability})...")
                    generator.generate_smt_file_quantitative_reach_simplified(config, output_path)
                else:
                    print(f"Generating SMT file for qualitative (almost-sure) reachability...")
                    generator.generate_smt_file_qualitative_reach_simplified(config, output_path)
            elif has_unsafe and not has_target:
                # Safety specification
                if target_probability < 1.0:
                    print(f"Generating SMT file for quantitative safety (probability: {target_probability})...")
                    generator.generate_smt_file_quantitative_safety_simplified(config, output_path)
                else:
                    raise NotImplementedError("Qualitative safety (target_probability = 1) not yet implemented")
            else:
                raise ValueError("Must specify either 'target_region' or 'unsafe_region', but not both")

        config_path = "./tmp/temporary_polyhorn_config.json"
        generator.generate_config_file(entailment_solver, degree, smt_solver, output_path, config_path)

        print("\nGeneration complete!")
        print(f"  SMT file: {output_path}")
        print(f"  Config file: {config_path}")

        if cutoff_time is None:
            # No timeout - run directly and track time
            print("\nExecuting PolyQnt solver...")
            start_time = time.time()
            is_sat, model = execute(formula=output_path, config=config_path)
            elapsed_time = time.time() - start_time
        else:
            # With timeout - use subprocess
            print(f"\nExecuting PolyQnt solver (timeout: {cutoff_time}s)...")
            result_queue = multiprocessing.Queue()

            def run_solver():
                try:
                    is_sat, model = execute(formula=output_path, config=config_path)
                    result_queue.put(('success', is_sat, model))
                except Exception as e:
                    result_queue.put(('error', str(e), None))

            process = multiprocessing.Process(target=run_solver)
            process.start()
            process.join(timeout=cutoff_time)

            if process.is_alive():
                # Timeout - terminate the process
                print(f"\n⏱ TIMEOUT - query exceeded {cutoff_time}s")
                process.terminate()
                process.join(timeout=1)
                if process.is_alive():
                    process.kill()
                    process.join()
                print("Result: TIMEOUT")
                return None

            # Process completed - get the result
            try:
                result = result_queue.get_nowait()
                if result[0] == 'success':
                    is_sat, model = result[1], result[2]
                else:
                    print(f"Error: {result[1]}")
                    return None
            except Exception as e:
                print(f"Error getting result: {e}")
                return None

        print("\nis_sat:")
        print(is_sat)
        if is_sat == 'sat':
            print("\nmodel:")
            print(model)

        # Report time if no cutoff was used
        if cutoff_time is None:
            print(f"\nTime taken: {elapsed_time:.2f} seconds")

        return 1


if __name__ == "__main__":
    main()