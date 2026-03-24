import sys
import os
from datetime import datetime, timedelta, timezone

# Add the project directory to sys.path
sys.path.append(os.getcwd())

from main import calculate_next_run

def test_utc_scheduling_with_local_input():
    # Our code uses datetime.now().astimezone() for local time
    now_local = datetime.now().astimezone()
    now_utc = datetime.utcnow()
    
    print(f"Current local time: {now_local}")
    print(f"Current UTC time: {now_utc}")
    
    # Schedule for 1 hour from now in local time
    target_local_hour = (now_local.hour + 1) % 24
    print(f"Target local hour: {target_local_hour}")
    
    # Calculate next run (returns naive UTC datetime)
    next_run_utc = calculate_next_run("DAILY", target_local_hour)
    print(f"Calculated next run (UTC): {next_run_utc}")
    
    # Expected UTC run time
    # It should be roughly 1 hour from now_utc
    diff = next_run_utc - now_utc
    print(f"Difference from current UTC: {diff}")
    
    assert diff > timedelta(minutes=45) and diff < timedelta(minutes=75), "Next run (UTC) should be roughly 1 hour from current UTC"
    print("Test passed: Local-to-UTC scheduling conversion is working correctly!")

if __name__ == "__main__":
    test_utc_scheduling_with_local_input()
