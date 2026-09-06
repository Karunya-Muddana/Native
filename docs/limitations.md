# Limitations

What is unfinished, untested, or a deliberate trade. Kept here rather
than in the README because it is long, and because it is honest — none of
it is a surprise waiting for you.

# Known Issues / Tech Debt

**Correctness**

- **The coding subagent has no enforced verification.** Its prompt forbids claiming code works without running it, and the delegation tool's docstring tells the caller to trust the result — neither is enforced. The hard step cap can also return partial work with no signal that it ran out of steps.
- **`delegate_to_coding_model` has never completed a delegation successfully.** Its one substantive test predates the tools-capable model swap and produced a fabricated tool call. It has not been re-tested since the provider rewrite either.
- **Capabilities are inferred for three providers.** Ollama and OpenRouter publish them; Groq, Gemini and NVIDIA do not, so eligibility there is guessed from the model id. The panel labels those as inferred, and a wrong guess costs one failed call before failover absorbs it.

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
