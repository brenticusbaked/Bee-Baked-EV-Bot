import os

def env_float(key, default):
    return float(os.getenv(key, default))

# Change this from 0.02 (2.0%) or whatever it is currently to 0.0125
MIN_EV_THRESHOLD = env_float("MIN_EV_THRESHOLD", 0.0125)
NEAR_MISS_THRESHOLD = env_float("NEAR_MISS_THRESHOLD", 0.0075) # Optional: lowered to keep seeing close calls
