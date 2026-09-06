# app/models/vision_model.py
"""The vision role — the one that cannot be filled by a text model.

Images travel differently per provider: Ollama takes a bare base64 string in
its own `images` field, while everything OpenAI-shaped and Gemini expect a
content list with an image part. LangChain's multimodal message format covers
the second group, so the local case is the one that needs its own path.
"""

from langchain_core.messages import HumanMessage

from app.config import models as model_config
from app.config import providers
from app.models.router import NoModelAvailable, invoke


def vision_model(question: str, image_b64: str) -> str:
    chain = model_config.candidates("vision")
    if not chain:
        raise NoModelAvailable("No vision-capable model is configured. Open Models and pick one.")

    provider, _ = providers.split_id(chain[0])
    if provider == "ollama":
        return _ollama(chain[0], question, image_b64)

    message = HumanMessage(content=[
        {"type": "text", "text": question},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
    ])
    reply = invoke("vision", [message])
    return reply.text if hasattr(reply, "text") else str(reply.content)


def _ollama(model_id: str, question: str, image_b64: str) -> str:
    import ollama

    _, model = providers.split_id(model_id)
    model_config.acquire(model_id)
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": question, "images": [image_b64]}],
        think=False,
        options={"num_ctx": 16384},
        keep_alive=model_config.KEEP_ALIVE,
    )
    return response["message"]["content"]
