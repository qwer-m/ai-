import os
import re

def get_all_files(root_dir, extensions):
    files = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Skip node_modules, .git, __pycache__, venv
        if 'node_modules' in dirpath or '.git' in dirpath or '__pycache__' in dirpath or 'venv' in dirpath or '.trae' in dirpath:
            continue
        for f in filenames:
            if any(f.endswith(ext) for ext in extensions):
                files.append(os.path.join(dirpath, f))
    return files

def check_usage(target_files, search_files):
    unused = []
    for target in target_files:
        basename = os.path.basename(target)
        name_no_ext = os.path.splitext(basename)[0]
        
        # Skip specific files
        if name_no_ext in ['main', 'App', 'index', '__init__', 'vite.config', 'setup']:
            continue
            
        is_used = False
        # Search in all search_files
        for search_file in search_files:
            if os.path.abspath(search_file) == os.path.abspath(target):
                continue
                
            try:
                with open(search_file, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    # Simple check: if filename (without ext) appears in content
                    # We can be slightly more specific: import ... name_no_ext ...
                    if name_no_ext in content:
                        is_used = True
                        break
            except:
                pass
        
        if not is_used:
            unused.append(target)
    return unused

def main():
    print("Scanning for potentially unused files (Heuristic: filename not mentioned in other files)...")
    
    # Frontend
    frontend_ext = ['.ts', '.tsx']
    frontend_files = get_all_files('frontend/src', frontend_ext)
    frontend_unused = check_usage(frontend_files, frontend_files)
    
    print(f"\nFrontend Unused Candidates ({len(frontend_unused)}):")
    for f in frontend_unused:
        print(f"  {f}")

    # Backend
    backend_ext = ['.py']
    backend_files = get_all_files('ai_test_platform', backend_ext)
    
    # Separate scripts from core
    core_files = [f for f in backend_files if 'scripts' not in f and os.path.basename(f) not in ['main.py', 'celery_worker.py', 'celery_config.py']]
    
    # Search for core usage in all backend files
    backend_unused = check_usage(core_files, backend_files)
    
    print(f"\nBackend Core Unused Candidates ({len(backend_unused)}):")
    for f in backend_unused:
        print(f"  {f}")
        
    # Root scripts
    root_files = get_all_files('.', ['.py'])
    root_scripts = [f for f in root_files if os.path.dirname(f) == '.']
    
    print(f"\nRoot Scripts (Likely manual tools):")
    for f in root_scripts:
        print(f"  {f}")

if __name__ == '__main__':
    main()
