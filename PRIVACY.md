# Privacy

Native runs on your machine. There is no Native server, no account, no
telemetry, no analytics, and no crash reporting. Nobody — including the author —
receives anything about your use of it.

That said, an agent that calls a hosted model necessarily sends something
somewhere. This page says exactly what, and how to stop it.

## What never leaves your machine

| | |
|---|---|
| Your documents | Files in `sandbox_input/`, `sandbox_output/` and `knowledge_base/` stay on disk. They are only sent to a provider if a tool reads one and that text becomes part of a prompt. |
| Knowledge-base embeddings | Generated locally by `nomic-embed-text` through Ollama, stored in `chroma/`. Retrieval never calls a hosted service. |
| Conversations | `app/data/agent.db`, a local SQLite file. |
| Recalled memory | The `history` collection in `chroma/`, embedded locally. |
| Your API keys | `.env`, read at startup. Never sent anywhere except as the auth header to the provider they belong to. |
| Code you run | Executes in a Docker container with networking disabled at the kernel level. It cannot phone home even if it tries. |

## What is sent, and to whom

**To whichever model provider serves a role.** A turn sends the system prompt,
the conversation so far, and any tool results in context — which can include
text extracted from your documents, spreadsheet rows, OCR output and file names.
Images go to the vision model. Prompts go to the image model.

The provider is whichever link in that role's chain answers first. Check the
**Models** panel to see which one is active. Their terms govern what they do
with it:

- [Groq](https://groq.com/privacy-policy/)
- [Google Gemini](https://ai.google.dev/gemini-api/terms) · [Vertex AI](https://cloud.google.com/terms/cloud-privacy-notice)
- [OpenRouter](https://openrouter.ai/privacy) — note that OpenRouter forwards to an upstream provider, so read theirs too
- [NVIDIA NIM](https://www.nvidia.com/en-us/about-nvidia/privacy-policy/)

Free tiers are frequently paid for with data. Several providers train on free-tier
traffic by default. If what you are working on is confidential, read the tier
you are actually on before you rely on it.

**To websites you ask it to visit.** `web_search` queries DuckDuckGo Lite;
`browse_web` fetches the URL you or the model chose. Those sites see a normal
HTTP request from your IP.

**To Ollama.** Local by default — `127.0.0.1:11434`, no network.

**To two CDNs, on page load.** The UI pulls fonts from Google Fonts and
`marked`/`highlight.js` from cdnjs. Those servers see your IP and user agent.
This is the one piece of unavoidable third-party contact in an otherwise local
front end, and it happens whether or not you send a message. Vendoring those
three files locally would remove it.

## Running it with nothing leaving at all

Point every role's chain at a local Ollama model in the **Models** panel and
remove the cloud links. Then the only outbound traffic is the two CDNs above,
and web tools if you use them. Delete the keys from `.env` to be certain.

## Deleting your data

It is all on your disk, so you delete it:

- **In the app** — `clear_workspace` clears sandbox files, conversations, and
  the vector indexes. It shows you what it will delete before it does.
- **By hand** — `app/data/` is conversations, `chroma/` is embeddings,
  `sandbox_input/` and `sandbox_output/` are files.

Anything already sent to a provider is subject to their retention policy, not
this one. Deleting locally does not reach back.

## Children

Not directed at children and not intended for use by anyone under 13.

## Changes

This file is versioned with the code. `git log PRIVACY.md` is the change history.

---

*Last updated: 2026-09-06. Not legal advice — this describes how the software
behaves, accurately and to the best of the author's knowledge. If you deploy
Native for other people, you are the data controller and you need your own
policy.*
