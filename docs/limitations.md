# Limitations

What is unfinished, untested, or a deliberate trade. Kept here rather
than in the README because it is long, and because it is honest — none of
it is a surprise waiting for you.

# Known Issues / Tech Debt

**Correctness**

- **The coding subagent has no enforced verification.** Its prompt forbids claiming code works without running it, and the delegation tool's docstring tells the caller to trust the result — neither is enforced. The hard step cap can also return partial work with no signal that it ran out of steps.
- **`delegate_to_coding_model` has never completed a delegation successfully.** Its one substantive test predates the tools-capable model swap and produced a fabricated tool call. It has not been re-tested since the provider rewrite either.
- **Capabilities are inferred for three providers.** Ollama and OpenRouter publish them; Groq, Gemini and NVIDIA do not, so eligibility there is guessed from the model id. The panel labels those as inferred, and a wrong guess costs one failed call before failover absorbs it.

**The live browser**

- **It needs Steel running, and Steel is a separate process you start.** Without it `browser_do` and `browser_page` fail at call time. `web_search` and `browse_web` do not fail — they fall back to plain HTTP — so losing Steel costs rendering rather than the internet, but it costs it silently: the result looks the same, it is just thinner.
- **Steel's own `npm run dev` does not work on Windows.** The UI workspace runs `vite --host ${HOST:-0.0.0.0}`, which `cmd.exe` passes through literally, so vite exits 1 and `concurrently` takes the API down with it. Use `npm run dev -w api`. The UI is the session viewer and is redundant here anyway, since the real window is on screen.
- **A Steel container will shadow the native one.** Steel's published Docker image binds `0.0.0.0:3000`, and Docker Desktop restarts it on launch, so a container left over from an earlier experiment silently takes the port from the local install. The container cannot do headful — a Linux container on a Windows host has no display — so sessions created with `headless: false` fail to launch Chrome, with a dbus error that says nothing about the real cause. `docker ps` shows it; `docker stop <name>` frees the port.
- **Google usually declines.** `web_search` tries Google first and falls through to DuckDuckGo, and in testing it fell through every time — a consent screen or a robot check, visible in the Chrome window. The cascade works, so search works; it is just rarely Google.
- **Search results include sitelinks as separate hits.** The index keys on `a h3`, which catches a result's own sub-links — `/issues`, `/pulls` — and reports them as if they were distinct sources. Real URLs, low value, and they eat result slots.
- **Steel spoofs the fingerprint.** A run from Windows Chrome reported `X11; Linux x86_64` and `Sec-Ch-Ua-Platform: "Linux"` to the server. That is Steel's anti-detection working as designed, and it is also a mismatch some sites check for.
- **Every fetch is now seconds rather than milliseconds.** A rendered page load is 2–5s against roughly 0.3s over plain HTTP. Research mode reads 12–25 sources per run and pays that on each one.
- **The profile persists and is not isolated.** `persist: true` and a `user-data-dir` on disk mean logins survive restarts, which is the point — and also means anything signed into stays signed in until that directory is cleared.
- **No per-domain allowlist, and the browser can act.** The read-only web tools could only fetch; this one clicks, types and submits. Nothing in code constrains where. The prompt tells it to stop rather than invent credentials and to stop at a CAPTCHA, and a prompt is not an enforcement mechanism. The window is visible so you can watch it, which is a mitigation and not a control.

**Context**

- **The session record is lossy, and its quality is a model's judgement.** Tier 2 folds evicted messages through the `base` role with a prompt telling it what to keep. What it decides to drop is gone for that session — the messages themselves are already outside the window.
- **A failed fold is silent to the user.** It logs and keeps the previous digest, so the session quietly runs on a record that is missing its most recent stretch.
- **`MIN_FOLD` means the newest evicted messages are briefly in neither tier.** Between falling out of the working set and accumulating enough to trigger a fold, up to five messages are visible only to long-term recall.
- **Research mode's notebook protocol is not enforced.** `RESEARCH_PROMPT` instructs `note_source` after every source read. In an observed run of roughly fifteen tool calls it was never called once — the model judged a lead-generation request not to be research and skipped it. Nothing in the graph checks.

**Efficiency**

- **`search_documents` truncates excerpts at 500 characters**, which is too aggressive for tables. In the validation run it cost three round trips: each search returned a table fragment, forcing a follow-up read.
- **Tool parameter naming is inconsistent** — `read_text_file` takes `path` where every other reader takes `filename`. This cost a wasted call.
- **`rag.py`'s Chroma collection is in-memory**, so the knowledge base is re-embedded on every process start. Fine at demo scale, visible lag at corpus scale.

**Isolation**

- **`calculate` does not use `eval`.** The expression is parsed to an AST and walked, with only numeric literals, arithmetic and comparison operators, a fixed function table and the constants pi/e/tau permitted — no names, no attribute access, no calls to anything else, and an exponent cap so `9**9**9` is refused rather than hanging the process. Attribute access is rejected at the node level, which closes `().__class__.__bases__`.
- **No host-side egress log.** The sandbox's isolation is a per-container kernel guarantee and was verified by hand; there is no audit trail proving the host made no calls.
- **No sandbox image version check** — a stale image silently drifts from the Dockerfile.
- **No per-domain allowlist** on the web tools, and none on `open_on_screen`. Nothing in code stops the agent fetching an arbitrary URL.
- **Provider keys sit in `.env` in plain text.** Standard, and still worth saying out loud.

**Architecture**

