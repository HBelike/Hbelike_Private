from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CareerTurnWorkerDeploymentTests(unittest.TestCase):
    def test_production_compose_runs_an_independent_durable_worker(self) -> None:
        compose = (ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")

        self.assertIn("  career-agent-worker:\n", compose)
        worker = compose.split("  career-agent-worker:\n", 1)[1].split(
            "\n  pipeline-scheduler:", 1
        )[0]
        self.assertIn('command: ["python", "scripts/run_career_agent_worker.py"]', worker)
        self.assertIn("CAREER_AGENT_GLOBAL_CONCURRENCY:", worker)
        self.assertIn("CAREER_AGENT_WORKER_CONCURRENCY:", worker)
        self.assertIn("CAREER_AGENT_LEASE_SECONDS:", worker)
        self.assertIn("CAREER_AGENT_HEARTBEAT_SECONDS:", worker)
        self.assertNotIn("CAREER_TEMPORARY_ATTACHMENT_ROOT", worker)
        self.assertNotIn("tmpfs:", worker)

    def test_production_keeps_complete_parsed_text_by_default(self) -> None:
        compose = (ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
        env_example = (ROOT / ".env.production.example").read_text(encoding="utf-8")

        self.assertIn("CAREER_REDACTION_ENABLED: ${CAREER_REDACTION_ENABLED:-false}", compose)
        self.assertIn("CAREER_REDACTION_ENABLED=false", env_example)
        self.assertIn("CAREER_AGENT_GLOBAL_CONCURRENCY=8", env_example)
        self.assertIn("CAREER_AGENT_WORKER_CONCURRENCY=4", env_example)


if __name__ == "__main__":
    unittest.main()
