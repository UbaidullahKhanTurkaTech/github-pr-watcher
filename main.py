#########################################################################################
# Author: Ubaidullah Khan
# License: 2025 - ?
#########################################################################################
import os
import json
import asyncio
import httpx
from fastapi import FastAPI, Request, Header, BackgroundTasks
from dotenv import load_dotenv
from utils import get_slack_id_by_email, send_slack_message

load_dotenv()

app = FastAPI()

# Load repository to team lead email mapping
with open("repo_team_map.json", "r") as f:
    repo_team_map = json.load(f)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

async def fetch_mergeable_state(repo_name: str, pr_number: int) -> str:
    """Poll GitHub API until the PR's mergeable state is resolved."""
    url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_number}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

    async with httpx.AsyncClient() as client:
        for attempt in range(3):
            response = await client.get(url, headers=headers)

            if response.status_code != 200:
                print(f"[ERROR] GitHub API error: {response.status_code} - {response.text}")
                return "❓ Merge status fetch failed"

            data = response.json()
            mergeable = data.get("mergeable")

            if mergeable is not None:
                return "✅ Mergeable" if mergeable else "❌ Has conflicts"

            print(f"[INFO] mergeable is null, retrying... ({attempt + 1}/3)")
            await asyncio.sleep(1)

    return "⏳ Merge status still unknown"

@app.post("/webhook")
async def github_webhook(request: Request, background_tasks: BackgroundTasks, x_github_event: str = Header(None)):
    if x_github_event != "pull_request":
        return {"status": "ignored"}

    payload = await request.json()
    action = payload.get("action")
    if action not in ["opened", "reopened", "ready_for_review", "synchronize"]:
        return {"status": "ignored"}

    # Respond fast, process in background
    background_tasks.add_task(handle_pr_event, payload)
    return {"status": "accepted"}

async def handle_pr_event(payload: dict):
    repo_name = payload["repository"]["full_name"]
    pr_number = payload["number"]
    pr_url = payload["pull_request"]["html_url"]
    pr_author = payload["pull_request"]["user"]["login"]
    pr_title = payload["pull_request"]["title"]
    pr_head = payload["pull_request"]["head"]["ref"]
    pr_base = payload["pull_request"]["base"]["ref"]
    commit_sha = payload["pull_request"]["head"]["sha"]
    short_commit = commit_sha[:7]
    workflow_url = f"https://github.com/{repo_name}/pull/{pr_number}"

    team_leads = repo_team_map.get(repo_name, [])
    print("[INFO] Team lead emails:", team_leads)
    if not team_leads:
        return

    slack_ids = []
    for email in team_leads:
        slack_id = await get_slack_id_by_email(email)
        if slack_id:
            slack_ids.append(f"<@{slack_id}>")

    if not slack_ids:
        print("[WARN] No Slack user IDs resolved")
        return

    # 🔍 Get mergeable status from GitHub
    merge_status = await fetch_mergeable_state(repo_name, pr_number)

    # Format Slack message
    mention_block = " ".join(slack_ids)
    channel = os.getenv("SLACK_CHANNEL", "#github-pr-review-notification")

    print("[INFO] Slack mentions:", mention_block)
    print("[INFO] Sending message to channel:", channel)

    message = {
        "channel": channel,
        "text": "🚨 New Pull Request Notification",
        "blocks": [
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Event:* `pull_request = {payload['action']}`"},
                    {"type": "mrkdwn", "text": f"*Ref:* `refs/pull/{pr_number}/merge`"},
                    {"type": "mrkdwn", "text": f"*Commit:* <{workflow_url}|`{short_commit}`>"},
                    {"type": "mrkdwn", "text": f"*Mergeable:* {merge_status}"}
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":rotating_light: *New PR* opened by `{pr_author}`:\n<{pr_url}|{pr_title}>\n\n:twisted_rightwards_arrows: *Branch:* `{pr_head}` → `{pr_base}`\n:bust_in_silhouette: *Team Lead(s):* {mention_block}"
                }
            }
        ]
    }

    await send_slack_message(message)
    return {"status": "notified"}
