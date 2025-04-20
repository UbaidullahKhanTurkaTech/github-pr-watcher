import os
from datetime import datetime, timedelta, timezone
import time
import requests
from dotenv import load_dotenv

load_dotenv()

STATUS_MAP = {
    "Ready For Review": "289995000000077054",
    "Changes Requested": "289995000000098514",
    "Ready For QA": "289995000000156067",
    "PR Merge": "289995000000164243"
}

CLIENT_ID = os.getenv("ZOHO_CLIENT_ID")
CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET")
PORTAL_NAME = os.getenv("ZOHO_PORTAL_NAME")
PORTAL_ID = 0
GIT_REPO_TOKEN = os.getenv("GITHUB_TOKEN")

print("CLIENT_ID = ", CLIENT_ID)
# class TaskUpdateRequest(BaseModel):
    # partial_title: str
class ZohoTokenManager:
    def __init__(self):
        self.access_token = None
        self.token_generated_time = None
        self.token_lifespan = timedelta(minutes=9)

    def get_access_token(self):
        if not self.access_token or self.is_token_expired():
            self.access_token = self._fetch_new_token()
            self.token_generated_time = datetime.now(timezone.utc)
        return self.access_token

    def is_token_expired(self):
        if not self.token_generated_time:
            return True
        return datetime.now(timezone.utc) - self.token_generated_time > self.token_lifespan

    def _fetch_new_token(self):
        url = 'https://accounts.zoho.in/oauth/v2/token'
        data = {
            'grant_type': 'client_credentials',
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'scope': 'ZohoProjects.tasks.ALL,ZohoProjects.projects.ALL,ZohoProjects.portals.ALL,ZohoProjects.users.ALL',
        }
        response = requests.post(url, data=data)
        if response.status_code == 200:
            print("✅ Refreshed Zoho token.")
            return response.json().get('access_token')
        else:
            raise Exception("❌ Access token error: " + response.text)

token_manager = ZohoTokenManager()

def get_portal_id_by_name(access_token: str, portal_name: str) -> str:
    url = "https://projectsapi.zoho.in/restapi/portals/"
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}"
    }
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        portals = response.json().get("portals", [])
        if not portals:
            raise Exception("❌ No portals found for the current user.")
        
        # Iterate over the portals and find the one with the matching name
        for portal in portals:
            if portal["name"].lower() == portal_name.lower():
                return portal["id"]
        
        raise Exception(f"❌ Portal with name '{portal_name}' not found.")
    else:
        raise Exception(f"❌ Failed to fetch portals: {response.text}")
        
def get_zoho_projects(access_token):
    url = f'https://projectsapi.zoho.in/restapi/portal/{PORTAL_ID}/projects/'
    headers = {'Authorization': f'Zoho-oauthtoken {access_token}'}
    response = requests.get(url, headers=headers)
    return response.json().get("projects", []) if response.status_code == 200 else []

def get_tasks_for_project(access_token, project_id):
    url = f'https://projectsapi.zoho.in/restapi/portal/{PORTAL_ID}/projects/{project_id}/tasks/'
    headers = {'Authorization': f'Zoho-oauthtoken {access_token}'}
    response = requests.get(url, headers=headers)
    return response.json().get("tasks", []) if response.status_code == 200 else []

def find_task_by_partial_title(access_token, partial_title):
    projects = get_zoho_projects(access_token)
    for proj in projects:
        project_id = proj["id"]
        tasks = get_tasks_for_project(access_token, project_id)
        for task in tasks:
            if partial_title in task.get("name", ""):
                return {
                    "project_id": project_id,
                    "task_id": task["id"],
                    "task_title": task["name"]
                }
    return None

def get_task_statuses(access_token, project_id):
    url = f"https://projectsapi.zoho.in/restapi/portal/{PORTAL_ID}/projects/{project_id}/taskstatuses/"
    headers = {'Authorization': f'Zoho-oauthtoken {access_token}'}
    response = requests.get(url, headers=headers)
    return response.json().get("taskstatuses", []) if response.status_code == 200 else []

def fetch_all_tasks_in_project(access_token: str, project_id: str) -> list:
    headers = {'Authorization': f'Zoho-oauthtoken {access_token}'}
    all_tasks = []
    index = 1
    range_size = 200  # max range size as per Zoho API

    while True:
        task_url = (
            f"https://projectsapi.zoho.in/restapi/portal/{PORTAL_ID}/projects/{project_id}/tasks/"
            f"?index={index}&range={range_size}"
        )
        response = requests.get(task_url, headers=headers)

        if response.status_code != 200:
            print(f"Failed to fetch tasks for project {project_id}: {response.text}")
            break

        tasks = response.json().get("tasks", [])
        all_tasks.extend(tasks)

        if len(tasks) < range_size:
            break  # No more pages
        index += range_size

    return all_tasks

def comment_on_task(access_token, portal_id, project_id, task_id, content):
    if len(content) == 0:
        return True
    
    url = f"https://projectsapi.zoho.in/restapi/portal/{portal_id}/projects/{project_id}/tasks/{task_id}/comments/"
    
    headers = {
        "Authorization": f"Zoho-oauthtoken {access_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
            "content": content
            #f"🔗 [View Pull Request]({pr_url})"
    }

    response = requests.post(url, headers=headers, params=payload)
    print("Status:", response.status_code)
    print("Response:", response.text)

    return response.status_code == 200
    
