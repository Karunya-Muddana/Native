# Security

## Reporting a vulnerability

**Please do not open a public issue.** Use GitHub's private reporting —
[open a security advisory](../../security/advisories/new) — and you will get a
response as soon as reasonably possible.

Useful things to include: what an attacker can do, the minimal steps to
reproduce it, and which component is involved. A proof of concept helps; please
do not include one that damages anything or touches data that is not yours.

## What this software is, from a security standpoint

Native runs on your machine, **executes code written by a language model**, and
holds API keys. That is the honest threat model, and it is worth reading before
you deploy it anywhere that matters.

### The sandbox is the load-bearing control

`python_runner` executes model-written Python in a Docker container with:

- **networking disabled at the kernel level** (`network_disabled=True`), not
  firewalled — the container has no network stack to use
- a **memory cap**, enforced by the kernel's OOM killer
- a **wall-clock timeout**
- **read-only** access to `sandbox_input/`, read-write only to `sandbox_output/`

This is a serious mitigation. It is not a guarantee: it depends on your Docker
installation being sound, and container escapes exist. Do not run Native on a
machine where a container escape would be catastrophic.

### What is deliberately not sandboxed

- **`open_on_screen`** asks the operating system to open a file or a URL. It is
  restricted to `http`/`https` and to files already inside the workspace, and
  path components are stripped so a filename cannot escape it — but it does
  cause your desktop to open things.
- **`calculate`** parses to an AST and walks it. No names, no attribute access,
  no calls outside a fixed function table, and an exponent cap. It does not use
  `eval`.
- **The web tools** perform plain HTTP GETs. There is **no per-domain
  allowlist** — the agent can fetch any URL it decides to. A hardened deployment
  wants one.
- **`clear_workspace`** deletes files and conversations irreversibly. It refuses
  to act without an explicit confirm flag and reports what it would remove
  first, but that guard is a convention the model follows, not something the
  runtime enforces.

### Keys and data

API keys live in `.env` in plain text and are sent only to the provider they
belong to. Prompts, and any document text a tool has pulled into context, go to
whichever provider serves the role — see [PRIVACY.md](PRIVACY.md). Point every
chain at local Ollama if that is not acceptable for your data.

### Prompt injection

A document, a spreadsheet cell or a web page can contain text aimed at the
model. Native does not defend against this. If you point it at untrusted
content, treat its subsequent tool calls as untrusted too — the run trace exists
precisely so you can see what it decided to do.

## Supported versions

This is a young project with a single maintained line: fixes land on `main`.
There is no backport branch.
