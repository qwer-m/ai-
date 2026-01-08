import os
from redis import ConnectionPool

# Get Redis configuration from environment variables
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

# Create a shared connection pool
# max_connections=100 ensures we don't exhaust Redis connections
# health_check_interval=30 ensures we don't use dead connections
redis_pool = ConnectionPool(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    password=REDIS_PASSWORD,
    decode_responses=True,
    max_connections=100,
    health_check_interval=30
)
