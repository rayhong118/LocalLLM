"""Quick test for _extract_job_essentials against a real Greenhouse HTML structure."""
import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from site_skills.sofi import _extract_job_essentials

sample_html = """
<div class="content-intro"><p><a href="https://privacy.sofi.com"><strong>Employee Applicant Privacy Notice</strong></a></p>
<p><strong>Who we are:</strong></p>
<div>
<p>Shape a brighter financial future with us.</p>
<p>Together with our members, we're changing the way people think about personal finance.</p>
<p>We're a next-generation financial services company. <strong>Join us to invest in yourself.</strong></p>
</div></div><p><strong>The Role</strong></p>
<p>SoFi is seeking Senior Software Engineers to lead the development of our platform.</p>
<p><strong>What You'll Do</strong></p>
<ul>
<li>Build backend services for our Lending Platform.</li>
<li>Ensure code quality and deliver scalable services.</li>
</ul>
<p><strong>What You'll Need</strong></p>
<ul>
<li>Bachelor's degree in Computer Science</li>
<li>3+ years as a Software Engineer</li>
<li>Proficient in Java, Kotlin</li>
<li>Experience with Kafka, Docker, Kubernetes</li>
</ul>
<p><strong>Nice To Have</strong></p>
<ul>
<li>Full-Stack experience (React, TypeScript)</li>
<li>Experience with microservices</li>
</ul>
<p>If you have the passion, we want to hear from you.</p><div class="content-conclusion"><div class="gmail_default"><strong>Compensation and Benefits</strong></div>
<div class="gmail_default">The base pay range for this role is listed below.</div>
<h5 style="text-align: center;"><span style="font-weight: 400;">SoFi provides equal employment opportunities (EEO).</span></h5>
<h5 style="text-align: center;"><span style="font-weight: 400;">The Company hires the best qualified candidate.</span></h5>
<div class="gmail_default"><strong>Internal Employees</strong></div>
<div class="gmail_default">If you are a current employee, do not apply here.</div></div>
"""

result = _extract_job_essentials(sample_html)
print("=== EXTRACTED ESSENTIALS ===")
print(result)
print("=== END ===")

# Verify boilerplate is stripped
assert "Shape a brighter financial" not in result, "FAIL: Intro boilerplate was NOT stripped"
assert "Compensation and Benefits" not in result, "FAIL: Conclusion boilerplate was NOT stripped"
assert "equal employment" not in result, "FAIL: EEO boilerplate was NOT stripped"
assert "Internal Employees" not in result, "FAIL: Internal Employees boilerplate was NOT stripped"

# Verify essentials are kept
assert "The Role" in result, "FAIL: 'The Role' section missing"
assert "What You'll Do" in result, "FAIL: 'What You'll Do' section missing"
assert "What You'll Need" in result, "FAIL: 'What You'll Need' section missing"
assert "Nice To Have" in result, "FAIL: 'Nice To Have' section missing"
assert "React" in result, "FAIL: Tech stack details missing"

print("\nAll assertions passed!")
