import os
import sys
import asyncio # <--- 1. Add this import
from services.pipeline import run_master_pipeline

def main():
    print("INITIALIZING BEE-BAKED MASTER RUN...")
    try:
        # 2. Use asyncio.run() to execute the coroutine
        asyncio.run(run_master_pipeline()) 
    except Exception as e:
        print(f"CRITICAL SYSTEM ERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
