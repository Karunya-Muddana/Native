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

# Left to itself an image model averages its training set and hands back
# something that reads as generic — muddy palette, no focal point, a stock-photo
# watermark, garbled lettering. A house style fixes that, but it cannot be one
# paragraph: a photograph wants depth of field and a real light source, and an
# explainer wants exactly the opposite — flat, evenly lit, nothing in the frame
# that is not carrying information. So the floor below is shared and the rest is
# chosen per call.
UNIVERSAL = (
    "Render this to a professional standard: deliberate composition with a clear "
    "focal point and balanced negative space, a restrained harmonious palette "
    "rather than oversaturated colour, accurate geometry and proportions, and "
    "detail that holds up at full size without looking noisy or over-processed. "
    "No watermarks, signatures, stock-photo logos, decorative borders, collage "
    "panels or UI chrome. Any lettering must be spelled exactly as given in the "
    "description and set in a clean legible typeface; do not add captions, "
    "labels or signage that were not asked for."
)

STYLES = {
    # Meant to pass as a real photograph, so the directions are camera
    # directions — the giveaway of a generated photo is plastic skin, uniform
    # focus and light coming from nowhere.
    "photo": (
        "Photorealistic. Shot on a full-frame camera with a fast prime lens: "
        "natural depth of field with a genuinely sharp plane of focus and soft "
        "falloff behind it, believable directional lighting with consistent "
        "shadows and specular highlights, true-to-life skin and material texture, "
        "subtle lens character and film-like grain. No plastic smoothing, no HDR "
        "glow, no illustration or 3D-render look."
    ),
    # An explainer earns its place by being read, not admired — so legibility and
    # honest structure outrank prettiness.
    "explainer": (
        "A clear explanatory diagram, not decorative art. Flat even lighting and "
        "a plain uncluttered background. Show the structure honestly: distinct "
        "labelled parts, sensible relative scale, arrows or connectors only where "
        "they carry meaning, and generous spacing so nothing crowds. A small "
        "palette used consistently, with colour distinguishing parts rather than "
        "decorating them. Every label short, correctly spelled and large enough "
        "to read at a glance. No gradients, glow, drop shadows, 3D perspective "
        "tricks or background scenery competing with the content."
    ),
    "illustration": (
        "A polished illustration with a coherent point of view: confident "
        "intentional linework, deliberate shape language, colour used in a "
        "limited harmonious set, and stylisation applied consistently across "
        "every element. Editorial quality rather than clip art, and not "
        "photorealistic."
    ),
    "icon": (
        "A flat vector-style icon: simple geometric silhouette, uniform stroke "
        "weight, generous padding inside the frame, one or two flat colours, and "
        "a plain solid or transparent background. Legible when shrunk to a small "
        "size. No gradients, shadows, textures, outlines-within-outlines or text."
    ),
    "render": (
        "A clean 3D product render: studio lighting with soft boxes and a gentle "
        "gradient backdrop, accurate materials with believable reflection and "
        "roughness, crisp edges, and a contact shadow grounding the subject. "
        "Sharp throughout rather than photographic depth of field."
    ),
}

DEFAULT_STYLE = "illustration"

ASPECTS = {
    "square": "1:1",
    "landscape": "16:9",
    "portrait": "9:16",
    "wide": "16:9",
    "tall": "9:16",
}


@tool
def generate_image(
    prompt: str,
    filename: str,
    aspect: str = "square",
    style: str = DEFAULT_STYLE,
) -> str:
    """
    Draw an image from a description and save it to /sandbox/output/.

    Use this when the user asks for a picture, sketch, diagram, illustration,
    icon or figure that does not already exist. prompt should describe what to
    draw in detail — subject, composition, and any text that must appear in it.
    filename is what to save it as. aspect is 'square', 'landscape' or
    'portrait'.

    style picks the house style applied on top of your prompt — set it to what
    the user actually asked for:
      - 'photo'        a photograph: real camera lighting and depth of field
      - 'explainer'    a labelled diagram meant to be read: flat, plain, legible
      - 'illustration' editorial artwork with a consistent visual style (default)
      - 'icon'         a small flat vector symbol on a plain background
      - 'render'       a studio 3D product render

    Craft directions — composition, palette, "no watermarks or stray lettering"
    — are added for you on every call, so spend the prompt on the subject and
    the specifics rather than on quality boilerplate.

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
    # An unrecognised style falls back to the universal floor alone rather than
    # to a guess — a diagram forced through the illustration voice is worse than
    # one with no house style at all.
    house = STYLES.get(str(style or "").strip().lower(), "")

    chain = model_config.candidates("image")
    if not chain:
        return (
            "Error: no image generation model is configured. Open Models and add "
            "one to the Image role — Gemini's image model is the free option."
        )

    failures = []
    for model_id in chain:
        try:
            data = _draw(model_id, prompt, ratio, house)
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


def _draw(model_id: str, prompt: str, ratio: str, house: str = "") -> bytes | None:
    provider, model = providers.split_id(model_id)
    # After the caller's description, so the subject leads and the house style
    # reads as a refinement of it rather than competing with it.
    prompt = "\n\n".join(p for p in (prompt, house, UNIVERSAL) if p)
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
