# utils.py
import json
from datetime import datetime

def save_to_json(data, filename="scraped_data.json"):
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    entry = {"timestamp": timestamp, "data": data}
    
    # Logic to append to a list in a JSON file
    # ...