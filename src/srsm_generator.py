import json
import sys
import re
from typing import List, Dict, Any, Tuple
from polyqent.main import execute


class SRSMGenerator:
    def __init__(self):
        self.constants = []
        self.constant_counter = 1
        self.system_type = None
        
    def new_constant(self, prefix="C"):
        """Generate a new constant name."""
        name = f"{prefix}_{self.constant_counter}"
        self.constant_counter += 1
        self.constants.append(name)
        return name
    
    # ============================================================================
    # POLYNOMIAL GENERATION
    # ============================================================================
    
    def generate_polynomial_template(self, variables: List[str], degree: int, prefix: str) -> str:
        """
        Generate a polynomial template of given degree with unknown coefficients.
        For variables [x], degree 1: c1 + c2*x
        For variables [x], degree 2: c1 + c2*x + c3*x*x
        """
        if degree == 0:
            return self.new_constant(prefix)
        
        terms = []
        for d in range(degree + 1):
            for powers in self.generate_power_combinations(len(variables), d):
                coeff = self.new_constant(prefix)
                
                if d == 0:
                    terms.append(coeff)
                else:
                    monomial_vars = []
                    for i, var in enumerate(variables):
                        monomial_vars.extend([var] * powers[i])
                    
                    if len(monomial_vars) == 0:
                        terms.append(coeff)
                    elif len(monomial_vars) == 1:
                        terms.append(f"(* {coeff} {monomial_vars[0]})")
                    else:
                        var_prod = monomial_vars[0]
                        for v in monomial_vars[1:]:
                            var_prod = f"(* {var_prod} {v})"
                        terms.append(f"(* {coeff} {var_prod})")
        
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
    
    # ============================================================================
    # CONSTRAINT PARSING AND MANIPULATION
    # ============================================================================
    
    def parse_condition(self, condition_str: str, state_vars: List[str]) -> str:
        """
        Parse a condition string into SMT format.
        Handles chained comparisons like "0 <= S1 <= 100"
        """
        condition_str = condition_str.strip()
        
        # Handle chained comparisons: "a <= x <= b"
        chained_pattern = r'([\d.e+-]+)\s*(<=|>=|<|>)\s*(\w+)\s*(<=|>=|<|>)\s*([\d.e+-]+)'
        match = re.match(chained_pattern, condition_str)
        if match:
            lower_val, lower_op, var, upper_op, upper_val = match.groups()
            
            # Convert to two separate conditions
            op_map = {'<=': '>=', '>=': '<=', '<': '>', '>': '<'}
            cond1 = f"({op_map[lower_op]} {var} {lower_val})"
            cond2 = f"({upper_op} {var} {upper_val})"
            
            return f"(and {cond1} {cond2})"
        
        # Handle 'and' combinations
        if ' and ' in condition_str.lower():
            parts = re.split(r'\s+and\s+', condition_str, flags=re.IGNORECASE)
            parsed_parts = [self.parse_condition(part.strip(), state_vars) for part in parts]
            return f"(and {' '.join(parsed_parts)})"
        
        # Parse single condition
        patterns = [
            (r'(\w+)\s*(<=)\s*([\d.e+-]+)', lambda m: f"(<= {m.group(1)} {m.group(3)})"),
            (r'(\w+)\s*(>=)\s*([\d.e+-]+)', lambda m: f"(>= {m.group(1)} {m.group(3)})"),
            (r'(\w+)\s*(<)\s*([\d.e+-]+)', lambda m: f"(< {m.group(1)} {m.group(3)})"),
            (r'(\w+)\s*(>)\s*([\d.e+-]+)', lambda m: f"(> {m.group(1)} {m.group(3)})"),
            (r'(\w+)\s*(=)\s*([\d.e+-]+)', lambda m: f"(= {m.group(1)} {m.group(3)})"),
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
    
    def parse_cartesian_bounds(self, bounds: List[Tuple[float, float]], vars: List[str]) -> str:
        """Generate constraints for Cartesian bounds."""
        constraints = []
        for i, (lower, upper) in enumerate(bounds):
            constraints.append(f"(>= {vars[i]} {lower})")
            constraints.append(f"(<= {vars[i]} {upper})")
        
        if len(constraints) == 0:
            return None
        if len(constraints) == 1:
            return constraints[0]
        return f"(and {' '.join(constraints)})"
    
    def parse_region(self, region: Dict[str, Any], vars: List[str]) -> str:
        """Parse a region specification."""
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
    
    def negate_cartesian_bounds(self, bounds: List[Tuple[float, float]], vars: List[str]) -> List[str]:
        """Generate constraints for complement of a Cartesian region."""
        negated_constraints = []
        for i, (lower, upper) in enumerate(bounds):
            negated_constraints.append(f"(< {vars[i]} {lower})")
            negated_constraints.append(f"(> {vars[i]} {upper})")
        return negated_constraints
    
    def get_noise_bounds(self, noise_vars: List[str], noise_dist: Dict[str, Any]) -> str:
        """Generate bounds for noise variables."""
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
        
        return None
    
    def combine_constraints(self, *constraints) -> str:
        """Combine multiple constraints with 'and'."""
        valid_constraints = [c for c in constraints if c is not None]
        
        if len(valid_constraints) == 0:
            return None
        if len(valid_constraints) == 1:
            return valid_constraints[0]
        return f"(and {' '.join(valid_constraints)})"
    
    # ============================================================================
    # SATISFIABILITY CHECKING
    # ============================================================================
    
    def extract_bounds_from_constraint(self, constraint: str, var: str) -> Tuple[float, float]:
        """Extract lower and upper bounds for a variable from a constraint."""
        lower = float('-inf')
        upper = float('inf')
        
        # Find all constraints involving this variable
        # Pattern for >= constraints
        for match in re.finditer(r'\(>=\s+' + var + r'\s+([\d.e+-]+)\)', constraint):
            lower = max(lower, float(match.group(1)))
        
        # Pattern for <= constraints
        for match in re.finditer(r'\(<=\s+' + var + r'\s+([\d.e+-]+)\)', constraint):
            upper = min(upper, float(match.group(1)))
        
        # Pattern for > constraints (strict)
        for match in re.finditer(r'\(>\s+' + var + r'\s+([\d.e+-]+)\)', constraint):
            val = float(match.group(1))
            lower = max(lower, val)
        
        # Pattern for < constraints (strict)
        for match in re.finditer(r'\(<\s+' + var + r'\s+([\d.e+-]+)\)', constraint):
            val = float(match.group(1))
            upper = min(upper, val)
        
        return (lower, upper)
    
    def is_satisfiable_combination(self, *constraints) -> bool:
        """Check if a combination of constraints is satisfiable."""
        if not constraints:
            return True
        
        valid_constraints = [c for c in constraints if c is not None]
        if not valid_constraints:
            return True
        
        combined = self.combine_constraints(*valid_constraints)
        if combined is None:
            return True
        
        # Extract all state variables (assume uppercase start)
        variables = set(re.findall(r'\b[A-Z]\w*\b', combined))
        
        for var in variables:
            lower, upper = self.extract_bounds_from_constraint(combined, var)
            
            # Clear contradiction: lower > upper
            if lower > upper:
                return False
            
            # Check if bounds create an empty set (lower == upper with strict inequality)
            if lower == upper:
                has_strict_lower = f'(> {var} {lower}' in combined
                has_nonstrict_upper = f'(<= {var} {upper}' in combined
                has_strict_upper = f'(< {var} {upper}' in combined
                has_nonstrict_lower = f'(>= {var} {lower}' in combined
                
                if has_strict_lower and has_nonstrict_upper:
                    return False
                if has_strict_upper and has_nonstrict_lower:
                    return False
                if has_strict_lower and has_strict_upper:
                    return False
            
            # Check if the constraint requires values outside valid range
            # If lower == upper (from state bounds), and we have strict inequality going beyond
            # For example: S1 <= 150 (from state bounds) and S1 > 150 creates empty set
            # This is caught by lower > upper above, but let's also check explicit violations
            
            # Additional check: if we have a strict inequality that's at the boundary
            # e.g., S1 > 150 when state bound is S1 <= 150
            if f'(> {var} ' in combined:
                # Find all strict lower bounds
                for match in re.finditer(r'\(>\s+' + var + r'\s+([\d.e+-]+)\)', combined):
                    strict_lower = float(match.group(1))
                    # If we also have an upper bound at or below this, it's unsatisfiable
                    if upper <= strict_lower:
                        return False
            
            if f'(< {var} ' in combined:
                # Find all strict upper bounds
                for match in re.finditer(r'\(<\s+' + var + r'\s+([\d.e+-]+)\)', combined):
                    strict_upper = float(match.group(1))
                    # If we also have a lower bound at or above this, it's unsatisfiable
                    if lower >= strict_upper:
                        return False
        
        return True
    
    # ============================================================================
    # DYNAMICS AND SUBSTITUTION
    # ============================================================================
    
    def fix_dynamics_expression(self, expr: str) -> str:
        """Fix malformed expressions like (* W1) to (* 1 W1)."""
        return re.sub(r'\(\*\s+(\w+)\)', r'(* 1 \1)', expr)
    
    def generate_dynamics_expression(self, dynamics: Any, state_vars: List[str],
                                    control_expr: str, noise_vars: List[str]) -> Any:
        """Generate expressions for next state variables."""
        if isinstance(dynamics, dict) and 'expressions' in dynamics:
            exprs = dynamics['expressions']
            fixed_exprs = {}
            for var, expr in exprs.items():
                fixed_exprs[var] = self.fix_dynamics_expression(expr)
            return fixed_exprs
        elif isinstance(dynamics, list):
            piecewise = []
            for piece in dynamics:
                condition = piece['condition']
                transforms = piece.get('transforms', piece.get('expressions', {}))
                
                parsed_condition = self.parse_condition(condition, state_vars)
                
                fixed_transforms = {}
                for var, expr in transforms.items():
                    fixed_transforms[var] = self.fix_dynamics_expression(expr)
                
                piecewise.append({
                    'condition': parsed_condition,
                    'transforms': fixed_transforms
                })
            return piecewise
        else:
            raise ValueError("Dynamics must specify 'expressions' or be a list")
    
    def compute_expected_value(self, expr: str, noise_vars: List[str], 
                              noise_dist: Dict[str, Any]) -> str:
        """Compute expected value by replacing noise with means."""
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
            if new_expr.startswith('('):
                result = result.replace(var, new_expr)
            else:
                result = result.replace(var, f"({new_expr})")
        return result
    
    def format_var_decls(self, vars: List[str]) -> str:
        """Format variable declarations."""
        return ' '.join([f"({var} Real)" for var in vars])
    
    # ============================================================================
    # FORMULA GENERATION SUBROUTINES
    # ============================================================================
    
    def generate_formula_initial_invariant(self, state_vars: List[str], state_bounds_constraint: str,
                                          initial_constraint: str, I_expr: str) -> str:
        """Formula 1: Initial states satisfy invariant."""
        lhs = self.combine_constraints(state_bounds_constraint, initial_constraint)
        return f"(forall ({self.format_var_decls(state_vars)}) (=> {lhs} (>= {I_expr} 0)))"
    
    def generate_formulas_invariant_preservation(self, state_vars: List[str], noise_vars: List[str],
                                                 state_bounds_constraint: str, noise_bounds_constraint: str,
                                                 I_expr: str, next_state_exprs: Any) -> List[str]:
        """Formula 2: Invariant is preserved."""
        formulas = []
        
        if isinstance(next_state_exprs, dict):
            I_next = self.substitute_vars(I_expr, next_state_exprs)
            all_vars = state_vars + noise_vars
            lhs = self.combine_constraints(state_bounds_constraint, noise_bounds_constraint, f"(>= {I_expr} 0)")
            formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {I_next} 0)))"
            formulas.append(formula)
        else:
            for piece in next_state_exprs:
                condition = piece['condition']
                transforms = piece['transforms']
                I_next = self.substitute_vars(I_expr, transforms)
                
                all_vars = state_vars + noise_vars
                lhs = self.combine_constraints(state_bounds_constraint, noise_bounds_constraint, 
                                              condition, f"(>= {I_expr} 0)")
                formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {I_next} 0)))"
                formulas.append(formula)
        
        return formulas
    
    def generate_formula_v_nonnegative(self, state_vars: List[str], state_bounds_constraint: str,
                                      I_expr: str, V_expr: str) -> str:
        """Formula 3: V is non-negative on invariant."""
        lhs = self.combine_constraints(state_bounds_constraint, f"(>= {I_expr} 0)")
        return f"(forall ({self.format_var_decls(state_vars)}) (=> {lhs} (>= {V_expr} 0)))"
    
    def generate_formula_epsilon_positive(self, epsilon: str) -> str:
        """Formula 4: Epsilon is positive."""
        min_float = "1.0e-15"
        return f"(>= (+ (* 1 {epsilon}) (* -1 {min_float})) 0)"
    
    def generate_formulas_expected_decrease(self, state_vars: List[str], noise_vars: List[str],
                                           state_bounds_constraint: str, target_bounds: List[Tuple[float, float]],
                                           I_expr: str, V_expr: str, epsilon: str,
                                           next_state_exprs: Any, noise_distribution: Dict[str, Any],
                                           v_upper_bound: str = None) -> List[str]:
        """Formula 5: Expected decrease outside target (with optional V upper bound)."""
        formulas = []
        
        if isinstance(next_state_exprs, dict):
            V_next = self.substitute_vars(V_expr, next_state_exprs)
            
            if noise_vars:
                E_V_next = self.compute_expected_value(V_next, noise_vars, noise_distribution)
            else:
                E_V_next = V_next
            
            decrease = f"(- {V_expr} {E_V_next})"
            
            not_target_cases = self.negate_cartesian_bounds(target_bounds, state_vars)
            for not_target_constraint in not_target_cases:
                if not self.is_satisfiable_combination(state_bounds_constraint, not_target_constraint):
                    continue
                
                # Build LHS with optional V upper bound constraint
                lhs_constraints = [state_bounds_constraint, not_target_constraint, f"(>= {I_expr} 0)"]
                if v_upper_bound:
                    lhs_constraints.append(f"(<= {V_expr} {v_upper_bound})")
                
                lhs = self.combine_constraints(*lhs_constraints)
                formula = f"(forall ({self.format_var_decls(state_vars)}) (=> {lhs} (>= {decrease} {epsilon})))"
                formulas.append(formula)
        else:
            for piece in next_state_exprs:
                condition = piece['condition']
                transforms = piece['transforms']
                V_next = self.substitute_vars(V_expr, transforms)
                
                if noise_vars:
                    E_V_next = self.compute_expected_value(V_next, noise_vars, noise_distribution)
                else:
                    E_V_next = V_next
                
                decrease = f"(- {V_expr} {E_V_next})"
                
                not_target_cases = self.negate_cartesian_bounds(target_bounds, state_vars)
                
                for not_target_constraint in not_target_cases:
                    if not self.is_satisfiable_combination(state_bounds_constraint, condition, not_target_constraint):
                        continue
                    
                    # Build LHS with optional V upper bound constraint
                    lhs_constraints = [state_bounds_constraint, condition, not_target_constraint, f"(>= {I_expr} 0)"]
                    if v_upper_bound:
                        lhs_constraints.append(f"(<= {V_expr} {v_upper_bound})")
                    
                    lhs = self.combine_constraints(*lhs_constraints)
                    formula = f"(forall ({self.format_var_decls(state_vars)}) (=> {lhs} (>= {decrease} {epsilon})))"
                    formulas.append(formula)
        
        return formulas
    
    def generate_formula_initial_value_bound(self, state_vars: List[str], state_bounds_constraint: str,
                                            initial_constraint: str, I_expr: str, V_expr: str,
                                            upper_bound: str) -> str:
        """Formula 6: Initial value of V is bounded."""
        lhs = self.combine_constraints(state_bounds_constraint, f"(>= {I_expr} 0)", initial_constraint)
        return f"(forall ({self.format_var_decls(state_vars)}) (=> {lhs} (<= {V_expr} {upper_bound})))"
    
    # ============================================================================
    # MAIN SMT GENERATION FUNCTIONS
    # ============================================================================
    
    def generate_smt_file_almost_sure_reach(self, config: Dict[str, Any], 
                                            output_path: str = "./tmp/temporary_polyhorn_input.smt2"):
        """Generate SMT2 file for almost-sure reachability (probability = 1)."""
        
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
        initial_constraint = self.parse_region(initial_region, state_vars)
        target_bounds = target_region['bounds']
        
        # Generate dynamics
        next_state_exprs = self.generate_dynamics_expression(dynamics, state_vars, C_expr, noise_vars)
        
        # Generate formulas
        formulas = []
        
        # Formula 1: Initial states satisfy invariant
        formulas.append(self.generate_formula_initial_invariant(
            state_vars, state_bounds_constraint, initial_constraint, I_expr))
        
        # Formula 2: Invariant is preserved
        formulas.extend(self.generate_formulas_invariant_preservation(
            state_vars, noise_vars, state_bounds_constraint, noise_bounds_constraint, I_expr, next_state_exprs))
        
        # Formula 3: V is non-negative on invariant
        formulas.append(self.generate_formula_v_nonnegative(
            state_vars, state_bounds_constraint, I_expr, V_expr))
        
        # Formula 4: Epsilon is positive
        formulas.append(self.generate_formula_epsilon_positive(epsilon))
        
        # Formula 5: Expected decrease (no V upper bound for almost-sure)
        formulas.extend(self.generate_formulas_expected_decrease(
            state_vars, noise_vars, state_bounds_constraint, target_bounds,
            I_expr, V_expr, epsilon, next_state_exprs, noise_distribution, v_upper_bound=None))
        
        # Write SMT2 file
        self._write_smt_file(output_path, formulas)
        
        print(f"Generated SMT2 file (almost-sure reachability): {output_path}")
        return output_path
    
    def generate_smt_file_quantitative_reach(self, config: Dict[str, Any], 
                                             output_path: str = "./tmp/temporary_polyhorn_input.smt2"):
        """Generate SMT2 file for quantitative reachability (probability < 1)."""
        
        system_type = config['system']['type']
        self.system_type = system_type
        
        degree = config['degree']
        state_vars = config['system']['state_vars']
        noise_vars = config['system'].get('noise_vars', [])
        state_bounds = config['system'].get('state_bounds', [])
        target_probability = config.get('target_probability', 1.0)
        
        if target_probability <= 0 or target_probability > 1:
            raise ValueError("target_probability must be in (0, 1]")
        
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
        initial_constraint = self.parse_region(initial_region, state_vars)
        target_bounds = target_region['bounds']
        
        # Calculate V upper bound: 1 / (1 - p)
        if target_probability < 1.0:
            v_upper_bound = str(1.0 / (1.0 - target_probability))
        else:
            v_upper_bound = None
        
        # Generate dynamics
        next_state_exprs = self.generate_dynamics_expression(dynamics, state_vars, C_expr, noise_vars)
        
        # Generate formulas
        formulas = []
        
        # Formula 1: Initial states satisfy invariant
        formulas.append(self.generate_formula_initial_invariant(
            state_vars, state_bounds_constraint, initial_constraint, I_expr))
        
        # Formula 2: Invariant is preserved
        formulas.extend(self.generate_formulas_invariant_preservation(
            state_vars, noise_vars, state_bounds_constraint, noise_bounds_constraint, I_expr, next_state_exprs))
        
        # Formula 3: V is non-negative on invariant
        formulas.append(self.generate_formula_v_nonnegative(
            state_vars, state_bounds_constraint, I_expr, V_expr))
        
        # Formula 4: Epsilon is positive
        formulas.append(self.generate_formula_epsilon_positive(epsilon))
        
        # Formula 5: Expected decrease with V upper bound
        formulas.extend(self.generate_formulas_expected_decrease(
            state_vars, noise_vars, state_bounds_constraint, target_bounds,
            I_expr, V_expr, epsilon, next_state_exprs, noise_distribution, v_upper_bound=v_upper_bound))
        
        # Formula 6: Initial value bound
        formulas.append(self.generate_formula_initial_value_bound(
            state_vars, state_bounds_constraint, initial_constraint, I_expr, V_expr, "1"))
        
        # Write SMT2 file
        self._write_smt_file(output_path, formulas)
        
        print(f"Generated SMT2 file (quantitative reachability, p={target_probability}): {output_path}")
        return output_path
    
    def _write_smt_file(self, output_path: str, formulas: List[str]):
        """Helper to write formulas to SMT2 file."""
        with open(output_path, 'w') as f:
            for const in self.constants:
                f.write(f"(declare-const {const} Real)\n")
            f.write("\n")
            
            for formula in formulas:
                f.write(f"(assert {formula})\n")
            f.write("\n")
            
            f.write("(check-sat)\n")
            f.write("(get-model)\n")
    
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
    target_probability = config.get('target_probability', 1.0)
    
    generator = SRSMGenerator()
    
    # Choose appropriate generation method based on target_probability
    if target_probability >= 1.0:
        print("Generating SMT file for almost-sure reachability...")
        generator.generate_smt_file_almost_sure_reach(config, output_path)
    else:
        print(f"Generating SMT file for quantitative reachability (p={target_probability})...")
        generator.generate_smt_file_quantitative_reach(config, output_path)
    
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