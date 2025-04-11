#########################################################################################
# Author: Ubaidullah Khan
# License: 2025 - ?
#########################################################################################
import os
import json
import asyncio
import httpx
from fastapi import FastAPI, Request, Header
from dotenv import load_dotenv
from utils import get_slack_id_by_email, send_slack_message

load_dotenv()

app = FastAPI()

# Load repository to team lead email mapping
with open("repo_team_map.json", "r") as f:
    repo_team_map = json.load(f)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# 📌 Respond immediately, then parse and process in background
@app.post("/webhook")
async def github_webhook(request: Request, x_github_event: str = Header(None)):
    raw_body = await request.body()
    asyncio.create_task(handle_webhook(raw_body, x_github_event))
    return {"status": "accepted"}  # Respond immediately to avoid GitHub timeout


async def handle_webhook(raw_body: bytes, event_type: str):
    try:
        if event_type != "pull_request":
            return

        payload = json.loads(raw_body)
        action = payload.get("action")
        # if action not in ["opened", "reopened", "ready_for_review", "synchronize"]:
        #     return

        await handle_pr_event(payload)

    except Exception as e:
        print(f"[ERROR] Failed to process webhook: {e}")


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

    # Team leads (if any)
    team_leads = repo_team_map.get(repo_name, [])
    team_lead_mentions = []
    for email in team_leads:
        slack_id = await get_slack_id_by_email(email)
        if slack_id:
            team_lead_mentions.append(f"<@{slack_id}>")

    # 🧠 Get PR author's email from commit history
    author_email = await get_email_from_pr_commits(repo_name, pr_number)
    author_slack_mention = f"`{pr_author}`"  # fallback
    if author_email:
        slack_id = await get_slack_id_by_email(author_email)
        if slack_id:
            author_slack_mention = f"<@{slack_id}>"
            print(f"[INFO] PR author Slack ID: {slack_id}")
        else:
            print(f"[WARN] Could not resolve Slack ID for PR author email: {author_email}")
    else:
        print(f"[WARN] Could not extract email for PR author {pr_author}")

    # 🔍 Get mergeable status from GitHub
    merge_status = await fetch_mergeable_state(repo_name, pr_number)

    channel = os.getenv("SLACK_CHANNEL", "#github-pr-review-notification")
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
                    "text": (
                        f":rotating_light: *New PR* opened by {author_slack_mention}:\n"
                        f"<{pr_url}|{pr_title}>\n\n"
                        f":twisted_rightwards_arrows: *Branch:* `{pr_head}` → `{pr_base}`\n"
                        f":bust_in_silhouette: *Team Lead(s):* {' '.join(team_lead_mentions) if team_lead_mentions else 'N/A'}"
                    )
                }
            }
        ]
    }

    await send_slack_message(message)


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


async def get_email_from_pr_commits(repo_name: str, pr_number: int) -> str | None:
    """Extracts email from commits in the PR (only if email is not hidden)."""
    url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_number}/commits"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        if response.status_code != 200:
            print(f"[ERROR] Could not fetch commits for PR #{pr_number}")
            return None

        commits = response.json()
        for commit in commits:
            author = commit.get("commit", {}).get("author", {})
            email = author.get("email")
            if email and "noreply" not in email:
                print(f"[INFO] Found email from commit: {email}")
                return email

    return None


@app.get("/health", tags=["Health Check"])
async def health_check():
    return {"status": "ok", "service": "GitHub PR Watcher"}
