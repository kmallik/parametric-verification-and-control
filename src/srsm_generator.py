import json
import sys
import itertools
from typing import List, Dict, Any, Tuple
import math
from polyqent.main import execute

class SRSMGenerator:
    def __init__(self):
        self.constants = []
        self.constant_counter = 1
        self.system_type = None  # 'cartesian' or 'finite'
        
    def new_constant(self, prefix="C"):
        """Generate a new constant name."""
        name = f"{prefix}_{self.constant_counter}"
        self.constant_counter += 1
        self.constants.append(name)
        return name
    
    def generate_polynomial_template(self, variables: List[str], degree: int, prefix: str) -> str:
        """
        Generate a polynomial template of given degree with unknown coefficients.
        For variables [x], degree 1: c1 + c2*x
        For variables [x], degree 2: c1 + c2*x + c3*x*x
        """
        if degree == 0:
            return self.new_constant(prefix)
        
        terms = []
        
        # Generate all monomials up to the given degree
        for d in range(degree + 1):
            for powers in self.generate_power_combinations(len(variables), d):
                coeff = self.new_constant(prefix)
                
                if d == 0:
                    # Constant term
                    terms.append(coeff)
                else:
                    # Build monomial from powers
                    monomial_vars = []
                    for i, var in enumerate(variables):
                        for _ in range(powers[i]):
                            monomial_vars.append(var)
                    
                    if len(monomial_vars) == 0:
                        terms.append(coeff)
                    elif len(monomial_vars) == 1:
                        # c * x
                        terms.append(f"(* {coeff} {monomial_vars[0]})")
                    else:
                        # c * x * y * z...
                        # Build nested multiplication for variables
                        var_prod = monomial_vars[0]
                        for v in monomial_vars[1:]:
                            var_prod = f"(* {var_prod} {v})"
                        terms.append(f"(* {coeff} {var_prod})")
        
        # Build sum of all terms
        if len(terms) == 0:
            return "0"
        elif len(terms) == 1:
            return terms[0]
        else:
            return f"(+ {' '.join(terms)})"
    
    def generate_power_combinations(self, num_vars: int, total_degree: int) -> List[List[int]]:
        """Generate all combinations of powers that sum to total_degree."""
        if num_vars == 1:
            return [[total_degree]]
        
        combinations = []
        for i in range(total_degree + 1):
            for rest in self.generate_power_combinations(num_vars - 1, total_degree - i):
                combinations.append([i] + rest)
        
        return combinations
    
    def parse_cartesian_bounds(self, bounds: List[Tuple[float, float]], vars: List[str]) -> str:
        """Generate constraints for Cartesian bounds: lower <= var <= upper."""
        constraints = []
        for i, (lower, upper) in enumerate(bounds):
            constraints.append(f"(>= {vars[i]} {lower})")
            constraints.append(f"(<= {vars[i]} {upper})")
        
        if len(constraints) == 0:
            return None
        if len(constraints) == 1:
            return constraints[0]
        return f"(and {' '.join(constraints)})"
    
    def get_noise_bounds(self, noise_vars: List[str], noise_dist: Dict[str, Any]) -> str:
        """Generate bounds for noise variables based on distribution."""
        if not noise_vars:
            return None
        
        dist_type = noise_dist.get('type', 'uniform')
        
        if dist_type == 'uniform':
            lower_bounds = noise_dist['params']['lower']
            upper_bounds = noise_dist['params']['upper']
            
            constraints = []
            for i, noise_var in enumerate(noise_vars):
                constraints.append(f"(>= {noise_var} {lower_bounds[i]})")
                constraints.append(f"(<= {noise_var} {upper_bounds[i]})")
            
            if len(constraints) == 1:
                return constraints[0]
            return f"(and {' '.join(constraints)})"
        elif dist_type == 'normal':
            # For normal distribution, we don't have hard bounds
            # Could add soft bounds based on mean +/- k*sigma if needed
            return None
        else:
            return None
    
    def parse_region(self, region: Dict[str, Any], vars: List[str]) -> str:
        """Parse a region specification (hyper-rectangle)."""
        if 'bounds' in region:
            return self.parse_cartesian_bounds(region['bounds'], vars)
        elif 'states' in region:
            states = region['states']
            if len(states) == 1:
                return f"(= state {states[0]})"
            else:
                disjuncts = [f"(= state {s})" for s in states]
                return f"(or {' '.join(disjuncts)})"
        else:
            raise ValueError("Region must specify either 'bounds' or 'states'")
    
    def negate_cartesian_bounds(self, bounds: List[Tuple[float, float]], 
                                vars: List[str]) -> List[str]:
        """
        Generate constraints for the complement of a Cartesian region.
        Returns a list of constraint strings, one for each case.
        """
        negated_constraints = []
        for i, (lower, upper) in enumerate(bounds):
            # Case: below lower bound
            negated_constraints.append(f"(< {vars[i]} {lower})")
            # Case: above upper bound  
            negated_constraints.append(f"(> {vars[i]} {upper})")
        
        return negated_constraints
    
    def combine_constraints(self, *constraints) -> str:
        """Combine multiple constraints with 'and', filtering out None values."""
        valid_constraints = [c for c in constraints if c is not None]
        
        if len(valid_constraints) == 0:
            return None
        if len(valid_constraints) == 1:
            return valid_constraints[0]
        return f"(and {' '.join(valid_constraints)})"
    
    def fix_dynamics_expression(self, expr: str) -> str:
        """Fix malformed expressions like (* W1) to (* 1 W1)."""
        import re
        # Fix pattern (* W) to (* 1 W) where W is a single token
        fixed_expr = re.sub(r'\(\*\s+(\w+)\)', r'(* 1 \1)', expr)
        return fixed_expr
    
    def parse_condition(self, condition_str: str, state_vars: List[str]) -> str:
        """
        Parse a condition string like "S1 <= 100" into SMT format.
        Supports: <=, >=, <, >, =, and combinations with 'and'
        """
        import re
        
        # Handle 'and' combinations
        if ' and ' in condition_str.lower():
            parts = re.split(r'\s+and\s+', condition_str, flags=re.IGNORECASE)
            parsed_parts = [self.parse_condition(part.strip(), state_vars) for part in parts]
            return f"(and {' '.join(parsed_parts)})"
        
        # Parse single condition: var op value or value op var
        condition_str = condition_str.strip()
        
        # Match patterns like "S1 <= 100" or "100 <= S1"
        patterns = [
            (r'(\w+)\s*(<=)\s*([\d.e+-]+)', lambda m: f"(<= {m.group(1)} {m.group(3)})"),
            (r'(\w+)\s*(>=)\s*([\d.e+-]+)', lambda m: f"(>= {m.group(1)} {m.group(3)})"),
            (r'(\w+)\s*(<)\s*([\d.e+-]+)', lambda m: f"(< {m.group(1)} {m.group(3)})"),
            (r'(\w+)\s*(>)\s*([\d.e+-]+)', lambda m: f"(> {m.group(1)} {m.group(3)})"),
            (r'(\w+)\s*(=)\s*([\d.e+-]+)', lambda m: f"(= {m.group(1)} {m.group(3)})"),
            # Reverse: value op var
            (r'([\d.e+-]+)\s*(<=)\s*(\w+)', lambda m: f"(>= {m.group(3)} {m.group(1)})"),
            (r'([\d.e+-]+)\s*(>=)\s*(\w+)', lambda m: f"(<= {m.group(3)} {m.group(1)})"),
            (r'([\d.e+-]+)\s*(<)\s*(\w+)', lambda m: f"(> {m.group(3)} {m.group(1)})"),
            (r'([\d.e+-]+)\s*(>)\s*(\w+)', lambda m: f"(< {m.group(3)} {m.group(1)})"),
        ]
        
        for pattern, formatter in patterns:
            match = re.match(pattern, condition_str)
            if match:
                return formatter(match)
        
        raise ValueError(f"Could not parse condition: {condition_str}")
    
    def generate_dynamics_expression(self, dynamics: Any, 
                                    state_vars: List[str],
                                    control_expr: str,
                                    noise_vars: List[str]) -> Any:
        """
        Generate expressions for next state variables.
        Returns either:
        - Dict[str, str] for simple dynamics
        - List[Dict] for piecewise dynamics with conditions
        """
        if isinstance(dynamics, dict) and 'expressions' in dynamics:
            # Old format: simple dynamics
            exprs = dynamics['expressions']
            fixed_exprs = {}
            for var, expr in exprs.items():
                fixed_exprs[var] = self.fix_dynamics_expression(expr)
            return fixed_exprs
        elif isinstance(dynamics, list):
            # New format: piecewise dynamics
            piecewise = []
            for piece in dynamics:
                condition = piece['condition']
                transforms = piece.get('transforms', piece.get('expressions', {}))
                
                # Parse condition
                parsed_condition = self.parse_condition(condition, state_vars)
                
                # Fix expressions
                fixed_transforms = {}
                for var, expr in transforms.items():
                    fixed_transforms[var] = self.fix_dynamics_expression(expr)
                
                piecewise.append({
                    'condition': parsed_condition,
                    'transforms': fixed_transforms
                })
            return piecewise
        else:
            raise ValueError("Dynamics must specify either 'expressions' (dict) or be a list of piecewise cases")
    
    def compute_expected_value(self, expr: str, noise_vars: List[str], 
                              noise_dist: Dict[str, Any]) -> str:
        """
        Compute expected value by replacing noise variables with their means.
        For Uniform[a, b]: mean = (a+b)/2
        For Normal(mu, sigma): mean = mu
        """
        dist_type = noise_dist.get('type', 'uniform')
        
        if dist_type == 'uniform':
            lower_bounds = noise_dist['params']['lower']
            upper_bounds = noise_dist['params']['upper']
            
            result = expr
            for i, noise_var in enumerate(noise_vars):
                mean = (lower_bounds[i] + upper_bounds[i]) / 2.0
                result = result.replace(noise_var, str(mean))
            
            return result
        elif dist_type == 'normal':
            means = noise_dist['params'].get('mean', [0] * len(noise_vars))
            result = expr
            for i, noise_var in enumerate(noise_vars):
                result = result.replace(noise_var, str(means[i]))
            return result
        else:
            raise ValueError(f"Unsupported distribution type: {dist_type}")
    
    def substitute_vars(self, expr: str, var_map: Dict[str, str]) -> str:
        """Substitute variables in expression."""
        result = expr
        for var, new_expr in var_map.items():
            # Only add parens if new_expr doesn't already have them
            if new_expr.startswith('('):
                result = result.replace(var, new_expr)
            else:
                result = result.replace(var, f"({new_expr})")
        return result
    
    def format_var_decls(self, vars: List[str]) -> str:
        """Format variable declarations: ((x Real) (y Real))."""
        return ' '.join([f"({var} Real)" for var in vars])
    
    def generate_smt_file(self, config: Dict[str, Any], output_path: str = "./tmp/temporary_polyhorn_input.smt2"):
        """Generate the SMT2 file with entailment constraints."""
        
        system_type = config['system']['type']
        self.system_type = system_type
        
        degree = config['degree']
        state_vars = config['system']['state_vars']
        noise_vars = config['system'].get('noise_vars', [])
        state_bounds = config['system'].get('state_bounds', [])
        
        initial_region = config['system']['initial_region']
        target_region = config['target_region']
        dynamics = config['system']['dynamics']
        noise_distribution = config['system'].get('noise_distribution', {})
        
        # Generate polynomial templates
        V_expr = self.generate_polynomial_template(state_vars, degree, "V")
        I_expr = self.generate_polynomial_template(state_vars, degree, "I")
        C_expr = self.generate_polynomial_template(state_vars, degree, "C")
        epsilon = self.new_constant("Epsilon")
        
        # Get bounds constraints
        state_bounds_constraint = self.parse_cartesian_bounds(state_bounds, state_vars) if state_bounds else None
        noise_bounds_constraint = self.get_noise_bounds(noise_vars, noise_distribution)
        
        # Build formulas
        formulas = []
        
        # 1. Initial states satisfy invariant
        initial_constraint = self.parse_region(initial_region, state_vars)
        lhs1 = self.combine_constraints(state_bounds_constraint, initial_constraint)
        formula1 = f"(forall ({self.format_var_decls(state_vars)}) (=> {lhs1} (>= {I_expr} 0)))"
        formulas.append(formula1)
        
        # 2. Invariant is preserved
        next_state_exprs = self.generate_dynamics_expression(dynamics, state_vars, C_expr, noise_vars)
        
        if isinstance(next_state_exprs, dict):
            # Simple dynamics
            I_next = self.substitute_vars(I_expr, next_state_exprs)
            all_vars = state_vars + noise_vars
            lhs2 = self.combine_constraints(state_bounds_constraint, noise_bounds_constraint, f"(>= {I_expr} 0)")
            formula2 = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs2} (>= {I_next} 0)))"
            formulas.append(formula2)
        else:
            # Piecewise dynamics - one formula per piece
            for piece in next_state_exprs:
                condition = piece['condition']
                transforms = piece['transforms']
                I_next = self.substitute_vars(I_expr, transforms)
                
                all_vars = state_vars + noise_vars
                lhs2 = self.combine_constraints(state_bounds_constraint, noise_bounds_constraint, condition, f"(>= {I_expr} 0)")
                formula2 = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs2} (>= {I_next} 0)))"
                formulas.append(formula2)
        
        # 3. V is non-negative on invariant
        lhs3 = self.combine_constraints(state_bounds_constraint, f"(>= {I_expr} 0)")
        formula3 = f"(forall ({self.format_var_decls(state_vars)}) (=> {lhs3} (>= {V_expr} 0)))"
        formulas.append(formula3)
        
        # 4. Epsilon is positive
        min_float = "1.0e-15"
        formula4 = f"(>= (+ (* 1 {epsilon}) (* -1 {min_float})) 0)"
        formulas.append(formula4)
        
        # 5. Expected decrease (only OUTSIDE target region)
        not_target_cases = self.negate_cartesian_bounds(target_region['bounds'], state_vars)
        
        if isinstance(next_state_exprs, dict):
            # Simple dynamics - no quantification over noise needed for expected value
            V_next = self.substitute_vars(V_expr, next_state_exprs)
            
            if noise_vars:
                E_V_next = self.compute_expected_value(V_next, noise_vars, noise_distribution)
            else:
                E_V_next = V_next
            
            decrease = f"(- {V_expr} {E_V_next})"
            
            for not_target_constraint in not_target_cases:
                lhs5 = self.combine_constraints(state_bounds_constraint, not_target_constraint, f"(>= {I_expr} 0)")
                # No quantification over noise - we've taken expectation
                formula5 = f"(forall ({self.format_var_decls(state_vars)}) (=> {lhs5} (>= {decrease} {epsilon})))"
                formulas.append(formula5)
        else:
            # Piecewise dynamics
            for piece in next_state_exprs:
                condition = piece['condition']
                transforms = piece['transforms']
                V_next = self.substitute_vars(V_expr, transforms)
                
                if noise_vars:
                    E_V_next = self.compute_expected_value(V_next, noise_vars, noise_distribution)
                else:
                    E_V_next = V_next
                
                decrease = f"(- {V_expr} {E_V_next})"
                
                for not_target_constraint in not_target_cases:
                    lhs5 = self.combine_constraints(state_bounds_constraint, condition, not_target_constraint, f"(>= {I_expr} 0)")
                    # No quantification over noise - we've taken expectation
                    formula5 = f"(forall ({self.format_var_decls(state_vars)}) (=> {lhs5} (>= {decrease} {epsilon})))"
                    formulas.append(formula5)
        
        # Write SMT2 file
        with open(output_path, 'w') as f:
            for const in self.constants:
                f.write(f"(declare-const {const} Real)\n")
            f.write("\n")
            
            for formula in formulas:
                f.write(f"(assert {formula})\n")
            f.write("\n")
            
            f.write("(check-sat)\n")
            f.write("(get-model)\n")
        
        print(f"Generated SMT2 file: {output_path}")
        return output_path
    
    def generate_config_file(self, theorem_name: str, degree: int, solver_name: str,
                           smt_output_path: str = "./tmp/temporary_polyhorn_input.smt2"):
        """Generate the configuration JSON file."""
        config = {
            "theorem_name": theorem_name,
            "degree_of_sat": degree,
            "degree_of_nonstrict_unsat": 0,
            "degree_of_strict_unsat": 0,
            "max_d_of_strict": 0,
            "solver_name": solver_name,
            "output_path": "./tmp/polyhorn_temp.txt",
            "unsat_core_heuristic": False,
            "SAT_heuristic": True,
            "integer_arithmetic": False
        }
        
        config_path = "./tmp/temporary_polyhorn_config.json"
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        
        print(f"Generated config file: {config_path}")
        return config_path

def load_config(filepath: str) -> Dict[str, Any]:
    """Load configuration from JSON file."""
    with open(filepath, 'r') as f:
        return json.load(f)

def main():
    if len(sys.argv) != 2:
        print("Usage: python srsm_generator.py <config.json>")
        sys.exit(1)
    
    config_file = sys.argv[1]
    config = load_config(config_file)
    
    degree = config['degree']
    smt_solver = config['smt_solver']
    entailment_solver = config['entailment_solver']
    output_path = config.get('output_smt_path', './tmp/temporary_polyhorn_input.smt2')
    
    generator = SRSMGenerator()
    generator.generate_smt_file(config, output_path)
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