- **Residency is tracked per process.** The API server is the authority on which local model is loaded; a second process talking to the same Ollama will not know what the first one parked in VRAM.
- **Model discovery is cached for five minutes.** Pull a model elsewhere and the panel will not see it until the cache expires or you press Refresh.
- **No quota accounting.** The router fails over when a tier rate-limits, but nothing tracks how much of each free tier has been spent, so the first sign of exhaustion is a failed call.

**Web tools**

- **`browse_web` runs no JavaScript.** It is an HTTP fetch and a regex HTML-to-text pass, so a site that builds its content client-side returns thin or empty text, and the tool says so rather than returning a blank page. Server-rendered pages, docs sites, standards bodies and vendor datasheets — the sources this workflow actually cites — serve their content directly and read fine.
- **No content behind an action.** Logins, cookie walls, in-site search boxes, forms and "load more" buttons are all out of reach. Where a site encodes its query in the URL, fetching that URL directly gets there anyway, and the prompt says so.
- **`web_search` depends on one upstream endpoint.** DuckDuckGo Lite is HTML-only and stable, but there is no second provider: if it blocks or changes its markup, search returns nothing until the parser is updated.
- **The authoring tools take content, not layout.** Block types are fixed (heading, paragraph, bullets, table, image, pagebreak) and slide layout is decided in code. That is what keeps a 7B model's output legible, and it means anything bespoke — a letterhead, a specific corporate template, a two-column page — is out of reach without editing the tool.
- **Read-back verifies structure, not meaning.** It reports that a table has 4 rows and that a column ranges 10.9–12.4. It cannot tell you the wrong 4 rows were written, only that these are the ones in the file.
- **`open_on_screen` opens windows on the machine the agent runs on.** That is the operator's own workstation here, which is the point — but it is a real assumption. Served to a remote user, the window would appear on the server, not on theirs.
- **`clear_workspace` is guarded by a convention, not by the runtime.** `confirm=False` returns a dry run and the prompt says to show it to the user first, but nothing outside the model's compliance stops a `confirm=True` call. Deletion is irreversible: there is no trash, no snapshot, and clearing chats drops the checkpointer rows outright.
- **Clearing history leaves recall memory unrebuilt.** The transcripts and the recalled-memory embeddings are cleared together, and long-term memory only refills from new turns — there is no reindex-from-transcripts path.
- **No Ollama host/port config**; hardcoded, along with the sandbox image name and resource limits.

**Housekeeping**

- **`context_manager.clear_db()`** deletes by explicit id list, since Chroma rejects an empty filter.
# Where it stands

| Requirement | Status | |
|---|---|---|
| Runs fully on-prem via a local model server | **Configurable** | Cloud free tiers are the default chain because local models under 8B cannot orchestrate this tool set. Point every chain at Ollama and nothing leaves the machine. Documents and embeddings never leave it either way. |
| Multi-model backend with task-based routing | **Done** | Five providers discovered live, four roles, capability-gated, with failover between links. |
| Pluggable model registry | **Done** | `app/config/providers.py`. Adding a provider is one lister and one client branch. |
| Agentic multi-step planning and iteration | **Done** | Validated at 14 tool calls with mid-run self-correction. |
| Local tool use: file read | **Done** | PDF, scanned PDF, text, source, spreadsheet, image. |
| Local tool use: file write | **Done** | Dedicated `.docx` / `.xlsx` / `.pptx` tools, each reading the saved file back. |
| Local tool use: sandboxed code execution | **Done** | Docker, network disabled at the kernel, memory-capped, verified by hand. |
| Local tool use: spreadsheet work | **Done** | Dedicated readers; `create_spreadsheet` for output. |
| Internal document search / KB connector | **Done** | Separate from chat memory, returns precise location pointers. |
| OCR / scanned document understanding | **Done** | PP-OCRv4 on ONNX Runtime with PyMuPDF. |
| Vision model for drawings, photos, handwriting | **Untested** | Wired through the role chain and reachable; not re-run since the provider rewrite. |
| Deliverable generation (Word / Excel / PowerPoint) | **Done** | All three, with structural read-back on each. |
| End-to-end demo: scanned report to approval note | **Not re-run** | Every part exists and was exercised separately. The full chained run has not been repeated since the workspace was cleared. |
| Coding task run and verified in sandbox | **Partial** | Execution works; there is no enforced verification step, and Docker must be running. |
| Visible proof of zero external network calls | **Not done** | Per-container kernel-level guarantee, hand-verified. No host-side egress audit log. |
| Long-term memory / RAG | **Done** | Two stores: conversation history and organizational documents. |

**Where it stands.** The tool layer, the sandbox, retrieval, authoring and the
orchestration loop are built and exercised. The provider layer replaced the
original single hardcoded model and is the part most recently changed, so it is
also the part with the least mileage on it. The honest gap is no longer
architectural — it is that a few paths have been built and unit-tested but not
re-run end to end since the rewrite, and that the sovereignty claim is
demonstrable by configuration rather than enforced by the code.
# Next Steps

1. **Re-run the inspection scenario end to end** — scanned report through to a signed `.docx`. Every piece works alone; the chained run is what proves it.
2. **Exercise the vision path** against the new provider chain. It is wired and reachable, and that is all that can currently be claimed.
3. **Add host-side egress logging**, so "nothing left the machine" is evidence rather than an assertion.
4. **Verify capability inference for Groq and NVIDIA.** Neither publishes capability data, so eligibility there is inferred from model ids — the panel says so, but a wrong guess still costs one failed call before failover covers it.
5. **Enforce a verification step** after sandboxed code, rather than trusting the run.
6. **Give the free tiers real quota handling** — the router fails over on a rate limit, but nothing yet tracks how much of each tier is spent.

---

[← Back to the README](../README.md)
