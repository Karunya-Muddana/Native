from app.tools import io, math, sandbox, sandbox_files, rag, ocr, pdf_info, excel_info, browser, desktop, workspace, authoring, imagegen
from app.tools.sub_agents import vision
from app.tools import delegate_to_subagent as sub_agents


tools = {
    math.calculate.name: math.calculate,
    sandbox.python.python_runner.name: sandbox.python.python_runner,
    ocr.run_ocr.name: ocr.run_ocr,
    sandbox_files.list_sandbox_files.name: sandbox_files.list_sandbox_files,
    io.read_text_file.name: io.read_text_file,
    io.read_pdf.name: io.read_pdf,
    rag.list_knowledge_base.name: rag.list_knowledge_base,
    rag.search_documents.name: rag.search_documents,
    sub_agents.delegate_to_coding_model.name: sub_agents.delegate_to_coding_model,
    vision.run_through_vision_model.name: vision.run_through_vision_model,
    pdf_info.get_pdf_info.name: pdf_info.get_pdf_info,
    pdf_info.extract_pdf_images.name: pdf_info.extract_pdf_images,
    excel_info.get_excel_info.name: excel_info.get_excel_info,
    excel_info.read_excel_range.name: excel_info.read_excel_range,
    browser.web_search.name: browser.web_search,
    browser.browse_web.name: browser.browse_web,
    desktop.open_on_screen.name: desktop.open_on_screen,
    workspace.clear_workspace.name: workspace.clear_workspace,
    authoring.write_document.name: authoring.write_document,
    authoring.create_spreadsheet.name: authoring.create_spreadsheet,
    authoring.create_presentation.name: authoring.create_presentation,
    imagegen.generate_image.name: imagegen.generate_image,
}