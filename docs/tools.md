# Tools

The full reference. The README covers what they are for; this is what they take.

Twenty-seven of them, on **LangGraph**. Your documents and your embeddings never
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
| `web_search` | Searches the public internet and returns ranked title / URL. Finds the page; `browse_web` reads it. Runs in the live browser — Google first, DuckDuckGo when Google shows a consent screen or a robot check. Falls back to DuckDuckGo's HTML-only endpoint over plain HTTP when Steel is not running |
| `browse_web` | Renders a public web page in the live browser and returns it as text, raw HTML, or a link list. One tool with an `action`, not three, because all three take the same argument and the model only has to pick the output shape. JavaScript runs, so client-side pages come back whole; it reads in a scratch tab, so it never disturbs a form `browser_do` is part-way through. Falls back to a plain HTTP fetch when Steel is not running, which costs rendering rather than the internet |
| `note_source` | Appends one source to a research notebook in `/sandbox/output/`, numbered `[S1]`, `[S2]` … with its URL. Answers with the running state of the whole notebook, not just "saved", so the model's sense of its own coverage is refreshed from the file at every step. Refuses a duplicate URL and a summary too thin to write from |
| `review_notes` | Reads a research notebook back from disk. The point of writing notes to a file is that they outlive the context window: by the twentieth source the first fifteen have been trimmed out of the conversation, and this is how they come back |
| `browser_do` | Operates a real Chrome that stays open between calls, holding its cookies and its logins — go to a page, click, type, select, submit. One tool with an `action` rather than nine, because Steel's API is large and the model should not have to shop in it. Elements are addressed by the number the last result gave them, never by a selector the model invented, and every call answers with the page it produced |
| `browser_page` | Reads the page the live browser is showing — text, links, raw HTML, or a screenshot saved to `/sandbox/output/` for `run_through_vision_model` to look at. It reads what is on screen; it does not navigate |
| `open_on_screen` | Hands a URL to the operator's real browser, or a sandbox file to whatever application the machine opens that file type with — images, PDFs, spreadsheets, documents. The one tool that sends content *out* to the desktop instead of pulling it in; it displays, it does not read |
| `clear_workspace` | Empties the workspace: sandbox files, saved conversations, and the vector indexes, by `scope`. Two-beat by design — without `confirm` it deletes nothing and reports what would go, so the count reaches the user before the deletion does. Knowledge base documents are never deleted, only their index |
| `write_document` | Writes a .docx from a JSON list of blocks — headings, paragraphs, bullets, tables, embedded images, page breaks. The step the flagship workflow was missing: OCR → data → rules → calculation had no deliverable at the end of it, only a chat message |
| `create_spreadsheet` | Writes a styled .xlsx from `{name, header, rows}` sheets — frozen header, auto-filter, auto-sized columns, and numeric coercion so `"10.9"` lands as a number that sorts and sums |
| `create_presentation` | Writes a designed 16:9 .pptx. The model supplies content and the tool composes it: section dividers, bullets, banded tables, headline-figure cards, pull quotes, image-plus-text splits and full-bleed images, in one of five palettes. Layout is fixed by the tool, not chosen by the model, because a model asked to place boxes produces overlapping text — and painting the palette itself is what keeps a deck off python-pptx's stock white master |
| `generate_image` | Draws an image and saves a PNG. Reached through the `image` model role. Every prompt is composed against a house style — a shared quality floor plus one of five per-medium voices (`photo`, `explainer`, `illustration`, `icon`, `render`) — because a photograph and a labelled diagram want opposite directions |
| `find_image` | Searches the web for a real photograph and downloads it to the workspace. `source='web'` scrapes Bing's result metadata for breadth; `source='open'` queries Wikimedia Commons for pictures licensed for reuse and reports the licence. Candidates are downloaded in order and decoded before being kept, so hotlink blocks, login redirects and 600 px previews are rejected rather than saved |

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
