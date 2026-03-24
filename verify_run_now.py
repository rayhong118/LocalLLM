import requests
import time

def test_run_now():
    # 1. Create a DAILY task
    res = requests.post("http://localhost:8000/tasks", json={
        "prompt": "Test Daily Task",
        "frequency": "DAILY",
        "hour_of_day": 12
    })
    res.raise_for_status()
    task = res.json()
    task_id = task["id"]
    print(f"Created task {task_id}: {task['status']}, next_run: {task['next_run_at']}")

    # 2. Call run_now
    print(f"Calling run_now on task {task_id}...")
    res = requests.post(f"http://localhost:8000/tasks/{task_id}/run_now")
    res.raise_for_status()
    
    # 3. Check status is RUNNING or PENDING
    time.sleep(2)
    res = requests.get(f"http://localhost:8000/tasks/{task_id}")
    updated_task = res.json()
    print(f"Updated task: {updated_task['status']}, next_run: {updated_task['next_run_at']}")
    
    if updated_task["next_run_at"] is None:
        print("ERROR: next_run_at was cleared!")
    else:
        print("SUCCESS: next_run_at was preserved!")

    # 4. Cleanup
    requests.delete(f"http://localhost:8000/tasks/{task_id}")
    print(f"Task {task_id} deleted.")

if __name__ == "__main__":
    test_run_now()
