# scratch/create_test_task.py
import sqlite3
import subprocess
import sys
import os

def main():
    db_path = "tasks.db"
    prompt = "find deals for green onion and sweet potatoes on weee"
    
    # 1. Connect to SQLite database
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    # 2. Insert new PENDING task
    print("Inserting test task for Weee deals into database...")
    c.execute(
        "INSERT INTO tasks (prompt, status, frequency, created_at, updated_at) VALUES (?, 'PENDING', 'ONCE', datetime('now'), datetime('now'))",
        (prompt,)
    )
    task_id = c.lastrowid
    conn.commit()
    conn.close()
    
    print(f"Successfully created Task ID: {task_id} with prompt: '{prompt}'")
    
    # 3. Execute the agent runner on this task
    print(f"Spawning agent process: uv run python agent.py {task_id} \"{prompt}\"")
    # Change directory to project root if needed
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen([sys.executable, "agent.py", str(task_id), prompt], stdout=sys.stdout, stderr=sys.stderr, env=env)
    proc.wait()
    print(f"Agent process finished with code: {proc.returncode}")

if __name__ == "__main__":
    main()
