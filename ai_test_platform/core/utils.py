import os
import subprocess
import tempfile
from typing import Optional, Tuple
import logging

# Configure logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ai_test_platform")

def extract_code_block(text: str, language: Optional[str] = None) -> str:
    """
    Extract code from markdown code blocks.
    If language is specified, looks for ```language ... ```.
    Otherwise looks for generic ``` ... ```.
    """
    if not text:
        return ""
        
    # Try with specific language first if provided
    if language and f"```{language}" in text:
        return text.split(f"```{language}")[1].split("```")[0].strip()
    
    # Try generic markdown
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 3: # ```code```
            return parts[1].strip()
        elif len(parts) == 2: # Unclosed or malformed
            # Check if the part starts with language name
            content = parts[1]
            if language and content.startswith(language):
                content = content[len(language):]
            return content.strip()
            
    return text.strip()

def run_temp_script(
    script_content: str, 
    suffix: str = ".py", 
    command: list[str] = None, 
    timeout: int = 30
) -> Tuple[str, str, int]:
    """
    Write script to temp file and execute it.
    Returns (stdout, stderr, returncode).
    """
    if command is None:
        command = ["python"]
        
    with tempfile.NamedTemporaryFile(mode='w', suffix=suffix, delete=False, encoding='utf-8') as tmp:
        tmp.write(script_content)
        tmp_path = tmp.name
        
    try:
        # Prepare command
        full_command = command + [tmp_path]
        
        result = subprocess.run(
            full_command,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", f"Execution timed out after {timeout} seconds", -1
    except Exception as e:
        return "", f"Execution failed: {str(e)}", -1
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except:
                pass

