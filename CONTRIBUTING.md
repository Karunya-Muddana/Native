# Contributing

Thanks for looking. This project has one house rule, and it is the same rule the
software follows:

> **If you claim something works, show the run that proves it.**

The whole design is about making an agent's working visible instead of asking
you to trust a summary. Pull requests are held to the same standard: a trace, a
test, a screenshot, a before and after — something a reviewer can check. "Tested
locally" is not evidence.

## Getting set up

```bash
git clone <your fork> && cd Native
python install.py --skip-steel --skip-docker      # the Python side only
.venv/Scripts/activate                            # source .venv/bin/activate elsewhere
native start --reload                             # restarts on code changes
```

You need **no keys at all** to work on most of this. With none set, every role
falls through to local Ollama, and the UI tells you exactly what is missing. For
UI work you do not even need that — the app loads and renders with nothing
configured.

Optional, depending on what you are touching:

- **Ollama** — for the local link in every chain. `ollama pull qwen3:4b` and
  `ollama pull nomic-embed-text`.
- **Docker** — only for `python_runner` and `run_ocr`. Build the sandbox with
  `docker build -t mrpl-sandbox-python app/tools/sandbox/python`.

## What the code expects of you

**Comments say why, not what.** The repository is fairly heavily commented, and
almost none of it restates the code. If a line is surprising, the comment
explains the constraint that made it that way. If it is obvious, there is no
comment. Please match that.

**Write down what you found out.** Several comments in here exist because
something was measured and the result was counter-intuitive — a model that
reports `tools` and cannot use them, a `keyTimes` list that silently disables an
animation, a CDN that rejects `urllib` but accepts an SDK. That knowledge is
worth more than the fix. Put it next to the code.

**Limitations go in the docs.** If your change has a rough edge, add it to
[docs/limitations.md](docs/limitations.md). A limitation written down is a
design decision; the same limitation discovered later is a bug.

**Never widen the sandbox quietly.** `python_runner` runs model-written code
with networking disabled at the kernel and a memory cap. If a change touches
those settings, say so prominently — that is the load-bearing safety property of
the whole project.

## Where things live

| | |
|---|---|
| `app/tools/` | One file per tool, registered in `registery.py` |
| `app/config/providers.py` | Providers, discovery, capabilities |
| `app/config/models.py` | Roles, chains, residency |
| `app/models/router.py` | Failover — which link answers, and when to move on |
| `app/runtime/runtime.py` | The LangGraph loop and session checkpointing |
| `frontend/index.html` | The entire UI. One file, no build step |
| `docs/` | Everything the README links out to |

## Adding a tool

1. Write it in `app/tools/`, decorated with `@tool`. The docstring is the
   model's only instruction — write it for a reader who cannot see the code.
2. Register it in `app/tools/registery.py`.
3. Document it in `app/config/prompts.py`. **CI fails if a registered tool is
   not in the system prompt**, because a tool the model has never been told
   about is a tool it will not use.
4. If it writes a file, read the file back and report what is actually in it.
   Every authoring tool does this; it is not optional.

## Adding a provider

`app/config/providers.py` needs a lister and a client branch. If the provider
publishes capability data, use it and add the provider to `VERIFIED`; if not,
inference is fine — it will be labelled as inferred in the UI, which is honest.

## The UI

One file, no build step, no framework. Open it and edit it. It is long, but it
is organised in labelled sections and the CSS is grouped by component. Keep
motion tied to real state changing — see the note on that in
[docs/architecture.md](docs/architecture.md) — and gate anything animated behind
`prefers-reduced-motion`.

## Commits and pull requests

Write the commit message for someone reading it in a year with no memory of the
conversation: what changed, and why it needed to. Explain the reasoning, not the
diff.

CI runs on every pull request: it imports the tree, checks every tool is
documented, checks the role chains are coherent, byte-compiles the Python, and
parses the UI script. It cannot run a turn — there are no providers in CI — so
the manual verification in your PR description is doing the real work.

## Reporting bugs and security issues

Bugs go in [issues](../../issues), with the run trace. Vulnerabilities go
privately — see [SECURITY.md](SECURITY.md). Please do not open a public issue
for a security problem.

## Licence and brand

Contributions are accepted under [Apache 2.0](LICENSE). The name and mark are
not covered by it — see [TRADEMARKS.md](TRADEMARKS.md). If you fork, please
rename.
