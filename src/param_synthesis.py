import json
import sys
from typing import List, Dict, Any, Tuple
from polyqent.main import execute
from srsm_generator import SRSMGenerator

def load_config(filepath: str) -> Dict[str, Any]:
    """Load configuration from JSON file."""
    with open(filepath, 'r') as f:
        return json.load(f)


def refine_parameter_space(config: Dict[str, Any], entailment_solver: str, 
                          degree: int, smt_solver: str, threshold: float = 0.01) -> List[Dict]:
    """Iteratively refine parameter space to find all SAT regions."""
    
    if 'param_bounds' not in config['system']:
        raise ValueError("Parameter space refinement requires 'param_bounds'")
    
    initial_param_bounds = config['system']['param_bounds']
    param_vars = config['system'].get('param_vars', [])
    
    if not param_vars:
        raise ValueError("No parameter variables specified")
    
    print(f"\n{'='*80}")
    print(f"PARAMETER SPACE REFINEMENT")
    print(f"{'='*80}")
    print(f"Initial parameter bounds: {initial_param_bounds}")
    print(f"Refinement threshold: {threshold}")
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
    
    def explore_region(current_bounds: List[Tuple[float, float]], depth: int = 0) -> List[Dict]:
        """Recursively explore parameter region."""
        indent = "  " * depth
        width = compute_width(current_bounds)

        print(f"{indent}Exploring region: {current_bounds} (width: {width:.6f})")

        generator = SRSMGenerator()
        output_path = f"./tmp/temporary_polyhorn_input_depth{depth}.smt2"

        has_target = 'target_region' in config
        has_unsafe = 'unsafe_region' in config
        target_probability = config.get('target_probability', 1.0)

        # Determine which type of specification
        if has_target and not has_unsafe:
            # Reachability specification
            if target_probability < 1.0:
                generator.generate_smt_file_quantitative_reach(config, output_path, override_param_bounds=current_bounds)
            else:
                generator.generate_smt_file_almost_sure_reach(config, output_path, override_param_bounds=current_bounds)
        elif has_unsafe and not has_target:
            # Safety specification
            if target_probability < 1.0:
                generator.generate_smt_file_quantitative_safety(config, output_path, override_param_bounds=current_bounds)
            else:
                raise NotImplementedError("Qualitative safety (target_probability = 1) not yet implemented")
        else:
            raise ValueError("Must specify either 'target_region' or 'unsafe_region', but not both")

        config_path = f"./tmp/temporary_polyhorn_config_depth{depth}.json"
        generator.generate_config_file(entailment_solver, degree, smt_solver, output_path)
        
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
            
            print(f"{indent}Exploring left child...")
            left_models = explore_region(left_bounds, depth + 1)
            
            print(f"{indent}Exploring right child...")
            right_models = explore_region(right_bounds, depth + 1)
            
            return left_models + right_models
    
    models = explore_region(initial_param_bounds)
    
    print(f"\n{'='*80}")
    print(f"REFINEMENT COMPLETE")
    print(f"{'='*80}")
    print(f"Total SAT regions found: {len(models)}")
    for i, model_info in enumerate(models, 1):
        print(f"  Region {i}: {model_info['param_bounds']}")
    print(f"{'='*80}\n")
    
    return models


def main():
    """Main entry point."""
    if len(sys.argv) != 2:
        print("Usage: python srsm_generator.py <config.json>")
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
        models = refine_parameter_space(config, entailment_solver, degree, smt_solver, threshold)
        
        print(f"\n{'='*80}")
        print("FINAL SUMMARY")
        print(f"{'='*80}")
        print(f"Total satisfiable regions: {len(models)}")
        
        for i, model_info in enumerate(models, 1):
            print(f"\nRegion {i}:")
            print(f"  Parameter bounds: {model_info['param_bounds']}")
            print(f"  Model: {models[i-1]['model']}")
        print(f"{'='*80}\n")
        
        return models
    
    else:
        generator = SRSMGenerator()

        has_target = 'target_region' in config
        has_unsafe = 'unsafe_region' in config
        target_probability = config.get('target_probability', 1.0)

        # Determine which type of specification
        if has_target and not has_unsafe:
            # Reachability specification
            if target_probability < 1.0:
                print(f"Generating SMT file for quantitative reachability (probability: {target_probability})...")
                generator.generate_smt_file_quantitative_reach(config, output_path)
            else:
                print("Generating SMT file for almost-sure reachability...")
                generator.generate_smt_file_quantitative_reach(config, output_path)
        elif has_unsafe and not has_target:
            # Safety specification
            if target_probability < 1.0:
                print(f"Generating SMT file for quantitative safety (probability: {target_probability})...")
                generator.generate_smt_file_quantitative_safety(config, output_path)
            else:
                print("Generating SMT file for qualitative safety...")
                raise NotImplementedError("Qualitative safety (target_probability = 1) not yet implemented")
        else:
            raise ValueError("Must specify either 'target_region' or 'unsafe_region', but not both")

        generator.generate_config_file(entailment_solver, degree, smt_solver, output_path)
        
        print("\nGeneration complete!")
        print(f"  SMT file: {output_path}")
        print(f"  Config file: ./tmp/temporary_polyhorn_config.json")
        
        print("\nExecuting PolyQnt solver...")
        is_sat, model = execute(
            formula="./tmp/temporary_polyhorn_input.smt2",
            config="./tmp/temporary_polyhorn_config.json",
        )
        
        print("\nis_sat:")
        print(is_sat)
        if is_sat == 'sat':
            print("\nmodel:")
            print(model)
        return 1


if __name__ == "__main__":
    main()