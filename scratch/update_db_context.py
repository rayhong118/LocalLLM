import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from database import SessionLocal, Context

db = SessionLocal()
try:
    ctx = db.query(Context).filter(Context.name == 'How to look for Safeway deals').first()
    if ctx:
        ctx.content = """### SAFEWAY COUPON DRILL-DOWN WORKFLOW

PHASE 1: SETUP
1. Use 'nav_to_url' to go to https://www.safeway.com/loyalty/coupons-deals

PHASE 2: CATEGORY FILTERING (MANDATORY SKILL)
2. MANDATORY: You MUST use the `safeway_filter_category` skill to apply filters (e.g., 'Frozen Foods').
3. Do NOT attempt to find or click checkboxes manually using 'smart_click' or 'find_elements'. The skill handles this for you.
4. Once the broad category filter is applied, proceed to Phase 3.

PHASE 3: DETAILED COUPON INSPECTION
5. MANDATORY: You MUST use the `safeway_click_details` skill for viewing coupon cards.
6. For each potential coupon, provide the brand or keyword (e.g., 'Lucerne', 'Talenti') to the skill.
7. ELIGIBILITY: On the details popup, check 'Eligible Items' for the specific flavor/item requested.

### OUTPUT REQUIREMENTS
List every matching product individually:

Item Name: [Name]
Original Price: [Price]
Deal Price: [Price after coupon]
Item Link: [URL]

FORBIDDEN: Do NOT use the built-in 'extract' tool.
FORBIDDEN: Do NOT search for 'checkbox-state' manually."""
        db.commit()
        print("Success: Context updated via script.")
    else:
        print("Error: Context item not found.")
finally:
    db.close()
