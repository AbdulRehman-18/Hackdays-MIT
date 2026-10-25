# Fine-tuning Gemma on Forge's own traces (Unsloth)

Forge improves a small model in two ways. The harness fixes outputs at run time (schema, repair, policy). The
traces it records can also teach the model to need less fixing. This folder is the second part.

```
use Forge  ──►  ~/.forge/traces/*.jsonl  ──►  forge export  ──►  Unsloth LoRA  ──►  GGUF  ──►  ollama create  ──►  forge bench
```

1. **Collect.** Every validated decision is logged as a `sample` event: the exact prompt and the clean JSON
   action. `forge export` keeps only samples from tasks that **finished successfully**, and skips samples that
   carried screenshots (the dataset is text-only).
2. **Train.** [`finetune_unsloth.py`](finetune_unsloth.py) uses [Unsloth](https://github.com/unslothai/unsloth)
   for a LoRA fine-tune of `unsloth/gemma-4-E2B-it`, with loss on the assistant's answer only. A free Colab T4
   is enough.
3. **Ship.** The script exports a GGUF and an Ollama `Modelfile`. Then run `ollama create forge-gemma -f Modelfile`
   and point `FORGE_FAST_MODEL` at it.
4. **Measure.** `forge bench --model forge-gemma` gives the same table as for the base model, so the change is
   visible rather than assumed.

```bash
forge export --out training/data/forge_traces.jsonl
pip install unsloth datasets trl          # on the training machine
python training/finetune_unsloth.py --steps 120
```

Notes:
- Traces stay on your machine, and nothing is uploaded. Review `training/data/*.jsonl` before training on another
  computer, because prompts can contain on-screen text.
- Below about 200 examples, expect a small gain at best. The repair turns in the traces are the most useful
  signal, because they show exactly where the base model goes wrong.
- Status: the pipeline is written but has not been run to completion yet. Results will be added to
  `evals/` once it has been.
