import os
import httpx

token = os.environ["BOT_TOKEN"]
base_url = os.environ["PUBLIC_URL"].rstrip("/")
secret = os.environ["WEBHOOK_SECRET"]

response = httpx.post(
    f"https://api.telegram.org/bot{token}/setWebhook",
    json={
        "url": f"{base_url}/telegram/{secret}",
        "secret_token": secret,
        "allowed_updates": ["message", "edited_message"],
        "drop_pending_updates": False
    },
    timeout=20
)
response.raise_for_status()
print(response.json())
