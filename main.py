
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
import traceback
from collections import defaultdict
from datetime import datetime, timedelta

# Temporary in-memory storage for debounce
label_event_buffer = defaultdict(lambda: {"labeled": set(), "unlabeled": set(), "last_updated": datetime.utcnow()})

load_dotenv()

app = FastAPI()

PR_Actions = [
    "assigned", "auto_merge_disabled", "auto_merge_enabled", "closed", "converted_to_draft",
    "demilestoned", "dequeued", "edited", "enqueued", "labeled", "locked", "milestoned",
    "opened", "ready_for_review", "reopened", "review_request_removed", "review_requested",
    "synchronize", "unassigned", "unlabeled", "unlocked"
]

# Load mapping files
with open("repo_team_map.json", "r") as f:
    repo_team_map = json.load(f)

with open("user_map_emails.json", "r") as f:
    user_map_emails = json.load(f)

def resolve_email_from_username(username: str) -> str | None:
    return user_map_emails.get(username)

async def resolve_slack_mention(username: str) -> str:
    email = resolve_email_from_username(username)
    if email:
        slack_id = await get_slack_id_by_email(email)
        if slack_id:
            return f"<@{slack_id}>"
    return f"`{username}`"

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
AUTHOR_EMAIL = os.getenv("AUTHOR_EMAIL")

@app.get("/health", tags=["Health Check"])
async def health_check():
    return {"status": "ok", "service": "GitHub PR Watcher"}
    
@app.post("/webhook")
async def github_webhook(request: Request, x_github_event: str = Header(None)):
    raw_body = await request.body()
    asyncio.create_task(handle_webhook(raw_body, x_github_event))
    return {"status": "accepted"}

async def handle_webhook(raw_body: bytes, event_type: str):
    try:
        if event_type != "pull_request":
            return
        payload = json.loads(raw_body)
        action = payload.get("action")
        if action not in PR_Actions:
            return
        # with open("payload.json", "w", encoding="utf-8") as f:
            # json.dump(payload, f, indent=4, ensure_ascii=False)
        await handle_pr_event(payload)
    except Exception as e:
        print(f"[ERROR] Failed to process webhook: ", e)
        traceback.print_exc()

async def get_email_of_merger(repo_name: str, pr_number: int) -> tuple[str | None, str | None]:
    pr_api_url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_number}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    async with httpx.AsyncClient() as client:
        pr_resp = await client.get(pr_api_url, headers=headers)
        if pr_resp.status_code != 200:
            return None, None
        pr_data = pr_resp.json()
        merge_commit_sha = pr_data.get("merge_commit_sha")
        if not merge_commit_sha:
            return None, None
        commit_url = f"https://api.github.com/repos/{repo_name}/commits/{merge_commit_sha}"
        commit_resp = await client.get(commit_url, headers=headers)
        if commit_resp.status_code != 200:
            return None, None
        commit_data = commit_resp.json()
        email = commit_data.get("commit", {}).get("author", {}).get("email")
        login = commit_data.get("author", {}).get("login")
        return email, login

