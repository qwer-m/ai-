import requests
import sys

try:
    url = 'http://localhost:5000/api/knowledge-list?page=1&page_size=10'
    print(f"Fetching {url}...")
    resp = requests.get(url)
    print(f"Status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        docs = data.get('documents', [])
        print(f"Docs count: {len(docs)}")
        for d in docs:
            preview = d.get('content_preview', '')
            print(f"Doc: {d.get('filename')} (Type: {d.get('doc_type')})")
            print(f"Preview length: {len(preview)}")
            print(f"Preview snippet: {preview[:20]}...")
            print("-" * 10)
    else:
        print(resp.text)
except Exception as e:
    print(e)
