import argparse
import os
import shlex
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.test_models import DeterministicModel, make_output


class CheckedReplay(DeterministicModel):
    def query(self, messages: list[dict], **kwargs) -> dict:
        expectations = [(10, "LEAK"), (0, ""), (0, "OK")]
        if self.current_index >= 0:
            code, last_line = expectations[self.current_index]
            observation = messages[-1]["extra"]
            raw = observation["raw_output"].strip()
            if observation["returncode"] != code or (last_line and raw.splitlines()[-1:] != [last_line]):
                raise RuntimeError(f"Unexpected observation: {observation}")
            print(f"returncode={code}" + (f" | {raw.replace(chr(10), ' | ')}" if raw else " | patch applied"))
        return super().query(messages, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a toy leak repair without a model API.")
    parser.add_argument("--trajectory", type=Path, default=Path("lesson5.traj.json"))
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("Run this replay under WSL/Linux. leak_probe.py alone can run on Windows.")

    probe = shlex.join([sys.executable, "leak_probe.py"])
    patch = shlex.join([
        sys.executable, "-c",
        "from pathlib import Path; p = Path('leak_probe.py'); s = p.read_text(encoding='utf-8'); "
        "old = 'RETAIN = True'; "
        "assert s.count(old) == 1; p.write_text(s.replace(old, 'RETAIN = False'), encoding='utf-8')",
    ])
    steps = [
        ("Measure the leaky baseline", probe),
        ("Change the retention flag in the sandbox copy", patch),
        ("Rerun exactly the same measurement", probe),
        ("Submit only after the verification observation", "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"),
    ]
    model = CheckedReplay(outputs=[make_output(note, [{"command": command}], cost=0.0) for note, command in steps])
    with TemporaryDirectory(prefix="lesson5-") as work:
        shutil.copyfile(Path(__file__).with_name("leak_probe.py"), Path(work) / "leak_probe.py")
        agent = DefaultAgent(
            model,
            LocalEnvironment(cwd=work, timeout=10),
            system_template="Replay fixed, local commands on the toy memory probe.",
            instance_template="{{ task }}",
            step_limit=len(steps),
            output_path=args.trajectory.resolve(),
        )
        result = agent.run("Detect and fix the retention leak, then verify.")
        if result.get("exit_status") != "Submitted":
            raise RuntimeError(f"Unexpected agent result: {result}")
    print(f"exit_status={result['exit_status']}")
    print(f"trajectory={args.trajectory.resolve()}")


if __name__ == "__main__":
    main()
