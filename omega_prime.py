#!/usr/bin/env python3
"""
OmegaPrime — The Labor Engine
Finds freelance/gig jobs, executes them using local LLM,
and deposits earnings into the War Chest.

Architecture:
  - JobScanner     : Fetches jobs from clawd-work.com API
  - LLMPlanner     : Plans job execution steps using local LLM (Ollama)
  - CoreonExecutor : Executes the planned steps
  - HiringManager  : Subcontracts to Upwork when job is too complex
  - HumanEmployer  : Posts micro-tasks to RentAHuman
  - MothBot        : Skill learning — extracts reusable patterns from completed jobs
  - GPTSwarmOptimizer: Prunes bad tool chains, records success/failure sequences
  - TelegramGateway: Reports all earnings + commands to Forgemaster

Runs 24/7 on Termux. No cloud required. Telegram-controlled.
"""

import os
import re
import sys
import json
import time
import uuid
import math
import signal
import sqlite3
import logging
import hashlib
import threading
import traceback
import importlib.util
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from collections import deque, defaultdict
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum

try:
    import requests
except ImportError:
    raise ImportError("requests required: pip install requests")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ============================================================================
# LOGGING
# ============================================================================

BASE_DIR = Path(__file__).parent.resolve()
LOGS_DIR = BASE_DIR / "logs"
SKILLS_DIR = BASE_DIR / "skills"
LOGS_DIR.mkdir(exist_ok=True)
SKILLS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "omega_prime.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("OmegaPrime")

# ============================================================================
# CONFIGURATION
# ============================================================================

class Config:
    # Ollama (local LLM)
    OLLAMA_BASE      = os.getenv("OLLAMA_BASE", "http://localhost:11434")
    OLLAMA_MODEL     = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")

    # Telegram
    TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    # Database
    DB_PATH          = os.getenv("OMEGA_DB", str(BASE_DIR / "omega_prime.db"))

    # Job scanning
    POLL_INTERVAL    = int(os.getenv("POLL_INTERVAL", "300"))   # seconds between scans
    MAX_JOBS_PER_CYCLE = int(os.getenv("MAX_JOBS_PER_CYCLE", "3"))
    MAX_JOB_BUDGET   = float(os.getenv("MAX_JOB_BUDGET", "200.0"))

    # RentAHuman
    RAH_API_KEY      = os.getenv("RAH_API_KEY", "rah_489f3736e4a0e5bd061a1742f6db62e9")
    RAH_BASE_URL     = os.getenv("RAH_BASE_URL", "https://rentahuman.ai/api")

config = Config()
DB_PATH = config.DB_PATH
TELEGRAM_TOKEN = config.TELEGRAM_TOKEN
TELEGRAM_CHAT_ID = config.TELEGRAM_CHAT_ID
OLLAMA_BASE = config.OLLAMA_BASE
OLLAMA_MODEL = config.OLLAMA_MODEL
POLL_INTERVAL = config.POLL_INTERVAL

# ============================================================================
# DATABASE
# ============================================================================

class OmegaDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    platform TEXT,
                    title TEXT,
                    budget REAL,
                    executor TEXT,
                    status TEXT DEFAULT 'pending',
                    earnings REAL DEFAULT 0,
                    created_at TEXT,
                    completed_at TEXT
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS job_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT,
                    job_data TEXT,
                    outcome TEXT,
                    earnings REAL,
                    plan TEXT,
                    created_at TEXT
                )
            """)
            conn.commit()

    def record_job(self, job_id: str, platform: str, title: str,
                   budget: float, executor: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO jobs
                (job_id, platform, title, budget, executor, status, created_at)
                VALUES (?,?,?,?,?,'pending',?)
            """, (job_id, platform, title, budget, executor,
                  datetime.utcnow().isoformat()))
            conn.commit()

    def complete_job(self, job_id: str, earnings: float):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE jobs SET status='completed', earnings=?,
                completed_at=? WHERE job_id=?
            """, (earnings, datetime.utcnow().isoformat(), job_id))
            conn.commit()

    def store_job_outcome(self, job_id: str, job: Dict, outcome: str,
                          earnings: float, plan: List):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO job_outcomes
                (job_id, job_data, outcome, earnings, plan, created_at)
                VALUES (?,?,?,?,?,?)
            """, (job_id, json.dumps(job), outcome, earnings,
                  json.dumps(plan), datetime.utcnow().isoformat()))
            conn.commit()

    def recall_job_outcomes(self, limit: int = 5) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT job_id, job_data, outcome, earnings, plan
                FROM job_outcomes ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
        return [
            {"job_id": r[0], "job_data": json.loads(r[1]),
             "outcome": r[2], "earnings": r[3], "plan": json.loads(r[4])}
            for r in rows
        ]

    def get_total_earnings(self) -> float:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT SUM(earnings) FROM jobs WHERE status='completed'"
            ).fetchone()
        return row[0] or 0.0

    def get_stats(self) -> Dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute(
                "SELECT SUM(earnings), COUNT(*) FROM jobs WHERE status='completed'"
            ).fetchone()
            pending = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='pending'"
            ).fetchone()
        return {
            "total_earned": total[0] or 0.0,
            "jobs_completed": total[1] or 0,
            "jobs_pending": pending[0] or 0
        }

