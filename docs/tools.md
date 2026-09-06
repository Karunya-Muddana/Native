# Tools

The full reference. The README covers what they are for; this is what they take.

# Tools

Twenty-two of them, on **LangGraph**. Your documents and your embeddings never
leave the machine. The model does, unless you point every chain at Ollama — in
which case nothing does.

**Computation & code**

| Tool | Purpose |
|---|---|
| `calculate` | Single arithmetic expression |
| `python_runner` | Arbitrary Python in a Docker container — no network, memory-capped, `/sandbox/input` (ro) and `/sandbox/output` (rw) mounted, container destroyed per run |
| `delegate_to_coding_model` | Hands a self-contained coding task to a second LangGraph agent on a code-specialist model, with its own bounded write→run→fix loop |

**Document reading**

| Tool | Purpose |
|---|---|
| `get_pdf_info` | Page count, per-page text presence, embedded image counts, and a scanned-vs-text verdict |
| `read_pdf` | Page-range text extraction from text-based PDFs |
| `extract_pdf_images` | Pulls embedded images out of a PDF as PNGs |
| `run_ocr` | PP-OCRv4 (ONNX Runtime) OCR for scanned documents and image-based PDFs; rebuilds reading order, reports confidence, falls back to Tesseract; long output auto-saves to `ocr_output.md` |
| `read_text_file` | Line-range reading of any plain-text file — markdown, source, config, csv — across input, output, or the knowledge base |

**Spreadsheets**

| Tool | Purpose |
|---|---|
| `get_excel_info` | Sheet names and dimensions |
| `read_excel_range` | Row-range reading of one sheet as a delimited text table |

**Vision**

| Tool | Purpose |
|---|---|
| `run_through_vision_model` | Asks a local vision model a question about an image — for visual understanding rather than text extraction |

**Web**

| Tool | Purpose |
|---|---|
| `web_search` | Searches the public internet and returns ranked title / URL. Finds the page; `browse_web` reads it. Backed by DuckDuckGo's HTML-only endpoint, whose results are parsed off the page and unwrapped from their `/l/?uddg=` redirects |
| `browse_web` | Fetches a public web page over plain HTTP and returns it as text, raw HTML, or a link list. One tool with an `action`, not three, because all three take the same argument and the model only has to pick the output shape. Stateless and read-only: no cookies, no login, no JavaScript |
| `open_on_screen` | Hands a URL to the operator's real browser, or a sandbox file to whatever application the machine opens that file type with — images, PDFs, spreadsheets, documents. The one tool that sends content *out* to the desktop instead of pulling it in; it displays, it does not read |
| `clear_workspace` | Empties the workspace: sandbox files, saved conversations, and the vector indexes, by `scope`. Two-beat by design — without `confirm` it deletes nothing and reports what would go, so the count reaches the user before the deletion does. Knowledge base documents are never deleted, only their index |
| `write_document` | Writes a .docx from a JSON list of blocks — headings, paragraphs, bullets, tables, embedded images, page breaks. The step the flagship workflow was missing: OCR → data → rules → calculation had no deliverable at the end of it, only a chat message |
| `create_spreadsheet` | Writes a styled .xlsx from `{name, header, rows}` sheets — frozen header, auto-filter, auto-sized columns, and numeric coercion so `"10.9"` lands as a number that sorts and sums |
| `create_presentation` | Writes a 16:9 .pptx — title slide, then bullets / table / chart slides with speaker notes. Layout is fixed by the tool, not chosen by the model, because a model asked to place boxes produces overlapping text |

**Knowledge base**

| Tool | Purpose |
|---|---|
| `list_knowledge_base` | Lists organizational documents available to search |
| `search_documents` | Semantic search within one document, returning excerpts plus exact line/page pointers |

**Filesystem**

| Tool | Purpose |
|---|---|
| `list_sandbox_files` | Lists filenames in the input or output sandbox |

## Design principles

Three decisions shape the whole system:

**Inspect before reading.** `get_pdf_info` and `get_excel_info` exist so the agent never guesses whether a PDF is scanned or which sheet holds the data. This turns a trial-and-error loop into a single deterministic lookup.

**Search returns pointers, not blobs.** `search_documents` gives back a short excerpt plus a `start_line`/`end_line` (or page range) that feeds directly into `read_text_file`/`read_pdf`. Retrieval locates; the readers fetch exact text. Nothing dumps a whole document into context.

**Specialists are tools, not modes.** The coding model and vision model are reached through tool calls, so the orchestrating model stays in charge of the conversation and can keep using its other tools around them. No message-format surgery, no swapping who drives the turn.

---

[← Back to the README](../README.md)
