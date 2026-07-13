from tasks.india_tasks import india_trade_loop
from utils.logger import logger
import logging
import sys



if __name__ == "__main__":
    print("Running India Trade Loop Manually...")
    try:
        india_trade_loop()
        print("Trade Loop Execution Completed.")
    except Exception as e:
        print(f"Error: {e}")
