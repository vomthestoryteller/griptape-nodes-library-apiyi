"""The dropdowns offer the models in the catalog, and the script that writes the catalog keeps its rules."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from griptape_nodes.exe_types.node_types import BaseNode
from griptape_nodes.traits.options import Options

from griptape_nodes_library_apiyi import apiyi_model_catalog as catalog

from .conftest import LIBRARY_JSON, REPO_ROOT, create_node, run_node
from .fake_apiyi import FakeApiyi, image_bytes

NODES_AND_LISTS = {
    "ApiyiPrompt": catalog.CHAT_MODELS,
    "ApiyiGptImage": catalog.GPT_IMAGE_MODELS,
    "ApiyiNanoBanana": catalog.NANO_BANANA_MODELS,
    "ApiyiFlux": catalog.FLUX_MODELS,
    "ApiyiSeedream": catalog.SEEDREAM_MODELS,
    "ApiyiMaiImage": catalog.MAI_IMAGE_MODELS,
    "ApiyiOxygen": catalog.OXYGEN_MODELS,
    "ApiyiSeedance": catalog.SEEDANCE_MODELS,
}
EXCLUDED_MODELS = [
    "gpt-5-pro",
    "gpt-5-pro-2025-10-06",
    "gpt-5.2-pro",
    "gpt-5.2-pro-2025-12-11",
    "gpt-5.4-pro",
    "gpt-5.5-pro",
    "bge-m3",
]
PROMPT = "a red fox in the snow"


def choices(node: BaseNode, name: str) -> list[str]:
    return node.get_parameter_by_name(name).find_elements_by_type(Options)[0].choices


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_models", REPO_ROOT / "scripts" / "sync_models.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass reads the module from sys.modules while the module loads.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sync_models = _load_script()


def _model(model_id: str, category: str = "text", endpoints: tuple[str, ...] = ("chat",)) -> dict[str, Any]:
    return {"id": model_id, "category": category, "endpoints": list(endpoints)}


# ---- The dropdowns ----


@pytest.mark.parametrize(("node_type", "models"), NODES_AND_LISTS.items())
def test_each_dropdown_offers_the_catalog(node_type: str, models: list[str]) -> None:
    node = create_node(node_type)

    assert choices(node, "model") == models
    assert node.get_parameter_value("model") in models


@pytest.mark.parametrize("model", EXCLUDED_MODELS)
def test_the_chat_list_leaves_out_models_that_cannot_chat(model: str) -> None:
    assert model not in catalog.CHAT_MODELS


def test_the_veo_and_grok_lists_are_not_in_the_registry_so_their_nodes_hold_them() -> None:
    assert choices(create_node("ApiyiVeo"), "model") == ["veo-3.1-fast-generate-preview", "veo-3.1-generate-preview"]
    assert len(choices(create_node("ApiyiGrokVideo"), "model")) == 3


def test_the_library_lists_each_video_node_in_the_video_category() -> None:
    manifest = json.loads(LIBRARY_JSON.read_text(encoding="utf-8"))
    video_nodes = {node["class_name"] for node in manifest["nodes"] if node["metadata"]["category"] == "video"}

    assert video_nodes == {"ApiyiOxygen", "ApiyiVeo", "ApiyiGrokVideo", "ApiyiSeedance"}


def test_the_library_version_matches_the_project_version() -> None:
    manifest = json.loads(LIBRARY_JSON.read_text(encoding="utf-8"))

    assert f'version = "{manifest["metadata"]["library_version"]}"' in (REPO_ROOT / "pyproject.toml").read_text(
        encoding="utf-8"
    )


# ---- Image models ----


async def test_a_first_gpt_image_model_offers_its_own_sizes(fake: FakeApiyi) -> None:
    node = create_node("ApiyiGptImage", model="gpt-image-1", size="3840x2160")

    assert choices(node, "size")[:4] == ["1024x1024", "1536x1024", "1024x1536", "auto"]
    assert "max" not in choices(node, "quality")
    assert "transparent" in choices(node, "background")


@pytest.mark.parametrize("model", ["gpt-image-1.5", "gpt-image-1.5-2025-12-16", "gpt-image-1-mini"])
async def test_only_gpt_image_1_has_edits(model: str, fake: FakeApiyi) -> None:
    node = create_node("ApiyiGptImage", prompt=PROMPT, model=model, input_images=["https://example.com/a.png"])

    result = await run_node(node)

    assert f"{model} accepts 0" in str(result.result_details)
    assert fake.posts == []
    assert node.get_parameter_by_name("mask").ui_options.get("hide")


async def test_gpt_image_1_can_edit(tmp_path: Path, fake: FakeApiyi) -> None:
    image = tmp_path / "input.png"
    image.write_bytes(image_bytes())
    node = create_node("ApiyiGptImage", prompt=PROMPT, model="gpt-image-1", input_images=[str(image)])

    result = await run_node(node)

    assert result.succeeded(), result.result_details
    assert fake.last_post.path == "/v1/images/edits"


@pytest.mark.parametrize("model", ["gpt-image-1", "gpt-image-1.5", "gpt-image-1-mini"])
async def test_a_first_gpt_image_model_generates_on_the_official_rules(model: str, fake: FakeApiyi) -> None:
    result = await run_node(create_node("ApiyiGptImage", prompt=PROMPT, model=model, quality="high"))

    assert result.succeeded(), result.result_details
    assert fake.last_post.path == "/v1/images/generations"
    assert fake.last_post.json["model"] == model
    assert "response_format" not in fake.last_post.json


async def test_a_dated_gpt_image_model_has_the_rules_of_its_version(fake: FakeApiyi) -> None:
    node = create_node("ApiyiGptImage", model="gpt-image-2.5-flare-2026-09-08")

    assert "max" in choices(node, "quality")
    assert "3840x2160" in choices(node, "size")


@pytest.mark.parametrize(
    ("model", "image_sizes", "has_wide_ratios"),
    [
        ("gemini-2.5-flash-image", ["1K"], False),
        ("gemini-2.5-flash-image-preview", ["1K"], False),
        ("gemini-3-pro-image-preview", ["1K", "2K", "4K"], False),
        ("gemini-3-pro-image-preview-2k", ["2K"], False),
        ("gemini-3.1-flash-image-preview", ["512", "1K", "2K", "4K"], True),
        ("gemini-3.1-flash-image-4k", ["4K"], True),
        ("nano-banana", ["1K"], False),
        ("nano-banana-2", ["512", "1K", "2K", "4K"], True),
        ("nano-banana-pro", ["1K", "2K", "4K"], False),
    ],
)
async def test_each_new_nano_banana_model_has_its_rules(
    model: str, image_sizes: list[str], has_wide_ratios: bool, fake: FakeApiyi
) -> None:
    node = create_node("ApiyiNanoBanana", prompt=PROMPT, model=model)

    assert choices(node, "image_size") == image_sizes
    assert ("8:1" in choices(node, "aspect_ratio")) is has_wide_ratios

    result = await run_node(node)

    assert result.succeeded(), result.result_details
    assert fake.last_post.path.startswith(f"/v1beta/models/{model}:")


def test_each_nano_banana_model_in_the_catalog_has_a_rule() -> None:
    from griptape_nodes_library_apiyi.apiyi_nano_banana import MODEL_SPECS

    assert set(catalog.NANO_BANANA_MODELS) == set(MODEL_SPECS)


# ---- The script ----


def test_the_script_keeps_the_preferred_order_and_adds_new_models_at_the_end() -> None:
    assert sync_models.merged(("b", "a"), ["a", "b", "c"]) == ["b", "a", "c"]


def test_the_script_keeps_a_preferred_model_that_the_registry_does_not_list() -> None:
    assert sync_models.merged(("gone", "a"), ["a"]) == ["gone", "a"]


def test_the_script_puts_only_chat_models_in_the_chat_list() -> None:
    registry = {
        "models": [
            _model("new-chat"),
            _model("responses-only", endpoints=("responses",)),
            _model("bge-m3"),
            _model("new-image", "image", ("images",)),
        ]
    }

    built = sync_models.build_catalog(registry)

    assert "new-chat" in built["CHAT_MODELS"]
    assert "responses-only" not in built["CHAT_MODELS"]
    assert "bge-m3" not in built["CHAT_MODELS"]
    assert "new-image" not in built["CHAT_MODELS"]


def test_the_script_sorts_new_models_into_their_families() -> None:
    registry = {
        "models": [
            _model("gpt-image-3", "image", ("images",)),
            _model("nano-banana-3", "image", ("gemini",)),
            _model("doubao-seedance-3-0", "video", ("chat",)),
            _model("oxygen-2.0", "video", ("videos",)),
        ]
    }

    built = sync_models.build_catalog(registry)

    assert built["GPT_IMAGE_MODELS"][-1] == "gpt-image-3"
    assert built["NANO_BANANA_MODELS"][-1] == "nano-banana-3"
    assert built["SEEDANCE_MODELS"][-1] == "doubao-seedance-3-0"
    assert built["OXYGEN_MODELS"][-1] == "oxygen-2.0"


def test_the_script_reports_models_that_no_node_offers() -> None:
    registry = {"models": [_model("wan9-video", "video"), _model("tts-9", "audio"), _model("new-chat")]}

    built = sync_models.build_catalog(registry)

    assert sync_models.models_without_a_node(registry, built) == ["wan9-video"]


def test_the_script_reports_preferred_models_that_the_registry_does_not_list() -> None:
    missing = sync_models.preferred_models_not_listed({"models": [_model("claude-fable-5-1")]})

    assert "claude-fable-5-1" not in missing
    assert "gpt-6-luna" in missing


def test_the_script_writes_python_that_gives_the_same_lists(tmp_path: Path) -> None:
    built = {"CHAT_MODELS": ["a", "b"], "FLUX_MODELS": ["c"]}
    written = tmp_path / "written_catalog.py"

    written.write_text(sync_models.render(built, "2026-11-06"), encoding="utf-8")

    namespace: dict[str, Any] = {}
    exec(written.read_text(encoding="utf-8"), namespace)  # noqa: S102
    assert namespace["CHAT_MODELS"] == ["a", "b"]
    assert namespace["FLUX_MODELS"] == ["c"]
    assert namespace["REGISTRY_DATE"] == "2026-11-06"
