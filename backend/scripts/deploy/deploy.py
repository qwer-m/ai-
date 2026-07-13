import os
import sys
import subprocess
import time
import shutil
import requests
from datetime import datetime

# Configuration
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_DIR = os.path.join(APP_DIR, "backups")
HEALTH_URL = "http://localhost:8000/api/health"
MAX_RETRIES = 5
RETRY_INTERVAL = 10
DOCKER_COMPOSE_CMD = os.getenv("DOCKER_COMPOSE_CMD", "docker-compose")
MYSQL_USER = os.getenv("MYSQL_USER", os.getenv("DB_USER", "root"))
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", os.getenv("DB_PASSWORD", ""))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", os.getenv("DB_NAME", "ai_test_platform"))


def mysql_auth_args():
    auth = f"-u {MYSQL_USER}"
    if MYSQL_PASSWORD:
        auth += f" -p{MYSQL_PASSWORD}"
    return auth

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def run_command(cmd, cwd=None):
    log(f"Running: {cmd}")
    try:
        if cwd is None:
            cwd = APP_DIR
        subprocess.check_call(cmd, shell=True, cwd=cwd)
        return True
    except subprocess.CalledProcessError as e:
        log(f"Command failed: {e}")
        return False

def backup():
    log("Starting backup...")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # 1. Backup DB (MySQL Dump) - Assuming docker environment
    dump_file = os.path.join(BACKUP_DIR, f"db_backup_{timestamp}.sql")
    # This command works if mysql client is installed on host, or via docker exec
    # Trying docker exec approach first
    cmd = f"{DOCKER_COMPOSE_CMD} exec -T mysql mysqldump {mysql_auth_args()} {MYSQL_DATABASE} > {dump_file}"
    if not run_command(cmd):
        log("Warning: DB backup failed (Docker running?). Skipping DB backup.")
    
    # 2. Backup Code (Simple Copy for rollback demonstration)
    # In real world, we use git tags/commits
    code_backup_zip = os.path.join(BACKUP_DIR, f"code_{timestamp}")
    shutil.make_archive(code_backup_zip, 'zip', APP_DIR)
    
    log(f"Backup completed: {timestamp}")
    return timestamp

def rollback(timestamp):
    log(f"Rolling back to {timestamp}...")
    
    # 1. Restore DB
    dump_file = os.path.join(BACKUP_DIR, f"db_backup_{timestamp}.sql")
    if os.path.exists(dump_file):
        cmd = f"{DOCKER_COMPOSE_CMD} exec -T mysql mysql {mysql_auth_args()} {MYSQL_DATABASE} < {dump_file}"
        run_command(cmd)
    
    # 2. Restore Code (Unzip) - CAREFUL: This overrides current directory
    # In real world, we revert git commit or docker image tag
    log("Skipping code restore (zip overwrite is risky in script). Please revert via Git.")
    
    # 3. Restart Services
    run_command(f"{DOCKER_COMPOSE_CMD} restart app worker")
    
    log("Rollback triggered.")

def check_health():
    log("Checking health...")
    for i in range(MAX_RETRIES):
        try:
            resp = requests.get(HEALTH_URL, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("mysql", {}).get("ok") and data.get("redis", {}).get("ok"):
                    log("Health check PASSED.")
                    return True
                else:
                    log(f"Health check warning: {data}")
            else:
                log(f"Health check failed: Status {resp.status_code}")
        except Exception as e:
            log(f"Health check exception: {e}")
        
        time.sleep(RETRY_INTERVAL)
        log(f"Retrying health check ({i+1}/{MAX_RETRIES})...")
    
    return False

def deploy():
    log("Starting deployment...")
    
    # 1. Backup
    timestamp = backup()
    
    # 2. Update Code (Simulated)
    # run_command("git pull origin main")
    
    # 3. Rebuild/Restart
    if not run_command(f"{DOCKER_COMPOSE_CMD} up -d --build app worker"):
        log("Deployment failed during docker-compose up.")
        rollback(timestamp)
        return

    # 4. Verify
    if not check_health():
        log("Health check FAILED. Initiating auto-rollback...")
        rollback(timestamp)
        
        # Verify rollback
        if check_health():
            log("Rollback successful. System restored.")
        else:
            log("CRITICAL: Rollback failed or system still unhealthy.")
    else:
        log("Deployment SUCCESSFUL.")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "rollback":
        if len(sys.argv) > 2:
            rollback(sys.argv[2])
        else:
            print("Usage: python deploy.py rollback <timestamp>")
    else:
        deploy()
