import requests
import json

try:
    # Assuming backend is running on localhost:5000 (based on setup usually)
    # But wait, the environment might be different. 
    # Let's check main.py to see the port. usually 5000.
    
    response = requests.get('http://localhost:5000/api/knowledge-list?page=1&page_size=10')
    if response.status_code == 200:
        data = response.json()
        print(f"Total documents: {len(data.get('documents', []))}")
        for doc in data.get('documents', []):
            print(f"ID: {doc.get('id')}, GlobalID: {doc.get('global_id')}, Type: {doc.get('doc_type')}, Filename: {doc.get('filename')}")
            preview = doc.get('content_preview')
            print(f"Preview length: {len(preview) if preview else 0}")
            if preview:
                print(f"Preview start: {preview[:50]}...")
            else:
                print("No preview content")
            print("-" * 20)
    else:
        print(f"Error: {response.status_code} - {response.text}")
except Exception as e:
    print(f"Exception: {e}")
