# Changelog

Notable changes, newest first. Follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
loosely and [semantic versioning](https://semver.org/).

## [Unreleased]

### Added

- **A live browser.** `browser_do` and `browser_page` operate a real Chrome that
  keeps its cookies, its logins and its page between calls — signing in, filling
  and submitting forms, clicking through multi-step flows, and reading pages
  that only exist after their own JavaScript has run. Backed by
  [Steel](https://github.com/steel-dev/steel-browser), which runs as a separate
  service you start yourself. Sessions are created with `headless: false`, so
  the window is on your screen and the run can be watched and interrupted.
  Elements are addressed by the number the last result gave them, never by a
  selector the model invented.
- **`web_search` and `browse_web` now render.** Same names, same signatures, same
  routing — the work happens in the live browser, so client-side pages come back
  whole and search reaches Google (falling through to DuckDuckGo, which in
  practice is what answers). They fall back to plain HTTP when Steel is not
  running, so losing it costs rendering rather than the internet. Reads use a
  scratch tab and never disturb the tab `browser_do` is working in.
- **`find_image`** — searches the web for a real photograph and downloads it,
  from Bing for breadth or Wikimedia Commons for pictures licensed for reuse.
  The sibling of `generate_image`: one invents, this one finds.
- **`note_source` and `review_notes`** — a research notebook on disk. Each source
  is appended the moment it is read, numbered `[S1]`, `[S2]` … with its URL, so
  citations resolve to a line in a real file and twenty-five sources are all
  still legible at the end of a run.
- **Research mode.** A second run mode that plans its sub-questions, reads
  sources one at a time taking notes on each, and answers from the notebook. A
  per-turn choice on the composer, with a wider window and a step budget to
  match.
- **A third context tier.** Everything that scrolls out of the working set is
  now folded into a rolling per-session record, closing the gap between the
  verbatim window and cross-session recall — which is a similarity search and so
  surfaces what resembles the question rather than what actually happened.
  Folding is incremental, held back by `MIN_FOLD`, and a failed fold keeps the
  previous record rather than raising.
- **`install.py`** — one command that sets up everything: the virtual
  environment, the packages, `.env`, the Ollama models, the Docker sandbox image
  and Steel, including a launcher script for Steel with the Windows workaround
  baked in. Idempotent, and it steps over anything optional that is unavailable
  rather than stopping.
- **`native`**, a console command. `native` starts the app and opens it,
  `native chat` talks to it in the terminal, and `pip install -e .` replaces the
  requirements step.
- **Designed decks.** `create_presentation` now composes section dividers,
  banded tables, headline-figure cards, pull quotes and full-bleed images in one
  of five palettes, instead of dropping content onto python-pptx's stock master.
- **A house style for `generate_image`** — a shared quality floor plus one of
  five per-medium voices, because a photograph and a labelled diagram want
  opposite directions.

### Fixed

- **Turns no longer end when the model only describes its next step.** A reply
  with no tool call used to end the run, which is right when the model has
  answered and wrong when it wrote `ACT: I will now browse the repository` and
  attached nothing. Such replies — and empty ones — now route to a `nudge` node
  that pushes the model back into the loop, bounded at three per turn and reset
  by any real tool call.
- **Internal messages no longer reach the user.** The turn's answer is taken
  from the last message the *model* produced rather than the last message in
  state, and the nudge is filtered out of the run trace in both the blocking and
  streaming paths. Without this a turn that exhausted its nudges handed back the
  nudge text as its reply.

### Changed

- **27 tools**, up from 22.
- The system prompt no longer claims there is no interactive browser, which had
  become false and would have suppressed the tools that make one available.

## [1.0.0] — 2026-09-06

First release.

### Added

- **Multi-provider model layer.** Groq, Gemini (API key or Vertex), OpenRouter,
  NVIDIA NIM and local Ollama, discovered live against each provider's own API.
- **Roles as failover chains.** `base`, `coder`, `vision` and `image` each hold
  an ordered list; the first link that answers wins, and a rate limit, retired
  model, auth failure or timeout falls through to the next. Local Ollama sits at
  the end of every chain, so a run survives every free tier being exhausted.
- **Capability gating.** Roles require `tools`, image input or image output.
  Where a provider publishes capabilities they are used; where it does not they
  are inferred and labelled as inferred.
- **22 tools** — sandboxed Python, PDF and spreadsheet reading, OCR, vision,
  knowledge-base retrieval, read-only web access, `.docx`/`.xlsx`/`.pptx`
  authoring, image generation, and workspace management.
- **Read-back on everything written.** Each authoring tool reopens the file it
  produced and reports the real contents, so a summary cannot drift from the
  artifact.
- **Token streaming**, block-committed so only the incomplete tail re-renders.
- **Images in the app** — an inline viewer, figures inside answers, thumbnails
  in the run trace, and `open_on_screen` showing images in the page rather than
  launching a desktop viewer.
- **The run trace** as primary content: every tool call with its arguments,
  duration and raw result.

### Security

- `calculate` parses to an AST and walks it instead of calling `eval`. No names,
  no attribute access, no calls outside a fixed function table, and an exponent
  cap.
- The Python sandbox runs with networking disabled at the kernel level, a memory
  cap and a wall-clock timeout.
- `clear_workspace` reports what it would delete and refuses to act without an
  explicit confirmation.

### Known limitations

See [docs/limitations.md](docs/limitations.md). The short version: the vision
path and the full end-to-end inspection run have not been re-exercised since the
provider rewrite, there is no host-side egress log, and capabilities are
inferred for three of the five providers.

[1.0.0]: ../../releases/tag/v1.0.0
