
import re
import sys

def check_balance(filename):
    print(f"Checking {filename}...")
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"Failed to read file: {e}")
        return
    
    stack = []
    
    # Simple state machine
    in_string = False
    string_char = ''
    last_string_start = 0
    in_comment = False # //
    in_block_comment = False # /* */
    last_block_comment_start = 0
    debug_count = 0
    
    for line_idx, line in enumerate(lines):
        i = 0
        while i < len(line):
            char = line[i]
            
            if in_string and line_idx + 1 == 2977 and i == 0:
                 print(f"DEBUG: Still in STRING at 2977! Started at {last_string_start} with char {string_char}")

            # Handle comments
            if not in_string and not in_block_comment:
                if char == '/' and i + 1 < len(line) and line[i+1] == '/':
                    break # Ignore rest of line
                if char == '/' and i + 1 < len(line) and line[i+1] == '*':
                    in_block_comment = True
                    last_block_comment_start = line_idx + 1
                    i += 2
                    continue
            
            if in_block_comment:
                if line_idx + 1 == 2977 and i == 0:
                     print(f"DEBUG: Still in block comment at 2977! Started at {last_block_comment_start}")

                if char == '*' and i + 1 < len(line) and line[i+1] == '/':
                    in_block_comment = False
                    i += 2
                else:
                    i += 1
                continue
                
            # Handle strings
            if not in_string:
                if char == '\"' or char == '\'' or char == '`':
                    in_string = True
                    string_char = char
                    last_string_start = line_idx + 1
                    if line_idx + 1 >= 0 and debug_count < 50:
                        print(f"DEBUG: String START at {line_idx + 1}:{i} char={char}")
                        debug_count += 1
            else:
                if char == '\\':
                    i += 2
                    continue
                if char == string_char:
                    in_string = False
                    if line_idx + 1 >= 0 and debug_count < 50:
                         print(f"DEBUG: String END at {line_idx + 1}:{i} char={char}")
                         debug_count += 1
                i += 1
                continue
            
            # Check brackets
            if line_idx + 1 == 2977:
                print(f"DEBUG Line 2977: char='{char}', in_string={in_string}, in_comment={in_comment}, in_block_comment={in_block_comment}")

            if char in '{(':
                stack.append((char, line_idx + 1))
                if line_idx + 1 == 2977:
                    print(f"DEBUG Pushed {char} at 2977. Stack size: {len(stack)}")
            elif char in '})':
                if not stack:
                    print(f'Error: Unexpected {char} at line {line_idx + 1}')
                    # Don't return, keep checking to find more issues
                else:
                    last_char, last_line = stack.pop()
                    expected = '}' if last_char == '{' else ')'
                    if char != expected:
                        print(f'Error: Mismatched {char} at line {line_idx + 1}. Expected {expected} to match {last_char} from line {last_line}')
                        return # Found mismatch, stop
            
            i += 1

    if stack:
        print(f'Error: Unclosed brackets found ({len(stack)}):')
        for char, line in stack[-5:]: # Show last 5
            print(f'  {char} at line {line}')
    else:
        print("No syntax errors found by simple check.")

check_balance('c:/Users/Administrator/Desktop/ai技术辅助测试/frontend/src/components/StandardAPITesting.tsx')
