import json
import re
import argparse


def replace_parameters_with_states(data):
    """
    Replace all occurrences of Pi with S{n+i} where n is the state_space_dimension.
    Also adds parameter bounds to system_space and initial_space, and modifies dynamics entries.
    
    Args:
        data: Dictionary containing the parsed JSON input
        
    Returns:
        Modified dictionary with parameters replaced by state space references
    """
    # Get the dimensions
    n = data['stochastic_dynamical_system']['state_space_dimension']
    p = data['stochastic_dynamical_system']['parameter_space_dimension']
    
    # Create a copy of the data to avoid modifying the original
    output_data = json.loads(json.dumps(data))
    
    def replace_in_string(text):
        """Replace all Pi occurrences in a string with S{n+i}"""
        # Pattern to match P followed by one or more digits
        pattern = r'P(\d+)'
        
        def replacer(match):
            param_index = int(match.group(1))
            return f'S{n + param_index}'
        
        return re.sub(pattern, replacer, text)
    
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
    
    # Update state_space_dimension to be state_space_dimension + parameter_space_dimension
    output_data['stochastic_dynamical_system']['state_space_dimension'] = n + p
    
    # Convert system_space from list to string and add parameter bounds
    if 'system_space' in output_data['stochastic_dynamical_system']:
        system_space_list = output_data['stochastic_dynamical_system']['system_space']
        if 'parameter_space' in data['stochastic_dynamical_system']:
            parameter_space = data['stochastic_dynamical_system']['parameter_space']
            # Add parameter bounds to system_space list
            for i, param_bound in enumerate(parameter_space):
                state_bound = replace_in_string(param_bound)
                system_space_list.append(state_bound)
        # Join with ' and '
        output_data['stochastic_dynamical_system']['system_space'] = ' and '.join(system_space_list)
    
    # Convert initial_space from list to string and add parameter bounds
    if 'initial_space' in output_data['stochastic_dynamical_system']:
        initial_space_list = output_data['stochastic_dynamical_system']['initial_space']
        if 'parameter_space' in data['stochastic_dynamical_system']:
            parameter_space = data['stochastic_dynamical_system']['parameter_space']
            # Add parameter bounds to initial_space list
            for i, param_bound in enumerate(parameter_space):
                state_bound = replace_in_string(param_bound)
                initial_space_list.append(state_bound)
        # Join with ' and '
        output_data['stochastic_dynamical_system']['initial_space'] = ' and '.join(initial_space_list)
    
    # Modify existing dynamics entries
    if 'parameter_space' in data['stochastic_dynamical_system'] and 'dynamics' in output_data['stochastic_dynamical_system']:
        parameter_space = data['stochastic_dynamical_system']['parameter_space']
        
        # Process each existing dynamics entry
        for dyn_entry in output_data['stochastic_dynamical_system']['dynamics']:
            # Add parameter bounds conditions to existing condition
            param_conditions = []
            for i, param_bound in enumerate(parameter_space):
                # Replace Pi with S{n+i}
                state_bound = replace_in_string(param_bound)
                param_conditions.append(state_bound)
            
            # Combine original condition with parameter conditions
            if 'condition' in dyn_entry:
                original_condition = dyn_entry['condition']
                # Replace parameters in original condition
                transformed_condition = replace_in_string(original_condition)
                
                # Join all conditions with ' and '
                all_conditions = [transformed_condition] + param_conditions
                dyn_entry['condition'] = ' and '.join(all_conditions)
            
            # Replace parameters in transforms
            if 'transforms' in dyn_entry:
                transformed_list = []
                for transform in dyn_entry['transforms']:
                    transformed_list.append(replace_in_string(transform))
                
                # Add S{n+1} to S{n+p} as new entries
                for i in range(1, p + 1):
                    transformed_list.append(f"S{n + i}")
                
                dyn_entry['transforms'] = transformed_list
    
    # Remove parameter_space from output (optional, comment out if you want to keep it)
    if 'parameter_space' in output_data['stochastic_dynamical_system']:
        del output_data['stochastic_dynamical_system']['parameter_space']
    
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
        
    except FileNotFoundError:
        print(f"Error: Input file '{input_file}' not found.")
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in input file - {e}")
    except Exception as e:
        print(f"Error: {e}")


def main():
    parser = argparse.ArgumentParser(
        description='Parse JSON file and replace parameter references with state space indices'
    )
    parser.add_argument('input_file', help='Path to input JSON file')
    parser.add_argument('output_file', help='Path to output JSON file')
    
    args = parser.parse_args()
    
    parse_json_file(args.input_file, args.output_file)


if __name__ == '__main__':
    main()