"""Turn Forge's own traces into a fine-tuning dataset.

Every validated decision is logged as a "sample" (the exact prompt plus the clean JSON action). Only samples
from tasks that finished successfully are kept, so the dataset teaches the model the behaviour that worked,
including the cases where the harness had to repair its first answer. Train with training/finetune_unsloth.py."""
import json
from pathlib import Path


def export(trace_dir: Path, out: Path, include_images: bool = False) -> tuple[int, Path]:
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w") as f:
        for trace in sorted(trace_dir.glob("*.jsonl")):
            events = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
            end = next((e for e in reversed(events) if e["kind"] == "end"), None)
            if not end or not end.get("finished"):
                continue
            for e in events:
                if e["kind"] != "sample" or (e.get("images") and not include_images):
                    continue
                answer = json.dumps(e["action"], ensure_ascii=False)
                f.write(json.dumps({"messages": e["messages"] + [{"role": "assistant", "content": answer}]},
                                   ensure_ascii=False) + "\n")
                n += 1
    return n, out
