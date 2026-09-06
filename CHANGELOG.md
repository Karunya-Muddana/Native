# Changelog

Notable changes, newest first. Follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
loosely and [semantic versioning](https://semver.org/).

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
