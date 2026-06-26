import json
import re

backup_path = r"C:\Users\rayho\AppData\Roaming\Antigravity IDE\Backups\8884ad93cdeb17d897d0e420d646004f\untitled\-7f9c1a2e"
content = open(backup_path, 'rb').read().split(b'\n', 1)[1]
data = json.loads(content)
jobs = data['jobs']

def is_manager_role(title: str) -> bool:
    title_norm = re.sub(r'[-\_]', ' ', title.lower())
    pattern = r'\b(manager|director|vp|vice\s+president|head|chief)\b'
    return bool(re.search(pattern, title_norm))

def is_explicit_frontend_fullstack(title: str) -> bool:
    if is_manager_role(title):
        return False
    title_norm = re.sub(r'[-\_]', ' ', title.lower())
    pattern = r'\b(frontend|front\s+end|fullstack|full\s+stack|web|react|javascript|typescript|angular|vue|ui|ux)\b'
    return bool(re.search(pattern, title_norm))

def is_generic_software_engineering(title: str) -> bool:
    if is_manager_role(title):
        return False
    title_norm = re.sub(r'[-\_]', ' ', title.lower())
    if is_explicit_frontend_fullstack(title):
        return False
    
    keywords = [
        "software engineer", "software developer", "developer", "programmer", "systems engineer", "application engineer"
    ]
    is_eng = any(kw in title_norm for kw in keywords) or "engineer" in title_norm
    
    exclude_pattern = r'\b(data\s+scientist|data\s+science|analyst|teller|agent|underwriter|specialist|recruiter|marketing|sales|coach|designer|product\s+manager|project\s+manager|program\s+manager|scrum\s+master|incident\s+commander|business\s+manager|controls|payroll|security\s+manager|photographer|hr|operations|treasury|rewards|billing|compliance|fraud)\b'
    
    if re.search(exclude_pattern, title_norm):
        if is_eng:
            non_eng_pattern = r'\b(product\s+manager|project\s+manager|program\s+manager|recruiter|scrum\s+master|designer|teller|underwriter|coach|photographer|analyst)\b'
            if re.search(non_eng_pattern, title_norm):
                return False
            return True
        return False
    return is_eng

def is_unlikely_frontend(title: str) -> bool:
    title_norm = re.sub(r'[-\_]', ' ', title.lower())
    unlikely = [
        "backend", "data engineer", "data infrastructure", "devops", 
        "infrastructure", "crypto", "database", "security engineer", "firmware", "site reliability"
    ]
    if any(u in title_norm for u in unlikely):
        return True
    if re.search(r'\bsre\b', title_norm):
        return True
    return False

def check_description_heuristics(title: str, content: str) -> tuple[bool, str]:
    cleaned = re.sub(r'<[^>]+>', ' ', content)
    cleaned_lower = cleaned.lower()
    pattern = r'\b(full\s*-\s*stack|full\s+stack|fullstack|front\s*-\s*end|front\s+end|frontend)\b'
    match = re.search(pattern, cleaned_lower)
    if match:
        matched_word = match.group(1)
        return True, f"Found '{matched_word}' in description (heuristic match)"
    return False, ""

# Let's run the mock pipeline
explicit_matches = []
heuristic_matches = []
llm_candidates = []

for job in jobs:
    title = job.get("title", "")
    content_html = job.get("content", "")
    
    if is_manager_role(title):
        continue
        
    cleaned_content = re.sub(r'<[^>]+>', ' ', content_html)
    cleaned_content_lower = cleaned_content.lower()
    react_present = bool(re.search(r'\b(react|reactjs|react\.js)\b', cleaned_content_lower))
    if not react_present:
        continue
        
    job_item = {
        "title": title,
        "content": content_html
    }
    
    if is_explicit_frontend_fullstack(title):
        explicit_matches.append(job_item)
    elif is_generic_software_engineering(title) and not is_unlikely_frontend(title):
        has_heur, reason = check_description_heuristics(title, content_html)
        if has_heur:
            job_item["reason"] = reason
            heuristic_matches.append(job_item)
        else:
            llm_candidates.append(job_item)

print("Explicit Matches:")
for j in explicit_matches:
    print(f"  - {j['title']}")
print("\nHeuristic Matches:")
for j in heuristic_matches:
    print(f"  - {j['title']} ({j['reason']})")
print("\nLLM Candidates:")
for j in llm_candidates:
    print(f"  - {j['title']}")
