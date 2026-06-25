# scratch/test_pagination.py
from fastapi.testclient import TestClient
import sys
import os

# Add parent directory to path so main can be imported
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app

client = TestClient(app)

print("--- Testing /tasks pagination ---")

# 1. Fetch all tasks (no parameters)
response_all = client.get("/tasks")
if response_all.status_code == 200:
    tasks_all = response_all.json()
    total_count = response_all.headers.get("X-Total-Count")
    print(f"Fetch all success! Total tasks returned: {len(tasks_all)}")
    print(f"X-Total-Count header value: {total_count}")
    
    # 2. Fetch paginated tasks (page=1, limit=2)
    response_paginated = client.get("/tasks?page=1&limit=2")
    if response_paginated.status_code == 200:
        tasks_paginated = response_paginated.json()
        print(f"Fetch paginated success! Page 1 (limit 2) tasks returned: {len(tasks_paginated)}")
        for idx, task in enumerate(tasks_paginated):
            safe_prompt = task.get('prompt')[:40].encode('ascii', errors='ignore').decode('ascii')
            print(f"  [{idx}] ID: {task.get('id')}, Prompt: '{safe_prompt}...'")
        
        # 3. Check Access-Control-Expose-Headers
        exposed = response_paginated.headers.get("Access-Control-Expose-Headers")
        print(f"Access-Control-Expose-Headers header value: {exposed}")
        
        if len(tasks_paginated) <= 2:
            print("SUCCESS: Pagination works correctly!")
        else:
            print("ERROR: Pagination returned more than limit!")
    else:
        print(f"ERROR: Paginated fetch failed with status {response_paginated.status_code}")
else:
    print(f"ERROR: Fetch all failed with status {response_all.status_code}")
