import requests
import os
from dotenv import load_dotenv
import json

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
print("CLIENT_ID = ", CLIENT_ID)
# class TaskUpdateRequest(BaseModel):
    # partial_title: str
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
        
def get_access_token():
    url = 'https://accounts.zoho.in/oauth/v2/token'
    data = {
        'grant_type': 'client_credentials',
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'scope': 'ZohoProjects.tasks.ALL,ZohoProjects.projects.ALL,ZohoProjects.portals.ALL,ZohoProjects.users.ALL',
    }
    response = requests.post(url, data=data)
    if response.status_code == 200:
        return response.json().get('access_token')
    else:
        raise HTTPException(status_code=500, detail="Access token error: " + response.text)

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

def update_task_status(access_token, project_id, task_id, status_id):
    url = f"https://projectsapi.zoho.in/restapi/portal/{PORTAL_ID}/projects/{project_id}/tasks/{task_id}/"
    headers = {
        'Authorization': f'Zoho-oauthtoken {access_token}',
        'Content-Type': 'application/json'
    }
    data = {
        "status": status_id
    }
    response = requests.post(url, headers=headers, json=data)
    return response.status_code == 200

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
    access_token = get_access_token()
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
                # statuses = get_task_statuses(access_token, project_id)
                # matching_status = next((s for s in statuses if s["name"].lower() == target_status_name.lower()), None)

                # if not matching_status:
                    # return {
                        # "success": False,
                        # "message": f"Status '{target_status_name}' not found in project {project_id}."
                    # }

                # Update status
                update_url = f"https://projectsapi.zoho.in/restapi/portal/{PORTAL_ID}/projects/{project_id}/tasks/{task_id}/"
                headers = {
                    'Authorization': f'Zoho-oauthtoken {get_access_token()}',
                    'Content-Type': 'application/json'
                }
                payload = {"custom_status": STATUS_MAP.get(target_status_name)}
                response = requests.post(update_url, headers=headers, params=payload)
                
                # url = "https://projectsapi.zoho.in/restapi/portals/"
                # headers = {
                    # "Authorization": f"Zoho-oauthtoken {access_token}"
                # }
                # response = requests.get(url, headers=headers)
                # if response.status_code == 200:
                    # print(response.json())
                    # portals = response.json().get("portals", [])
                    # if not portals:
                        # raise Exception("❌ No portals found for the current user.")
                    # # You can return the first portal or match by name if needed
                    # return portals[0]["id"]
                # else:
                    # raise Exception(f"❌ Failed to fetch portal ID: {response.text}")
                
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

try:
    PORTAL_ID = get_portal_id_by_name(get_access_token(), PORTAL_NAME)
    print(f"Portal ID for '{PORTAL_NAME}': {PORTAL_ID}")
except Exception as e:
    print(str(e))
# def update_ready_for_review(partial_title: str) -> dict:
    # access_token = get_access_token()
    # task_info = find_task_by_partial_title(access_token, partial_title)
    
    # if not task_info:
        # return {"success": False, "message": f"No task found with title containing '{partial_title}'."}

    # statuses = get_task_statuses(access_token, task_info["project_id"])
    # ready_status = next((s for s in statuses if s["name"].lower() == "ready for review"), None)

    # if not ready_status:
        # return {"success": False, "message": "'Ready for Review' status not found."}

    # success = update_task_status(access_token, task_info["project_id"], task_info["task_id"], ready_status["id"])

    # return {
        # "success": success,
        # "task_title": task_info["task_title"],
        # "message": "Updated successfully" if success else "Update failed"
    # }

# # @app.post("/update-task-status/")
# def update_ready_for_review(request: TaskUpdateRequest):
    # access_token = get_access_token()
    # task_info = find_task_by_partial_title(access_token, request.partial_title)
    
    # if not task_info:
        # raise HTTPException(status_code=404, detail="Task not found.")

    # statuses = get_task_statuses(access_token, task_info["project_id"])
    # ready_status = next((s for s in statuses if s["name"].lower() == "ready for review"), None)

    # if not ready_status:
        # raise HTTPException(status_code=404, detail="'Ready for Review' status not found.")

    # success = update_task_status(access_token, task_info["project_id"], task_info["task_id"], ready_status["id"])

    # if success:
        # return {"message": "✅ Task status updated to 'Ready for Review'", "task_title": task_info["task_title"]}
    # else:
        # raise HTTPException(status_code=500, detail="❌ Failed to update task status.")
