import json
import sys
import os
import time
import threading
import multiprocessing
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
                          parallel: bool = False,
                          cutoff_time: Optional[float] = None) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """Iteratively refine parameter space to find angelic and demonic winning regions.

    First finds angelic winning regions (exists controller satisfying spec).
    Then finds demonic winning regions in the complement (for all controllers, dual spec satisfied).

    Args:
        config: Configuration dictionary
        entailment_solver: Solver for entailment checking
        degree: Polynomial degree
        smt_solver: SMT solver to use
        threshold: Refinement threshold
        parallel: If True, explore children in parallel; if False, use serial exploration
        cutoff_time: Optional timeout in seconds for each SMT query. If None, no timeout.

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

    # If no cutoff time, use the original recursive approach
    if cutoff_time is None:
        def explore_region(current_bounds: List[Tuple[float, float]], depth: int = 0) -> List[Dict]:
            """Recursively explore parameter region."""
            indent = "  " * depth
            width = compute_width(current_bounds)

            print(f"{indent}Exploring region: {current_bounds} (width: {width:.6f})")

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
                print(f"{indent}✓ SAT region found!")
                return [{'param_bounds': current_bounds, 'model': model, 'is_sat': 'sat'}]

            elif width <= threshold:
                print(f"{indent}✗ UNSAT (below threshold)")
                return []

            else:
                print(f"{indent}Splitting region...")
                max_dim = max(range(len(current_bounds)),
                             key=lambda d: current_bounds[d][1] - current_bounds[d][0])

                left_bounds, right_bounds = split_bounds(current_bounds, max_dim)

                if parallel:
                    # Explore both children in parallel
                    print(f"{indent}Exploring both children in parallel...")
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        left_future = executor.submit(explore_region, left_bounds, depth + 1)
                        right_future = executor.submit(explore_region, right_bounds, depth + 1)

                        left_models = left_future.result()
                        right_models = right_future.result()
                else:
                    # Serial exploration
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

                # If below threshold, mark as timed out; otherwise split and continue
                if width <= threshold:
                    print(f"{indent}⏱ Region at minimum granularity - marking as timed out")
                    timed_out_regions.append({'param_bounds': current_bounds, 'depth': depth})
                else:
                    print(f"{indent}Splitting timed-out region to continue exploration...")
                    max_dim = max(range(len(current_bounds)),
                                 key=lambda d: current_bounds[d][1] - current_bounds[d][0])

                    left_bounds, right_bounds = split_bounds(current_bounds, max_dim)

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
                print(f"{indent}✓ SAT region found!")
                sat_regions.append({'param_bounds': current_bounds, 'model': model, 'is_sat': 'sat'})

            elif width <= threshold:
                print(f"{indent}✗ UNSAT (below threshold)")
                # Don't add children

            else:
                print(f"{indent}Splitting region...")
                max_dim = max(range(len(current_bounds)),
                             key=lambda d: current_bounds[d][1] - current_bounds[d][0])

                left_bounds, right_bounds = split_bounds(current_bounds, max_dim)

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
                    print(f"{indent}[Demonic] ✓ Demonic winning region found!")
                    demonic_regions.append({'param_bounds': current_bounds, 'model': model, 'is_sat': 'sat'})
                elif width <= threshold:
                    print(f"{indent}[Demonic] ✗ UNSAT (below threshold)")
                else:
                    print(f"{indent}[Demonic] Splitting region...")
                    max_dim = max(range(len(current_bounds)),
                                 key=lambda d: current_bounds[d][1] - current_bounds[d][0])
                    left_bounds, right_bounds = split_bounds(current_bounds, max_dim)
                    queue.append((left_bounds, depth + 1))
                    queue.append((right_bounds, depth + 1))
            else:
                # With timeout - use subprocess
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
                        print(f"{indent}[Demonic] Splitting timed-out region...")
                        max_dim = max(range(len(current_bounds)),
                                     key=lambda d: current_bounds[d][1] - current_bounds[d][0])
                        left_bounds, right_bounds = split_bounds(current_bounds, max_dim)
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
                    print(f"{indent}[Demonic] ✓ Demonic winning region found!")
                    demonic_regions.append({'param_bounds': current_bounds, 'model': model, 'is_sat': 'sat'})
                elif width <= threshold:
                    print(f"{indent}[Demonic] ✗ UNSAT (below threshold)")
                else:
                    print(f"{indent}[Demonic] Splitting region...")
                    max_dim = max(range(len(current_bounds)),
                                 key=lambda d: current_bounds[d][1] - current_bounds[d][0])
                    left_bounds, right_bounds = split_bounds(current_bounds, max_dim)
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

    return angelic_regions, demonic_regions, truly_inconclusive


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
    enable_refinement = config.get('enable_param_refinement', False)

    if param_vars and 'param_bounds' in config['system'] and enable_refinement:
        print("Parameter space refinement enabled.")
        threshold = config.get('param_refinement_threshold', 0.01)
        parallel = config.get('parallel_refinement', False)
        cutoff_time = config.get('cutoff_time_per_smt_query', None)

        if parallel:
            print("Using parallel refinement mode.")
        else:
            print("Using serial refinement mode.")

        if cutoff_time is not None:
            print(f"Anytime mode enabled with cutoff time: {cutoff_time} seconds per query.")

        angelic_regions, demonic_regions, timed_out_regions = refine_parameter_space(
            config, entailment_solver, degree, smt_solver, threshold, parallel, cutoff_time
        )

        print(f"\n{'='*80}")
        print("FINAL SUMMARY")
        print(f"{'='*80}")

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
            print(f"\n--- INCONCLUSIVE REGIONS (Timed Out) ---")
            print(f"(Neither angelic nor demonic winning determined)")
            print(f"Total: {len(timed_out_regions)}")
            for i, region_info in enumerate(timed_out_regions, 1):
                print(f"  Region {i}: {region_info['param_bounds']}")

        print(f"\n{'='*80}\n")

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