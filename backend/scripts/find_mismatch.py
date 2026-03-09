
import re

def check_balance(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    stack = []
    # (char, line_num, col_num)
    
    # We want to find the last time stack size was 1 (assuming 185 starts the main function)
    last_stack_size_1_line = -1
    
    # Regex for tokens
    token_pattern = re.compile(r'//|/\*|\*/|["\'`]|[\{\}\(\)\[\]]')
    
    in_comment_block = False
    
    for line_idx, line in enumerate(lines):
        line_num = line_idx + 1
        i = 0
        while i < len(line):
            if in_comment_block:
                if line[i:i+2] == '*/':
                    in_comment_block = False
                    i += 2
                else:
                    i += 1
                continue

            # Check for line comment
            if line[i:i+2] == '//':
                break # Skip rest of line

            # Check for block comment start
            if line[i:i+2] == '/*':
                in_comment_block = True
                i += 2
                continue

            # Check for quotes (simplified, doesn't handle escapes perfectly but usually ok)
            char = line[i]
            if char in ["'", '"', '`']:
                quote_char = char
                start_line = line_num
                start_col = i
                i += 1
                while i < len(line):
                    # Simple escape check
                    if line[i] == '\\':
                        i += 2
                        continue
                    if line[i] == quote_char:
                        break # Found closing quote
                    i += 1
                else:
                    # End of line without closing quote
                    # For backticks, this is fine (multiline). For others, it's an issue usually.
                    if quote_char == '`':
                        # We need to continue scanning in next lines
                        # But for this script, let's just assume single line for simple quotes, 
                        # and handle backticks properly? 
                        # To keep it simple: if we hit EOL in backtick, we go to next line state.
                        # But here we loop line by line.
                        # Let's handle multiline strings properly.
                        pass
                
                # To handle multiline properly, we need a better loop structure.
                # But let's stick to the current logic for now. 
                # If we are in a string, we skip processing brackets.
                
                # REWRITE: using a global index or state machine is better.
                pass

            # Let's restart with a better state machine approach for the whole file content
            i += 1

    # New approach: Process whole content as one string
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    pos = 0
    line = 1
    col = 1
    
    stack = []
    last_stack_1_pos = -1 # Position in content
    
    while pos < len(content):
        # Update line/col
        # (This is expensive to calc every char, but ok for 1 file)
        # Better: keep track.
        pass

    # Using re.finditer is better
    pass

def analyze_brackets(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        text = f.read()

    # Tokenizer regex
    # 1. Comments
    # 2. Strings (including backticks)
    # 3. Brackets
    # We need to match strictly.
    
    # Regex for strings: "..." or '...' or `...`
    # Handle escapes: \\.
    string_re = r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`(?:[^`\\]|\\.)*`'
    comment_re = r'//.*|/\*[\s\S]*?\*/'
    brackets_re = r'[\{\}\(\)\[\]]'
    
    # Combine
    pattern = re.compile(f'({string_re})|({comment_re})|({brackets_re})', re.MULTILINE)
    
    stack = []
    last_valid_stack_1_idx = -1
    
    # To map index to line number
    line_starts = [0] + [m.start() + 1 for m in re.finditer(r'\n', text)]
    
    def get_line_col(index):
        # binary search or simple scan
        import bisect
        line_idx = bisect.bisect_right(line_starts, index) - 1
        line_num = line_idx + 1
        col_num = index - line_starts[line_idx] + 1
        return line_num, col_num

    matches = list(pattern.finditer(text))
    
    for m in matches:
        s = m.group(0)
        start_idx = m.start()
        
        # If string or comment, ignore brackets inside
        if s.startswith('/') or s.startswith('"') or s.startswith("'") or s.startswith('`'):
            continue
            
        # Bracket
        char = s
        if char in '{[(':
            stack.append((char, start_idx))
        elif char in '}])':
            if not stack:
                l, c = get_line_col(start_idx)
                print(f"Error: Extra closing {char} at {l}:{c}")
                return
            
            top, top_idx = stack[-1]
            matches_pair = (top == '{' and char == '}') or \
                           (top == '[' and char == ']') or \
                           (top == '(' and char == ')')
            
            if matches_pair:
                stack.pop()
                if len(stack) == 1:
                    last_valid_stack_1_idx = start_idx
                if len(stack) == 0:
                    # We closed the main function?
                    pass
            else:
                l, c = get_line_col(start_idx)
                tl, tc = get_line_col(top_idx)
                print(f"Error: Mismatched {char} at {l}:{c}, expected closing for {top} at {tl}:{tc}")
                return

    if stack:
        print(f"Stack not empty ({len(stack)} items). Top items:")
        for char, idx in stack[-5:]:
            l, c = get_line_col(idx)
            print(f"  {char} at {l}:{c}")
            
        if len(stack) >= 1:
            # The unclosed block starts after the last time we were at stack 1
            # (i.e. after the last successful closure of a level-2 block)
            # Or if we never closed a level-2 block, it starts after level-1 block start.
            
            # Find the index of the stack item at index 1 (the first inner block that wasn't closed)
            if len(stack) > 1:
                bad_block_char, bad_block_idx = stack[1]
                l, c = get_line_col(bad_block_idx)
                print(f"\nSuspected unclosed block starts at {l}:{c} ({bad_block_char})")
                
                # Also print the previous sibling closing
                if last_valid_stack_1_idx != -1:
                     l, c = get_line_col(last_valid_stack_1_idx)
                     print(f"Last successful closure at level 1 was at {l}:{c}")
            else:
                print("\nOnly root block is open. Did we miss a } at the end?")
                
check_balance('c:/Users/Administrator/Desktop/ai技术辅助测试/frontend/src/components/StandardAPITesting.tsx')
