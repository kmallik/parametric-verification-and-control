import json
import re
import argparse


def check_balanced_parentheses(expr):
    """Check if parentheses are balanced in an expression."""
    count = 0
    for char in expr:
        if char == '(':
            count += 1
        elif char == ')':
            count -= 1
        if count < 0:
            return False
    return count == 0


def validate_parenthesis_balance(dynamics):
    """Validate that all transforms in dynamics have balanced parentheses."""
    if isinstance(dynamics, dict) and 'expressions' in dynamics:
        for var, expr in dynamics['expressions'].items():
            if not check_balanced_parentheses(expr):
                raise ValueError(f"Unbalanced parentheses in transform for {var}: {expr}")
    elif isinstance(dynamics, list):
        for i, piece in enumerate(dynamics):
            if 'transforms' in piece:
                for var, expr in piece['transforms'].items():
                    if not check_balanced_parentheses(expr):
                        raise ValueError(f"Unbalanced parentheses in dynamics piece {i+1}, transform for {var}: {expr}")


def replace_parameters_with_states(data):
    """
    Replace all occurrences of Pi with S{n+i} where n is the number of state variables.
    Transforms a parametric system into a non-parametric system by treating parameters as additional state variables.
    
    Args:
        data: Dictionary containing the parsed JSON input in SRSMGenerator format
        
    Returns:
        Modified dictionary with parameters replaced by state space references
    """
    # Get state and parameter variables
    state_vars = data['system']['state_vars']
    param_vars = data['system'].get('param_vars', [])
    
    if not param_vars:
        print("No parameter variables found. Returning original data.")
        return data
    
    n = len(state_vars)
    p = len(param_vars)
    
    # Validate parenthesis balance in dynamics
    if 'dynamics' in data['system']:
        validate_parenthesis_balance(data['system']['dynamics'])
    
    # Create a copy of the data
    output_data = json.loads(json.dumps(data))
    
    # Create mapping from parameter variables to new state variables
    param_to_state_map = {}
    new_state_vars = state_vars.copy()
    
    for i, param_var in enumerate(param_vars):
        new_state_var = f"S{n + i + 1}"
        param_to_state_map[param_var] = new_state_var
        new_state_vars.append(new_state_var)
    
    def replace_in_string(text):
        """Replace all parameter variable occurrences with new state variables"""
        result = text
        for param_var, state_var in param_to_state_map.items():
            # Use word boundaries to avoid partial replacements
            pattern = r'\b' + re.escape(param_var) + r'\b'
            result = re.sub(pattern, state_var, result)
        return result
    
    def ensure_parentheses(expr):
        """Ensure expression is wrapped in parentheses if it's not already."""
        expr = expr.strip()
        if not expr:
            return expr
        if expr.startswith('(') and expr.endswith(')'):
            # Check if these are the outermost matching parens
            count = 0
            for i, char in enumerate(expr):
                if char == '(':
                    count += 1
                elif char == ')':
                    count -= 1
                if count == 0 and i < len(expr) - 1:
                    # The first '(' closes before the end, so wrap it
                    return f"({expr})"
            return expr
        else:
            return f"({expr})"
    
    def process_value(value):
        """Recursively process values in the data structure"""
        if isinstance(value, str):
            return replace_in_string(value)
        elif isinstance(value, list):
            return [process_value(item) for item in value]
        elif isinstance(value, dict):
            return {key: process_value(val) for key, val in value.items()}
        else:
            return value
    
    # Update state_vars to include parameter variables as states
    output_data['system']['state_vars'] = new_state_vars
    
    # Extend state_bounds with parameter bounds
    if 'state_bounds' in output_data['system'] and 'param_bounds' in data['system']:
        original_state_bounds = output_data['system']['state_bounds']
        param_bounds = data['system']['param_bounds']
        
        # Process parameter bounds and add them
        for param_bound in param_bounds:
            original_state_bounds.append(param_bound)
        
        output_data['system']['state_bounds'] = original_state_bounds
    
    # Update initial_region bounds
    if 'initial_region' in output_data['system'] and 'bounds' in output_data['system']['initial_region']:
        if 'param_bounds' in data['system']:
            initial_bounds = output_data['system']['initial_region']['bounds']
            param_bounds = data['system']['param_bounds']
            
            # Add parameter bounds to initial region
            for param_bound in param_bounds:
                initial_bounds.append(param_bound)
    
    # Process dynamics - update conditions and transforms
    if 'dynamics' in output_data['system']:
        dynamics = output_data['system']['dynamics']
        
        if isinstance(dynamics, dict) and 'expressions' in dynamics:
            # Old format: simple expressions
            expressions = dynamics['expressions']
            new_expressions = {}
            
            # Replace parameters in existing expressions
            for var, expr in expressions.items():
                new_expressions[replace_in_string(var)] = ensure_parentheses(replace_in_string(expr))
            
            # Add identity transforms for parameter variables (they don't change)
            for new_state_var in new_state_vars[n:]:
                new_expressions[new_state_var] = f"(+ {new_state_var} 0)"
            
            output_data['system']['dynamics']['expressions'] = new_expressions
            
        elif isinstance(dynamics, list):
            # New format: piecewise dynamics
            for piece in dynamics:
                # Replace parameters in condition
                if 'condition' in piece:
                    piece['condition'] = replace_in_string(piece['condition'])
                    
                    # Add parameter bounds to condition
                    if 'param_bounds' in data['system']:
                        param_bound_conditions = []
                        for i, (lower, upper) in enumerate(data['system']['param_bounds']):
                            new_state_var = new_state_vars[n + i]
                            param_bound_conditions.append(f"{lower} <= {new_state_var} <= {upper}")
                        
                        # Append parameter bound conditions
                        original_condition = piece['condition']
                        all_conditions = [original_condition] + param_bound_conditions
                        piece['condition'] = ' and '.join(all_conditions)
                
                # Replace parameters in transforms
                if 'transforms' in piece:
                    transforms = piece['transforms']
                    new_transforms = {}
                    
                    # Replace parameters in existing transforms and ensure parentheses
                    for var, expr in transforms.items():
                        new_transforms[replace_in_string(var)] = ensure_parentheses(replace_in_string(expr))
                    
                    # Add identity transforms for parameter variables
                    for new_state_var in new_state_vars[n:]:
                        new_transforms[new_state_var] = f"(+ {new_state_var} 0)"
                    
                    piece['transforms'] = new_transforms
    
    # Process target_region if it exists
    if 'target_region' in output_data and 'bounds' in output_data['target_region']:
        # Target region should not change (parameters don't affect target)
        # But we need to ensure dimensions match by adding dummy bounds for parameters
        target_bounds = output_data['target_region']['bounds']
        if 'param_bounds' in data['system']:
            # Add parameter bounds to target region (parameters must stay within bounds)
            param_bounds = data['system']['param_bounds']
            for param_bound in param_bounds:
                target_bounds.append(param_bound)
    
    # Process unsafe_region if it exists
    if 'unsafe_region' in output_data and 'bounds' in output_data['unsafe_region']:
        unsafe_bounds = output_data['unsafe_region']['bounds']
        if 'param_bounds' in data['system']:
            param_bounds = data['system']['param_bounds']
            for param_bound in param_bounds:
                unsafe_bounds.append(param_bound)
    
    # Remove param_vars and param_bounds from output
    if 'param_vars' in output_data['system']:
        del output_data['system']['param_vars']
    if 'param_bounds' in output_data['system']:
        del output_data['system']['param_bounds']
    
    return output_data


