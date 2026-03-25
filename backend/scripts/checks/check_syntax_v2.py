
import re
import sys

def check_balance_advanced(filename):
    print(f"Checking {filename}...")
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"Failed to read file: {e}")
        return
    
    stack = []
    
    # States
    # 0: code
    # 1: string "
    # 2: string '
    # 3: template string `
    # 4: regex /
    # 5: line comment // (handled by loop logic)
    # 6: block comment /* */
    
    state = 0
    string_start = (0, 0)
    
    i = 0
    length = len(content)
    
    line_num = 1
    col_num = 0
    
    while i < length:
        char = content[i]
        
        # Track line numbers
        if char == '\n':
            line_num += 1
            col_num = 0
            if state == 5: # Line comment ends
                state = 0
            elif state == 1 or state == 2:
                # Strings usually don't span lines unless escaped, but let's be lenient
                pass
            i += 1
            continue
            
        col_num += 1
        
        if state == 0: # Code
            # Handle comments
            if char == '/' and i+1 < length:
                next_char = content[i+1]
                if next_char == '/':
                    state = 5 # Line comment
                    i += 2; col_num += 1
                    continue
                elif next_char == '*':
                    state = 6 # Block comment
                    i += 2; col_num += 1
                    continue
                else:
                    # Regex detection heuristic
                    j = i - 1
                    while j >= 0 and content[j].isspace():
                        j -= 1
                    
                    is_regex = False
                    if j < 0:
                        is_regex = True
                    else:
                        prev = content[j]
                        if prev in '(=,:;[{!&|?':
                            is_regex = True
                        elif prev in '}])':
                            is_regex = False
                        elif content[max(0, j-6):j+1] == 'return':
                             is_regex = True
                        elif content[max(0, j-4):j+1] == 'case':
                             is_regex = True
                        
                    if is_regex:
                        state = 4
                        string_start = (line_num, col_num)
                        i += 1
                        continue
            
            if char == '"':
                state = 1
                string_start = (line_num, col_num)
            elif char == "'":
                state = 2
                string_start = (line_num, col_num)
            elif char == '`':
                state = 3
                string_start = (line_num, col_num)
            elif char in '{(':
                stack.append((char, line_num, col_num))
            elif char == ')':
                if not stack:
                    print(f"Error: Unexpected ) at {line_num}:{col_num}")
                else:
                    last, ll, lc = stack.pop()
                    if last != '(':
                        print(f"Error: Mismatched ) at {line_num}:{col_num}. Expected closing for {last} from {ll}:{lc}")
                        return
                    if line_num > 3990:
                        print(f"Closing {last} from {ll}:{lc} at {line_num}:{i}")
            elif char == '}':
                if not stack:
                    print(f"Error: Unexpected }} at {line_num}:{col_num}")
                else:
                    last, ll, lc = stack.pop()
                    if last == '${':
                        state = 3 # Back to template string
                    elif last == '{':
                        pass
                    else:
                        print(f"Error: Mismatched }} at {line_num}:{col_num}. Expected closing for {last} from {ll}:{lc}")
                        return
                    if line_num > 3990:
                        print(f"Closing {last} from {ll}:{lc} at {line_num}:{i}")

        elif state == 1: # " string
            if char == '\\':
                i += 1
                col_num += 1
            elif char == '"':
                state = 0
        
        elif state == 2: # ' string
            if char == '\\':
                i += 1
                col_num += 1
            elif char == "'":
                state = 0
                
        elif state == 3: # ` string
            if char == '\\':
                i += 1
                col_num += 1
            elif char == '`':
                state = 0
            elif char == '$' and i+1 < length and content[i+1] == '{':
                stack.append(('${', line_num, col_num))
                state = 0
                i += 1
                col_num += 1
                
        elif state == 4: # Regex
            if char == '\\':
                i += 1
                col_num += 1
            elif char == '/':
                # Check for flags? No need, just end regex mode
                state = 0
            elif char == '[': # Handle char class [^/] in regex
                # Simple skip until ]
                # But careful about escapes
                # This is getting complicated. Let's just trust basic escaping.
                pass
                
        elif state == 6: # Block comment
            if char == '*' and i+1 < length and content[i+1] == '/':
                state = 0
                i += 2
                col_num += 1
                continue

        i += 1

    if stack:
        print(f"Error: Unclosed brackets ({len(stack)}):")
        for char, l, c in stack:
            print(f"  {char} at {l}:{c}")
    else:
        print("No errors found.")

check_balance_advanced('c:/Users/Administrator/Desktop/ai技术辅助测试/frontend/src/components/StandardAPITesting.tsx')
