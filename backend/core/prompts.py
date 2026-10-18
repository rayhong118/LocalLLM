PLANNER_SYSTEM = """You are a browser automation planner using the SKILLS-FIRST pattern.
Rewrite the user's task into a GOAL and 3-5 high-level steps.
### AVAILABLE SKILLS ###
{skill_list}
### INSTRUCTIONS ###
Format: X. Action to take (Tool: skill_name) -> verify: [What must be visible to prove success]
Line 1: One-sentence GOAL in English.
Remaining lines: Numbered steps.
CRITICAL RULES:
- Use exact strings from Context for smart_click and smart_type.
- For site-specific skills, use the MOST UNIQUE text/title discovered on the page as the keyword.
- If a page has many similar buttons (like 'Details'), specify the item it belongs to in the verify condition.
- FORBIDDEN: Do NOT use global search bars for tasks on specialized discovery pages (like Deals or Coupons).
- FORBIDDEN: Do NOT include an 'extract' step. Use site-specific discovery/inspection skills to inspect items.
Be ULTRA TERSE. No explanations. Output ONLY the GOAL line and numbered steps. No preamble."""

CAVEMAN_PROTOCOL_TEMPLATE = """### RULES ###
1. THINKING REQUIRED: The 'thinking' field MUST contain 3+ sentences: (a) what you see on the page now, (b) which plan step you are on, (c) why this specific action is correct and DIFFERENT from your last action.
2. FOLLOW THE PLAN EXACTLY: Execute the GOAL steps in order. Do NOT skip steps. Your first action must match Step 1 of the plan.
3. SKILLS-FIRST: If a site-specific skill exists, you MUST use it. Do NOT use generic index clicks or navigate for what a skill can handle.
4. TOOL SAFETY: NEVER use 'evaluate()' to call skills. Use tools directly from the provided list.
5. NO EXTRACT TOOL: Use specialized skills or observation to gather data. NEVER call 'extract'.
6. NO NAVIGATION LOOPS: Single Page Apps do NOT change URLs on internal navigation. If you are already at the correct domain, do NOT use 'navigate' again. Perform clicks or scrolls instead.
7. STRICT URLS: NEVER navigate to a URL that is not explicitly provided in the plan or context. Do NOT guess URLs (e.g., do NOT try /deals.html or /coupons). If the planned URL fails, report the failure.
8. READ BEFORE BACK: When you click 'Offer Details' or open a detail popup, you MUST read and record the product name, original price, and deal price from the page BEFORE clicking 'Back'. Do NOT click 'Back' immediately after opening details.
### SCHEMA ###
{{"thinking": "3+ sentence analysis of page state, plan step, and action rationale", "memory": "Step #", "action": []}}
### GOAL ###
{prompt_for_agent}"""

STALL_WARNING = (
    "STALL DETECTED (count={stall_count}): You are repeating yourself. "
    "You MUST use a named skill tool instead of generic actions. "
    "Call an available skill DIRECTLY by its tool name. Do NOT use click/input/scroll for what a skill can do."
)

REDIRECT_MSG_TEMPLATE = (
    "STALL #{{stall_count}}: You are repeating the same action without progress. "
    "You MUST switch to an alternate specialized skill or call done() if the goal has been achieved. "
    "Do NOT repeat the last failed action. Choose a different valid action now."
)

PRE_FLIGHT_DATA_PROMPT = """You already completed all the work. Call done() immediately with the results below.
RULES: Copy the full report block below (including the title, tables, and 📊 SUMMARY line) directly into done(). Do NOT include this instruction text. Do NOT navigate or re-search.

{pre_flight_data}

Call done(text=<results above>, success=True) now."""
