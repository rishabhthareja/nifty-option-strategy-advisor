import json
import os
import uuid
from datetime import datetime
from pathlib import Path


class SessionLogger:
    def __init__(self):
        self.run_id = str(uuid.uuid4())[:8]
        date_str = datetime.now().strftime("%Y-%m-%d")
        self.log_dir = Path("logs") / date_str / self.run_id
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.agent_outputs = {}
        self.agent_timings = {}
        self.step_counter = 0

    def get_run_id(self) -> str:
        return self.run_id

    def log(self, agent_name: str, data_dict: dict, step: int = None):
        if step is None:
            self.step_counter += 1
            step = self.step_counter

        self.agent_outputs[agent_name] = data_dict

        filename = f"{step:02d}_{agent_name}.json"
        filepath = self.log_dir / filename
        with open(filepath, "w") as f:
            json.dump(data_dict, f, indent=2, default=str)

    def set_timing(self, agent_name: str, elapsed_seconds: float):
        self.agent_timings[agent_name] = round(elapsed_seconds, 2)

    def save_master(self, journal: dict | None = None):
        master = {
            "run_id": self.run_id,
            "timestamp": datetime.now().isoformat(),
            "agent_timings": self.agent_timings,
            "agents": self.agent_outputs,
        }
        if journal:
            master["journal"] = journal
        filepath = self.log_dir / "00_master.json"
        with open(filepath, "w") as f:
            json.dump(master, f, indent=2, default=str)
        return str(filepath)
