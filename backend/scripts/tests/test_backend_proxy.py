
import httpx
import asyncio

async def test_backend_proxy():
    # Request to backend (port 8000), which should proxy to Vite
    urls = [
        "http://127.0.0.1:8000/src/components/StandardAPITesting.tsx"
    ]
    
    async with httpx.AsyncClient(trust_env=False) as client:
        for url in urls:
            print(f"Testing {url}...")
            try:
                resp = await client.get(url)
                print(f"Status: {resp.status_code}")
                print(f"Content-Type: {resp.headers.get('content-type')}")
                if resp.status_code != 200:
                    print(f"Error Content: {resp.text}")
                # print(f"Content: {resp.text[:200]}") # Print first 200 chars
                print("-" * 20)
            except Exception as e:
                print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_backend_proxy())
