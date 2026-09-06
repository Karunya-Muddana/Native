# Validation

What has actually been tested, and what came back.

# Validation

A deliberately adversarial scenario was built to test the whole system end to end.

**The task:** review a scanned inspection report and its measurement workbook against the organization's inspection standard, determine whether the finding requires escalation, and identify the category, required action, and approval chain.

**The fixtures:** a two-page scanned PDF with no text layer, a two-sheet xlsx of gauge-point readings, a vessel diagram, and two knowledge-base documents (an inspection standard and an approval authority matrix).

**The traps:**

- The visually prominent gauge point (flagged red in the diagram, singled out in the report's re-measurement note) is *not* the worst one. The standard specifies escalation by worst point.
- Both the current and superseded minimum-thickness values appear in the same document.
- The inspector's written recommendation contradicts what the standard requires.
- A settlement reading sits comfortably within limits as a red herring.
- The answer exists in no single document: vessel class from the spreadsheet, threshold from one table, escalation band from another, approver from a separate document.

**Result:** the agent chained 14 tool calls — listing files, inspecting the PDF, running OCR, reading both spreadsheet sheets, searching and then precisely reading two knowledge-base documents — and returned the correct category, worst gauge point, threshold, margin, required action, approving authority, and both countersignatories, with line-range citations for each claim. It avoided all four traps and self-corrected a bad tool parameter mid-run.

**The control:** this run used `gemini-3.7-flash` via Vertex AI, deliberately, to separate architecture failures from model-capability failures. `qwen3:4b` had previously failed the same task — not on plumbing, but by abandoning the plan partway and fabricating a verification it never performed. Swapping the model with everything else held constant is what established that the tools, prompt, retrieval, and orchestration were sound.

**The open question.** The architecture is validated. Running the same task entirely on local models is not — that remains the one claim resting on an untested path.

A second test confirmed the deliverable path: given seven vessels across three classes, the agent looked up each class's threshold, computed margins, applied the four-band escalation table, and wrote a sorted summary `.xlsx` via pandas/openpyxl inside the sandbox. Six of seven rows were correct; the one error was a boundary condition at an interval endpoint the source standard words ambiguously. Notably, the chat summary it typed did not match the file it had just written — the file was correctly sorted and the summary was not. That failure is what the read-back step now exists to prevent: every authoring tool reopens the file it wrote and reports the real contents, and the prompt tells the model the file wins.

---

[← Back to the README](../README.md)
