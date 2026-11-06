"""Update griptape_nodes_library_apiyi/apiyi_model_catalog.py from the APIYI model registry.

Run this script when APIYI adds models:

    python scripts/sync_models.py
    python scripts/sync_models.py --registry saved_copy.json

Each dropdown starts with the preferred models, in the order below. New models go at the end. The script
also prints two lists for a person to check: models that no node offers, and preferred models that the
registry does not list. It keeps the preferred models, because the registry can be incomplete.

The registry does not list Veo 3.1 or Grok Imagine Video. Their model IDs are in their node files.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

REGISTRY_URL = "https://docs.apiyi.com/model-registry.json"
CATALOG_PATH = Path(__file__).parent.parent / "griptape_nodes_library_apiyi" / "apiyi_model_catalog.py"

CHAT_CONSTANT = "CHAT_MODELS"
# Text models that have the `chat` endpoint in the registry, but do not chat.
NOT_CHAT_MODELS = frozenset({"bge-m3"})


@dataclass(frozen=True)
class Family:
    """The models of one image or video node."""

    constant: str
    category: str
    prefixes: tuple[str, ...]
    preferred: tuple[str, ...]


PREFERRED_CHAT_MODELS = (
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
    "gpt-6.1-sol",
    "gpt-6.1-terra",
    "gpt-6-astra",
    "gpt-6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-4.1",
    "gpt-4o-mini",
    "gemini-4-argon",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
    "grok-4.7",
    "grok-4.6",
    "grok-4.5",
    "deepseek-v4-pro",
    "deepseek-v4.1-flash",
    "qwen3.8-max",
    "qwen3.8-flash",
    "glm-5.3",
    "kimi-k3.1-pro",
    "MiniMax-M3",
    "seed-2-0-pro-260328",
)

FAMILIES = (
    Family(
        "GPT_IMAGE_MODELS",
        "image",
        ("gpt-image-",),
        (
            "gpt-image-2.5-flare",
            "gpt-image-2.5-sunburst",
            "gpt-image-2.6-flare",
            "gpt-image-2.6-sunburst",
            "gpt-image-2",
            "gpt-image-2.5-vip",
            "gpt-image-2.5-flare-vip",
            "gpt-image-2.5-sunburst-vip",
            "gpt-image-2-vip",
            "gpt-image-2.5-all",
            "gpt-image-2-all",
            "gpt-image-1.5",
            "gpt-image-1",
            "gpt-image-1-mini",
        ),
    ),
    Family(
        "NANO_BANANA_MODELS",
        "image",
        ("gemini-", "nano-banana"),
        (
            "gemini-nano-banana-2.1",
            "gemini-3-pro-image",
            "gemini-3.1-flash-image",
            "gemini-3.1-flash-lite-image",
            "gemini-2.5-flash-image",
        ),
    ),
    Family(
        "FLUX_MODELS",
        "image",
        ("flux-",),
        (
            "flux-2-pro",
            "flux-2-max",
            "flux-2-flex",
            "flux-2-klein-9b",
            "flux-2-klein-4b",
            "flux-kontext-max",
            "flux-kontext-pro",
            "flux-dev",
        ),
    ),
    Family(
        "SEEDREAM_MODELS",
        "image",
        ("seedream-",),
        (
            "seedream-5-0-260128",
            "seedream-5-0-flash-260915",
            "seedream-5-0-pro-260628",
            "seedream-4-5-251128",
            "seedream-4-0-250828",
        ),
    ),
    Family("MAI_IMAGE_MODELS", "image", ("MAI-Image",), ("MAI-Image-2.6", "MAI-Image-2.6-Flash")),
    Family("OXYGEN_MODELS", "video", ("oxygen-",), ("oxygen-1.0",)),
    Family(
        "SEEDANCE_MODELS",
        "video",
        ("doubao-seedance-",),
        (
            "doubao-seedance-2-0-mini-260615",
            "doubao-seedance-2-0-fast-260128",
            "doubao-seedance-2-0-260128",
            "doubao-seedance-2-5-260628",
        ),
    ),
)
# Categories that this library covers. A model of another category is out of scope, so no report names it.
COVERED_CATEGORIES = ("text", "image", "video")


def merged(preferred: tuple[str, ...], found: list[str]) -> list[str]:
    """The preferred models in order, then the other models."""
    return [*preferred, *(model for model in found if model not in preferred)]


def build_catalog(registry: dict[str, Any]) -> dict[str, list[str]]:
    """The dropdown lists, by constant name."""
    models = registry["models"]
    chat = [
        model["id"]
        for model in models
        if model["category"] == "text" and "chat" in model["endpoints"] and model["id"] not in NOT_CHAT_MODELS
    ]
    catalog = {CHAT_CONSTANT: merged(PREFERRED_CHAT_MODELS, chat)}
    for family in FAMILIES:
        found = [
            model["id"]
            for model in models
            if model["category"] == family.category and model["id"].startswith(family.prefixes)
        ]
        catalog[family.constant] = merged(family.preferred, found)
    return catalog


def models_without_a_node(registry: dict[str, Any], catalog: dict[str, list[str]]) -> list[str]:
    offered = {model for models in catalog.values() for model in models}
    return [
        model["id"]
        for model in registry["models"]
        if model["category"] in COVERED_CATEGORIES and model["id"] not in offered
    ]


def preferred_models_not_listed(registry: dict[str, Any]) -> list[str]:
    listed = {model["id"] for model in registry["models"]}
    preferred = [*PREFERRED_CHAT_MODELS, *(model for family in FAMILIES for model in family.preferred)]
    return [model for model in preferred if model not in listed]


def render(catalog: dict[str, list[str]], registry_date: str) -> str:
    lines = [
        '"""Model IDs from the APIYI model registry. scripts/sync_models.py writes this file. Do not edit it."""',
        "",
        f'REGISTRY_DATE = "{registry_date}"',
    ]
    for constant, models in catalog.items():
        lines.extend(["", f"{constant} = ["])
        lines.extend(f'    "{model}",' for model in models)
        lines.append("]")
    return "\n".join(lines) + "\n"


def load_registry(path: str | None) -> dict[str, Any]:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    with urllib.request.urlopen(REGISTRY_URL, timeout=30) as response:  # noqa: S310
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry", help="Read the registry from this file, not from APIYI.")
    arguments = parser.parse_args()

    registry = load_registry(arguments.registry)
    catalog = build_catalog(registry)
    registry_date = datetime.fromisoformat(registry["generated_at"]).date().isoformat()
    CATALOG_PATH.write_text(render(catalog, registry_date), encoding="utf-8", newline="\n")

    for constant, models in catalog.items():
        print(f"{constant}: {len(models)}")
    print("No node offers:", ", ".join(models_without_a_node(registry, catalog)) or "nothing")
    print("Preferred but not in the registry:", ", ".join(preferred_models_not_listed(registry)) or "nothing")


if __name__ == "__main__":
    main()
