import httpx
import sys

def test_api():
    base_url = "http://localhost:8000"
    
    print("1. Creating a new saved task...")
    task_data = {
        "prompt": "Test saved task - check for software engineer roles on sofi",
        "frequency": "ONCE",
        "hour_of_day": None
    }
    
    # We must handle the case where the server is not running yet
    try:
        resp = httpx.post(f"{base_url}/saved_tasks", json=task_data)
    except httpx.ConnectError:
        print("Server is not running. Please start the server first.")
        sys.exit(1)
        
    assert resp.status_code == 200, f"Failed: {resp.status_code}"
    saved_task = resp.json()
    saved_task_id = saved_task["id"]
    print(f"   Success: Created saved task with ID {saved_task_id}")
    
    print("\n2. Fetching all saved tasks...")
    resp = httpx.get(f"{base_url}/saved_tasks")
    assert resp.status_code == 200
    saved_list = resp.json()
    print(f"   Success: Found {len(saved_list)} saved tasks")
    
    print(f"\n3. Running the saved task {saved_task_id} immediately...")
    resp = httpx.post(f"{base_url}/saved_tasks/{saved_task_id}/run")
    assert resp.status_code == 200
    spawned_task = resp.json()
    print(f"   Success: Spawned task ID {spawned_task['id']} (status: {spawned_task['status']})")
    
    print(f"\n4. Deleting saved task {saved_task_id}...")
    resp = httpx.delete(f"{base_url}/saved_tasks/{saved_task_id}")
    assert resp.status_code == 200
    print("   Success: Deleted saved task")

if __name__ == "__main__":
    test_api()
