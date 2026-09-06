# app/tools/imagegen.py
"""`generate_image` — draw a picture and leave it in the workspace.

The output lands in /sandbox/output/ as a real PNG, which means every part of
the system that already knows what to do with an image picks it up for free:
the viewer shows it, `write_document` and `create_presentation` embed it by
name, `run_through_vision_model` can be pointed back at it, and the user can
open it in their own application. A generator that returned bytes into the chat
would have none of that.

Reached through the `image` role, so which model draws is a setting rather than
a hard-coded name. Today that is Gemini on Vertex: `generate_content` with
image output, since Imagen is not enabled on every project while this is.
"""

import base64
import os
import re

from langchain_core.tools import tool

from app.config import models as model_config
from app.config import providers
from app.tools.authoring.blocks import output_path
from app.tools.readback import describe

MAX_PROMPT = 2000

ASPECTS = {
    "square": "1:1",
    "landscape": "16:9",
    "portrait": "9:16",
    "wide": "16:9",
    "tall": "9:16",
}


@tool
def generate_image(prompt: str, filename: str, aspect: str = "square") -> str:
    """
    Draw an image from a description and save it to /sandbox/output/.

    Use this when the user asks for a picture, sketch, diagram, illustration,
    icon or figure that does not already exist. prompt should describe what to
    draw in detail — subject, style, composition, and any text that must appear
    in it. filename is what to save it as. aspect is 'square', 'landscape' or
    'portrait'.

    This DRAWS a new image. To look at an image that already exists, use
    run_through_vision_model. To plot data, use python_runner — a chart is
    computed from numbers, not imagined, and a generated picture of a chart
    would show invented values.

    The saved file can be embedded in a report with write_document or
    create_presentation by naming it, and shown to the user with open_on_screen.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return "Error: no prompt given — describe what should be drawn."
    if len(prompt) > MAX_PROMPT:
        return f"Error: the prompt is longer than {MAX_PROMPT} characters."

    ratio = ASPECTS.get(str(aspect or "square").strip().lower(), "1:1")

    chain = model_config.candidates("image")
    if not chain:
        return (
            "Error: no image generation model is configured. Open Models and add "
            "one to the Image role — Gemini's image model is the free option."
        )

    failures = []
    for model_id in chain:
        try:
            data = _draw(model_id, prompt, ratio)
        except Exception as e:
            failures.append(f"{model_id}: {type(e).__name__}: {str(e)[:140]}")
            continue
        if not data:
            failures.append(f"{model_id}: returned no image")
            continue

        try:
            path = output_path(filename, ".png")
        except Exception as e:
            return f"Error: {e}"
        path.write_bytes(data)
        return (
            f"Drew /sandbox/output/{path.name} with {model_id}.\n"
            f"Read back from the saved file:\n  - {describe(path)}\n"
            "Show it with open_on_screen, or name it in write_document or "
            "create_presentation to place it in a report."
        )

    return "Error: every image model failed.\n" + "\n".join(f"  - {f}" for f in failures)


def _draw(model_id: str, prompt: str, ratio: str) -> bytes | None:
    provider, model = providers.split_id(model_id)
    if provider == "gemini":
        return _gemini(model, prompt, ratio)
    if provider in providers.OPENAI_COMPATIBLE:
        return _openai_compatible(provider, model, prompt)
    raise ValueError(f"{provider} has no image generation path")


def _gemini(model: str, prompt: str, ratio: str) -> bytes | None:
    """Vertex if there are project credentials, the API key path otherwise.

    Both are the same SDK; only the client differs, so the response handling
    below is shared.
    """
    from google import genai
    from google.genai import types

    api_key = providers.key_for("gemini")
    if api_key:
        client = genai.Client(api_key=api_key)
    else:
        client = genai.Client(
            vertexai=True,
            project=os.getenv("PROJECT_ID"),
            location=providers.VERTEX_LOCATION,
        )

    response = client.models.generate_content(
        model=model,
        # This endpoint takes no aspect parameter, so the ratio can only be
        # asked for in words — and measured output shows it is often ignored
        # (a request for landscape came back 1024x1024). Treated as a hint, not
        # a guarantee; the read-back reports the size actually produced.
        contents=f"{prompt}\n\nAspect ratio: {ratio}.",
        config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
    )

    for candidate in response.candidates or []:
        for part in getattr(candidate.content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            if inline and inline.data:
                return inline.data
    # A refusal comes back as text rather than an error, and saying so beats
    # reporting "no image" as though the call had failed.
    text = " ".join(
        (getattr(p, "text", "") or "")
        for c in (response.candidates or [])
        for p in (getattr(c.content, "parts", None) or [])
    ).strip()
    if text:
        raise RuntimeError(f"the model replied with text instead of an image: {text[:200]}")
    return None


def _openai_compatible(provider: str, model: str, prompt: str) -> bytes | None:
    """OpenRouter and friends return images as data URLs inside the message."""
    import httpx

    r = httpx.post(
        f"{providers.OPENAI_COMPATIBLE[provider]}/chat/completions",
        headers={"Authorization": f"Bearer {providers.key_for(provider)}"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}],
              "modalities": ["image", "text"]},
        timeout=180,
    )
    r.raise_for_status()
    body = r.json()
    message = (body.get("choices") or [{}])[0].get("message") or {}
    for image in message.get("images") or []:
        url = ((image or {}).get("image_url") or {}).get("url") or ""
        found = re.match(r"data:image/[^;]+;base64,(.+)", url)
        if found:
            return base64.b64decode(found.group(1))
    return None