def parse_json_file(input_file, output_file):
    """
    Read JSON from input file, transform it, and write to output file.
    
    Args:
        input_file: Path to the input JSON file
        output_file: Path to the output JSON file
    """
    try:
        # Read the input JSON file
        with open(input_file, 'r') as f:
            data = json.load(f)
        
        # Transform the data
        output_data = replace_parameters_with_states(data)
        
        # Write the output JSON file
        with open(output_file, 'w') as f:
            json.dump(output_data, f, indent=2)
        
        print(f"Successfully processed {input_file} -> {output_file}")
        print(f"Parameters converted to state variables:")
        
        if 'param_vars' in data['system']:
            param_vars = data['system']['param_vars']
            state_vars = data['system']['state_vars']
            n = len(state_vars)
            for i, param_var in enumerate(param_vars):
                print(f"  {param_var} -> S{n + i + 1}")
        
    except FileNotFoundError:
        print(f"Error: Input file '{input_file}' not found.")
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in input file - {e}")
    except Exception as e:
        print(f"Error: {e}")


def main():
    parser = argparse.ArgumentParser(
        description='Transform parametric MDP to non-parametric MDP by converting parameters to state variables'
    )
    parser.add_argument('input_file', help='Path to input JSON file (parametric system)')
    parser.add_argument('output_file', help='Path to output JSON file (non-parametric system)')
    
    args = parser.parse_args()
    
    parse_json_file(args.input_file, args.output_file)


if __name__ == '__main__':
    main()