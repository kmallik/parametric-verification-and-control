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

    def generate_formula_initial_value_bound_parametric(self, state_vars: List[str], param_vars: List[str],
                                                        state_bounds_constraint: str, initial_constraint: str,
                                                        I_expr: str, V_expr: str, Q_expr: str,
                                                        upper_bound: str) -> str:
        """Formula 6 (parametric reachability): Initial value of V is bounded."""
        all_vars = state_vars + param_vars
        lhs = self.combine_constraints(state_bounds_constraint, f"(>= {I_expr} 0)",
                                       initial_constraint, f"(>= {Q_expr} 0)")
        return f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (<= {V_expr} {upper_bound})))"

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
    # PARAMETRIC FORMULA GENERATION
    # ============================================================================
    
    def generate_formula_initial_invariant_parametric(self, state_vars: List[str], param_vars: List[str],
                                                      state_bounds_constraint: str, initial_constraint: str,
                                                      I_expr: str, Q_expr: str) -> str:
        """Formula 1 (parametric): Initial states satisfy invariant."""
        all_vars = state_vars + param_vars
        lhs = self.combine_constraints(state_bounds_constraint, initial_constraint, f"(>= {Q_expr} 0)")
        return f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {I_expr} 0)))"
    
    def generate_formulas_invariant_preservation_parametric(self, state_vars: List[str], param_vars: List[str],
                                                            noise_vars: List[str], state_bounds_constraint: str,
                                                            noise_bounds_constraint: str, I_expr: str,
                                                            Q_expr: str, next_state_exprs: Any) -> List[str]:
        """Formula 2 (parametric): Invariant is preserved."""
        formulas = []
        
        if isinstance(next_state_exprs, dict):
            I_next = self.substitute_vars(I_expr, next_state_exprs)
            all_vars = state_vars + param_vars + noise_vars
            lhs = self.combine_constraints(state_bounds_constraint, noise_bounds_constraint,
                                          f"(>= {Q_expr} 0)", f"(>= {I_expr} 0)")
            formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {I_next} 0)))"
            formulas.append(formula)
        else:
            for piece in next_state_exprs:
                condition = piece['condition']
                transforms = piece['transforms']
                I_next = self.substitute_vars(I_expr, transforms)
                
                all_vars = state_vars + param_vars + noise_vars
                lhs = self.combine_constraints(state_bounds_constraint, noise_bounds_constraint,
                                              f"(>= {Q_expr} 0)", condition, f"(>= {I_expr} 0)")
                formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {I_next} 0)))"
                formulas.append(formula)
        
        return formulas
    
    def generate_formula_v_nonnegative_parametric(self, state_vars: List[str], param_vars: List[str],
                                                  state_bounds_constraint: str, I_expr: str,
                                                  V_expr: str, Q_expr: str) -> str:
        """Formula 3 (parametric): V is non-negative on invariant."""
        all_vars = state_vars + param_vars
        lhs = self.combine_constraints(state_bounds_constraint, f"(>= {Q_expr} 0)", f"(>= {I_expr} 0)")
        return f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {V_expr} 0)))"
    
    def generate_formulas_expected_decrease_parametric(self, state_vars: List[str], param_vars: List[str],
                                                       noise_vars: List[str], state_bounds_constraint: str,
                                                       target_bounds: List[Tuple[float, float]],
                                                       I_expr: str, V_expr: str, Q_expr: str, epsilon: str,
                                                       next_state_exprs: Any, noise_distribution: Dict[str, Any],
                                                       v_upper_bound: str = None) -> List[str]:
        """Formula 5 (parametric): Expected decrease outside target."""
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
                
                lhs_constraints = [state_bounds_constraint, f"(>= {Q_expr} 0)", 
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
                    
                    lhs_constraints = [state_bounds_constraint, f"(>= {Q_expr} 0)", condition,
                                      not_target_constraint, f"(>= {I_expr} 0)"]
                    if v_upper_bound:
                        lhs_constraints.append(f"(<= {V_expr} {v_upper_bound})")
                    
                    lhs = self.combine_constraints(*lhs_constraints)
                    formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} (>= {decrease} {epsilon})))"
                    formulas.append(formula)
        
        return formulas
    
    def generate_formula_q_positive(self, param_vars: List[str], Q_expr: str,
                                   param_bounds: List[Tuple[float, float]] = None,
                                   param_vals: List[float] = None) -> str:
        """Generate Q positivity formula."""
        min_float = "1.0e-15"
        
        if param_bounds is not None:
            param_bounds_constraint = self.parse_cartesian_bounds(param_bounds, param_vars)
            lhs = param_bounds_constraint if param_bounds_constraint else None
            rhs = f"(>= (+ {Q_expr} (* -1 {min_float})) 0)"
            
            if lhs:
                return f"(forall ({self.format_var_decls(param_vars)}) (=> {lhs} {rhs}))"
            else:
                return f"(forall ({self.format_var_decls(param_vars)}) {rhs})"
        
        elif param_vals is not None:
            Q_at_vals = Q_expr
            for i, param_var in enumerate(param_vars):
                val = param_vals[i]
                pattern = r'\b' + re.escape(param_var) + r'\b'
                Q_at_vals = re.sub(pattern, str(val), Q_at_vals)
            
            return f"(>= (+ {Q_at_vals} (* -1 {min_float})) 0)"
        else:
            raise ValueError("Either param_bounds or param_vals must be provided")
    
    def generate_formulas_control_bounds_parametric(self, state_vars: List[str], param_vars: List[str],
                                                    state_bounds_constraint: str, I_expr: str, Q_expr: str,
                                                    control_vars: List[str], controller_exprs: Dict[str, str],
                                                    control_bounds: List[Tuple[float, float]]) -> List[str]:
        """Generate control bound formulas for parametric systems."""
        formulas = []
        all_vars = state_vars + param_vars
        
        for i, control_var in enumerate(control_vars):
            if control_var not in controller_exprs:
                continue
            
            controller = controller_exprs[control_var]
            u_min, u_max = control_bounds[i]
            
            lhs = self.combine_constraints(state_bounds_constraint, f"(>= {Q_expr} 0)", f"(>= {I_expr} 0)")
            rhs = f"(and (>= {controller} {u_min}) (<= {controller} {u_max}))"
            
            formula = f"(forall ({self.format_var_decls(all_vars)}) (=> {lhs} {rhs}))"
            formulas.append(formula)
        
        return formulas
    
    # ============================================================================
    # MAIN SMT GENERATION FUNCTIONS
    # ============================================================================
    
    def generate_smt_file_almost_sure_reach(self, config: Dict[str, Any], 
                                            output_path: str = "./tmp/temporary_polyhorn_input.smt2",
                                            override_param_bounds: List[Tuple[float, float]] = None):
        """Generate SMT2 file for almost-sure reachability."""
        self.validate_config(config)
        
        degree = config['degree']
        state_vars = config['system']['state_vars']
        noise_vars = config['system'].get('noise_vars', [])
        control_vars = config['system'].get('control_vars', [])
        param_vars = config['system'].get('param_vars', [])
        state_bounds = config['system'].get('state_bounds', [])
        
        is_parametric = len(param_vars) > 0
        
        param_bounds = None
        param_vals = None
        
        if is_parametric:
            if override_param_bounds is not None:
                param_bounds = override_param_bounds
            elif 'param_bounds' in config['system']:
                param_bounds = config['system']['param_bounds']
            elif 'param_vals' in config['system']:
                param_vals = config['system']['param_vals']
            else:
                raise ValueError("Parametric system requires 'param_bounds' or 'param_vals'")
        
        V_expr = self.generate_polynomial_template(state_vars, degree, "V")
        I_expr = self.generate_polynomial_template(state_vars, degree, "I")
        
        Q_expr = None
        if is_parametric:
            Q_expr = self.generate_polynomial_template(param_vars, degree, "Q")
        
        controller_exprs = {}
        if control_vars:
            for control_var in control_vars:
                controller_exprs[control_var] = self.generate_polynomial_template(state_vars, degree, "C")
        
        epsilon = self.new_constant("Epsilon")
        
        state_bounds_constraint = self.parse_cartesian_bounds(state_bounds, state_vars) if state_bounds else None
        noise_bounds_constraint = self.get_noise_bounds(noise_vars, config['system'].get('noise_distribution', {}))
        initial_constraint = self.parse_region(config['system']['initial_region'], state_vars)
        target_bounds = config['target_region']['bounds']
        
        next_state_exprs = self.generate_dynamics_expression(config['system']['dynamics'], state_vars, control_vars, noise_vars)
        
        if control_vars:
            if isinstance(next_state_exprs, dict):
                for var in next_state_exprs:
                    next_state_exprs[var] = self.substitute_control(next_state_exprs[var], control_vars, controller_exprs)
            else:
                for piece in next_state_exprs:
                    for var in piece['transforms']:
                        piece['transforms'][var] = self.substitute_control(piece['transforms'][var], control_vars, controller_exprs)
        
        formulas = []
        
        if is_parametric:
            formulas.append(self.generate_formula_initial_invariant_parametric(
                state_vars, param_vars, state_bounds_constraint, initial_constraint, I_expr, Q_expr))
            formulas.extend(self.generate_formulas_invariant_preservation_parametric(
                state_vars, param_vars, noise_vars, state_bounds_constraint, noise_bounds_constraint, 
                I_expr, Q_expr, next_state_exprs))
            formulas.append(self.generate_formula_v_nonnegative_parametric(
                state_vars, param_vars, state_bounds_constraint, I_expr, V_expr, Q_expr))
        else:
            formulas.append(self.generate_formula_initial_invariant(
                state_vars, state_bounds_constraint, initial_constraint, I_expr))
            formulas.extend(self.generate_formulas_invariant_preservation(
                state_vars, noise_vars, state_bounds_constraint, noise_bounds_constraint, I_expr, next_state_exprs))
            formulas.append(self.generate_formula_v_nonnegative(
                state_vars, state_bounds_constraint, I_expr, V_expr))
        
        formulas.append(self.generate_formula_epsilon_positive(epsilon))
        
        if is_parametric:
            formulas.extend(self.generate_formulas_expected_decrease_parametric(
                state_vars, param_vars, noise_vars, state_bounds_constraint, target_bounds,
                I_expr, V_expr, Q_expr, epsilon, next_state_exprs, 
                config['system'].get('noise_distribution', {}), v_upper_bound=None))
            formulas.append(self.generate_formula_q_positive(param_vars, Q_expr, param_bounds, param_vals))
        else:
            formulas.extend(self.generate_formulas_expected_decrease(
                state_vars, noise_vars, state_bounds_constraint, target_bounds,
                I_expr, V_expr, epsilon, next_state_exprs, 
                config['system'].get('noise_distribution', {}), v_upper_bound=None))
        
        if control_vars:
            control_bounds = config['system']['control_bounds']
            if is_parametric:
                formulas.extend(self.generate_formulas_control_bounds_parametric(
                    state_vars, param_vars, state_bounds_constraint, I_expr, Q_expr,
                    control_vars, controller_exprs, control_bounds))
            else:
                formulas.extend(self.generate_formulas_control_bounds(
                    state_vars, state_bounds_constraint, I_expr, control_vars, controller_exprs, control_bounds))
        
        self._write_smt_file(output_path, formulas)
        return output_path
    
    def generate_smt_file_quantitative_reach(self, config: Dict[str, Any],
                                             output_path: str = "./tmp/temporary_polyhorn_input.smt2",
                                             override_param_bounds: List[Tuple[float, float]] = None):
        """Generate SMT2 file for quantitative reachability.

        Similar to almost-sure reachability, but the expected decrease condition includes
        an additional constraint: V(x) <= 1/(1-p), where p is the target probability.

        If target_probability is 1, delegates to generate_smt_file_almost_sure_reach.
        """
        # Check that target_probability is specified
        if 'target_probability' not in config:
            raise ValueError("Quantitative reachability requires 'target_probability' in config")

        target_probability = config['target_probability']

        # Special case: if target probability is 1, use almost-sure reachability
        if target_probability == 1:
            return self.generate_smt_file_almost_sure_reach(config, output_path, override_param_bounds)

        if target_probability <= 0 or target_probability > 1:
            raise ValueError("Target probability must be in (0, 1]")

        self.validate_config(config)

        degree = config['degree']
        state_vars = config['system']['state_vars']
        noise_vars = config['system'].get('noise_vars', [])
        control_vars = config['system'].get('control_vars', [])
        param_vars = config['system'].get('param_vars', [])
        state_bounds = config['system'].get('state_bounds', [])

        is_parametric = len(param_vars) > 0

        param_bounds = None
        param_vals = None

        if is_parametric:
            if override_param_bounds is not None:
                param_bounds = override_param_bounds
            elif 'param_bounds' in config['system']:
                param_bounds = config['system']['param_bounds']
            elif 'param_vals' in config['system']:
                param_vals = config['system']['param_vals']
            else:
                raise ValueError("Parametric system requires 'param_bounds' or 'param_vals'")

        V_expr = self.generate_polynomial_template(state_vars, degree, "V")
        I_expr = self.generate_polynomial_template(state_vars, degree, "I")

        Q_expr = None
        if is_parametric:
            Q_expr = self.generate_polynomial_template(param_vars, degree, "Q")

        controller_exprs = {}
        if control_vars:
            for control_var in control_vars:
                controller_exprs[control_var] = self.generate_polynomial_template(state_vars, degree, "C")

        epsilon = self.new_constant("Epsilon")

        # Compute V upper bound from target probability: 1/(1-p)
        v_upper_bound = str(1.0 / (1.0 - target_probability))

        state_bounds_constraint = self.parse_cartesian_bounds(state_bounds, state_vars) if state_bounds else None
        noise_bounds_constraint = self.get_noise_bounds(noise_vars, config['system'].get('noise_distribution', {}))
        initial_constraint = self.parse_region(config['system']['initial_region'], state_vars)
        target_bounds = config['target_region']['bounds']

        next_state_exprs = self.generate_dynamics_expression(config['system']['dynamics'], state_vars, control_vars, noise_vars)

        if control_vars:
            if isinstance(next_state_exprs, dict):
                for var in next_state_exprs:
                    next_state_exprs[var] = self.substitute_control(next_state_exprs[var], control_vars, controller_exprs)
            else:
                for piece in next_state_exprs:
                    for var in piece['transforms']:
                        piece['transforms'][var] = self.substitute_control(piece['transforms'][var], control_vars, controller_exprs)

        formulas = []

        if is_parametric:
            formulas.append(self.generate_formula_initial_invariant_parametric(
                state_vars, param_vars, state_bounds_constraint, initial_constraint, I_expr, Q_expr))
            formulas.extend(self.generate_formulas_invariant_preservation_parametric(
                state_vars, param_vars, noise_vars, state_bounds_constraint, noise_bounds_constraint,
                I_expr, Q_expr, next_state_exprs))
            formulas.append(self.generate_formula_v_nonnegative_parametric(
                state_vars, param_vars, state_bounds_constraint, I_expr, V_expr, Q_expr))
        else:
            formulas.append(self.generate_formula_initial_invariant(
                state_vars, state_bounds_constraint, initial_constraint, I_expr))
            formulas.extend(self.generate_formulas_invariant_preservation(
                state_vars, noise_vars, state_bounds_constraint, noise_bounds_constraint, I_expr, next_state_exprs))
            formulas.append(self.generate_formula_v_nonnegative(
                state_vars, state_bounds_constraint, I_expr, V_expr))

        formulas.append(self.generate_formula_epsilon_positive(epsilon))

        # Additional formula for quantitative reachability: V is bounded by 1 on initial states
        if is_parametric:
            formulas.append(self.generate_formula_initial_value_bound_parametric(
                state_vars, param_vars, state_bounds_constraint, initial_constraint,
                I_expr, V_expr, Q_expr, "1"))
        else:
            formulas.append(self.generate_formula_initial_value_bound(
                state_vars, state_bounds_constraint, initial_constraint,
                I_expr, V_expr, "1"))

        # The key difference: include v_upper_bound in expected decrease formulas
        if is_parametric:
            formulas.extend(self.generate_formulas_expected_decrease_parametric(
                state_vars, param_vars, noise_vars, state_bounds_constraint, target_bounds,
                I_expr, V_expr, Q_expr, epsilon, next_state_exprs,
                config['system'].get('noise_distribution', {}), v_upper_bound=v_upper_bound))
            formulas.append(self.generate_formula_q_positive(param_vars, Q_expr, param_bounds, param_vals))
        else:
            formulas.extend(self.generate_formulas_expected_decrease(
                state_vars, noise_vars, state_bounds_constraint, target_bounds,
                I_expr, V_expr, epsilon, next_state_exprs,
                config['system'].get('noise_distribution', {}), v_upper_bound=v_upper_bound))

        if control_vars:
            control_bounds = config['system']['control_bounds']
            if is_parametric:
                formulas.extend(self.generate_formulas_control_bounds_parametric(
                    state_vars, param_vars, state_bounds_constraint, I_expr, Q_expr,
                    control_vars, controller_exprs, control_bounds))
            else:
                formulas.extend(self.generate_formulas_control_bounds(
                    state_vars, state_bounds_constraint, I_expr, control_vars, controller_exprs, control_bounds))

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
                           smt_output_path: str = "./tmp/temporary_polyhorn_input.smt2"):
        """Generate configuration JSON file."""
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

        return config_path
