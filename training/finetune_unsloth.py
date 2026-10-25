"""Fine-tune Gemma 4 on Forge's own successful decisions with Unsloth (LoRA), then export it for Ollama.

    forge export                                   # traces -> training/data/forge_traces.jsonl
    python training/finetune_unsloth.py            # on a CUDA GPU (a free Colab T4 works) or a supported Mac

The dataset is the exact prompts Forge sends plus the validated JSON action, so the model learns Forge's
tool-call format and tool choice directly; the harness's schema and repair then have less to fix.
Loss is only computed on the assistant's answer, never on the prompt."""
import argparse
import json
from pathlib import Path


def response_markers(tokenizer) -> tuple[str, str]:
    """Find the chat template's user and assistant turn prefixes by rendering a probe conversation,
    so this works across Gemma versions without hardcoding their special tokens."""
    probe = tokenizer.apply_chat_template(
        [{"role": "user", "content": "@@USER@@"}, {"role": "assistant", "content": "@@MODEL@@"}], tokenize=False)
    user_at, model_at = probe.index("@@USER@@"), probe.index("@@MODEL@@")

    def last_line(s: str) -> str:  # the turn header right before the content, keeping its trailing newline
        return s[s.rfind("\n", 0, len(s) - 1) + 1:]
    instruction = last_line(probe[:user_at]).replace("<bos>", "")
    response = last_line(probe[user_at + len("@@USER@@"):model_at])
    return instruction, response


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="training/data/forge_traces.jsonl")
    p.add_argument("--model", default="unsloth/gemma-4-E2B-it", help="e.g. unsloth/gemma-4-E4B-it on a bigger GPU")
    p.add_argument("--out", default="training/out/forge-gemma")
    p.add_argument("--steps", type=int, default=120)
    p.add_argument("--rank", type=int, default=16)
    p.add_argument("--max-len", type=int, default=4096)
    p.add_argument("--gguf", default="q4_k_m", help="GGUF quantization for Ollama, or 'none'")
    a = p.parse_args()

    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer
    from unsloth import FastModel
    from unsloth.chat_templates import get_chat_template, train_on_responses_only

    rows = [json.loads(line) for line in Path(a.data).read_text().splitlines() if line.strip()]
    if len(rows) < 20:
        raise SystemExit(f"only {len(rows)} examples in {a.data}: use Forge more (or run evals) and `forge export`")
    print(f"{len(rows)} examples")

    model, tokenizer = FastModel.from_pretrained(a.model, max_seq_length=a.max_len, load_in_4bit=True,
                                                 full_finetuning=False)
    model = FastModel.get_peft_model(model, finetune_vision_layers=False, finetune_language_layers=True,
                                     finetune_attention_modules=True, finetune_mlp_modules=True, r=a.rank,
                                     lora_alpha=a.rank, lora_dropout=0, bias="none", random_state=3407)
    tokenizer = get_chat_template(tokenizer, "gemma-4")

    def render(batch):
        # Gemma has no separate system turn in older templates; fold it into the first user message.
        texts = []
        for msgs in batch["messages"]:
            if msgs and msgs[0]["role"] == "system":
                msgs = [{"role": "user", "content": msgs[0]["content"] + "\n\n" + msgs[1]["content"]}, *msgs[2:]]
            texts.append(tokenizer.apply_chat_template(msgs, tokenize=False).removeprefix("<bos>"))
        return {"text": texts}

    ds = Dataset.from_list(rows).map(render, batched=True, remove_columns=["messages"])
    trainer = SFTTrainer(model=model, tokenizer=tokenizer, train_dataset=ds, args=SFTConfig(
        dataset_text_field="text", per_device_train_batch_size=1, gradient_accumulation_steps=4, warmup_steps=5,
        max_steps=a.steps, learning_rate=2e-4, logging_steps=5, optim="adamw_8bit", weight_decay=0.01,
        lr_scheduler_type="linear", seed=3407, max_length=a.max_len, report_to="none"))
    instruction, response = response_markers(tokenizer)
    print(f"training on responses only (instruction={instruction!r}, response={response!r})")
    trainer = train_on_responses_only(trainer, instruction_part=instruction, response_part=response)
    trainer.train()

    out = Path(a.out)
    model.save_pretrained(out / "lora")
    tokenizer.save_pretrained(out / "lora")
    if a.gguf != "none":
        model.save_pretrained_gguf(str(out / "gguf"), tokenizer, quantization_method=a.gguf)
        gguf = next((out / "gguf").glob("*.gguf"))
        (out / "Modelfile").write_text(f"FROM ./gguf/{gguf.name}\n")
        print(f"\nRun it with Forge:\n  cd {out} && ollama create forge-gemma -f Modelfile\n"
              "  then set FORGE_FAST_MODEL=forge-gemma in .env and compare: forge bench --model forge-gemma")


if __name__ == "__main__":
    main()
