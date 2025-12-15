
import asyncio
import json
import logging
import aiohttp
from azure.identity import AzureDeveloperCliCredential

# Revert to original endpoint
ENDPOINT = "https://aoai-e3y4nkdhqnh4q.services.ai.azure.com/api/projects/proj-e3y4nkdhqnh4q/applications/attempt-1/protocols/openai/responses?api-version=2025-11-15-preview"
SCOPE = "https://ai.azure.com/.default"

logging.basicConfig(level=logging.INFO)

async def run_test():
    print(f"Authenticating...")
    cred = AzureDeveloperCliCredential()
    token_obj = cred.get_token(SCOPE)
    token = token_obj.token
    print(f"Got token: {token[:10]}...")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Test CamelCase parameters
    payload_1 = {
        "messages": [{"role": "user", "content": "Hello world"}],
        "conversationId": "test-conv-123",
        "stream": False
    }

    payload_2 = {
        "messages": [{"role": "user", "content": "Hello world"}],
        "sessionId": "test-session-123",
        "stream": False
    }

    test_cases = [
        ("CamelCase conversationId", payload_1),
        ("CamelCase sessionId", payload_2),
    ]

    with open("debug_output.txt", "w") as f:
        async with aiohttp.ClientSession() as session:
            for name, payload in test_cases:
                f.write(f"\n--- Testing {name} ---\n")
                f.write(f"Payload: {json.dumps(payload, indent=2)}\n")
                print(f"Testing {name}...")
                try:
                    async with session.post(ENDPOINT, headers=headers, json=payload) as resp:
                        f.write(f"Status: {resp.status}\n")
                        f.write(f"Headers: {dict(resp.headers)}\n")
                        body = await resp.text()
                        f.write(f"Body: {body}\n")
                except Exception as e:
                    f.write(f"Error: {e}\n")

if __name__ == "__main__":
    asyncio.run(run_test())
