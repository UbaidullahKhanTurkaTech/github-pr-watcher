import os
import httpx

SLACK_API_URL = "https://slack.com/api"
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_PR_REVIEW_TOKEN")

print("SLACK_BOT_TOKEN = ", SLACK_BOT_TOKEN)

async def get_slack_id_by_email(email: str) -> str | None:
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}"
    }
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{SLACK_API_URL}/users.lookupByEmail?email={email}", headers=headers)
        if response.status_code == 200:
            data = response.json()
            return data.get("user", {}).get("id")
    return None

async def send_slack_message(payload: dict):
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient() as client:
        await client.post(f"{SLACK_API_URL}/chat.postMessage", headers=headers, json=payload)