async def fetch_mergeable_state(repo_name: str, pr_number: int) -> str:
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
                return "✅" if mergeable else "❌ `Has conflicts`"
            print(f"[INFO] mergeable is null, retrying... ({attempt + 1}/3)")
            await asyncio.sleep(1)
    return "⏳ Merge status still unknown"

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
    
    PR_AUTHOR = payload["pull_request"]["user"]["login"]  # the one who originally opened the PR action username
    PR_ACTOR = payload["sender"]["login"]  # the one who performed the
    
    # Team leads (if any)
    team_leads = repo_team_map.get(repo_name, [])
    team_lead_mentions = []
    for email in team_leads:
        slack_id = await get_slack_id_by_email(email)
        if slack_id:
            team_lead_mentions.append(f"<@{slack_id}>")
    
    team_lead_mentions = ' '.join(team_lead_mentions) if team_lead_mentions else 'N/A'
    
    # 🧠 Get PR author's email from commit history
    PR_AUTHOR_SLACK = await resolve_slack_mention(PR_AUTHOR)
    PR_ACTOR_SLACK = await resolve_slack_mention(PR_ACTOR)

    # 🔍 Get mergeable status from GitHub
    merge_status = await fetch_mergeable_state(repo_name, pr_number)
    
    Message_in_Body = ""
            
    channel = os.getenv("SLACK_CHANNEL", "#github-pr-review-notification")
    
    if payload['action'] in ["opened", "reopened", "synchronize", "closed", "edited", "converted_to_draft"]:
        action = payload['action']

        # Slack formatting maps
        emoji_map = {
            "opened": ":rotating_light:",
            "reopened": ":arrows_counterclockwise:",
            "synchronize": ":rotating_light:",
            "closed": ":lock:",
            "edited": ":pencil2:",
            "converted_to_draft": ":memo:"
        }

        header_map = {
            "opened": "*New PR* opened",
            "reopened": "PR was *reopened*",
            "synchronize": "*New PR* updated",
            "closed": "PR was *opened*",
            "edited": "PR was *edited*",
            "converted_to_draft": "PR was *converted to draft*"
        }

        status_map = {
            "opened": "`Recently Created`",
            "reopened": "`Reopened`",
            "synchronize": "`Opened PR file edited / changed during active PR`",
            "edited": None,
            "closed": None,
            "converted_to_draft": "`Draft Mode Enabled`"
        }

        # Logic for closed PR
        if action == "closed":
            # Determine who closed/merged the PR
            # Get merger's email from the merge commit
            merged = payload['pull_request'].get("merged", False)
            merge_method_status = "Not Merged"

            if merged:
                pr_api_url = f"https://api.github.com/repos/{repo_name}/pulls/{pr_number}"
                headers = {"Authorization": f"token {GITHUB_TOKEN}"}
                async with httpx.AsyncClient() as client:
                    pr_response = await client.get(pr_api_url, headers=headers)
                    if pr_response.status_code == 200:
                        pr_data = pr_response.json()
                        merge_commit_sha = pr_data.get("merge_commit_sha")
                        commit_api_url = f"https://api.github.com/repos/{repo_name}/git/commits/{merge_commit_sha}"
                        commit_response = await client.get(commit_api_url, headers=headers)
                        if commit_response.status_code == 200:
                            commit_data = commit_response.json()
                            parent_count = len(commit_data.get("parents", []))
                            signature = commit_data.get("verification", {}).get("signature")

                            if parent_count == 2:
                                merge_method_status = "Merge Commit"
                            elif parent_count == 1:
                                merge_method_status = "Squash and Merged" if signature else "Rebase and Merged"
                            else:
                                merge_method_status = "Unknown Merge Type"
                        else:
                            merge_method_status = "Merged"
                    else:
                        merge_method_status = "Merged"

            status_map["closed"] = f"`{'Closed Merged PR' if merged else 'Closed PR without merge'}`"
            Message_in_Body = (
                f"This <{pr_url}|PR> was *{merge_method_status if merged else 'closed without merge'}* by {PR_ACTOR_SLACK}.\n"
                f"`[TL]` {team_lead_mentions} Kindly review the <{pr_url}|PR> closed.\n"
            )

        # Logic for edited PR
        elif action == "edited":
            edited_parts = []
            changes = payload.get("changes", {})
            if "title" in changes:
                edited_parts.append("Title")
            if "body" in changes:
                edited_parts.append("Description")
            if "base" in changes:
                edited_parts.append("Base branch")
            edit_summary = ", ".join(edited_parts) if edited_parts else "_Unknown edits_"
            status_map["edited"] = f"`{edit_summary} Edited`"

            if merge_status == "✅":
                Message_in_Body = (
                    f"`[TL]` {team_lead_mentions}, Please review this <{pr_url}|PR> now after edits by {PR_ACTOR_SLACK}."
                )
            elif merge_status == "❌ `Has conflicts`":
                Message_in_Body = (
                    f"`[TL]` {team_lead_mentions}, Please ask `[Developer]` {PR_ACTOR_SLACK} / {PR_AUTHOR_SLACK} to resolve this <{pr_url}| PR>."
                )
            else:
                Message_in_Body = (
                    f"`[TL]` {team_lead_mentions}, PR has been edited by {PR_ACTOR_SLACK}. Please review <{pr_url}|PR>."
                )

        # Logic for converted_to_draft PR
        elif action == "converted_to_draft":
            Message_in_Body = (
                f"`[TL]` {team_lead_mentions}, This <{pr_url}|PR> has been converted to *Draft* mode.\n"
            )

        # Default logic for opened / reopened / synchronize
        elif action in ["opened", "reopened", "synchronize"]:
            if merge_status == "✅":
                Message_in_Body = (
                    f"`[TL]` {team_lead_mentions} "
                    f"Kindly review this <{pr_url}| PR>{f' recently edited by the {PR_ACTOR_SLACK}.' if action == 'synchronize' else ''}"
                )
            elif merge_status == "❌ `Has conflicts`":
                Message_in_Body = (
                    f"`[TL]` {team_lead_mentions} "
                    f"Please ask `[Developer]` {PR_ACTOR_SLACK} to resolve this <{pr_url}| PR>."
                )
            else:
                Message_in_Body = (
                    f"`[TL]` {team_lead_mentions} Please review this <{pr_url}| PR>."
                )

        # Final Slack message structure
        message = {
            "channel": channel,
            "text": f"{emoji_map[action]} Pull Request Notification",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"{emoji_map[action]} {header_map[action]} by {PR_AUTHOR_SLACK}:\n"
                            f":twisted_rightwards_arrows: *Branch:* `{pr_head}` → `{pr_base}`\n"
                            f"Current Status: {status_map[action]}"
                        )
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Commit:* <{workflow_url}|`{short_commit}`>"},
                        {"type": "mrkdwn", "text": f"*Mergeable:* `{merge_method_status}`" if action == "closed" else f"*Mergeable:* {merge_status}"}
                    ]
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": Message_in_Body
                    }
                }
            ]
        }

        await send_slack_message(message)
        
    elif payload['action'] in ["locked", "unlocked"]:
        lock_action = payload['action']  # "locked" or "unlocked"
        lock_emoji = ":lock:" if lock_action == "locked" else ":unlock:"
        lock_text = "locked" if lock_action == "locked" else "unlocked"
        lock_title = "PR Locked" if lock_action == "locked" else "PR Unlocked"

        Message_in_Body = (
            f"{lock_emoji} {lock_title}\n"
            f"`[TL]` {team_lead_mentions} This <{pr_url}|PR> by opened by {PR_AUTHOR_SLACK} "
            f"has been `{lock_text}` by {PR_ACTOR_SLACK}."
        )

        message = {
            "channel": channel,
            "text": f"{lock_emoji} {lock_title}",
            "blocks": [
                {
                    "type": "section",
                    "text": { "type": "mrkdwn", "text": Message_in_Body }
                }
            ]
        }

        await send_slack_message(message)

    elif payload['action'] in ["labeled", "unlabeled"]:
        repo_pr_key = f"{repo_name}#{pr_number}"
        action_type = payload["action"]
        label_name = payload.get("label", {}).get("name", "")
        label_name = f"`{label_name}`"

        # Add to buffer
        label_event_buffer[repo_pr_key][action_type].add(label_name)
        label_event_buffer[repo_pr_key]["last_updated"] = datetime.utcnow()

        # Define async flush inside but only run in background
        async def flush_labels_after_delay(repo_pr_key, repo_name, pr_number, pr_url, team_lead_mentions):
            await asyncio.sleep(1.2)  # wait to accumulate events
            now = datetime.utcnow()
            last = label_event_buffer[repo_pr_key]["last_updated"]
            if (now - last) >= timedelta(seconds=1.1):
                added = label_event_buffer[repo_pr_key]["labeled"]
                removed = label_event_buffer[repo_pr_key]["unlabeled"]

                added_str = ", ".join(sorted(added)) if added else ""
                removed_str = ", ".join(sorted(removed)) if removed else ""

                summary_parts = []
                if added_str:
                    summary_parts.append(f"*added* {added_str}")
                if removed_str:
                    summary_parts.append(f"*removed* {removed_str}")

                summary = " and ".join(summary_parts)

                message = {
                    "channel": channel,
                    "text": "🏷️ Labels Updated on PR",
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    f"🏷️ Labels Updated on PR.\n"
                                    f"`[TL]` {team_lead_mentions} Labels {summary} on this <{pr_url}|PR> by {PR_ACTOR_SLACK}.\n"
                                )
                            }
                        }
                    ]
                }

                await send_slack_message(message)
                del label_event_buffer[repo_pr_key]

        # Schedule the flush task only
        asyncio.create_task(flush_labels_after_delay(repo_pr_key, repo_name, pr_number, pr_url, team_lead_mentions))

    elif payload['action'] in ["auto_merge_enabled", "auto_merge_disabled"]:
        action = payload['action']
        # Dynamic values
        title = "✅ Auto-Merge Enabled" if action == "auto_merge_enabled" else "🚫 Auto-Merge Disabled"
        icon = ":white_check_mark:" if action == "auto_merge_enabled" else ":no_entry_sign:"
        verb = "*enabled*" if action == "auto_merge_enabled" else "*disabled*"

        message = {
            "channel": channel,
            "text": title,
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"{icon} Auto-merge was {verb} by {PR_ACTOR_SLACK} on this PR.\n"
                            f":twisted_rightwards_arrows: *Branch:* `{pr_head}` → `{pr_base}`\n"
                            f"`[TL]` {team_lead_mentions} Please have a look at this <{pr_url}|PR>."
                        )
                    }
                }
            ]
        }

        await send_slack_message(message)

    elif payload['action'] in ["assigned", "unassigned"]:
        action = payload['action']
        assignee = payload["assignee"]["login"]
        actor = payload["sender"]["login"]  # who performed the action

        # Default mentions
        assignee_mention = f"`{assignee}`"
        actor_mention = f"`{actor}`"

        # Attempt to resolve Slack IDs
        assignee_email = await get_email_from_pr_commits(repo_name, pr_number)
        actor_email = await get_email_from_pr_commits(repo_name, pr_number)
        if assignee_email:
            slack_id = await get_slack_id_by_email(assignee_email)
            if slack_id:
                assignee_mention = f"<@{slack_id}>"

        if actor_email:
            slack_id = await get_slack_id_by_email(actor_email)
            if slack_id:
                actor_mention = f"<@{slack_id}>"

        tl_mentions = ' '.join(team_lead_mentions) if team_lead_mentions else "N/A"
        verb = "*assigned*" if action == "assigned" else "*unassigned*"
        emoji = ":heavy_plus_sign:" if action == "assigned" else ":heavy_division_sign:" 

        message = {
            "channel": channel,
            "text": ":heavy_plus_sign: Pull Request Assigned" if action == "assigned" else ":heavy_division_sign: Pull Request Unassigned",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"{emoji} {actor_mention} {verb} {assignee_mention} {'to' if action == 'assigned' else 'from'} this PR.\n"
                            f":twisted_rightwards_arrows: *Branch:* `{pr_head}` → `{pr_base}`\n"
                            f"{tl_mentions} please be informed about this <{pr_url}|PR>.\n"
                        )
                    }
                }
            ]
        }
        await send_slack_message(message)
        
    elif payload['action'] in ["milestoned", "demilestoned"]:
        # Check if milestone exists in the PR payload
        milestone = payload["pull_request"].get("milestone")
        
        # If there's no milestone, handle it gracefully for "demilestoned"
        if milestone:
            milestone_title = milestone.get("title", "unknown")
            milestone_due_date = milestone.get("due_on", "No due date set")
        else:
            milestone_title = "No milestone"
            milestone_due_date = "No due date set"
        
        # Determine the action type and corresponding message
        if payload['action'] == "milestoned":
            action_message = "📌 PR Milestoned"
            additional_message = f"assigned to milestone `{milestone_title}`"
            # Include milestone info in the message
            Message_in_Body = (
                f"{action_message}\n"
                f"Milestone: `{milestone_title}`\n"
                f"Due Date: `{milestone_due_date}`\n"
            )
        else:  # For "demilestoned"
            action_message = "🚫 Milestone Removed"
            additional_message = f"removed from the milestone"
            # Only include the title of the removed milestone
            Message_in_Body = (
                f"{action_message}\n"
                f"Removed Milestone: `{milestone_title}`\n"
            )

        # Append action details
        Message_in_Body += (
            f"Actioned by: {PR_ACTOR_SLACK}\n"
            f"`[TL]` {team_lead_mentions} {additional_message}. Kindly check the <{pr_url}|PR> here.\n"
        )
        
        # Prepare the message
        message = {
            "channel": channel,
            "text": action_message,
            "blocks": [
                {
                    "type": "section",
                    "text": { "type": "mrkdwn", "text": Message_in_Body }
                }
            ]
        }
        
        # Send the Slack message
        await send_slack_message(message)
    
    elif payload['action'] == "dequeued":
        Message_in_Body = f"This PR was dequeued from a merge queue by {PR_ACTOR_SLACK}.\n `[TL]` {team_lead_mentions}, Kindly have a look at this <{pr_url}|PR>."
        message = {
            "channel": channel,
            "text": "⏳ PR Dequeued",
            "blocks": [
                { "type": "section", "text": { "type": "mrkdwn", "text": Message_in_Body } }
            ]
        }
        await send_slack_message(message)
        
    elif payload['action'] == "enqueued":
        Message_in_Body = (
                            f"📥 PR Enqueued\n"
                            f"This PR was added to a merge queue By {PR_ACTOR_SLACK}.\n `[TL]` {team_lead_mentions} Kindly check this <{pr_url}|PR>."
                        )
        message = {
            "channel": channel,
            "text": "📥 PR Enqueued",
            "blocks": [
                { "type": "section", "text": { "type": "mrkdwn", "text": Message_in_Body } }
            ]
        }
        await send_slack_message(message)
        
    elif payload['action'] == "ready_for_review":
        Message_in_Body = (
                            f"✅ PR Ready for Review\n"
                            f"`[TL]` {team_lead_mentions} This draft <{pr_url}|PR> is now *ready for review*.\n"
                        )
        message = {
            "channel": channel,
            "text": "✅ PR Ready for Review",
            "blocks": [
                { "type": "section", "text": { "type": "mrkdwn", "text": Message_in_Body } }
            ]
        }
        await send_slack_message(message)
        
    elif payload['action'] == "review_requested":
        reviewers = payload["pull_request"].get("requested_reviewers", [])
    
        # 🔁 Resolve GitHub usernames to Slack mentions using your helper
        requested_mentions = []
        for reviewer in reviewers:
            github_username = reviewer["login"]
            mention = await resolve_slack_mention(github_username)
            requested_mentions.append(mention)

        requested_mentions_str = ", ".join(requested_mentions) if requested_mentions else "`Reviewer Not Found`"

        print(requested_mentions)
        Message_in_Body = (
                            f"🧐 Review Requested\n"
                            f"`[TL]` {team_lead_mentions} Review has been requested for this <{pr_url}|PR> from {requested_mentions_str} by {PR_ACTOR_SLACK}.\n"
                        )
        message = {
            "channel": channel,
            "text": "🧐 Review Requested",
            "blocks": [
                { "type": "section", "text": { "type": "mrkdwn", "text": Message_in_Body } }
            ]
        }
        await send_slack_message(message)
        
    elif payload['action'] == "review_request_removed":
        reviewers = payload.get("requested_reviewer", [])
        removed_mentions = await resolve_slack_mention(reviewers['login'])
        Message_in_Body = (
                            f"🚫 Review Request Removed\n"
                            f"`[TL]` {team_lead_mentions} Review request was removed for <{pr_url}|PR>. Member Removed:  {removed_mentions} by {PR_ACTOR_SLACK}.\n"
                        )
        message = {
            "channel": channel,
            "text": "🚫 Review Request Removed",
            "blocks": [
                { "type": "section", "text": { "type": "mrkdwn", "text": Message_in_Body } }
            ]
        }
        await send_slack_message(message)
                
    else:
        slack_id = await get_slack_id_by_email({AUTHOR_EMAIL})
        author_slack_mention = f"<@{slack_id}>"
        message = {
            "channel": channel,
            "text": "👀 New Pull Request Notification",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f":eyes: PR Unknown Event Found.\n"
                            f"Current Status: `Event: {payload['action']} Devops Check it`\n"
                        )
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{author_slack_mention} Kindly add event for this action {payload['action']}."
                    }
                }
            ]
        }
        
        await send_slack_message(message)
