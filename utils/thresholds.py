import os

def env_float(key, default):
    """Retrieves an environment variable as a float."""
    return float(os.getenv(key, default))

# UPDATED: Lowered to 1.25% to increase alert volume
MIN_EV_THRESHOLD = env_float("MIN_EV_THRESHOLD", 0.0125)

# UPDATED: Lowered to 0.75% to keep tracking close opportunities in the moderator channel
NEAR_MISS_THRESHOLD = env_float("NEAR_MISS_THRESHOLD", 0.0075)