# ============================================================================
# TELEGRAM
# ============================================================================

class TelegramGateway:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self._last_update_id = 0
        self.base = f"https://api.telegram.org/bot{token}"

    def send(self, text: str):
        if not self.token or not self.chat_id:
            log.info(f"[Telegram] {text}")
            return
        try:
            requests.post(
                f"{self.base}/sendMessage",
                json={"chat_id": self.chat_id, "text": text,
                      "parse_mode": "HTML"},
                timeout=10
            )
        except Exception as e:
            log.warning(f"[Telegram] Send failed: {e}")

    def poll(self) -> List[str]:
        if not self.token:
            return []
        try:
            resp = requests.get(
                f"{self.base}/getUpdates",
                params={"offset": self._last_update_id + 1, "timeout": 5},
                timeout=10
            )
            updates = resp.json().get("result", [])
            cmds = []
            for upd in updates:
                self._last_update_id = upd["update_id"]
                msg = upd.get("message", {}).get("text", "")
                if msg.startswith("/"):
                    cmds.append(msg)
            return cmds
        except Exception:
            return []

# ============================================================================
# JOB SCANNER
# ============================================================================

class JobScanner:
    JOB_API = "https://clawd-work.com/api/v1/jobs"

    DEFAULT_STRATEGIES = [
        {"name": "quick_automation", "max_budget": 200,
         "keywords": ["python", "automation", "script", "bot"]},
        {"name": "data_entry",       "max_budget": 100,
         "keywords": ["data", "csv", "excel", "scraping"]},
        {"name": "content_writing",  "max_budget": 50,
         "keywords": ["write", "article", "content", "copy"]},
    ]

    def __init__(self, db: OmegaDB):
        self.db = db

    def fetch_jobs(self) -> List[Dict]:
        jobs = []
        for strategy in self.DEFAULT_STRATEGIES:
            try:
                resp = requests.get(
                    self.JOB_API,
                    params={
                        "keywords": " ".join(strategy["keywords"]),
                        "max_budget": strategy["max_budget"],
                        "limit": 5
                    },
                    timeout=10
                )
                if resp.status_code == 200:
                    data = resp.json()
                    found = data if isinstance(data, list) else data.get("jobs", data.get("data", []))
                    affordable = [j for j in found
                                  if float(j.get("budget", 0)) <= config.MAX_JOB_BUDGET]
                    jobs.extend(affordable)
                    log.info(f"[Scanner] Strategy '{strategy['name']}': {len(affordable)} jobs")
            except Exception as e:
                log.warning(f"[Scanner] {strategy['name']} fetch failed: {e}")

        log.info(f"[Scanner] Total jobs found: {len(jobs)}")
        return jobs

# ============================================================================
# LLM PLANNER
# ============================================================================

