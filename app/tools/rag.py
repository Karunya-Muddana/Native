from pathlib import Path
from langchain_core.tools import tool
from langchain_ollama import OllamaEmbeddings
import chromadb
from pypdf import PdfReader

KB_DIR = Path(__file__).resolve().parents[2] / "knowledge_base"
KB_DIR.mkdir(exist_ok=True)

LINES_PER_CHUNK, LINE_OVERLAP = 40, 5
PAGES_PER_CHUNK = 2

embeddings = OllamaEmbeddings(model="nomic-embed-text")
collection = chromadb.Client().get_or_create_collection("org_docs")
indexed = False


def chunk_file(path: Path) -> list[dict]:
    chunks = []
    if path.suffix.lower() in [".md", ".txt"]:
        lines = open(path, "r", encoding="utf-8", errors="ignore").readlines()
        step = LINES_PER_CHUNK - LINE_OVERLAP
        for i in range(0, len(lines), step):
            end = min(i + LINES_PER_CHUNK, len(lines))
            text = "".join(lines[i:end])
            if text.strip():
                chunks.append({"text": text, "source": path.name, "type": "md", "start": i, "end": end})

    elif path.suffix.lower() == ".pdf":
        pages = PdfReader(str(path)).pages
        for i in range(0, len(pages), PAGES_PER_CHUNK):
            end = min(i + PAGES_PER_CHUNK, len(pages))
            text = "\n".join((p.extract_text() or "") for p in pages[i:end])
            if text.strip():
                chunks.append({"text": text, "source": path.name, "type": "pdf", "start": i, "end": end})
    return chunks


def ensure_indexed():
    global indexed
    if indexed:
        return
    chunks = [c for f in KB_DIR.iterdir() for c in chunk_file(f)]
    if chunks:
        collection.upsert(
            ids=[f"{c['source']}-{i}" for i, c in enumerate(chunks)],
            embeddings=embeddings.embed_documents([c["text"] for c in chunks]),
            documents=[c["text"] for c in chunks],
            metadatas=[{"source": c["source"], "type": c["type"], "start": c["start"], "end": c["end"]} for c in chunks],
        )
    indexed = True


@tool
def list_knowledge_base() -> str:
    """List documents available in the knowledge base. Call this before search_documents to pick a filename."""
    files = [f.name for f in KB_DIR.iterdir()]
    return "\n".join(files) if files else "No documents found."


@tool
def search_documents(query: str, filename: str) -> str:
    """
    Search one document from the knowledge base for content relevant to the query.
    Use list_knowledge_base first to get a valid filename.
    Returns excerpts with exact location — start_line/end_line for .md/.txt (pass
    to read_text_file), or start_page/end_page for .pdf (pass to read_pdf).
    """
    try:
        ensure_indexed()
        results = collection.query(
            query_embeddings=[embeddings.embed_query(query)],
            n_results=3,
            where={"source": filename},
        )
        if not results["documents"][0]:
            return f"No relevant content found in {filename}."

        output = []
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            loc = f"start_line={meta['start']}, end_line={meta['end']}" if meta["type"] == "md" else f"start_page={meta['start']}, end_page={meta['end']}"
            output.append(f"[{meta['source']} | {loc}]\n{doc[:500]}")
        return "\n\n".join(output)
    except Exception as e:
        return f"Error: {str(e)}"