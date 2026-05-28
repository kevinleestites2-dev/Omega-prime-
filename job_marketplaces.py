"""
OmegaPrime — Job Marketplace Integrations
Hiring Manager (Upwork) + Human Employer (RentAHuman)
"""

import json
import time
import requests
import threading
from typing import Dict, Optional

RAH_API_KEY = "rah_489f3736e4a0e5bd061a1742f6db62e9"
RAH_BASE_URL = "https://rentahuman.ai/api"


class UpworkPoster:
    """Hiring Manager — posts subcontracts when local capability is exceeded"""

    def __init__(self, llm_call_fn):
        self.llm = llm_call_fn

    def post_subcontract(self, job_description: str, budget: float) -> Dict:
        post_content = self.llm(
            f"Write a professional Upwork job post for:\n\n{job_description}\n\n"
            f"Budget: ${budget:.2f}\nReturn JSON with keys: title, description, skills"
        )
        return {
            "status": "drafted",
            "platform": "upwork",
            "content": post_content,
            "budget": budget
        }


class RentAHumanPoster:
    """Human Employer — posts micro-tasks to RentAHuman and tracks completion"""

    def __init__(self, llm_call_fn):
        self.llm = llm_call_fn
        self.task_status: Dict[str, str] = {}
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {RAH_API_KEY}",
            "Content-Type": "application/json"
        })

    def post_task(self, task_description: str, payment: float) -> Dict:
        task_details = self.llm(
            f"Break down this task into clear steps for a human worker:\n\n"
            f"{task_description}\n\nReturn a concise numbered list."
        )
        payload = {
            "title": task_description[:80],
            "description": task_details,
            "payment": payment,
            "category": "general"
        }
        try:
            resp = self.session.post(f"{RAH_BASE_URL}/tasks", json=payload, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            task_id = data.get("id", "unknown")
            self.task_status[task_id] = "pending"
            return {"task_id": task_id, "status": "posted", "payment": payment}
        except Exception as e:
            return {"task_id": None, "status": "error", "error": str(e)}

    def track_completion(self, task_id: str) -> str:
        try:
            resp = self.session.get(f"{RAH_BASE_URL}/tasks/{task_id}", timeout=10)
            resp.raise_for_status()
            status = resp.json().get("status", "unknown")
            self.task_status[task_id] = status
            return status
        except Exception as e:
            return f"error: {e}"

    def monitor_task(self, task_id: str, callback_fn=None, interval: int = 60):
        """Background thread — fires callback when task completes"""
        def _monitor():
            while True:
                status = self.track_completion(task_id)
                if status == "completed":
                    if callback_fn:
                        callback_fn(task_id, status)
                    break
                elif status.startswith("error"):
                    break
                time.sleep(interval)
        threading.Thread(target=_monitor, daemon=True).start()
