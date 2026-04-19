import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from skills import controller, get_skill_descriptions

print("Skill Descriptions:")
desc = get_skill_descriptions()
print(desc)

excluded = ['save_as_pdf', 'screenshot', 'extract']
for tool in excluded:
    if tool in desc:
        print(f"WARNING: {tool} is still in descriptions!")
    else:
        print(f"SUCCESS: {tool} is excluded from descriptions.")

# Note: extract is built-in, so exclude_action might work differently or it might just be the others.
# Actually I only excluded save_as_pdf and screenshot in skills.py.
