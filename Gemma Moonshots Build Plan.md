# Gemma City: One Project, Four Ideas

Prepared October 25, 2026

Gemma City is one local-first 3D world that merges all four ideas: your codebase becomes a city, Gemma agents live in it, you reshape it by talking, and you can look inside any agent's mind.

## The 60-second demo

A first-time user should see the whole idea in one minute:

1. Run gemmacity ./your-repo and a 3D city opens in the browser. Folders are districts, files are buildings, height is complexity, color is recent change.
2. Type: add a park next to the auth district and make the whole city night with rain. Gemma edits the world through tool calls and checks a screenshot of its own work.
3. Start Saboteur mode. Every district gets a Gemma maintainer agent. One of them is secretly the saboteur who flagged a fake bug somewhere in the city. The maintainers talk, accuse, defend, and vote while you watch or play one of them.
4. Click any agent and open its Mind panel to see what it privately thought when it accused someone.

The bug in Saboteur mode is a fictional flag in the game, never a real change to your code.

## How the four ideas fit

Each original project becomes one layer of the same world, so you build one product instead of four.

| Original idea | Role in Gemma City | What changes |
| --- | --- | --- |
| Codebase City | The world itself | It becomes the map every other layer lives on |
| Blender Co-Pilot | World director | Same tool-calling idea, but it edits the browser scene (Three.js) instead of Blender. glTF export to Blender is a stretch goal |
| Social deduction game | Saboteur mode | The agents are district maintainers, and one is the saboteur |
| Brain Viewer | Mind panel | Tier 1 shows each agent's private thought log. Tier 2 shows Gemma Scope 2 feature traces on a Gemma 3 model |

## Architecture

One local server and one browser client, tied together by a single append-only event log. The world, the game, and the Mind panel all read from that log, which is the decision that keeps four ideas from turning into four codebases.

1. Analyzer: tree-sitter and git history turn a repository into city.json (districts, buildings, roads for imports, per-file metrics).
2. World engine (browser): react-three-fiber renders city.json with instanced meshes and exposes a typed tool API such as add\_object, set\_style, set\_time\_of\_day, fly\_to, and highlight.
3. World director: Gemma turns chat requests into tool calls, then looks at a screenshot of the scene and fixes mismatches, with a hard cap on retries.
4. Agent runtime (FastAPI): each agent has a persona, memory, and a private view of the game, and returns structured JSON turns with a private thought, a public speech, and an action.
5. Game server: an authoritative state machine (night, discussion, vote, resolution) that validates every action and never puts hidden information into another agent's prompt.
6. Mind recorder: stores each agent turn in the event log. The Mind panel replays it, and Tier 2 adds feature traces when that mode is enabled.
7. Model endpoint: any OpenAI-compatible local server (Ollama, llama.cpp, LM Studio, MLX), configured by URL and model name.

## Model plan

Start with one small model for everything and add bigger ones only as options.

- Gemma 4 E4B: default for the world director and all agents. It supports function calling and image input, and it is small enough to run several agents on one laptop.
- Gemma 4 26B A4B: optional quality setting for smarter deception and better scene edits.
- EmbeddingGemma (308M): retrieval over code chunks for the codebase chat.
- Gemma 3 (1B or 4B) with Gemma Scope 2: only for Mind Tier 2, because the Scope 2 suite targets Gemma 3 models. Tier 2 runs as a separate mode with precomputed traces.

Check the license and model card of every variant before you ship. Gemma 4 is Apache 2.0, and Gemma 3 uses the Gemma Terms of Use.

## Four-week build plan

Each week ends with something you can record as a demo clip.

| Week | Build | Done when |
| --- | --- | --- |
| 1 | Analyzer for JS and TS repos, 3D city, hover cards, search. Plus a half-day spike to check that Gemma 3 and the Scope 2 tooling load and give clean traces | Any JS or TS repo renders as a city you can fly around, and you know whether Mind Tier 2 is feasible |
| 2 | World director with tool calls and screenshot check, codebase chat with EmbeddingGemma | You say make it night and add a park, and it works. You ask where is auth, and the camera flies there |
| 3 | Event log, agent runtime, Saboteur mode | Six agents finish a full game, and an automated test proves no hidden information reaches the wrong agent |
| 4 | Mind panel Tier 1, polish, packaging, launch. Tier 2 only if the week 1 spike passed | v0.1.0 release with a demo video and a three-command install |

## Cut order and risks

If time runs short, cut in this order: Mind Tier 2, voice, extra languages beyond JS and TS, glTF export to Blender. Never cut the event log or the hidden-information test, because the rest of the project depends on them.

- Four layers can blur the pitch. Lead the README and the video with one scene: the city at night, then a Saboteur vote, then the Mind panel.
- Small models make mistakes. Use structured tool calls, a retry cap, and a larger-model setting.
- Many agents mean latency. Run turns in parallel, keep outputs short, and stream speech to the UI.
- Hidden information can leak. Build each agent's prompt from its own private view only, and test it.
- Huge repositories overload the renderer. Use level of detail and folder-depth limits.
- Mind Tier 2 depends on Gemma 3 tooling. Treat it as a bonus mode until the week 1 spike proves it.

## Launch plan

Ship one 45-second video as the main asset: the city, the night-and-rain edit, a Saboteur vote, and a click into the Mind panel.

- Repo: Apache 2.0, a README that opens with the demo GIF, a three-command install, a hardware table, and ten good first issues.
- Release: a tagged v0.1.0 with a prebuilt package so nobody has to build from source.
- Posts: Show HN, r/LocalLLaMA, r/gamedev, X, and LinkedIn, each leading with the clip.
- After launch: answer every issue within 24 hours, merge small contributions fast, and publish a short build log.

Next step: pick a final name, then I can draft the repo scaffold, the event log schema, and the tool API for the world director.
