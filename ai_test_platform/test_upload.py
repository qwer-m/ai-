import requests

url = "http://localhost:8000/api/generate-tests-file"
import uuid

filename = f"test_{uuid.uuid4()}.txt"
content = f"This is a test requirement {uuid.uuid4()}."
files = {'file': (filename, content, 'text/plain')}
data = {
    'project_id': 8,
    'doc_type': 'requirement',
    'compress': False,
    'expected_count': 1,
    'force': True
}

try:
    response = requests.post(url, files=files, data=data)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text[:200]}")
except Exception as e:
    print(f"Error: {e}")
