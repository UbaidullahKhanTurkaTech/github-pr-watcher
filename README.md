# Centralized GitHub PR Watcher (FastAPI)

## 🚀 Overview

This FastAPI service listens to GitHub Pull Request webhook events and notifies team leads via Slack based on repository mappings.

## 📦 Setup

1. Clone or deploy this repo (Railway, Render, etc.)
2. Create a `.env` file:

```
cp .env.example .env
```

3. Add your Slack Bot token and preferred channel.
4. Update `repo_team_map.json` to map GitHub repos to lead emails.

## 📬 GitHub Webhook

- **URL**: `https://your-domain.com/webhook`
- **Event**: `pull_request`
- **Content type**: `application/json`

## 🧪 Local Run

```
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

## 📝 Example Mapping

```json
{
  "my-org/my-repo": ["lead1@example.com", "lead2@example.com"]
}
```