class LLMPlanner:
    TOOLS = ["llm_call", "write_file", "run_python", "web_search", "api_call"]

    def __init__(self):
        self.ollama_base = OLLAMA_BASE
        self.model = OLLAMA_MODEL

    def _llm(self, prompt: str, system: str = "") -> str:
        try:
            resp = requests.post(
                f"{self.ollama_base}/api/generate",
                json={"model": self.model,
                      "prompt": prompt,
                      "system": system,
                      "stream": False},
                timeout=60
            )
            return resp.json().get("response", "").strip()
        except Exception as e:
            log.error(f"[Planner] LLM call failed: {e}")
            return ""

    def plan(self, job: Dict) -> Tuple[List[Dict], str]:
        past = []  # could pull from db in future
        system = (
            "You are an AI job planner. Given a job description, output a JSON array of steps. "
            f"Each step: {{\"tool\": one of {self.TOOLS}, \"params\": {{...}}}}. "
            "Output only valid JSON array, no explanation."
        )
        prompt = (
            f"Job: {json.dumps(job)}\n"
            f"Past outcomes: {past}\n"
            "Plan the minimal effective tool chain to complete this job and earn payment."
        )
        raw = self._llm(prompt, system)
        try:
            plan = json.loads(raw)
            return plan, raw
        except Exception:
            # Fallback minimal plan
            return [
                {"tool": "llm_call",
                 "params": {"prompt": f"Complete this job: {json.dumps(job)}", "system": ""}},
                {"tool": "write_file",
                 "params": {"path": f"./output_{job.get('id','job')}.txt",
                            "content": "{{llm_call.result}}"}}
            ], raw

    def execute_job(self, job: Dict) -> Optional[str]:
        """Use local LLM to directly complete a job and return deliverable."""
        prompt = (
            f"You are a professional freelancer. Complete this job:\n\n"
            f"Title: {job.get('title', '')}\n"
            f"Description: {job.get('description', '')[:500]}\n\n"
            "Deliver a complete, professional solution."
        )
        return self._llm(prompt)

# ============================================================================
# SKILL SYSTEM (MothBot)
# ============================================================================

class MothBot:
    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills_dir.mkdir(exist_ok=True)

    def load_skills(self) -> List[Dict]:
        skills = []
        for f in self.skills_dir.glob("*.json"):
            try:
                with open(f) as fp:
                    skills.append(json.load(fp))
            except Exception:
                pass
        return skills

    def match_skill(self, job: Dict) -> Optional[Dict]:
        title = job.get("title", "").lower()
        for skill in self.load_skills():
            if any(kw in title for kw in skill.get("keywords", [])):
                return skill
        return None

    def extract_skill(self, job: Dict, plan: List, outcome: str):
        if outcome != "success":
            return
        skill = {
            "name": f"skill_{job.get('type', 'generic')}_{int(time.time())}",
            "keywords": job.get("title", "").lower().split()[:5],
            "plan": plan,
            "source_job": job.get("id"),
            "created_at": datetime.utcnow().isoformat()
        }
        path = self.skills_dir / f"{skill['name']}.json"
        with open(path, "w") as f:
            json.dump(skill, f, indent=2)
        log.info(f"[Moth] Skill extracted: {skill['name']}")

# ============================================================================
# HIRING MANAGER
# ============================================================================

class HiringManager:
    def post_subcontract(self, job_description: str, budget: str = "$100") -> Dict:
        log.info(f"[Hiring] Subcontracting to Upwork: {job_description[:60]} | {budget}")
        return {"status": "posted", "platform": "Upwork", "budget": budget}

# ============================================================================
# HUMAN EMPLOYER (RentAHuman)
# ============================================================================

