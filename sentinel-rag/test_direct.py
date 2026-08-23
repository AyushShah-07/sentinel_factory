import ollama
import time

print("Testing Ollama directly...")
print("First request will be slow (loading model)...")

start = time.time()

response = ollama.chat(
    model='mistral',
    messages=[{
        'role': 'user',
        'content': 'How many danger incidents are there? Here is the data: [{"timestamp": "2026-08-21T12:13:06.136", "person_id": "P2", "zone_name": "DANGER_1", "incident_type": "DANGER", "dwell_seconds": 0.0}]'
    }]
)

elapsed = time.time() - start
print(f"✅ Response in {elapsed:.2f} seconds")
print(f"Answer: {response['message']['content']}")