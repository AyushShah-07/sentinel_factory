# test.py
import requests
import time

print("Testing API...")
start = time.time()

response = requests.post(
    "http://localhost:8001/ask",
    json={"question": "How many danger incidents?"},
    timeout=60
)

elapsed = time.time() - start
print(f"✅ Response in {elapsed:.2f} seconds")
print(f"Answer: {response.json()['answer']}")