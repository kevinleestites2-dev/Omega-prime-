"""
OmegaPrime — Enterprise Agent
Public Telegram gateway + Agentalent.ai listing
"""

import subprocess
import threading
import requests
import time
import logging
from typing import Dict, List, Optional

TELEGRAM_TOKEN = "8679655550:AAGUB1m5fmqHc8OHqqM24Vixz8FfwX-gqD4"
TELEGRAM_CHAT_ID = "7135054241"

log = logging.getLogger("AgentalentLister")


def send_telegram(message: str):
    """Fire a Telegram message to the Forgemaster"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        log.error(f"Telegram send failed: {e}")


class AgentalentLister:
    """
    Enterprise Agent — lists OmegaPrime on Agentalent.ai as a hireable AI agent
    and sets up a public Telegram webhook for inbound hire requests.
    """

    def __init__(self):
        self.public_url: Optional[str] = None
        self.tunnel_process = None

    def setup_tunnel(self) -> str:
        """Spin up a Cloudflare quick tunnel on port 8080"""
        try:
            proc = subprocess.Popen(
                ["cloudflared", "tunnel", "--url", "http://localhost:8080"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            self.tunnel_process = proc
            # Parse the public URL from cloudflared output
            for line in proc.stdout:
                if "trycloudflare.com" in line or "ngrok" in line:
                    url = [w for w in line.split() if "https://" in w]
                    if url:
                        self.public_url = url[0].strip()
                        log.info(f"Tunnel live: {self.public_url}")
                        return self.public_url
        except FileNotFoundError:
            log.warning("cloudflared not found — tunnel skipped")
        self.public_url = "http://localhost:8080"
        return self.public_url

    def list_on_agentalent(self, capabilities: List[str], price_per_task: str = "$10") -> Dict:
        """Register OmegaPrime on Agentalent.ai"""
        if not self.public_url:
            self.setup_tunnel()

        agent_profile = {
            "name": "OmegaPrime — Autonomous Labor Agent",
            "capabilities": capabilities,
            "telegram_gateway": f"{self.public_url}/telegram/webhook",
            "price_per_task": price_per_task,
            "model": "qwen2.5-coder:7b (local)",
            "response_time": "<10s",
            "available": True
        }

        try:
            resp = requests.post(
                "https://agentalent.ai/api/agents/register",
                json=agent_profile,
                timeout=15
            )
            agent_id = resp.json().get("id", "pending")
            status = "listed"
        except Exception as e:
            agent_id = "offline"
            status = f"agentalent unreachable: {e}"

        msg = (
            f"🤖 OmegaPrime listed as hire-able agent\n"
            f"URL: {self.public_url}\n"
            f"Price: {price_per_task}/task\n"
            f"Capabilities: {', '.join(capabilities)}\n"
            f"Status: {status}"
        )
        send_telegram(msg)
        return {"agent_id": agent_id, "status": status, "url": self.public_url}

    def handle_hire_request(self, request_data: Dict) -> Dict:
        """Process an inbound hire request"""
        command = request_data.get("command", "")
        chat_id = request_data.get("chat_id", TELEGRAM_CHAT_ID)
        send_telegram(f"📥 Hire request received\nFrom: {chat_id}\nTask: {command}")
        return {"received": True, "task": command}

    def start(self):
        """Launch agent mode — tunnel + listing"""
        capabilities = [
            "python_automation",
            "data_entry",
            "content_writing",
            "web_scraping",
            "task_delegation"
        ]
        result = self.list_on_agentalent(capabilities)
        return result

    def stop(self):
        if self.tunnel_process:
            self.tunnel_process.terminate()
            log.info("Tunnel stopped")
