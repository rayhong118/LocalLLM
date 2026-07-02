import re

def contains_genuine_failure(text: str) -> bool:
    direct_fail_words = [
        "i failed", "terminated", "no task results", "hallucination", 
        "captcha", "bot detection", "access denied", "security check", 
        "verify you are human", "blocked"
    ]
    lower_text = text.lower()
    if any(dfw in lower_text for dfw in direct_fail_words):
        return True

    if "unable to" in lower_text:
        return True

    for match in re.finditer(r"\b(fail|failed|failure|failures|failing)\b", lower_text):
        start, end = match.span()
        
        preceding = lower_text[max(0, start - 25):start].strip()
        following = lower_text[end:min(len(lower_text), end + 25)].strip()

        preceding_words = re.findall(r"\b\w+\b", preceding)
        if preceding_words:
            # Check the last 3 words preceding the failure keyword
            negation_words = {"0", "zero", "no", "none", "without"}
            if any(w in negation_words for w in preceding_words[-3:]):
                continue

        # Fix: word boundary only after word-based outcomes, not brackets
        if re.match(r"^(?:\s*\w+)*\s*:\s*(?:(?:0|zero|none|null|false)\b|\[\s*\])", following):
            continue

        return True

    return False

test_cases = [
    # Should match (genuine failures)
    ("I failed to find the price of eggs", True),
    ("The search failed", True),
    ("Task execution resulted in a failure.", True),
    ("failing to load the page", True),
    ("failed searches: 3", True),
    ("failed searches: 1", True),
    
    # Should NOT match (negated or zero failures)
    ("0 failed", False),
    ("failed: 0", False),
    ("failures: 0", False),
    ("failed: []", False),
    ("failed searches: none", False),
    ("failed searches: zero", False),
    ("no failures found", False),
    ("zero failure", False),
    ("without failure", False),
    ("processed without any fail", False),
    ("no real fail", False),
]

passed = True
for text, expected in test_cases:
    res = contains_genuine_failure(text)
    if res != expected:
        print(f"FAIL: '{text}' -> expected={expected}, got={res}")
        passed = False
    else:
        print(f"PASS: '{text}' -> got={res}")

if passed:
    print("All contains_genuine_failure tests passed!")
