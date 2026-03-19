# utils.py
import json
from datetime import datetime

def save_to_json(data, filename="scraped_data.json"):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    entry = {"timestamp": timestamp, "data": data}
    
    # Simple JSON save (overwrite for now as per placeholder)
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=4)

def save_to_markdown(data, filename="scraped_data.md"):
    """Saves the provided data as a markdown file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = f"# Scraped Data - {timestamp}\n\n{data}\n"
    
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Results saved to {filename}")