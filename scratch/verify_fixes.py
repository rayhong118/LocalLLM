import sys
import os

# Add root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from skills import get_skill_descriptions

print("Skill Descriptions:")
desc = get_skill_descriptions()
print(desc)

if "safeway_filter_category" in desc:
    print("\nSUCCESS: safeway_filter_category is registered!")
else:
    print("\nFAILURE: safeway_filter_category not found.")

if "No skill descriptions available" not in desc:
    print("SUCCESS: Orchestrator regsitry access fixed!")
else:
    print("FAILURE: Orchestrator registry access still failing.")
