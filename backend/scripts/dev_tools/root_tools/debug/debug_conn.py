import httpx
import asyncio
import os

async def main():
    url = "http://localhost:8000/"
    print(f"Testing connection to {url}")
    for k, v in os.environ.items():
        if "PROXY" in k.upper():
            print(f"{k}: {v}")
    
    try:
        print("--- trust_env=True (default) ---")
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            print(f"Status: {resp.status_code}")
    except Exception as e:
        print(f"Error: {type(e).__name__}: {e}")

    try:
        print("--- trust_env=False ---")
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.get(url)
            print(f"Status: {resp.status_code}")
    except Exception as e:
        print(f"Error: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
