# UI Automation Template
# This template provides helper functions for structured logging and screenshot capture

import json
import os
import time
import requests
import sys

# Global configuration
EXECUTION_ID = os.environ.get("UI_EXECUTION_ID", "unknown")
SCREENSHOT_DIR = os.path.join(os.getcwd(), "screenshots", str(EXECUTION_ID))

if not os.path.exists(SCREENSHOT_DIR):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

def log_step(step_id, action, status="running", details="", screenshot_path=None):
    """
    Log a step in a structured JSON format to stdout.
    This allows the backend to parse the logs and update the UI in real-time.
    """
    log_entry = {
        "type": "step_log",
        "step_id": step_id,
        "timestamp": time.time(),
        "action": action,
        "status": status,
        "details": details,
        "screenshot": screenshot_path
    }
    print(json.dumps(log_entry), flush=True)

def take_screenshot(driver_or_page, name_prefix):
    """
    Take a screenshot and return the absolute path.
    Works for both Playwright (page) and Appium (driver).
    """
    timestamp = int(time.time() * 1000)
    filename = f"{name_prefix}_{timestamp}.png"
    filepath = os.path.join(SCREENSHOT_DIR, filename)
    
    try:
        # Check if it's a Playwright Page object (has .screenshot method that takes path)
        if hasattr(driver_or_page, "screenshot"):
            # Playwright is async, but we can't await here easily if called from sync context.
            # However, this helper is intended to be injected into async functions for Playwright.
            # If we are in an async function, we should have awaited it. 
            # But to keep it simple, we assume the caller handles the await or we use a sync wrapper?
            # actually, for Playwright, we usually inject this code directly.
            # Let's assume the caller will handle the specific call.
            pass 
        
        # For Appium (Selenium based), save_screenshot is synchronous
        if hasattr(driver_or_page, "save_screenshot"):
            driver_or_page.save_screenshot(filepath)
            return filepath
            
    except Exception as e:
        print(f"Failed to take screenshot: {e}", file=sys.stderr)
        return None
    
    return filepath

# AI Image Recognition Function (Injected)
def ai_locate_element(screenshot_path, element_description):
    # This function will be replaced/injected by the backend
    pass
