import os
import json
from fastapi import FastAPI, Request, Header
from dotenv import load_dotenv
from utils import get_slack_id_by_email, send_slack_message

load_dotenv()

app = FastAPI()

# Load repository to team lead email mapping
with open("repo_team_map.json", "r") as f:
    repo_team_map = json.load(f)

@app.post("/webhook")
async def github_webhook(request: Request, x_github_event: str = Header(None)):
    if x_github_event != "pull_request":
        return {"status": "ignored"}

    payload = await request.json()
    action = payload.get("action")
    if action not in ["opened", "reopened", "ready_for_review", "synchronize"]:
        return {"status": "ignored"}

    repo_name = payload["repository"]["full_name"]
    pr_url = payload["pull_request"]["html_url"]
    pr_author = payload["pull_request"]["user"]["login"]
    pr_title = payload["pull_request"]["title"]
    pr_head = payload["pull_request"]["head"]["ref"]
    pr_base = payload["pull_request"]["base"]["ref"]
    commit_sha = payload["pull_request"]["head"]["sha"]
    short_commit = commit_sha[:7]
    workflow_url = f"https://github.com/{repo_name}/pull/{payload['number']}"

    team_leads = repo_team_map.get(repo_name, [])
    print("Team lead = ", team_leads)
    if not team_leads:
        return {"status": "no_team_leads"}

    slack_ids = []
    for email in team_leads:
        slack_id = await get_slack_id_by_email(email)
        if slack_id:
            slack_ids.append(f"<@{slack_id}>")

    if not slack_ids:
        return {"status": "no_valid_slack_ids"}

    # Format Slack message
    mention_block = " ".join(slack_ids)
    channel = os.getenv("SLACK_CHANNEL", "#github-pr-review-notification")
    
    print("Slack ID = ", mention_block)
    print("Channel = ", channel)
    
    message = {
        "channel": channel,
        "text": "🚨 New Pull Request Notification",
        "blocks": [
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Event:* `pull_request = {action}`"},
                    {"type": "mrkdwn", "text": f"*Ref:* `refs/pull/{payload['number']}/merge`"},
                    {"type": "mrkdwn", "text": f"*Commit:* <{workflow_url}|`{short_commit}`>"}
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
