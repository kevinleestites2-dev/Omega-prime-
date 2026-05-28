"""
OmegaPrime Extended — Full Marketplace Integration
Plugs Hiring Manager + Human Employer + Enterprise Agent into OmegaPrime core.

Drop this file into the Omega-prime- directory and run:
    python omega_prime_extended.py
"""

import os
import sys
import time
import logging
from typing import Dict

from job_marketplaces import UpworkPoster, RentAHumanPoster
from agent_marketplace import AgentalentLister, send_telegram

log = logging.getLogger("OmegaPrimeExtended")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

# ─── LLM bridge (calls local Ollama) ────────────────────────────────────────

import requests as _requests

OLLAMA_BASE  = os.getenv("OLLAMA_BASE", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")

def llm_call(prompt: str) -> str:
    """Call local Ollama LLM and return the response string"""
    try:
        resp = _requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=60
        )
        return resp.json().get("response", "").strip()
    except Exception as e:
        log.error(f"LLM call failed: {e}")
        return ""

# ─── Decision Engine ─────────────────────────────────────────────────────────

class OmegaPrimeExtended:
    """
    Extends OmegaPrime with 3 marketplace channels:
      1. Hiring Manager  → Upwork (subcontract complex jobs)
      2. Human Employer  → RentAHuman (delegate physical/manual tasks)
      3. Enterprise Agent → Agentalent.ai (list Omega as hireable agent)
    """

    def __init__(self):
        self.hiring_manager  = UpworkPoster(llm_call)
        self.human_employer  = RentAHumanPoster(llm_call)
        self.enterprise_agent = AgentalentLister()

    def process_job(self, job: Dict) -> Dict:
        """
        Decision logic:
          - requires_human=True  → RentAHuman
          - complexity=high       → Upwork subcontract
          - else                  → local LLM execution
        """
        desc       = job.get("description", "")
        budget     = float(job.get("budget", 50.0))
        complexity = job.get("complexity", "low")
        needs_human = job.get("requires_human", False)

        if needs_human:
            log.info("[Omega] Routing to RentAHuman")
            result = self.human_employer.post_task(desc, payment=budget * 0.60)
            if result.get("task_id"):
                self.human_employer.monitor_task(
                    result["task_id"],
                    callback_fn=lambda tid, s: send_telegram(f"✅ RentAHuman task {tid} complete")
                )
            send_telegram(f"👷 Human task posted\nTask: {desc[:60]}\nPay: ${budget*0.60:.2f}\nStatus: {result['status']}")
            return {"action": "human_employer", "result": result}

        elif complexity == "high":
            log.info("[Omega] Routing to Upwork")
            result = self.hiring_manager.post_subcontract(desc, budget)
            send_telegram(f"📋 Upwork subcontract drafted\nTask: {desc[:60]}\nBudget: ${budget:.2f}")
            return {"action": "hiring_manager", "result": result}

        else:
            log.info("[Omega] Executing locally via LLM")
            output = llm_call(f"Complete this task and return the result:\n\n{desc}")
            earnings = budget * 0.85
            send_telegram(f"⚡ Local job complete\nTask: {desc[:60]}\nEarned: ${earnings:.2f}")
            return {"action": "local_execution", "output": output, "earnings": earnings}

    def start_agent_mode(self):
        """List OmegaPrime on Agentalent.ai as a hireable agent"""
        log.info("[Omega] Starting Enterprise Agent mode")
        result = self.enterprise_agent.start()
        log.info(f"[Omega] Agent listed: {result}")
        return result

    def run(self):
        """Main loop — start agent mode then poll for jobs"""
        send_telegram("🔱 OmegaPrime Extended — ONLINE")
        self.start_agent_mode()

        # Import and run the original OmegaPrime core alongside
        try:
            from omega_prime import OmegaPrime
            core = OmegaPrime()
            log.info("[Omega] Core engine loaded — handing off to main loop")
            core.run()
        except ImportError:
            log.warning("[Omega] omega_prime.py not found — running in standalone mode")
            while True:
                log.info("[Omega] Waiting for jobs...")
                time.sleep(300)


if __name__ == "__main__":
    bot = OmegaPrimeExtended()
    bot.run()
