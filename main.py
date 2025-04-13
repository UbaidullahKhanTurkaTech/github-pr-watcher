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
                "assigned",
                "auto_merge_disabled",
                "auto_merge_enabled",
                "closed",
                "converted_to_draft",
                "demilestoned",
                "dequeued",
                "edited",
                "enqueued",
                "labeled",
                "locked",
                "milestoned",
                "opened",
                "ready_for_review",
                "reopened",
                "review_request_removed",
                "review_requested",
                "synchronize",
                "unassigned",
                "unlabeled",
                "unlocked"
            ]

# Load repository to team lead email mapping
with open("repo_team_map.json", "r") as f:
    repo_team_map = json.load(f)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
AUTHOR_EMAIL = os.getenv("AUTHOR_EMAIL")

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
        if action not in PR_Actions:
            return

        await handle_pr_event(payload)

    except Exception as e:
        print(f"[ERROR] Failed to process webhook: ", e)
        traceback.print_exc()


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
            "closed": "PR was *closed*",
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

        # Get who performed the action
        actor = payload["sender"]["login"]
        actor_email = await get_email_from_pr_commits(repo_name, pr_number)
        actor_mention = f"`{actor}`"
        if actor_email:
            slack_id = await get_slack_id_by_email(actor_email)
            if slack_id:
                actor_mention = f"<@{slack_id}>"

        action_actor_mention = actor_mention

        # Logic for closed PR
        if action == "closed":
            merged = payload['pull_request'].get("merged", False)
            closer_slack_mention = action_actor_mention
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
                f"This <{pr_url}|PR> was *{merge_method_status if merged else 'closed without merge'}* by {closer_slack_mention}.\n"
                f"`[TL]` {' '.join(team_lead_mentions) if team_lead_mentions else 'N/A'} Kindly review the <{pr_url}|PR> closed.\n"
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
                    f"`[TL]` {' '.join(team_lead_mentions) if team_lead_mentions else 'N/A'}, "
                    f"please review this <{pr_url}|PR> now after edits."
                )
            elif merge_status == "❌ `Has conflicts`":
                Message_in_Body = (
                    f"`[TL]` {' '.join(team_lead_mentions) if team_lead_mentions else 'N/A'} "
                    f"Please ask `[Developer]` {action_actor_mention} to resolve this <{pr_url}| PR>."
                )
            else:
                Message_in_Body = (
                    f"`[TL]` {' '.join(team_lead_mentions) if team_lead_mentions else 'N/A'} "
                    f"PR has been edited by {action_actor_mention}. Please review <{pr_url}|PR>."
                )

        # Logic for converted_to_draft PR
        elif action == "converted_to_draft":
            Message_in_Body = (
                f"`[TL]` {' '.join(team_lead_mentions) if team_lead_mentions else 'N/A'} "
                f"This PR has been converted to *Draft* mode.\n<{pr_url}|PR>"
            )

        # Default logic for opened / reopened / synchronize
        elif action in ["opened", "reopened", "synchronize"]:
            if merge_status == "✅":
                Message_in_Body = (
                    f"`[TL]` {' '.join(team_lead_mentions) if team_lead_mentions else 'N/A'} "
                    f"Kindly review this <{pr_url}| PR>{' recently edited by the author.' if action == 'synchronize' else ''}"
                )
            elif merge_status == "❌ `Has conflicts`":
                Message_in_Body = (
                    f"`[TL]` {' '.join(team_lead_mentions) if team_lead_mentions else 'N/A'} "
                    f"Please ask `[Developer]` {action_actor_mention} to resolve this <{pr_url}| PR>."
                )
            else:
                Message_in_Body = (
                    f"`[TL]` {' '.join(team_lead_mentions) if team_lead_mentions else 'N/A'} "
                    f"Please review this <{pr_url}| PR>."
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
                            f"{emoji_map[action]} {header_map[action]} by {action_actor_mention}:\n"
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

        # PR author mention
        pr_author = payload["pull_request"]["user"]["login"]
        author_email = await get_email_from_pr_commits(repo_name, pr_number)
        author_slack_mention = f"`{pr_author}`"
        if author_email:
            slack_id = await get_slack_id_by_email(author_email)
            if slack_id:
                author_slack_mention = f"<@{slack_id}>"

        # Action performer (who locked/unlocked)
        actor = payload["sender"]["login"]
        actor_email = await get_email_from_pr_commits(repo_name, pr_number)  # fallback
        actor_mention = f"`{actor}`"
        if actor_email:
            slack_id = await get_slack_id_by_email(actor_email)
            if slack_id:
                actor_mention = f"<@{slack_id}>"

        Message_in_Body = (
            f"{lock_emoji} {lock_title}\n"
            f"`[TL]` {' '.join(team_lead_mentions)} This <{pr_url}|PR> by {author_slack_mention} "
            f"has been `{lock_text}` by {actor_mention}."
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

                actor = payload["sender"]["login"]
                actor_email = await get_email_from_pr_commits(repo_name, pr_number)
                actor_mention = f"`{actor}`"
                if actor_email:
                    slack_id = await get_slack_id_by_email(actor_email)
                    if slack_id:
                        actor_mention = f"<@{slack_id}>"

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
                                    f"`[TL]` {' '.join(team_lead_mentions)} Labels {summary} on this <{pr_url}|PR> by {actor_mention}.\n"
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
        actor = payload["sender"]["login"]

        # Mention formatting
        actor_email = await get_email_from_pr_commits(repo_name, pr_number)
        actor_mention = f"`{actor}`"
        if actor_email:
            slack_id = await get_slack_id_by_email(actor_email)
            if slack_id:
                actor_mention = f"<@{slack_id}>"

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
                            f"{icon} Auto-merge was {verb} by {actor_mention} on this PR.\n"
                            f":twisted_rightwards_arrows: *Branch:* `{pr_head}` → `{pr_base}`\n"
                            f"<{pr_url}|PR>"
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

        # Get the user who added or removed the milestone
        user = payload["sender"].get("login", "unknown user")
        user_email = payload["sender"]["login"]
        # Fetch the Slack ID for the user who actioned the PR
        slack_id = await get_slack_id_by_email(user_email)

        if slack_id:
            actioned_by_message = f"<@{slack_id}>"
        else:
            actioned_by_message = f"{user}"

        # Format the team lead mentions (TL) in the message
        TL_Info = ' '.join([f"{tl}" for tl in team_lead_mentions])  # Assuming `team_lead_mentions` is defined elsewhere

        # Append action details
        Message_in_Body += (
            f"Actioned by: {actioned_by_message}\n"
            f"`[TL]` {TL_Info} {additional_message}. Kindly check the <{pr_url}|PR> here.\n"
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
        Message_in_Body = f"`[TL]` {' '.join(team_lead_mentions)} This PR was dequeued from a merge queue.\n<{pr_url}|PR>"
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
                            f"`[TL]` {' '.join(team_lead_mentions)} This PR was added to a merge queue.\n<{pr_url}|PR>"
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
                            f"`[TL]` {' '.join(team_lead_mentions)} This draft PR is now *ready for review*.\n<{pr_url}|PR>"
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
        reviewers = payload.get("requested_reviewers", [])
        requested_mentions = ", ".join(f"`{r['login']}`" for r in reviewers)
        Message_in_Body = (
                            f"🧐 Review Requested\n"
                            f"`[TL]` {' '.join(team_lead_mentions)} Review has been requested from {requested_mentions}.\n<{pr_url}|PR>"
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
        reviewers = payload.get("requested_reviewers", [])
        removed_mentions = ", ".join(f"`{r['login']}`" for r in reviewers)
        Message_in_Body = (
                            f"🚫 Review Request Removed\n"
                            f"`[TL]` {' '.join(team_lead_mentions)} Review request was removed for {removed_mentions}.\n<{pr_url}|PR>"
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
                return "✅" if mergeable else "❌ `Has conflicts`"

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