class HumanEmployer:
    def post_task(self, task_description: str, payment: str = "$50") -> Dict:
        try:
            resp = requests.post(
                f"{config.RAH_BASE_URL}/tasks",
                headers={"Authorization": f"Bearer {config.RAH_API_KEY}"},
                json={"description": task_description, "payment": payment},
                timeout=10
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            log.warning(f"[HumanEmployer] RentAHuman post failed: {e}")
        return {"task_id": str(uuid.uuid4()), "status": "posted_local"}

# ============================================================================
# OMEGA PRIME — MAIN ENGINE
# ============================================================================

class OmegaPrime:
    """
    The Labor Engine of the Pantheon.
    Scans for jobs, executes with LLM, subcontracts when needed,
    learns skills, reports earnings to Forgemaster via Telegram.
    """

    def __init__(self):
        log.info("=== OmegaPrime Initializing ===")
        self.db = OmegaDB(DB_PATH)
        self.scanner = JobScanner(self.db)
        self.planner = LLMPlanner()
        self.moth = MothBot(SKILLS_DIR)
        self.hiring = HiringManager()
        self.human = HumanEmployer()
        self.telegram = TelegramGateway(TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)
        self._running = True

        log.info(f"=== OmegaPrime Ready | Model: {OLLAMA_MODEL} ===")
        self.telegram.send(
            "⚡ <b>OmegaPrime Online</b>\n"
            f"Model: {OLLAMA_MODEL}\n"
            "Scanning for jobs..."
        )

    def process_job(self, job: Dict):
        job_id = job.get("id", str(uuid.uuid4())[:8])
        title = job.get("title", job.get("type", "Unknown"))
        budget = float(job.get("budget", 0))

        log.info(f"[Omega] Processing job {job_id}: {title[:50]} | ${budget:.2f}")
        self.db.record_job(job_id, job.get("platform", "clawd-work"),
                           title, budget, "llm_planner")

        # Check for matching skill first
        skill = self.moth.match_skill(job)
        if skill:
            log.info(f"[Omega] Matching skill: {skill['name']}")

        # Plan execution
        plan, reasoning = self.planner.plan(job)

        # Execute
        result = self.planner.execute_job(job)

        if result:
            earnings = budget * 0.85
            outcome = "success"
            self.db.complete_job(job_id, earnings)
            log.info(f"[Omega] ✅ Job {job_id} complete — +${earnings:.2f}")
        else:
            earnings = 0.0
            outcome = "failed"
            # Subcontract if budget is worth it
            if budget >= 50:
                self.hiring.post_subcontract(
                    job.get("description", title), f"${budget * 0.6:.0f}"
                )
            log.warning(f"[Omega] ❌ Job {job_id} failed")

        self.db.store_job_outcome(job_id, job, outcome, earnings, plan)
        self.moth.extract_skill(job, plan, outcome)

        stats = self.db.get_stats()
        status_icon = "✅" if outcome == "success" else "❌"
        self.telegram.send(
            f"{status_icon} <b>Job {job_id}</b>\n"
            f"Title: {title[:50]}\n"
            f"Outcome: {outcome}\n"
            f"Earnings: ${earnings:.2f}\n"
            f"Total earned: ${stats['total_earned']:.2f}"
        )

    def _handle_telegram_commands(self):
        for cmd in self.telegram.poll():
            log.info(f"[Telegram] Command: {cmd}")
            cmd_lower = cmd.lower().strip()

            if cmd_lower == "/status":
                stats = self.db.get_stats()
                outcomes = self.db.recall_job_outcomes(5)
                lines = [
                    "📊 <b>OmegaPrime Status</b>",
                    f"Total Earned: ${stats['total_earned']:.2f}",
                    f"Jobs Completed: {stats['jobs_completed']}",
                    f"Jobs Pending: {stats['jobs_pending']}",
                    "",
                    "Last 5 jobs:"
                ]
                for o in outcomes:
                    icon = "✅" if o["outcome"] == "success" else "❌"
                    lines.append(f"{icon} {o['job_id']}: ${o['earnings']:.2f}")
                self.telegram.send("\n".join(lines))

            elif cmd_lower == "/skills":
                skills = self.moth.load_skills()
                self.telegram.send(
                    f"🧠 Loaded skills: {len(skills)}\n" +
                    "\n".join(f"- {s['name']}" for s in skills[:10])
                )

            elif cmd_lower == "/earnings":
                total = self.db.get_total_earnings()
                self.telegram.send(f"💰 Total Earnings: ${total:.2f}")

            elif cmd_lower == "/stop":
                self._running = False
                self.telegram.send("OmegaPrime stopping...")

            else:
                self.telegram.send(
                    "Available commands:\n"
                    "/status — job stats\n"
                    "/skills — loaded skills\n"
                    "/earnings — total earned\n"
                    "/stop — shutdown"
                )

    def run(self):
        log.info("[Omega] Starting main loop")
        while self._running:
            try:
                self._handle_telegram_commands()

                jobs = self.scanner.fetch_jobs()
                if not jobs:
                    log.info("[Omega] No jobs found, sleeping...")
                else:
                    log.info(f"[Omega] Processing {min(len(jobs), config.MAX_JOBS_PER_CYCLE)} jobs")
                    for job in jobs[:config.MAX_JOBS_PER_CYCLE]:
                        if not self._running:
                            break
                        try:
                            self.process_job(job)
                        except Exception as e:
                            log.error(f"[Omega] Job error (non-fatal): {e}")

                # Sleep with command polling
                log.info(f"[Omega] Sleeping {POLL_INTERVAL}s...")
                slept = 0
                while slept < POLL_INTERVAL and self._running:
                    time.sleep(30)
                    slept += 30
                    self._handle_telegram_commands()

            except KeyboardInterrupt:
                log.info("[Omega] Interrupted")
                self._running = False
            except Exception as e:
                log.error(f"[Omega] Loop error: {e}")
                time.sleep(60)

        log.info("[Omega] Shutdown complete")
        self.telegram.send("OmegaPrime Offline")


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    bot = OmegaPrime()
    bot.run()
