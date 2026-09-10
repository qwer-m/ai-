"""统一环境文件路径与加载顺序，供 API、Worker 和启动器共同使用。"""

from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent
BACKEND_ENV_PATH = BACKEND_DIR / ".env"
PROJECT_ENV_PATH = PROJECT_DIR / ".env"


def load_environment() -> None:
    """进程环境优先，其次后端配置，最后仓库根配置。"""

    load_dotenv(BACKEND_ENV_PATH)
    load_dotenv(PROJECT_ENV_PATH)