def update_status_with_task_key(task_key: str, target_status_name: str = "Ready for Review", comment: str = f"Nothing to say") -> dict:
    access_token = token_manager.get_access_token()
    projects = get_zoho_projects(access_token)

    for proj in projects:
        project_id = proj["id"]
        tasks = fetch_all_tasks_in_project(access_token, project_id)
        # with open("{}.json".format(proj["name"]), "w", encoding="utf-8") as f:
            # json.dump(tasks, f, indent=4, ensure_ascii=False)
        for task in tasks:
            if task.get("key") == task_key:
                task_id = task["id"]
                task_title = task.get("name")

                # Update status
                update_url = f"https://projectsapi.zoho.in/restapi/portal/{PORTAL_ID}/projects/{project_id}/tasks/{task_id}/"
                headers = {
                    'Authorization': f'Zoho-oauthtoken {token_manager.get_access_token()}',
                    'Content-Type': 'application/json'
                }
                payload = {"custom_status": STATUS_MAP.get(target_status_name)}
                response = requests.post(update_url, headers=headers, params=payload)
                
                comment_on_task(access_token, PORTAL_ID, project_id, task_id, comment)
                
                return {
                    "success": response.status_code == 200,
                    "project_id": project_id,
                    "task_id": task_id,
                    "task_title": task_title,
                    "message": "✅ Status updated successfully"
                    if response.status_code == 200 else f"❌ Update failed: {response.text}"
                }
                
    return {"success": False, "message": f"❌ Task with key '{task_key}' not found in any project."}

# def update_task_status(access_token, project_id, task_id, status_name):
#     update_url = f"https://projectsapi.zoho.in/restapi/portal/{PORTAL_ID}/projects/{project_id}/tasks/{task_id}/"
#     headers = {
#         'Authorization': f'Zoho-oauthtoken {access_token}',
#         'Content-Type': 'application/json'
#     }
#     payload = {"custom_status": STATUS_MAP.get(status_name)}
#     response = requests.post(update_url, headers=headers, params=payload)
#     print(f"🔁 Updated Task ID {task_id} to '{status_name}': {response.status_code}")

def get_merged_prs(repo_full_name: str, TARGET_BRANCH: str, DAYS_LOOKBACK: int = 2):
    since_dt = datetime.now(timezone.utc) - timedelta(days=DAYS_LOOKBACK)

    headers = {
        "Authorization": f"Bearer {GIT_REPO_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

    merged_branches = set()
    page = 1

    while True:
        url = f"https://api.github.com/repos/{repo_full_name}/pulls"
        params = {
            "state": "closed",
            "base": TARGET_BRANCH,
            "sort": "updated",  # GitHub doesn't allow sort=merged
            "direction": "desc",
            "per_page": 100,
            "page": page
        }

        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        prs = response.json()
        
        if not prs:
            break

        for pr in prs:
            merged_at = pr.get("merged_at")
            if not merged_at:
                continue

            merged_time = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
            print(f"[DEBUG] PR: {pr['title']}, merged_at: {merged_time.isoformat()}")

            if merged_time >= since_dt:
                head_branch = pr["head"]["ref"]
                merged_branches.add(head_branch)

        # Go to next page
        page += 1
        time.sleep(0.5)

    return merged_branches

def Read_For_QA(TARGET_BRANCH: str, repo_full_name: str, DAYS_LOOKBACK: int = 2) -> dict:
    access_token = token_manager.get_access_token()
    print(f"Fetching unique source branches merged into `{TARGET_BRANCH}` in the last {DAYS_LOOKBACK} days...\n")
    branches = get_merged_prs(repo_full_name, TARGET_BRANCH, DAYS_LOOKBACK)
    print("🟢 Unique Branches:")
    print(branches)
    if not branches:
        print("⚠️ No task keys found in merged branches.")
        return None
    
    DATA_BACK = {}
        
    projects = get_zoho_projects(access_token)
    
    for proj in projects:
        project_id = proj["id"]
        tasks = fetch_all_tasks_in_project(access_token, project_id)
        
        for task in tasks:
            task_key = task.get("key")
            if task_key == TARGET_BRANCH:
                return None
            if task_key in branches:
                DATA_BACK[task_key] = {
                                            "title" : task.get("name"),
                                            "link" : task.get("link").get("web").get("url"),
                                            "project_id" : project_id,
                                            "task_id" : task["id"]
                                      }
        
    
    for task_key, info in DATA_BACK.items():
        update_url = f"https://projectsapi.zoho.in/restapi/portal/{PORTAL_ID}/projects/{info['project_id']}/tasks/{info['task_id']}/"
        headers = {
            'Authorization': f'Zoho-oauthtoken {access_token}',
            'Content-Type': 'application/json'
        }
        payload = {"custom_status": STATUS_MAP.get("Ready For QA")}
        response = requests.post(update_url, headers=headers, params=payload)
        
    return DATA_BACK
try:
    PORTAL_ID = get_portal_id_by_name(token_manager.get_access_token(), PORTAL_NAME)
    print(f"Portal ID for '{PORTAL_NAME}': {PORTAL_ID}")
except Exception as e:
    print(str(e))
