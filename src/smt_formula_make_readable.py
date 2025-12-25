import re
import sys

class SMTConverter:
    def __init__(self):
        self.constants = []
    
    def parse_file(self, filepath):
        """Read and parse the SMT file."""
        try:
            with open(filepath, 'r') as f:
                content = f.read()
            return content
        except FileNotFoundError:
            print(f"Error: File '{filepath}' not found.")
            sys.exit(1)
    
    def extract_constants(self, content):
        """Extract declared constants from the SMT file."""
        pattern = r'\(declare-const\s+(\w+)\s+Real\)'
        self.constants = re.findall(pattern, content)
        return self.constants
    
    def extract_assertions(self, content):
        """Extract assert statements from the content."""
        assertions = []
        i = 0
        while i < len(content):
            start = content.find('(assert', i)
            if start == -1:
                break
            
            # Find matching closing paren
            paren_count = 0
            j = start
            while j < len(content):
                if content[j] == '(':
                    paren_count += 1
                elif content[j] == ')':
                    paren_count -= 1
                    if paren_count == 0:
                        # Extract everything between (assert and final )
                        assertion = content[start+7:j].strip()
                        assertions.append(assertion)
                        i = j + 1
                        break
                j += 1
            else:
                break
        
        return assertions
    
    def parse_smt(self, s):
        """Parse SMT expression string into nested structure."""
        s = s.strip()
        if not s:
            return None
        
        if not s.startswith('('):
            # Atomic value
            return s
        
        # Remove outer parentheses
        s = s[1:-1].strip()
        
        result = []
        current = ""
        depth = 0
        
        i = 0
        while i < len(s):
            char = s[i]
            
            if char == '(' :
                depth += 1
                current += char
            elif char == ')':
                depth -= 1
                current += char
            elif char.isspace() and depth == 0:
                if current:
                    result.append(current)
                    current = ""
            else:
                current += char
            
            i += 1
        
        if current:
            result.append(current)
        
        # Recursively parse each element
        parsed_result = []
        for item in result:
            if item.startswith('('):
                parsed_result.append(self.parse_smt(item))
            else:
                parsed_result.append(item)
        
        return parsed_result
    
    def format_expr(self, expr, precedence=0):
        """Convert parsed expression to readable format."""
        if expr is None:
            return ""
        
        if isinstance(expr, str):
            return expr
        
        if not isinstance(expr, list) or len(expr) == 0:
            return str(expr)
        
        operator = expr[0]
        args = expr[1:] if len(expr) > 1 else []
        
        # Handle quantifiers
        if operator in ['forall', 'exists']:
            symbol = '∀' if operator == 'forall' else '∃'
            
            if len(args) < 2:
                return f"{symbol}??. (incomplete)"
            
            # First argument is variable list: ((var1 Type) (var2 Type) ...)
            var_list = args[0]
            body = args[1]
            
            variables = []
            if isinstance(var_list, list):
                for var_decl in var_list:
                    if isinstance(var_decl, list) and len(var_decl) >= 1:
                        variables.append(var_decl[0])
            
            vars_str = ', '.join(variables) if variables else '??'
            body_str = self.format_expr(body, 0)
            
            return f"{symbol}{vars_str}. {body_str}"
        
        # Handle implication
        if operator == '=>' and len(args) == 2:
            left = self.format_expr(args[0], 1)
            right = self.format_expr(args[1], 1)
            return f"({left} → {right})"
        
        # Handle logical operators
        if operator == 'and' and len(args) >= 1:
            if len(args) == 1:
                return self.format_expr(args[0], precedence)
            formatted = [self.format_expr(arg, 2) for arg in args]
            return f"({' ∧ '.join(formatted)})"
        
        if operator == 'or' and len(args) >= 1:
            if len(args) == 1:
                return self.format_expr(args[0], precedence)
            formatted = [self.format_expr(arg, 2) for arg in args]
            return f"({' ∨ '.join(formatted)})"
        
        if operator == 'not' and len(args) >= 1:
            arg_str = self.format_expr(args[0], 3)
            return f"¬{arg_str}"
        
        # Handle comparison operators
        if operator in ['=', '<', '>', '<=', '>='] and len(args) == 2:
            op_map = {'<=': '≤', '>=': '≥', '=': '=', '<': '<', '>': '>'}
            op_symbol = op_map.get(operator, operator)
            left = self.format_expr(args[0], 2)
            right = self.format_expr(args[1], 2)
            return f"({left} {op_symbol} {right})"
        
        # Handle arithmetic operators
        if operator == '+' and len(args) >= 1:
            if len(args) == 1:
                return self.format_expr(args[0], precedence)
            formatted = [self.format_expr(arg, 3) for arg in args]
            return f"({' + '.join(formatted)})"
        
        if operator == '-' and len(args) >= 1:
            if len(args) == 1:
                # Unary minus
                arg_str = self.format_expr(args[0], 3)
                return f"(-{arg_str})"
            formatted = [self.format_expr(arg, 3) for arg in args]
            return f"({' - '.join(formatted)})"
        
        if operator == '*' and len(args) >= 1:
            if len(args) == 1:
                return self.format_expr(args[0], precedence)
            formatted = [self.format_expr(arg, 4) for arg in args]
            return f"({' × '.join(formatted)})"
        
        if operator == '/' and len(args) == 2:
            left = self.format_expr(args[0], 4)
            right = self.format_expr(args[1], 4)
            return f"({left} / {right})"
        
        # Default format
        formatted_args = [self.format_expr(arg, 0) for arg in args]
        return f"{operator}({', '.join(formatted_args)})"
    
    def convert_file(self, filepath, output_filepath=None):
        """Main conversion function."""
        content = self.parse_file(filepath)
        
        # Extract constants
        constants = self.extract_constants(content)
        
        # Build output string
        output_lines = []
        output_lines.append("=" * 80)
        output_lines.append("SMT FORMULA TO HUMAN-READABLE CONVERTER")
        output_lines.append("=" * 80)
        output_lines.append("")
        
        if constants:
            output_lines.append("Constants declared:")
            for const in constants:
                output_lines.append(f"  • {const} ∈ ℝ")
            output_lines.append("")
        
        # Extract and convert assertions
        assertions = self.extract_assertions(content)
        
        if assertions:
            output_lines.append("Formulas:")
            output_lines.append("")
            for i, assertion in enumerate(assertions, 1):
                try:
                    parsed = self.parse_smt(assertion)
                    readable = self.format_expr(parsed)
                    output_lines.append(f"Formula {i}:")
                    output_lines.append(f"  {readable}")
                    output_lines.append("")
                except Exception as e:
                    output_lines.append(f"Formula {i}: [Error parsing: {e}]")
                    output_lines.append("")
        else:
            output_lines.append("No assertions found in the file.")
        
        output_lines.append("=" * 80)
        
        # Join all lines
        output_text = "\n".join(output_lines)
        
        # Print to console
        print(output_text)
        
        # Write to file if output path is provided
        if output_filepath:
            try:
                with open(output_filepath, 'w', encoding='utf-8') as f:
                    f.write(output_text)
                print(f"\nOutput saved to: {output_filepath}")
            except Exception as e:
                print(f"\nError writing to output file: {e}")

def main():
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Usage: python smt_converter.py <input_smt_file> [output_file]")
        print("\nArguments:")
        print("  input_smt_file  - Path to the SMT formula file (required)")
        print("  output_file     - Path to save the converted output (optional)")
        print("\nExamples:")
        print("  python smt_converter.py input.smt2")
        print("  python smt_converter.py input.smt2 output.txt")
        sys.exit(1)
    
    filepath = sys.argv[1]
    output_filepath = sys.argv[2] if len(sys.argv) == 3 else None
    
    converter = SMTConverter()
    converter.convert_file(filepath, output_filepath)

if __name__ == "__main__":
    main()