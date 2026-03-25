
import httpx
import asyncio

async def test_proxy():
    urls = [
        "http://127.0.0.1:5173/src/main.tsx",
        "http://127.0.0.1:5173/@react-refresh",
        "http://127.0.0.1:5173/@vite/client"
    ]
    
    async with httpx.AsyncClient(trust_env=False) as client:
        for url in urls:
            print(f"Testing {url}...")
            try:
                resp = await client.get(url)
                print(f"Status: {resp.status_code}")
                print(f"Content-Type: {resp.headers.get('content-type')}")
                print(f"Content length: {len(resp.content)}")
                print("-" * 20)
            except Exception as e:
                print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_proxy())
