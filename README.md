# ⚡ OmegaPrime — The Labor Engine

*"While the Pantheon sleeps, Omega earns."*

OmegaPrime is the **Labor Engine** of the Pantheon. It finds freelance jobs, executes them using a local LLM, learns skills from every job it completes, and deposits earnings into the War Chest.

## Role in the Pantheon
OmegaPrime is the **earner**. It works with MidasPrime (Treasury) to fund the War Chest through labor-based income — freelance automation, data tasks, content, and micro-jobs via RentAHuman.

## Architecture

```
JobScanner ──► LLMPlanner ──► CoreExecutor ──► Earnings
     │               │               │
     │         HiringManager    HumanEmployer
     │         (Upwork fallback) (RentAHuman)
     │
MothBot (skill learning)
TelegramGateway (Forgemaster reporting)
```

## Components

| Component | Role |
|-----------|------|
| **JobScanner** | Fetches jobs from clawd-work.com API across 3 strategies |
| **LLMPlanner** | Plans + executes jobs using local Ollama LLM |
| **MothBot** | Extracts reusable skills from completed jobs |
| **HiringManager** | Subcontracts complex jobs to Upwork |
| **HumanEmployer** | Posts micro-tasks to RentAHuman |
| **TelegramGateway** | Reports all earnings to Forgemaster |

## Setup

```bash
# Install dependencies
pip install requests python-dotenv

# Copy and fill env
cp .env.example .env

# Run
python3 omega_prime.py
```

## Telegram Commands

| Command | Action |
|---------|--------|
| `/status` | Job stats + last 5 outcomes |
| `/skills` | List learned skills |
| `/earnings` | Total earned to date |
| `/stop` | Shutdown gracefully |

## Run as Daemon (Termux)
```bash
nohup python3 omega_prime.py &
```

---
*The Labor Engine of the Pantheon. Built by the Forgemaster.*
