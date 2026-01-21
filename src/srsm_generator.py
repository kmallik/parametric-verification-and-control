import json
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
        """Generate a polynomial template with binary operators only."""
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
        
        return self.build_binary_addition(terms)
    
    def build_binary_addition(self, terms: List[str]) -> str:
        """Build addition with binary + operators."""
        if len(terms) == 0:
            return "0"
        elif len(terms) == 1:
            return terms[0]
        elif len(terms) == 2:
            return f"(+ {terms[0]} {terms[1]})"
        else:
            result = f"(+ {terms[-2]} {terms[-1]})"
            for i in range(len(terms) - 3, -1, -1):
                result = f"(+ {terms[i]} {result})"
            return result
    
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
    # CONSTRAINT PARSING
    # ============================================================================
    
    def parse_condition(self, condition_str: str, state_vars: List[str]) -> str:
        """Parse condition string into SMT format."""
        condition_str = condition_str.strip()
        
        # Handle chained comparisons
        chained_pattern = r'([\d.e+-]+)\s*(<=|>=|<|>)\s*(\w+)\s*(<=|>=|<|>)\s*([\d.e+-]+)'
        match = re.match(chained_pattern, condition_str)
        if match:
            lower_val, lower_op, var, upper_op, upper_val = match.groups()
            op_map = {'<=': '>=', '>=': '<=', '<': '>', '>': '<'}
            cond1 = f"({op_map[lower_op]} {var} {lower_val})"
            cond2 = f"({upper_op} {var} {upper_val})"
            return f"(and {cond1} {cond2})"
        
        # Handle 'and' combinations
        if ' and ' in condition_str.lower():
            parts = re.split(r'\s+and\s+', condition_str, flags=re.IGNORECASE)
            parsed_parts = [self.parse_condition(part.strip(), state_vars) for part in parts]
            return self.combine_constraints(*parsed_parts)
        
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
        return self.combine_constraints(*constraints)
    
    def parse_region(self, region: Dict[str, Any], vars: List[str]) -> str:
        """Parse a region specification."""
        if 'bounds' in region:
            return self.parse_cartesian_bounds(region['bounds'], vars)
        else:
            raise ValueError("Region must specify 'bounds'")
    
    def negate_cartesian_bounds(self, bounds: List[Tuple[float, float]], vars: List[str]) -> List[str]:
        """Generate constraints for complement of region."""
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
            
            return self.combine_constraints(*constraints)
        
        return None
    
    def combine_constraints(self, *constraints) -> str:
        """Combine constraints with binary 'and' operators."""
        valid_constraints = [c for c in constraints if c is not None]
        
        if len(valid_constraints) == 0:
            return None
        if len(valid_constraints) == 1:
            return valid_constraints[0]
        elif len(valid_constraints) == 2:
            return f"(and {valid_constraints[0]} {valid_constraints[1]})"
        else:
            result = f"(and {valid_constraints[-2]} {valid_constraints[-1]})"
            for i in range(len(valid_constraints) - 3, -1, -1):
                result = f"(and {valid_constraints[i]} {result})"
            return result
    
    # ============================================================================
    # SATISFIABILITY CHECKING
    # ============================================================================
    
    def extract_bounds_from_constraint(self, constraint: str, var: str) -> Tuple[float, float]:
        """Extract lower and upper bounds for a variable."""
        lower = float('-inf')
        upper = float('inf')
        
        for match in re.finditer(r'\(>=\s+' + var + r'\s+([\d.e+-]+)\)', constraint):
            lower = max(lower, float(match.group(1)))
        
        for match in re.finditer(r'\(<=\s+' + var + r'\s+([\d.e+-]+)\)', constraint):
            upper = min(upper, float(match.group(1)))
        
        for match in re.finditer(r'\(>\s+' + var + r'\s+([\d.e+-]+)\)', constraint):
            val = float(match.group(1))
            lower = max(lower, val)
        
        for match in re.finditer(r'\(<\s+' + var + r'\s+([\d.e+-]+)\)', constraint):
            val = float(match.group(1))
            upper = min(upper, val)
        
        return (lower, upper)
    
    def is_satisfiable_combination(self, *constraints) -> bool:
        """Check if combination of constraints is satisfiable."""
        if not constraints:
            return True
        
        valid_constraints = [c for c in constraints if c is not None]
        if not valid_constraints:
            return True
        
        combined = self.combine_constraints(*valid_constraints)
        if combined is None:
            return True
        
        variables = set(re.findall(r'\b[A-Z]\w*\b', combined))
        
        for var in variables:
            lower, upper = self.extract_bounds_from_constraint(combined, var)
            
            if lower > upper:
                return False
            
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
            
            if f'(> {var} ' in combined:
                for match in re.finditer(r'\(>\s+' + var + r'\s+([\d.e+-]+)\)', combined):
                    strict_lower = float(match.group(1))
                    if upper <= strict_lower:
                        return False
            
            if f'(< {var} ' in combined:
                for match in re.finditer(r'\(<\s+' + var + r'\s+([\d.e+-]+)\)', combined):
                    strict_upper = float(match.group(1))
                    if lower >= strict_upper:
                        return False
        
        return True
    
    # ============================================================================
    # DYNAMICS AND SUBSTITUTION
    # ============================================================================
    
    def fix_dynamics_expression(self, expr: str) -> str:
        """Fix malformed expressions."""
        return re.sub(r'\(\*\s+(\w+)\)', r'(* 1 \1)', expr)
    
    def generate_dynamics_expression(self, dynamics: Any, state_vars: List[str],
                                    control_vars: List[str], noise_vars: List[str]) -> Any:
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
    
    def substitute_control(self, expr: str, control_vars: List[str], controller_exprs: Dict[str, str]) -> str:
        """Substitute control variables with controller expressions."""
        result = expr
        for control_var in control_vars:
            if control_var in controller_exprs:
                controller = controller_exprs[control_var]
                pattern = r'\b' + re.escape(control_var) + r'\b'
                result = re.sub(pattern, controller, result)
        return result
    
    def substitute_noise_with_bound(self, expr: str, noise_var: str, bound_value: float) -> str:
        """Substitute a specific noise variable with a bound value (lower or upper)."""
        pattern = r'\b' + re.escape(noise_var) + r'\b'
        return re.sub(pattern, str(bound_value), expr)

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
            pattern = r'\b' + re.escape(var) + r'\b'
            result = re.sub(pattern, new_expr, result)
        return result
    
    def format_var_decls(self, vars: List[str]) -> str:
        """Format variable declarations."""
        return ' '.join([f"({var} Real)" for var in vars])
    
    def validate_config(self, config: Dict[str, Any]):
        """Validate configuration."""
        has_target = 'target_region' in config
        has_unsafe = 'unsafe_region' in config
        
        if has_target and has_unsafe:
            raise ValueError("Cannot specify both 'target_region' and 'unsafe_region'.")
        
        if not has_target and not has_unsafe:
            raise ValueError("Must specify either 'target_region' or 'unsafe_region'.")
        
        if has_unsafe and 'target_probability' not in config:
            raise ValueError("For safety specifications, 'target_probability' is required.")
        
        control_vars = config['system'].get('control_vars', [])
        if control_vars:
            if 'control_bounds' not in config['system']:
                raise ValueError("When 'control_vars' are specified, 'control_bounds' must be provided.")
            
            control_bounds = config['system']['control_bounds']
            if len(control_bounds) != len(control_vars):
                raise ValueError(f"Number of control_bounds must match number of control_vars.")
    
    def detect_param_noise_multiplication(self, expr: str, param_vars: List[str],
                                          noise_vars: List[str]) -> List[Tuple[str, str]]:
        """Detect parameter*noise multiplication patterns in expression.

        Returns list of (param, noise) tuples found in the expression.
        """
        param_noise_pairs = []

        # Look for patterns like (* P W) or (* W P)
        for param in param_vars:
            for noise in noise_vars:
                pattern1 = f"(* {param} {noise})"
                pattern2 = f"(* {noise} {param})"
                if pattern1 in expr or pattern2 in expr:
                    param_noise_pairs.append((param, noise))

        return param_noise_pairs

    def validate_linearity_with_param_noise(self, expr: str, state_vars: List[str],
                                           control_vars: List[str], param_vars: List[str],
                                           noise_vars: List[str]) -> bool:
        """Validate that expression is linear except for allowed param*noise terms.

        Allowed: P*W terms
        Not allowed: S*S, S*U, S*P, U*U, U*P, W*W, P*P terms
        """
        # Check for state variable multiplications
        for s1 in state_vars:
            for s2 in state_vars:
                if f"(* {s1} {s2})" in expr or f"(* {s2} {s1})" in expr:
                    return False

        # Check for state*control multiplications
        for s in state_vars:
            for u in control_vars:
                if f"(* {s} {u})" in expr or f"(* {u} {s})" in expr:
                    return False

        # Check for state*param multiplications
        for s in state_vars:
            for p in param_vars:
                if f"(* {s} {p})" in expr or f"(* {p} {s})" in expr:
                    return False

        # Check for control*control multiplications
        for u1 in control_vars:
            for u2 in control_vars:
                if f"(* {u1} {u2})" in expr or f"(* {u2} {u1})" in expr:
                    return False

        # Check for control*param multiplications
        for u in control_vars:
            for p in param_vars:
                if f"(* {u} {p})" in expr or f"(* {p} {u})" in expr:
                    return False

        # Check for noise*noise multiplications (not allowed)
        for w1 in noise_vars:
            for w2 in noise_vars:
                if f"(* {w1} {w2})" in expr or f"(* {w2} {w1})" in expr:
                    return False

        # Check for param*param multiplications
        for p1 in param_vars:
            for p2 in param_vars:
                if f"(* {p1} {p2})" in expr or f"(* {p2} {p1})" in expr:
                    return False

        return True

    def validate_affine_disturbance(self, dynamics: Any, noise_vars: List[str]):
        """Validate affine disturbance for safety."""
        if not noise_vars:
            return True

        def check_transform(transform_expr: str) -> bool:
            for noise_var in noise_vars:
                if noise_var in transform_expr:
                    if f'(* {noise_var}' in transform_expr or f'* {noise_var})' in transform_expr:
                        return False
                    if f'(/ {noise_var}' in transform_expr or f'/ {noise_var})' in transform_expr:
                        return False
            return True

        if isinstance(dynamics, dict) and 'expressions' in dynamics:
            for var, expr in dynamics['expressions'].items():
                if not check_transform(expr):
                    raise ValueError(f"Disturbance must appear in affine form: {var} = g(...) + W. Got: {expr}")
        elif isinstance(dynamics, list):
            for piece in dynamics:
                transforms = piece.get('transforms', piece.get('expressions', {}))
                for var, expr in transforms.items():
                    if not check_transform(expr):
                        raise ValueError(f"Disturbance must appear in affine form: {var} = g(...) + W. Got: {expr}")

        return True
    
    # ============================================================================
    # NON-PARAMETRIC FORMULA GENERATION
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
        """Formula 5: Expected decrease outside target."""
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
        """Formula 6 (reachability): Initial value of V is bounded."""
        lhs = self.combine_constraints(state_bounds_constraint, f"(>= {I_expr} 0)", initial_constraint)
        return f"(forall ({self.format_var_decls(state_vars)}) (=> {lhs} (<= {V_expr} {upper_bound})))"

    def generate_formulas_control_bounds(self, state_vars: List[str], state_bounds_constraint: str,
                                        I_expr: str, control_vars: List[str], 
                                        controller_exprs: Dict[str, str],
                                        control_bounds: List[Tuple[float, float]]) -> List[str]:
        """Generate control bound formulas."""
        formulas = []
        
        for i, control_var in enumerate(control_vars):
            if control_var not in controller_exprs:
                continue
            
            controller = controller_exprs[control_var]
            u_min, u_max = control_bounds[i]
            
            lhs = self.combine_constraints(state_bounds_constraint, f"(>= {I_expr} 0)")
            rhs = f"(and (>= {controller} {u_min}) (<= {controller} {u_max}))"
            
            formula = f"(forall ({self.format_var_decls(state_vars)}) (=> {lhs} {rhs}))"
            formulas.append(formula)
        
        return formulas

    # ============================================================================
    # SIMPLIFIED PARAMETRIC FORMULA GENERATION (No Q constraints)
    # ============================================================================

    def generate_formula_initial_invariant_parametric_simplified(self, state_vars: List[str], param_vars: List[str],
                                                                 state_bounds_constraint: str, initial_constraint: str,
                                                                 param_bounds_constraint: str, I_expr: str) -> str:
        """Formula 1 (parametric simplified): Initial states satisfy invariant.

        Simplified version that uses parameter bounds instead of Q(P) >= 0.
        """
        all_vars = state_vars + param_vars
        lhs = self.combine_constraints(state_bounds_constraint, initial_constraint, param_bounds_constraint)
        return f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {I_expr} 0)))"

    def generate_formulas_invariant_preservation_parametric_simplified(self, state_vars: List[str], param_vars: List[str],
                                                                       noise_vars: List[str], state_bounds_constraint: str,
                                                                       param_bounds_constraint: str, noise_bounds_constraint: str,
                                                                       I_expr: str, next_state_exprs: Any,
                                                                       noise_distribution: Dict[str, Any] = None,
                                                                       use_param_noise_bounds: bool = False) -> List[str]:
        """Formula 2 (parametric simplified): Invariant is preserved by dynamics.

        Simplified version that uses parameter bounds instead of Q(P) >= 0.
        """
        formulas = []

        # Determine lower and upper bounds for noise variables
        lower_bounds = []
        upper_bounds = []
        if noise_distribution and noise_distribution.get('type') == 'uniform':
            params = noise_distribution.get('params', {})
            lower_bounds = params.get('lower', [])
            upper_bounds = params.get('upper', [])

        # Check if we should use param*noise corner point substitution
        if use_param_noise_bounds and len(lower_bounds) == len(noise_vars):
            # This is the param*noise multiplication case - use corner points

            if isinstance(next_state_exprs, list):
                # Piecewise dynamics
                for piece in next_state_exprs:
                    condition = piece['condition']
                    transforms = piece['transforms']

                    # For each transform in this piece, determine which noise variables it uses
                    transform_noise_usage = {}
                    for state_var, expr in transforms.items():
                        used_noise = set()
                        for noise_var in noise_vars:
                            if noise_var in expr:
                                used_noise.add(noise_var)
                        transform_noise_usage[state_var] = used_noise

                    # Check if any transform in this piece uses noise
                    has_any_noise = any(len(used) > 0 for used in transform_noise_usage.values())

                    if not has_any_noise:
                        # No noise in this piece, use standard formulation without noise variables
                        I_next = self.substitute_vars(I_expr, transforms)
                        all_vars = state_vars + param_vars
                        lhs = self.combine_constraints(state_bounds_constraint, param_bounds_constraint,
                                                      condition, f"(>= {I_expr} 0)")
                        formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {I_next} 0)))"
                        formulas.append(formula)
                    else:
                        # Find all unique sets of noise variables used in this piece
                        unique_noise_sets = set()
                        for used_noise in transform_noise_usage.values():
                            unique_noise_sets.add(frozenset(used_noise))

                        # For each unique set, generate formulas
                        for noise_set in unique_noise_sets:
                            noise_set_list = sorted(list(noise_set))
                            active_indices = [noise_vars.index(nv) for nv in noise_set_list]
                            n_active = len(active_indices)

                            if n_active == 0:
                                continue

                            # Generate all 2^n_active combinations
                            for i in range(2**n_active):
                                noise_substitutions = {}
                                for local_j, global_j in enumerate(active_indices):
                                    noise_var = noise_vars[global_j]
                                    if (i >> local_j) & 1:
                                        noise_substitutions[noise_var] = upper_bounds[global_j]
                                    else:
                                        noise_substitutions[noise_var] = lower_bounds[global_j]

                                # Apply substitutions only to transforms that use these noise variables
                                transforms_subst = {}
                                for state_var, expr in transforms.items():
                                    if transform_noise_usage[state_var] == set(noise_set_list):
                                        result_expr = expr
                                        for noise_var, bound_value in noise_substitutions.items():
                                            result_expr = self.substitute_noise_with_bound(result_expr, noise_var, bound_value)
                                        transforms_subst[state_var] = result_expr
                                    else:
                                        transforms_subst[state_var] = expr

                                I_next = self.substitute_vars(I_expr, transforms_subst)

                                all_vars = state_vars + param_vars
                                lhs = self.combine_constraints(state_bounds_constraint, param_bounds_constraint,
                                                              condition, f"(>= {I_expr} 0)")
                                formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {I_next} 0)))"
                                formulas.append(formula)
            else:
                # Single transform case with param*noise
                # Detect which noise variables are used
                used_noise = set()
                for noise_var in noise_vars:
                    for expr in next_state_exprs.values():
                        if noise_var in expr:
                            used_noise.add(noise_var)
                            break

                used_noise_list = sorted(list(used_noise))
                active_indices = [noise_vars.index(nv) for nv in used_noise_list]
                n_active = len(active_indices)

                # Generate all 2^n_active combinations
                for i in range(2**n_active):
                    noise_substitutions = {}
                    for local_j, global_j in enumerate(active_indices):
                        noise_var = noise_vars[global_j]
                        if (i >> local_j) & 1:
                            noise_substitutions[noise_var] = upper_bounds[global_j]
                        else:
                            noise_substitutions[noise_var] = lower_bounds[global_j]

                    # Apply substitutions
                    transforms_subst = {}
                    for state_var, expr in next_state_exprs.items():
                        result_expr = expr
                        for noise_var, bound_value in noise_substitutions.items():
                            result_expr = self.substitute_noise_with_bound(result_expr, noise_var, bound_value)
                        transforms_subst[state_var] = result_expr

                    I_next = self.substitute_vars(I_expr, transforms_subst)

                    all_vars = state_vars + param_vars
                    lhs = self.combine_constraints(state_bounds_constraint, param_bounds_constraint, f"(>= {I_expr} 0)")
                    formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {I_next} 0)))"
                    formulas.append(formula)
        else:
            # Standard case: quantify over noise
            if isinstance(next_state_exprs, list):
                # Piecewise dynamics
                for piece in next_state_exprs:
                    condition = piece['condition']
                    transforms = piece['transforms']
                    I_next = self.substitute_vars(I_expr, transforms)

                    all_vars = state_vars + param_vars + noise_vars
                    lhs = self.combine_constraints(state_bounds_constraint, param_bounds_constraint,
                                                  noise_bounds_constraint, condition, f"(>= {I_expr} 0)")
                    formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {I_next} 0)))"
                    formulas.append(formula)
            else:
                # Single transform
                I_next = self.substitute_vars(I_expr, next_state_exprs)
                all_vars = state_vars + param_vars + noise_vars
                lhs = self.combine_constraints(state_bounds_constraint, param_bounds_constraint,
                                              noise_bounds_constraint, f"(>= {I_expr} 0)")
                formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {I_next} 0)))"
                formulas.append(formula)

        return formulas

    def generate_formula_v_nonnegative_parametric_simplified(self, state_vars: List[str], param_vars: List[str],
                                                             state_bounds_constraint: str, param_bounds_constraint: str,
                                                             I_expr: str, V_expr: str) -> str:
        """Formula 3 (parametric simplified): V is non-negative on invariant.

        Simplified version that uses parameter bounds instead of Q(P) >= 0.
        """
        all_vars = state_vars + param_vars
        lhs = self.combine_constraints(state_bounds_constraint, param_bounds_constraint, f"(>= {I_expr} 0)")
        return f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {V_expr} 0)))"

    def generate_formulas_expected_decrease_parametric_simplified(self, state_vars: List[str], param_vars: List[str],
                                                                  noise_vars: List[str], state_bounds_constraint: str,
                                                                  param_bounds_constraint: str, target_bounds: List[Tuple[float, float]],
                                                                  I_expr: str, V_expr: str, epsilon: str,
                                                                  next_state_exprs: Any, noise_distribution: Dict[str, Any],
                                                                  v_upper_bound: str = None) -> List[str]:
        """Formula 5 (parametric simplified): Expected decrease outside target.

        Simplified version that uses parameter bounds instead of Q(P) >= 0.
        """
        formulas = []
        all_vars = state_vars + param_vars

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

                lhs_constraints = [state_bounds_constraint, param_bounds_constraint,
                                  not_target_constraint, f"(>= {I_expr} 0)"]
                if v_upper_bound:
                    lhs_constraints.append(f"(<= {V_expr} {v_upper_bound})")

                lhs = self.combine_constraints(*lhs_constraints)
                formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {decrease} {epsilon})))"
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

                    lhs_constraints = [state_bounds_constraint, param_bounds_constraint,
                                      condition, not_target_constraint, f"(>= {I_expr} 0)"]
                    if v_upper_bound:
                        lhs_constraints.append(f"(<= {V_expr} {v_upper_bound})")

                    lhs = self.combine_constraints(*lhs_constraints)
                    formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {decrease} {epsilon})))"
                    formulas.append(formula)

        return formulas

    def generate_formula_initial_value_bound_parametric_simplified(self, state_vars: List[str], param_vars: List[str],
                                                                   state_bounds_constraint: str, initial_constraint: str,
                                                                   param_bounds_constraint: str, I_expr: str,
                                                                   V_expr: str, upper_bound: str) -> str:
        """Formula 6 (parametric simplified): Initial value of V is bounded.

        Simplified version that uses parameter bounds instead of Q(P) >= 0.
        """
        all_vars = state_vars + param_vars
        lhs = self.combine_constraints(state_bounds_constraint, param_bounds_constraint,
                                       f"(>= {I_expr} 0)", initial_constraint)
        return f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (<= {V_expr} {upper_bound})))"

    def generate_formulas_expected_decrease_safety_parametric_simplified(self, state_vars: List[str], param_vars: List[str],
                                                                         noise_vars: List[str], state_bounds_constraint: str,
                                                                         param_bounds_constraint: str, I_expr: str,
                                                                         V_expr: str, next_state_exprs: Any,
                                                                         noise_distribution: Dict[str, Any],
                                                                         v_upper_bound: str = None) -> List[str]:
        """Formula 5 (parametric safety simplified): Expected decrease everywhere (no epsilon, no target check).

        Simplified version that uses parameter bounds instead of Q(P) >= 0.
        """
        formulas = []
        all_vars = state_vars + param_vars

        if isinstance(next_state_exprs, dict):
            V_next = self.substitute_vars(V_expr, next_state_exprs)

            if noise_vars:
                E_V_next = self.compute_expected_value(V_next, noise_vars, noise_distribution)
            else:
                E_V_next = V_next

            decrease = f"(- {V_expr} {E_V_next})"

            lhs_constraints = [state_bounds_constraint, param_bounds_constraint, f"(>= {I_expr} 0)"]
            if v_upper_bound:
                lhs_constraints.append(f"(<= {V_expr} {v_upper_bound})")

            lhs = self.combine_constraints(*lhs_constraints)
            formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {decrease} 0)))"
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

                lhs_constraints = [state_bounds_constraint, param_bounds_constraint, condition, f"(>= {I_expr} 0)"]
                if v_upper_bound:
                    lhs_constraints.append(f"(<= {V_expr} {v_upper_bound})")

                lhs = self.combine_constraints(*lhs_constraints)
                formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {decrease} 0)))"
                formulas.append(formula)

        return formulas

    def generate_formula_unsafe_lower_bound_parametric_simplified(self, state_vars: List[str], param_vars: List[str],
                                                                  state_bounds_constraint: str, param_bounds_constraint: str,
                                                                  unsafe_bounds: str, V_expr: str, lower_bound: str) -> str:
        """Formula for safety (parametric simplified): V is bounded from below in unsafe region.

        Simplified version that uses parameter bounds instead of Q(P) >= 0.
        """
        all_vars = state_vars + param_vars
        lhs = self.combine_constraints(state_bounds_constraint, param_bounds_constraint, unsafe_bounds)
        return f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {V_expr} {lower_bound})))"

    def generate_formulas_control_bounds_parametric_simplified(self, state_vars: List[str], param_vars: List[str],
                                                               state_bounds_constraint: str, param_bounds_constraint: str,
                                                               I_expr: str, control_vars: List[str],
                                                               controller_exprs: Dict[str, str],
                                                               control_bounds: List[Tuple[float, float]]) -> List[str]:
        """Generate control bound formulas (parametric simplified).

        Simplified version that uses parameter bounds instead of Q(P) >= 0.
        """
        formulas = []

        for i, control_var in enumerate(control_vars):
            if control_var not in controller_exprs:
                continue

            controller = controller_exprs[control_var]
            u_min, u_max = control_bounds[i]

            all_vars = state_vars + param_vars
            lhs = self.combine_constraints(state_bounds_constraint, param_bounds_constraint, f"(>= {I_expr} 0)")
            rhs = f"(and (>= {controller} {u_min}) (<= {controller} {u_max}))"

            formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} {rhs}))"
            formulas.append(formula)

        return formulas

    # ============================================================================
    # SIMPLIFIED SMT GENERATION FUNCTIONS (No Q constraints)
    # ============================================================================

    def generate_smt_file_quantitative_reach_simplified(self, config: Dict[str, Any],
                                                        output_path: str = "./tmp/temporary_polyhorn_input.smt2",
                                                        override_param_bounds: List[Tuple[float, float]] = None):
        """Generate SMT2 file for quantitative reachability (simplified parametric version).

        This is a simplified version that replaces Q(P) >= 0 constraints with parameter bounds,
        and does not include the formula asserting Q(P) >= 0 over the entire parameter space.

        Similar to the standard version, but simpler for parameter constraints.
        """
        # Check that target_probability is specified
        if 'target_probability' not in config:
            raise ValueError("Quantitative reachability requires 'target_probability' in config")

        target_probability = config['target_probability']

        # Special case: if target probability is 1, use qualitative reachability instead
        if target_probability == 1:
            return self.generate_smt_file_qualitative_reach_simplified(config, output_path, override_param_bounds)

        if target_probability <= 0 or target_probability > 1:
            raise ValueError("Target probability must be in (0, 1]")

        self.validate_config(config)

        degree = config['degree']
        state_vars = config['system']['state_vars']
        noise_vars = config['system'].get('noise_vars', [])
        control_vars = config['system'].get('control_vars', [])
        param_vars = config['system'].get('param_vars', [])
        state_bounds = config['system'].get('state_bounds', [])

        if not param_vars:
            raise ValueError("Simplified parametric version requires parameters (param_vars)")

        param_bounds = None
        if override_param_bounds is not None:
            param_bounds = override_param_bounds
        else:
            param_bounds = config['system'].get('param_bounds')

        if not param_bounds:
            raise ValueError("Parameter bounds (param_bounds) required for simplified parametric version")

        # Compute v_upper_bound = 1 / (1 - p)
        v_upper_bound = str(1.0 / (1.0 - target_probability))

        # Parse target region
        target_region = config.get('target_region', {})
        target_bounds_list = target_region.get('bounds', [])
        target_bounds = self.parse_cartesian_bounds(target_bounds_list, state_vars)

        # Parse initial region
        initial_region = config['system'].get('initial_region', {})
        initial_bounds = initial_region.get('bounds', [])
        initial_constraint = self.parse_cartesian_bounds(initial_bounds, state_vars)

        # State bounds constraint
        state_bounds_constraint = self.parse_cartesian_bounds(state_bounds, state_vars)

        # Parameter bounds constraint
        param_bounds_constraint = self.parse_cartesian_bounds(param_bounds, param_vars)

        # Noise bounds
        noise_distribution = config['system'].get('noise_distribution', {})
        noise_bounds_constraint = self.get_noise_bounds(noise_vars, noise_distribution)

        # Dynamics
        next_state_exprs = self.generate_dynamics_expression(config['system']['dynamics'], state_vars, control_vars, noise_vars)

        # Check for param*noise multiplication
        use_param_noise_bounds = False
        if param_vars and noise_vars:
            if isinstance(next_state_exprs, list):
                for piece in next_state_exprs:
                    transforms = piece.get('transforms', {})
                    for expr in transforms.values():
                        pairs = self.detect_param_noise_multiplication(expr, param_vars, noise_vars)
                        if pairs:
                            use_param_noise_bounds = True
                            break
                    if use_param_noise_bounds:
                        break
            else:
                for expr in next_state_exprs.values():
                    pairs = self.detect_param_noise_multiplication(expr, param_vars, noise_vars)
                    if pairs:
                        use_param_noise_bounds = True
                        break

        # Declare symbolic constants
        # V and C depend on both state and parameter variables
        v_c_vars = state_vars + param_vars
        V_expr = self.generate_polynomial_template(v_c_vars, degree, "V")
        I_expr = self.generate_polynomial_template(state_vars, degree, "I")

        controller_exprs = {}
        if control_vars:
            for control_var in control_vars:
                controller_exprs[control_var] = self.generate_polynomial_template(v_c_vars, degree, "C")

        epsilon = self.new_constant("Epsilon")

        # Substitute control variables with controller expressions
        if control_vars:
            if isinstance(next_state_exprs, dict):
                for var in next_state_exprs:
                    next_state_exprs[var] = self.substitute_control(next_state_exprs[var], control_vars, controller_exprs)
            else:
                for piece in next_state_exprs:
                    for var in piece['transforms']:
                        piece['transforms'][var] = self.substitute_control(piece['transforms'][var], control_vars, controller_exprs)

        formulas = []

        # Formula 1: Initial states satisfy invariant
        formulas.append(self.generate_formula_initial_invariant_parametric_simplified(
            state_vars, param_vars, state_bounds_constraint, initial_constraint,
            param_bounds_constraint, I_expr))

        # Formula 2: Invariant preservation
        formulas.extend(self.generate_formulas_invariant_preservation_parametric_simplified(
            state_vars, param_vars, noise_vars, state_bounds_constraint,
            param_bounds_constraint, noise_bounds_constraint, I_expr, next_state_exprs,
            noise_distribution, use_param_noise_bounds))

        # Formula 3: V is non-negative on invariant
        formulas.append(self.generate_formula_v_nonnegative_parametric_simplified(
            state_vars, param_vars, state_bounds_constraint, param_bounds_constraint, I_expr, V_expr))

        # Epsilon is positive
        formulas.append(self.generate_formula_epsilon_positive(epsilon))

        # Formula 4/5: Expected decrease
        formulas.extend(self.generate_formulas_expected_decrease_parametric_simplified(
            state_vars, param_vars, noise_vars, state_bounds_constraint,
            param_bounds_constraint, target_bounds_list, I_expr, V_expr, epsilon, next_state_exprs,
            noise_distribution, v_upper_bound))

        # Formula 6: Initial value bound V(S) <= 1
        formulas.append(self.generate_formula_initial_value_bound_parametric_simplified(
            state_vars, param_vars, state_bounds_constraint, initial_constraint,
            param_bounds_constraint, I_expr, V_expr, "1"))

        # Control bounds (if applicable)
        if control_vars:
            control_bounds_list = config['system'].get('control_bounds', [])
            formulas.extend(self.generate_formulas_control_bounds_parametric_simplified(
                state_vars, param_vars, state_bounds_constraint, param_bounds_constraint,
                I_expr, control_vars, controller_exprs, control_bounds_list))

        # Note: No Q(P) >= 0 formula in simplified version

        self._write_smt_file(output_path, formulas)
        return output_path

    def generate_smt_file_qualitative_reach_simplified(self, config: Dict[str, Any],
                                                       output_path: str = "./tmp/temporary_polyhorn_input.smt2",
                                                       override_param_bounds: List[Tuple[float, float]] = None):
        """Generate SMT2 file for qualitative (almost-sure) reachability (simplified parametric version).

        Certificate conditions for qualitative reachability:
        - Invariant conditions (same as quantitative):
          (a) For all initial states, I(S) >= 0
          (b) For all states S, I(S) >= 0 => I(S') >= 0
          (c) For all states S, I(S) >= 0 => V(S) >= 0
        - Expected decrease (without V upper bound):
          (a) Epsilon >= min float
          (b) For all S, I(S) >= 0 and S not in target => V(S) >= E[V(S')] + Epsilon
        - Control bounds: For all S, C(S) respects bounds

        Key differences from quantitative reachability:
        - No "V(S) <= 1/(1-p)" in expected decrease LHS
        - No "V(S) <= 1" condition for initial states
        """
        self.validate_config(config)

        degree = config['degree']
        state_vars = config['system']['state_vars']
        noise_vars = config['system'].get('noise_vars', [])
        control_vars = config['system'].get('control_vars', [])
        param_vars = config['system'].get('param_vars', [])
        state_bounds = config['system'].get('state_bounds', [])

        if not param_vars:
            raise ValueError("Simplified parametric version requires parameters (param_vars)")

        param_bounds = None
        if override_param_bounds is not None:
            param_bounds = override_param_bounds
        else:
            param_bounds = config['system'].get('param_bounds')

        if not param_bounds:
            raise ValueError("Parameter bounds (param_bounds) required for simplified parametric version")

        # Parse target region
        target_region = config.get('target_region', {})
        target_bounds_list = target_region.get('bounds', [])
        target_bounds = self.parse_cartesian_bounds(target_bounds_list, state_vars)

        # Parse initial region
        initial_region = config['system'].get('initial_region', {})
        initial_bounds = initial_region.get('bounds', [])
        initial_constraint = self.parse_cartesian_bounds(initial_bounds, state_vars)

        # State bounds constraint
        state_bounds_constraint = self.parse_cartesian_bounds(state_bounds, state_vars)

        # Parameter bounds constraint
        param_bounds_constraint = self.parse_cartesian_bounds(param_bounds, param_vars)

        # Noise bounds
        noise_distribution = config['system'].get('noise_distribution', {})
        noise_bounds_constraint = self.get_noise_bounds(noise_vars, noise_distribution)

        # Dynamics
        next_state_exprs = self.generate_dynamics_expression(config['system']['dynamics'], state_vars, control_vars, noise_vars)

        # Check for param*noise multiplication
        use_param_noise_bounds = False
        if param_vars and noise_vars:
            if isinstance(next_state_exprs, list):
                for piece in next_state_exprs:
                    transforms = piece.get('transforms', {})
                    for expr in transforms.values():
                        pairs = self.detect_param_noise_multiplication(expr, param_vars, noise_vars)
                        if pairs:
                            use_param_noise_bounds = True
                            break
                    if use_param_noise_bounds:
                        break
            else:
                for expr in next_state_exprs.values():
                    pairs = self.detect_param_noise_multiplication(expr, param_vars, noise_vars)
                    if pairs:
                        use_param_noise_bounds = True
                        break

        # Declare symbolic constants
        # V and C depend on both state and parameter variables
        v_c_vars = state_vars + param_vars
        V_expr = self.generate_polynomial_template(v_c_vars, degree, "V")
        I_expr = self.generate_polynomial_template(state_vars, degree, "I")

        controller_exprs = {}
        if control_vars:
            for control_var in control_vars:
                controller_exprs[control_var] = self.generate_polynomial_template(v_c_vars, degree, "C")

        epsilon = self.new_constant("Epsilon")

        # Substitute control variables with controller expressions
        if control_vars:
            if isinstance(next_state_exprs, dict):
                for var in next_state_exprs:
                    next_state_exprs[var] = self.substitute_control(next_state_exprs[var], control_vars, controller_exprs)
            else:
                for piece in next_state_exprs:
                    for var in piece['transforms']:
                        piece['transforms'][var] = self.substitute_control(piece['transforms'][var], control_vars, controller_exprs)

        formulas = []

        # Formula 1: Initial states satisfy invariant (same as quantitative)
        formulas.append(self.generate_formula_initial_invariant_parametric_simplified(
            state_vars, param_vars, state_bounds_constraint, initial_constraint,
            param_bounds_constraint, I_expr))

        # Formula 2: Invariant preservation (same as quantitative)
        formulas.extend(self.generate_formulas_invariant_preservation_parametric_simplified(
            state_vars, param_vars, noise_vars, state_bounds_constraint,
            param_bounds_constraint, noise_bounds_constraint, I_expr, next_state_exprs,
            noise_distribution, use_param_noise_bounds))

        # Formula 3: V is non-negative on invariant (same as quantitative)
        formulas.append(self.generate_formula_v_nonnegative_parametric_simplified(
            state_vars, param_vars, state_bounds_constraint, param_bounds_constraint, I_expr, V_expr))

        # Epsilon is positive
        formulas.append(self.generate_formula_epsilon_positive(epsilon))

        # Formula 4/5: Expected decrease (WITHOUT v_upper_bound - key difference from quantitative)
        # Pass v_upper_bound=None to exclude the "V <= 1/(1-p)" constraint from LHS
        formulas.extend(self.generate_formulas_expected_decrease_parametric_simplified(
            state_vars, param_vars, noise_vars, state_bounds_constraint,
            param_bounds_constraint, target_bounds_list, I_expr, V_expr, epsilon, next_state_exprs,
            noise_distribution, v_upper_bound=None))

        # Note: NO initial value bound V(S) <= 1 for qualitative reachability
        # (This is the second key difference from quantitative)

        # Control bounds (if applicable) - same as quantitative
        if control_vars:
            control_bounds_list = config['system'].get('control_bounds', [])
            formulas.extend(self.generate_formulas_control_bounds_parametric_simplified(
                state_vars, param_vars, state_bounds_constraint, param_bounds_constraint,
                I_expr, control_vars, controller_exprs, control_bounds_list))

        # Note: No Q(P) >= 0 formula in simplified version

        self._write_smt_file(output_path, formulas)
        return output_path

    def generate_smt_file_quantitative_safety_simplified(self, config: Dict[str, Any],
                                                         output_path: str = "./tmp/temporary_polyhorn_input.smt2",
                                                         override_param_bounds: List[Tuple[float, float]] = None):
        """Generate SMT2 file for quantitative safety (simplified parametric version).

        This is a simplified version that replaces Q(P) >= 0 constraints with parameter bounds,
        and does not include the formula asserting Q(P) >= 0 over the entire parameter space.
        """
        if 'target_probability' not in config:
            raise ValueError("Quantitative safety requires 'target_probability' in config")

        target_probability = config['target_probability']

        if target_probability <= 0 or target_probability >= 1:
            raise ValueError("Target probability must be in (0, 1) for quantitative safety")

        self.validate_config(config)

        degree = config['degree']
        state_vars = config['system']['state_vars']
        noise_vars = config['system'].get('noise_vars', [])
        control_vars = config['system'].get('control_vars', [])
        param_vars = config['system'].get('param_vars', [])
        state_bounds = config['system'].get('state_bounds', [])

        if not param_vars:
            raise ValueError("Simplified parametric version requires parameters (param_vars)")

        param_bounds = None
        if override_param_bounds is not None:
            param_bounds = override_param_bounds
        else:
            param_bounds = config['system'].get('param_bounds')

        if not param_bounds:
            raise ValueError("Parameter bounds (param_bounds) required for simplified parametric version")

        # Compute v_lower_bound = 1 / (1 - p)
        v_lower_bound = str(1.0 / (1.0 - target_probability))

        # Parse unsafe region
        unsafe_region = config.get('unsafe_region', {})
        unsafe_bounds_list = unsafe_region.get('bounds', [])
        unsafe_bounds = self.parse_cartesian_bounds(unsafe_bounds_list, state_vars)

        # Parse initial region
        initial_region = config['system'].get('initial_region', {})
        initial_bounds = initial_region.get('bounds', [])
        initial_constraint = self.parse_cartesian_bounds(initial_bounds, state_vars)

        # State bounds constraint
        state_bounds_constraint = self.parse_cartesian_bounds(state_bounds, state_vars)

        # Parameter bounds constraint
        param_bounds_constraint = self.parse_cartesian_bounds(param_bounds, param_vars)

        # Noise bounds
        noise_distribution = config['system'].get('noise_distribution', {})
        noise_bounds_constraint = self.get_noise_bounds(noise_vars, noise_distribution)

        # Dynamics
        next_state_exprs = self.generate_dynamics_expression(config['system']['dynamics'], state_vars, control_vars, noise_vars)

        # Check for param*noise multiplication
        use_param_noise_bounds = False
        if param_vars and noise_vars:
            if isinstance(next_state_exprs, list):
                for piece in next_state_exprs:
                    transforms = piece.get('transforms', {})
                    for expr in transforms.values():
                        pairs = self.detect_param_noise_multiplication(expr, param_vars, noise_vars)
                        if pairs:
                            use_param_noise_bounds = True
                            break
                    if use_param_noise_bounds:
                        break
            else:
                for expr in next_state_exprs.values():
                    pairs = self.detect_param_noise_multiplication(expr, param_vars, noise_vars)
                    if pairs:
                        use_param_noise_bounds = True
                        break

        # Declare symbolic constants
        # V and C depend on both state and parameter variables
        v_c_vars = state_vars + param_vars
        V_expr = self.generate_polynomial_template(v_c_vars, degree, "V")
        I_expr = self.generate_polynomial_template(state_vars, degree, "I")

        controller_exprs = {}
        if control_vars:
            for control_var in control_vars:
                controller_exprs[control_var] = self.generate_polynomial_template(v_c_vars, degree, "C")

        # Substitute control variables with controller expressions
        if control_vars:
            if isinstance(next_state_exprs, dict):
                for var in next_state_exprs:
                    next_state_exprs[var] = self.substitute_control(next_state_exprs[var], control_vars, controller_exprs)
            else:
                for piece in next_state_exprs:
                    for var in piece['transforms']:
                        piece['transforms'][var] = self.substitute_control(piece['transforms'][var], control_vars, controller_exprs)

        formulas = []

        # Formula 1: Initial states satisfy invariant
        formulas.append(self.generate_formula_initial_invariant_parametric_simplified(
            state_vars, param_vars, state_bounds_constraint, initial_constraint,
            param_bounds_constraint, I_expr))

        # Formula 2: Invariant preservation
        formulas.extend(self.generate_formulas_invariant_preservation_parametric_simplified(
            state_vars, param_vars, noise_vars, state_bounds_constraint,
            param_bounds_constraint, noise_bounds_constraint, I_expr, next_state_exprs,
            noise_distribution, use_param_noise_bounds))

        # Formula 3: V is non-negative on invariant
        formulas.append(self.generate_formula_v_nonnegative_parametric_simplified(
            state_vars, param_vars, state_bounds_constraint, param_bounds_constraint, I_expr, V_expr))

        # Formula 4: V is bounded by 1 on initial states
        formulas.append(self.generate_formula_initial_value_bound_parametric_simplified(
            state_vars, param_vars, state_bounds_constraint, initial_constraint,
            param_bounds_constraint, I_expr, V_expr, "1"))

        # Formula 5: Expected non-increase everywhere (no target check, no epsilon)
        # LHS includes V <= 1/(1-p) constraint
        formulas.extend(self.generate_formulas_expected_decrease_safety_parametric_simplified(
            state_vars, param_vars, noise_vars, state_bounds_constraint,
            param_bounds_constraint, I_expr, V_expr, next_state_exprs,
            noise_distribution, v_upper_bound=v_lower_bound))

        # Formula 6: V >= 1/(1-p) in unsafe region
        formulas.append(self.generate_formula_unsafe_lower_bound_parametric_simplified(
            state_vars, param_vars, state_bounds_constraint, param_bounds_constraint,
            unsafe_bounds, V_expr, v_lower_bound))

        # Control bounds (if applicable)
        if control_vars:
            control_bounds_list = config['system'].get('control_bounds', [])
            formulas.extend(self.generate_formulas_control_bounds_parametric_simplified(
                state_vars, param_vars, state_bounds_constraint, param_bounds_constraint,
                I_expr, control_vars, controller_exprs, control_bounds_list))

        # Note: No Q(P) >= 0 formula in simplified version

        self._write_smt_file(output_path, formulas)
        return output_path

    # ============================================================================
    # DUAL PROBLEM SMT GENERATION FUNCTIONS (Demonic/Adversarial)
    # ============================================================================

    def generate_control_bounds_constraint(self, control_vars: List[str], control_bounds: List[Tuple[float, float]]) -> str:
        """Generate constraint that all control variables are within bounds."""
        constraints = []
        for i, control_var in enumerate(control_vars):
            u_min, u_max = control_bounds[i]
            constraints.append(f"(>= {control_var} {u_min})")
            constraints.append(f"(<= {control_var} {u_max})")
        return self.combine_constraints(*constraints)

    def generate_formulas_invariant_preservation_dual(self, state_vars: List[str], param_vars: List[str],
                                                       control_vars: List[str], noise_vars: List[str],
                                                       state_bounds_constraint: str, param_bounds_constraint: str,
                                                       control_bounds_constraint: str, noise_bounds_constraint: str,
                                                       I_expr: str, next_state_exprs: Any,
                                                       noise_distribution: Dict[str, Any] = None,
                                                       use_param_noise_bounds: bool = False) -> List[str]:
        """Formula 2 (dual): Invariant is preserved for ALL control inputs.

        In dual problems, we quantify universally over control variables.
        """
        formulas = []

        # Determine lower and upper bounds for noise variables
        lower_bounds = []
        upper_bounds = []
        if noise_distribution and noise_distribution.get('type') == 'uniform':
            params = noise_distribution.get('params', {})
            lower_bounds = params.get('lower', [])
            upper_bounds = params.get('upper', [])

        # For dual problems, we always quantify over control variables
        # The LHS includes control bounds as a constraint

        if use_param_noise_bounds and len(lower_bounds) == len(noise_vars):
            # param*noise case - use corner points for noise
            if isinstance(next_state_exprs, list):
                for piece in next_state_exprs:
                    condition = piece['condition']
                    transforms = piece['transforms']

                    # Determine which noise variables are used
                    transform_noise_usage = {}
                    for state_var, expr in transforms.items():
                        used_noise = set()
                        for noise_var in noise_vars:
                            if noise_var in expr:
                                used_noise.add(noise_var)
                        transform_noise_usage[state_var] = used_noise

                    has_any_noise = any(len(used) > 0 for used in transform_noise_usage.values())

                    if not has_any_noise:
                        I_next = self.substitute_vars(I_expr, transforms)
                        all_vars = state_vars + param_vars + control_vars
                        lhs = self.combine_constraints(state_bounds_constraint, param_bounds_constraint,
                                                      control_bounds_constraint, condition, f"(>= {I_expr} 0)")
                        formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {I_next} 0)))"
                        formulas.append(formula)
                    else:
                        unique_noise_sets = set()
                        for used_noise in transform_noise_usage.values():
                            unique_noise_sets.add(frozenset(used_noise))

                        for noise_set in unique_noise_sets:
                            noise_set_list = sorted(list(noise_set))
                            active_indices = [noise_vars.index(nv) for nv in noise_set_list]
                            n_active = len(active_indices)

                            if n_active == 0:
                                continue

                            for i in range(2**n_active):
                                noise_substitutions = {}
                                for local_j, global_j in enumerate(active_indices):
                                    noise_var = noise_vars[global_j]
                                    if (i >> local_j) & 1:
                                        noise_substitutions[noise_var] = upper_bounds[global_j]
                                    else:
                                        noise_substitutions[noise_var] = lower_bounds[global_j]

                                transforms_subst = {}
                                for state_var, expr in transforms.items():
                                    if transform_noise_usage[state_var] == set(noise_set_list):
                                        result_expr = expr
                                        for noise_var, bound_value in noise_substitutions.items():
                                            result_expr = self.substitute_noise_with_bound(result_expr, noise_var, bound_value)
                                        transforms_subst[state_var] = result_expr
                                    else:
                                        transforms_subst[state_var] = expr

                                I_next = self.substitute_vars(I_expr, transforms_subst)
                                all_vars = state_vars + param_vars + control_vars
                                lhs = self.combine_constraints(state_bounds_constraint, param_bounds_constraint,
                                                              control_bounds_constraint, condition, f"(>= {I_expr} 0)")
                                formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {I_next} 0)))"
                                formulas.append(formula)
            else:
                # Single transform case
                used_noise = set()
                for noise_var in noise_vars:
                    for expr in next_state_exprs.values():
                        if noise_var in expr:
                            used_noise.add(noise_var)
                            break

                used_noise_list = sorted(list(used_noise))
                active_indices = [noise_vars.index(nv) for nv in used_noise_list]
                n_active = len(active_indices)

                for i in range(2**n_active):
                    noise_substitutions = {}
                    for local_j, global_j in enumerate(active_indices):
                        noise_var = noise_vars[global_j]
                        if (i >> local_j) & 1:
                            noise_substitutions[noise_var] = upper_bounds[global_j]
                        else:
                            noise_substitutions[noise_var] = lower_bounds[global_j]

                    transforms_subst = {}
                    for state_var, expr in next_state_exprs.items():
                        result_expr = expr
                        for noise_var, bound_value in noise_substitutions.items():
                            result_expr = self.substitute_noise_with_bound(result_expr, noise_var, bound_value)
                        transforms_subst[state_var] = result_expr

                    I_next = self.substitute_vars(I_expr, transforms_subst)
                    all_vars = state_vars + param_vars + control_vars
                    lhs = self.combine_constraints(state_bounds_constraint, param_bounds_constraint,
                                                  control_bounds_constraint, f"(>= {I_expr} 0)")
                    formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {I_next} 0)))"
                    formulas.append(formula)
        else:
            # Standard case: quantify over noise and control
            if isinstance(next_state_exprs, list):
                for piece in next_state_exprs:
                    condition = piece['condition']
                    transforms = piece['transforms']
                    I_next = self.substitute_vars(I_expr, transforms)

                    all_vars = state_vars + param_vars + control_vars + noise_vars
                    lhs = self.combine_constraints(state_bounds_constraint, param_bounds_constraint,
                                                  control_bounds_constraint, noise_bounds_constraint,
                                                  condition, f"(>= {I_expr} 0)")
                    formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {I_next} 0)))"
                    formulas.append(formula)
            else:
                I_next = self.substitute_vars(I_expr, next_state_exprs)
                all_vars = state_vars + param_vars + control_vars + noise_vars
                lhs = self.combine_constraints(state_bounds_constraint, param_bounds_constraint,
                                              control_bounds_constraint, noise_bounds_constraint, f"(>= {I_expr} 0)")
                formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {I_next} 0)))"
                formulas.append(formula)

        return formulas

    def generate_formulas_expected_decrease_dual(self, state_vars: List[str], param_vars: List[str],
                                                  control_vars: List[str], noise_vars: List[str],
                                                  state_bounds_constraint: str, param_bounds_constraint: str,
                                                  control_bounds_constraint: str, unsafe_bounds: List[Tuple[float, float]],
                                                  I_expr: str, V_expr: str, epsilon: str,
                                                  next_state_exprs: Any, noise_distribution: Dict[str, Any],
                                                  v_upper_bound: str = None) -> List[str]:
        """Formula 5 (dual for reachability -> safety): Expected decrease outside unsafe region.

        For dual of reachability: target becomes unsafe, we want to avoid it.
        Quantify universally over control variables.
        """
        formulas = []
        all_vars = state_vars + param_vars + control_vars

        if isinstance(next_state_exprs, dict):
            V_next = self.substitute_vars(V_expr, next_state_exprs)

            if noise_vars:
                E_V_next = self.compute_expected_value(V_next, noise_vars, noise_distribution)
            else:
                E_V_next = V_next

            decrease = f"(- {V_expr} {E_V_next})"

            # For safety (dual of reach), unsafe region = original target
            not_unsafe_cases = self.negate_cartesian_bounds(unsafe_bounds, state_vars)
            for not_unsafe_constraint in not_unsafe_cases:
                if not self.is_satisfiable_combination(state_bounds_constraint, not_unsafe_constraint):
                    continue

                lhs_constraints = [state_bounds_constraint, param_bounds_constraint,
                                  control_bounds_constraint, not_unsafe_constraint, f"(>= {I_expr} 0)"]
                if v_upper_bound:
                    lhs_constraints.append(f"(<= {V_expr} {v_upper_bound})")

                lhs = self.combine_constraints(*lhs_constraints)
                formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {decrease} {epsilon})))"
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

                not_unsafe_cases = self.negate_cartesian_bounds(unsafe_bounds, state_vars)

                for not_unsafe_constraint in not_unsafe_cases:
                    if not self.is_satisfiable_combination(state_bounds_constraint, condition, not_unsafe_constraint):
                        continue

                    lhs_constraints = [state_bounds_constraint, param_bounds_constraint,
                                      control_bounds_constraint, condition, not_unsafe_constraint, f"(>= {I_expr} 0)"]
                    if v_upper_bound:
                        lhs_constraints.append(f"(<= {V_expr} {v_upper_bound})")

                    lhs = self.combine_constraints(*lhs_constraints)
                    formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {decrease} {epsilon})))"
                    formulas.append(formula)

        return formulas

    def generate_formulas_expected_decrease_dual_reach(self, state_vars: List[str], param_vars: List[str],
                                                        control_vars: List[str], noise_vars: List[str],
                                                        state_bounds_constraint: str, param_bounds_constraint: str,
                                                        control_bounds_constraint: str, target_bounds: List[Tuple[float, float]],
                                                        I_expr: str, V_expr: str, epsilon: str,
                                                        next_state_exprs: Any, noise_distribution: Dict[str, Any],
                                                        v_upper_bound: str = None) -> List[str]:
        """Formula 5 (dual for safety -> reachability): Expected decrease outside target.

        For dual of safety: unsafe becomes target, we want to reach it.
        Quantify universally over control variables.
        """
        formulas = []
        all_vars = state_vars + param_vars + control_vars

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

                lhs_constraints = [state_bounds_constraint, param_bounds_constraint,
                                  control_bounds_constraint, not_target_constraint, f"(>= {I_expr} 0)"]
                if v_upper_bound:
                    lhs_constraints.append(f"(<= {V_expr} {v_upper_bound})")

                lhs = self.combine_constraints(*lhs_constraints)
                formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {decrease} {epsilon})))"
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

                    lhs_constraints = [state_bounds_constraint, param_bounds_constraint,
                                      control_bounds_constraint, condition, not_target_constraint, f"(>= {I_expr} 0)"]
                    if v_upper_bound:
                        lhs_constraints.append(f"(<= {V_expr} {v_upper_bound})")

                    lhs = self.combine_constraints(*lhs_constraints)
                    formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {decrease} {epsilon})))"
                    formulas.append(formula)

        return formulas

    def generate_smt_file_dual_reach_simplified(self, config: Dict[str, Any],
                                                 output_path: str = "./tmp/temporary_polyhorn_input.smt2",
                                                 override_param_bounds: List[Tuple[float, float]] = None):
        """Generate SMT2 file for dual of reachability (safety/avoidance).

        Dual of reachability: For all controllers, the system avoids the target (target becomes unsafe).
        - No controller C polynomial
        - Quantify universally over control variables with control bounds on LHS
        - Target region becomes unsafe region
        """
        if 'target_probability' not in config:
            raise ValueError("Dual reachability requires 'target_probability' in config")

        target_probability = config['target_probability']

        if target_probability <= 0 or target_probability >= 1:
            raise ValueError("Target probability must be in (0, 1) for dual reachability")

        # Note: We skip validate_config as it expects either target or unsafe, not both transformations
        degree = config['degree']
        state_vars = config['system']['state_vars']
        noise_vars = config['system'].get('noise_vars', [])
        control_vars = config['system'].get('control_vars', [])
        param_vars = config['system'].get('param_vars', [])
        state_bounds = config['system'].get('state_bounds', [])
        control_bounds_list = config['system'].get('control_bounds', [])

        if not param_vars:
            raise ValueError("Dual problem requires parameters (param_vars)")

        param_bounds = override_param_bounds if override_param_bounds is not None else config['system'].get('param_bounds')

        if not param_bounds:
            raise ValueError("Parameter bounds required for dual problem")

        # For dual of reachability: target becomes unsafe
        # v_lower_bound = 1 / (1 - p) for safety
        v_lower_bound = str(1.0 / (1.0 - target_probability))

        # Original target region becomes unsafe region
        target_region = config.get('target_region', {})
        unsafe_bounds_list = target_region.get('bounds', [])
        unsafe_bounds = self.parse_cartesian_bounds(unsafe_bounds_list, state_vars)

        # Parse initial region
        initial_region = config['system'].get('initial_region', {})
        initial_bounds = initial_region.get('bounds', [])
        initial_constraint = self.parse_cartesian_bounds(initial_bounds, state_vars)

        # Constraints
        state_bounds_constraint = self.parse_cartesian_bounds(state_bounds, state_vars)
        param_bounds_constraint = self.parse_cartesian_bounds(param_bounds, param_vars)
        control_bounds_constraint = self.generate_control_bounds_constraint(control_vars, control_bounds_list) if control_vars else None

        noise_distribution = config['system'].get('noise_distribution', {})
        noise_bounds_constraint = self.get_noise_bounds(noise_vars, noise_distribution)

        # Dynamics - DO NOT substitute control, keep control vars as is
        next_state_exprs = self.generate_dynamics_expression(config['system']['dynamics'], state_vars, control_vars, noise_vars)

        # Check for param*noise multiplication
        use_param_noise_bounds = False
        if param_vars and noise_vars:
            if isinstance(next_state_exprs, list):
                for piece in next_state_exprs:
                    transforms = piece.get('transforms', {})
                    for expr in transforms.values():
                        pairs = self.detect_param_noise_multiplication(expr, param_vars, noise_vars)
                        if pairs:
                            use_param_noise_bounds = True
                            break
                    if use_param_noise_bounds:
                        break
            else:
                for expr in next_state_exprs.values():
                    pairs = self.detect_param_noise_multiplication(expr, param_vars, noise_vars)
                    if pairs:
                        use_param_noise_bounds = True
                        break

        # Generate V and I (V depends on state and params, no C)
        v_vars = state_vars + param_vars
        V_expr = self.generate_polynomial_template(v_vars, degree, "V")
        I_expr = self.generate_polynomial_template(state_vars, degree, "I")

        # Note: No Epsilon needed for safety (unlike reachability)

        formulas = []

        # Formula 1: Initial states satisfy invariant (same as before, no control)
        formulas.append(self.generate_formula_initial_invariant_parametric_simplified(
            state_vars, param_vars, state_bounds_constraint, initial_constraint,
            param_bounds_constraint, I_expr))

        # Formula 2: Invariant preservation (forall control)
        formulas.extend(self.generate_formulas_invariant_preservation_dual(
            state_vars, param_vars, control_vars, noise_vars, state_bounds_constraint,
            param_bounds_constraint, control_bounds_constraint, noise_bounds_constraint,
            I_expr, next_state_exprs, noise_distribution, use_param_noise_bounds))

        # Formula 3: V is non-negative on invariant
        formulas.append(self.generate_formula_v_nonnegative_parametric_simplified(
            state_vars, param_vars, state_bounds_constraint, param_bounds_constraint, I_expr, V_expr))

        # Formula 4: V is bounded by 1 on initial states
        formulas.append(self.generate_formula_initial_value_bound_parametric_simplified(
            state_vars, param_vars, state_bounds_constraint, initial_constraint,
            param_bounds_constraint, I_expr, V_expr, "1"))

        # Formula 5: Expected non-increase everywhere (safety - no target check, no epsilon)
        # For safety: E[V(f(x,u,w))] <= V(x) for all states in invariant
        # LHS includes V <= 1/(1-p) constraint
        all_vars = state_vars + param_vars + control_vars
        if isinstance(next_state_exprs, dict):
            V_next = self.substitute_vars(V_expr, next_state_exprs)
            if noise_vars:
                E_V_next = self.compute_expected_value(V_next, noise_vars, noise_distribution)
            else:
                E_V_next = V_next
            decrease = f"(- {V_expr} {E_V_next})"
            lhs = self.combine_constraints(state_bounds_constraint, param_bounds_constraint,
                                          control_bounds_constraint, f"(>= {I_expr} 0)",
                                          f"(<= {V_expr} {v_lower_bound})")
            formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {decrease} 0)))"
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
                lhs = self.combine_constraints(state_bounds_constraint, param_bounds_constraint,
                                              control_bounds_constraint, condition, f"(>= {I_expr} 0)",
                                              f"(<= {V_expr} {v_lower_bound})")
                formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {decrease} 0)))"
                formulas.append(formula)

        # Formula 6: V >= 1/(1-p) in unsafe region (original target)
        formulas.append(self.generate_formula_unsafe_lower_bound_parametric_simplified(
            state_vars, param_vars, state_bounds_constraint, param_bounds_constraint,
            unsafe_bounds, V_expr, v_lower_bound))

        # Note: No control bounds formula needed - control vars are quantified universally

        self._write_smt_file(output_path, formulas)
        return output_path

    def generate_smt_file_dual_safety_simplified(self, config: Dict[str, Any],
                                                  output_path: str = "./tmp/temporary_polyhorn_input.smt2",
                                                  override_param_bounds: List[Tuple[float, float]] = None):
        """Generate SMT2 file for dual of safety (reachability).

        Dual of safety: For all controllers, the system reaches the unsafe region (unsafe becomes target).
        - No controller C polynomial
        - Quantify universally over control variables with control bounds on LHS
        - Unsafe region becomes target region
        """
        if 'target_probability' not in config:
            raise ValueError("Dual safety requires 'target_probability' in config")

        target_probability = config['target_probability']

        if target_probability <= 0 or target_probability > 1:
            raise ValueError("Target probability must be in (0, 1] for dual safety")

        degree = config['degree']
        state_vars = config['system']['state_vars']
        noise_vars = config['system'].get('noise_vars', [])
        control_vars = config['system'].get('control_vars', [])
        param_vars = config['system'].get('param_vars', [])
        state_bounds = config['system'].get('state_bounds', [])
        control_bounds_list = config['system'].get('control_bounds', [])

        if not param_vars:
            raise ValueError("Dual problem requires parameters (param_vars)")

        param_bounds = override_param_bounds if override_param_bounds is not None else config['system'].get('param_bounds')

        if not param_bounds:
            raise ValueError("Parameter bounds required for dual problem")

        # For dual of safety: unsafe becomes target
        # v_upper_bound = 1 / (1 - p) for reachability
        v_upper_bound = str(1.0 / (1.0 - target_probability))

        # Original unsafe region becomes target region
        unsafe_region = config.get('unsafe_region', {})
        target_bounds_list = unsafe_region.get('bounds', [])
        target_bounds = self.parse_cartesian_bounds(target_bounds_list, state_vars)

        # Parse initial region
        initial_region = config['system'].get('initial_region', {})
        initial_bounds = initial_region.get('bounds', [])
        initial_constraint = self.parse_cartesian_bounds(initial_bounds, state_vars)

        # Constraints
        state_bounds_constraint = self.parse_cartesian_bounds(state_bounds, state_vars)
        param_bounds_constraint = self.parse_cartesian_bounds(param_bounds, param_vars)
        control_bounds_constraint = self.generate_control_bounds_constraint(control_vars, control_bounds_list) if control_vars else None

        noise_distribution = config['system'].get('noise_distribution', {})
        noise_bounds_constraint = self.get_noise_bounds(noise_vars, noise_distribution)

        # Dynamics - DO NOT substitute control, keep control vars as is
        next_state_exprs = self.generate_dynamics_expression(config['system']['dynamics'], state_vars, control_vars, noise_vars)

        # Check for param*noise multiplication
        use_param_noise_bounds = False
        if param_vars and noise_vars:
            if isinstance(next_state_exprs, list):
                for piece in next_state_exprs:
                    transforms = piece.get('transforms', {})
                    for expr in transforms.values():
                        pairs = self.detect_param_noise_multiplication(expr, param_vars, noise_vars)
                        if pairs:
                            use_param_noise_bounds = True
                            break
                    if use_param_noise_bounds:
                        break
            else:
                for expr in next_state_exprs.values():
                    pairs = self.detect_param_noise_multiplication(expr, param_vars, noise_vars)
                    if pairs:
                        use_param_noise_bounds = True
                        break

        # Generate V and I (V depends on state and params, no C)
        v_vars = state_vars + param_vars
        V_expr = self.generate_polynomial_template(v_vars, degree, "V")
        I_expr = self.generate_polynomial_template(state_vars, degree, "I")

        epsilon = self.new_constant("Epsilon")

        formulas = []

        # Formula 1: Initial states satisfy invariant
        formulas.append(self.generate_formula_initial_invariant_parametric_simplified(
            state_vars, param_vars, state_bounds_constraint, initial_constraint,
            param_bounds_constraint, I_expr))

        # Formula 2: Invariant preservation (forall control)
        formulas.extend(self.generate_formulas_invariant_preservation_dual(
            state_vars, param_vars, control_vars, noise_vars, state_bounds_constraint,
            param_bounds_constraint, control_bounds_constraint, noise_bounds_constraint,
            I_expr, next_state_exprs, noise_distribution, use_param_noise_bounds))

        # Formula 3: V is non-negative on invariant
        formulas.append(self.generate_formula_v_nonnegative_parametric_simplified(
            state_vars, param_vars, state_bounds_constraint, param_bounds_constraint, I_expr, V_expr))

        # Epsilon is positive
        formulas.append(self.generate_formula_epsilon_positive(epsilon))

        # Formula 4/5: Expected decrease outside target (forall control)
        formulas.extend(self.generate_formulas_expected_decrease_dual_reach(
            state_vars, param_vars, control_vars, noise_vars, state_bounds_constraint,
            param_bounds_constraint, control_bounds_constraint, target_bounds_list,
            I_expr, V_expr, epsilon, next_state_exprs, noise_distribution, v_upper_bound))

        # Formula 6: Initial value bound V(S) <= 1
        formulas.append(self.generate_formula_initial_value_bound_parametric_simplified(
            state_vars, param_vars, state_bounds_constraint, initial_constraint,
            param_bounds_constraint, I_expr, V_expr, "1"))

        # Note: No control bounds formula needed - control vars are quantified universally

        self._write_smt_file(output_path, formulas)
        return output_path

    def _write_smt_file(self, output_path: str, formulas: List[str]):
        """Write formulas to SMT2 file."""
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
                           smt_output_path: str = "./tmp/temporary_polyhorn_input.smt2",
                           config_path: str = "./tmp/temporary_polyhorn_config.json",
                           temp_output_path: str = "./tmp/polyhorn_temp.txt"):
        """Generate configuration JSON file.

        Args:
            theorem_name: Name of the theorem/entailment solver
            degree: Polynomial degree
            solver_name: SMT solver name
            smt_output_path: Path to SMT file (not used in config, for reference)
            config_path: Path where to write the config JSON file
            temp_output_path: Path for temporary output file used by PolyQnt solver
        """
        config = {
            "theorem_name": theorem_name,
            "degree_of_sat": degree,
            "degree_of_nonstrict_unsat": 0,
            "degree_of_strict_unsat": 0,
            "max_d_of_strict": 0,
            "solver_name": solver_name,
            "output_path": temp_output_path,
            "unsat_core_heuristic": False,
            "SAT_heuristic": True,
            "integer_arithmetic": False
        }

        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)

        return config_path
