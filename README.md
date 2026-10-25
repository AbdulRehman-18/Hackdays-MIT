# Forge

**A harness that makes open-weight models like Gemma reliable at real tasks on your own computer.**

Small open models can reason, but they often fail as agents. They emit broken tool calls, invent tools, repeat
the same step, or claim they are done when they are not. Closed agents avoid much of this because of the harness
around the model. Forge is that harness, built for open weights and running on your machine.

> Status: **M0**. Harness core, two model routes, the schema and repair layer, and a minimal agent are in.
> Device tools, the voice assistant and the HUD come next.

## How it works

```
goal ─► plan (only for compound requests: "X and then Y" becomes a checklist)
      ─► prompt (goal + compact history + the checklist + only the tools that matter)
      ─► model, constrained to a JSON schema with one branch per tool
      ─► validate ─► invalid? one repair turn with the exact error
      ─► run the tool ─► check the result ─► repeat until done, step cap or cycle guard
```

| What small models get wrong | What Forge does |
|---|---|
| Broken or invented tool calls | Forge owns the tool-call protocol. Each step's JSON schema is enforced during decoding (`anyOf`, one branch per tool, typed args). Replies are validated, and a failed reply gets one repair turn that names exactly what to fix. |
| Losing the thread | A short, fresh prompt every step: the goal plus the last few steps and their results. |
| Forgetting half the request | A compound request ("check the weather and save it as a note") is planned first, in one constrained call, as a checklist with a tool per item. The loop may not answer while an item is open: it is pushed back once, then the item is marked skipped, and the answer must say what was not done. The screen agent gets a short step plan too. |
| Loops and runaway tasks | A cycle guard, a step cap, and a note in the history when an action is repeated. |
| "Done" when it is not | Tools check their own result, for example that the app actually came to the front. |

## Models (all open weights)

| Role | Model | Size class | License |
|---|---|---|---|
| Agent | [Gemma 4 E2B / E4B](https://ollama.com/library/gemma4) | small (≤10B) | [Apache 2.0](https://opensource.googleblog.com/2026/03/gemma-4-expanding-the-gemmaverse-with-apache-20.html) |
| Agent (optional, bigger) | [Gemma 4 12B](https://ollama.com/library/gemma4) | large (>10B) | [Apache 2.0](https://opensource.googleblog.com/2026/03/gemma-4-expanding-the-gemmaverse-with-apache-20.html) |
| Hosted route | `gemma-4-26b-a4b-it` on [Google AI Studio](https://aistudio.google.com) | large | Apache 2.0 weights; API use is also under Google's API terms |

Three routes, with the same harness on all of them:
- **local** (default): Ollama on your machine. Nothing leaves your computer.
- **openai**: any OpenAI-compatible local server, set by URL and model name (`FORGE_OPENAI_URL`,
  `FORGE_OPENAI_MODEL`): Ollama's `/v1`, llama.cpp `llama-server`, LM Studio or MLX `mlx_lm.server`. The step
  schema goes out as `response_format: json_schema`; a server that rejects it gets plain JSON mode, then the schema
  in the prompt, and validate-and-repair keeps the output usable either way.
- **aistudio**: hosted Gemma, for machines that can't run the model locally. If hosted Gemma refuses JSON mode or
  system instructions, Forge falls back to plain prompting automatically.

## Hardware

Forge reads the chip and memory at start-up and picks models that fit next to each other and next to speech, so
nothing swaps or spills onto the CPU. `forge doctor` prints the profile; anything in `.env` overrides it.

| Memory (Apple Silicon) | Chat and voice | Screen agent | Context | Speech |
|---|---|---|---|---|
| 8 GB | Gemma 4 E2B | Gemma 4 E2B | 8K | Whisper small.en |
| 16 GB | Gemma 4 E4B | Gemma 4 E4B | 16K | Whisper large-v3-turbo |
| 24-36 GB | Gemma 4 E4B | Gemma 4 12B QAT | 32K | Whisper large-v3-turbo |
| 48 GB+ | Gemma 4 E4B | Gemma 4 26B A4B | 32K | Whisper large-v3-turbo |
| No Apple GPU | Gemma 4 E2B (CPU) | Gemma 4 E2B | 8K | faster-whisper small.en (`pip install faster-whisper`) |

Speech uses Whisper large-v3-turbo on the Apple GPU, as Monday does: smaller models invent words out of room noise
and mishear commands. Whisper always encodes a 30-second window, so its speed depends on model size, not on how
long you spoke: on an M4 turbo takes about 0.8 s a command (a 4-bit turbo is no faster) and small.en about 0.2 s.
`FORGE_STT_MODEL=fast` picks small.en if you prefer speed and speak clearly in a quiet room.

## Quick start (macOS)

```bash
ollama pull gemma4:e4b
uv sync
cp .env.example .env
uv run forge run "open calculator"
```

Other commands:

```bash
uv run forge run "go to github.com" --dry-run            # decide, but don't act
uv run forge run "open notes" --route aistudio           # hosted Gemma (needs GEMINI_API_KEY)
uv run forge ask "weather in London and save it as a note" --route openai   # any OpenAI-compatible server
uv run forge doctor                                      # hardware profile, models, permissions
uv run forge bench --runs 20                             # raw model vs model + Forge
uv run pytest
```

## Measurements

Benchmark: 20 one-step tasks (open apps, open URLs, answer directly), run dry on an Apple M4 with 16 GB.
- **raw** is the same model and prompt without schema enforcement or repair.
- **forge** adds both.

Full tables are in [evals/m0_results.md](evals/m0_results.md).

Planning, measured on 8 compound requests (two or more tools each, such as "check the weather in London and save
it as a note" or "open WhatsApp and send hi to Ali"), with stubbed tools on an M4 with 16 GB:

| | before (no planning, `title` args dropped) | `title` fix only | fix + planning |
|---|---|---|---|
| `gemma4:e4b` (local) | 5/8 | 7/8 | 8/8 |
| `gemma4:e4b` (openai route, Ollama `/v1`) | | | 7/8 |
| `gemma4:e2b` (local) | | | 7/8 |

## Layout

```
src/forge/harness/   agent loop, planner, tool-call schema + repair, tool registry,
                     model backends (ollama, openai-compatible, aistudio)
src/forge/hardware.py  chip and memory detection, the per-machine model profile
src/forge/tools/     device tools (macOS): open_app, open_url
tests/               unit tests (fake backend; no model needed)
evals/               benchmark results
```

## License

MIT, see [LICENSE](LICENSE). Model weights are under their own licenses, listed above.
