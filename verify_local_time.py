import sys
import os
from datetime import datetime, timedelta, timezone

# Add the project directory to sys.path
sys.path.append(os.getcwd())

from backend.main import calculate_next_run

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
    target_local = now_local.replace(hour=target_local_hour, minute=0, second=0, microsecond=0)
    if target_local <= now_local:
        target_local += timedelta(days=1)
    expected_utc = target_local.astimezone(timezone.utc).replace(tzinfo=None)
    
    diff = abs((next_run_utc - expected_utc).total_seconds())
    print(f"Difference from expected UTC: {diff} seconds")
    
    assert diff < 2.0, "Calculated next run UTC does not match expected local-to-UTC target time"
    print("Test passed: Local-to-UTC scheduling conversion is working correctly!")

if __name__ == "__main__":
    test_utc_scheduling_with_local_input()
