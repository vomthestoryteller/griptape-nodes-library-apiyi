"""GPT Image models on APIYI.

The same model family comes in three sales lines with different request rules:

- Official lines, such as `gpt-image-2.5-flare`: billed by token, with every parameter.
- `-vip` lines: billed by call, `size` and `quality` only.
- `-all` lines: billed by call, no `size` or `quality`. The prompt sets the shape of the image.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Any, ClassVar

from griptape_nodes.exe_types.core_types import NodeError, ParameterGroup, ParameterMode
from griptape_nodes.exe_types.param_types.parameter_image import ParameterImage
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.traits.options import Options

from griptape_nodes_library_apiyi.apiyi_client import ApiyiRequest
from griptape_nodes_library_apiyi.apiyi_image_node import EDITS_PATH, GENERATIONS_PATH, ApiyiImageNode
from griptape_nodes_library_apiyi.apiyi_images import prepare_mask, read_image
from griptape_nodes_library_apiyi.apiyi_model_catalog import GPT_IMAGE_MODELS

__all__ = ["ApiyiGptImage", "GptLine", "line_of"]

# `xhigh`, `max`, and transparent backgrounds start at these model versions.
NEWER_MODEL_PREFIXES = ("gpt-image-2.5", "gpt-image-2.6")
COMPLEXITY_MODEL_PREFIX = "gpt-image-2.6"
# The first models have fewer sizes, and only `gpt-image-1` has the edit endpoint.
FIRST_MODELS_PREFIX = "gpt-image-1"
FIRST_MODEL_WITH_EDITS = "gpt-image-1"

FIRST_MODEL_SIZES = ["1024x1024", "1536x1024", "1024x1536", "auto"]
OFFICIAL_SIZES = ["1024x1024", "1536x1024", "1024x1536", "2048x2048", "2048x1152", "3840x2160", "2160x3840", "auto"]
# The -vip lines rewrite a size outside this table, so the dropdown offers the table.
VIP_SIZES = [
    "auto",
    "1280x1280",
    "848x1280",
    "1280x848",
    "960x1280",
    "1280x960",
    "1024x1280",
    "1280x1024",
    "720x1280",
    "1280x720",
    "1280x544",
    "2048x2048",
    "1360x2048",
    "2048x1360",
    "1536x2048",
    "2048x1536",
    "1632x2048",
    "2048x1632",
    "1152x2048",
    "2048x1152",
    "2048x864",
    "2880x2880",
    "2336x3520",
    "3520x2336",
    "2480x3312",
    "3312x2480",
    "2560x3216",
    "3216x2560",
    "2160x3840",
    "3840x2160",
    "3840x1632",
]
# Highest first: a model change that drops the current quality moves to the closest value.
BASE_QUALITIES = ["high", "medium", "low", "auto"]
EXTENDED_QUALITIES = ["max", "xhigh", "high", "medium", "low", "auto"]
BASE_BACKGROUNDS = ["auto", "opaque"]
EXTENDED_BACKGROUNDS = ["auto", "opaque", "transparent"]
OUTPUT_FORMATS = ["png", "jpeg", "webp"]
FORMATS_WITH_ALPHA = ("png", "webp")
FORMATS_WITH_COMPRESSION = ("jpeg", "webp")
# "default" sends no value, which lets the API use its own default of high.
COMPLEXITIES = ["default", "low", "medium", "high"]


class GptLine(StrEnum):
    OFFICIAL = "official"
    VIP = "vip"
    ALL = "all"


def line_of(model: str) -> GptLine:
    """The sales line of a model ID. The suffix also works for a model ID that a user types."""
    if model.endswith("-vip"):
        return GptLine.VIP
    if model.endswith("-all"):
        return GptLine.ALL
    return GptLine.OFFICIAL


def _has_edits(model: str) -> bool:
    return not model.startswith(FIRST_MODELS_PREFIX) or model == FIRST_MODEL_WITH_EDITS


def _sizes(line: GptLine, *, first: bool) -> list[str]:
    if line is GptLine.VIP:
        return VIP_SIZES
    return FIRST_MODEL_SIZES if first else OFFICIAL_SIZES


class ApiyiGptImage(ApiyiImageNode):
    """Generate or edit images with GPT Image models through APIYI."""

    MODEL_CHOICES: ClassVar[list[str]] = GPT_IMAGE_MODELS
    DEFAULT_MODEL: ClassVar[str] = "gpt-image-2.5-flare"
    MAX_INPUT_IMAGES: ClassVar[int] = 16
    DEFAULT_FILENAME: ClassVar[str] = "apiyi_gpt_image.png"

    def _add_model_parameters(self) -> None:
        self.add_parameter(
            ParameterImage(
                name="mask",
                tooltip=(
                    "Optional PNG mask for the first input image. It must have the same size as that image. "
                    "Transparent pixels mark the area to change."
                ),
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )
        )
        self.add_parameter(
            ParameterString(
                name="size",
                default_value=OFFICIAL_SIZES[0],
                tooltip=(
                    "Output size. You can also type WIDTHxHEIGHT: both values must be multiples of 16, "
                    "and the long edge can be 3840 at most."
                ),
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=list(OFFICIAL_SIZES), allow_custom=True)},
            )
        )
        self.add_parameter(
            ParameterString(
                name="quality",
                default_value="medium",
                tooltip="Output quality. A higher value takes more time and costs more on token-billed models.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=list(EXTENDED_QUALITIES))},
            )
        )

        with ParameterGroup(name="Generation Settings", ui_options={"collapsed": True}) as generation_settings_group:
            ParameterString(
                name="complexity",
                default_value=COMPLEXITIES[0],
                tooltip="How much the model plans before it renders. Use a higher value for dense text and layouts.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=COMPLEXITIES)},
            )
            ParameterString(
                name="output_format",
                default_value=OUTPUT_FORMATS[0],
                tooltip="Output image format.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=OUTPUT_FORMATS)},
            )
            ParameterInt(
                name="output_compression",
                default_value=85,
                tooltip="Compression for JPEG or WEBP output, from 0 to 100.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                min_val=0,
                max_val=100,
                slider=True,
                hide=True,
            )
            ParameterString(
                name="background",
                default_value=EXTENDED_BACKGROUNDS[0],
                tooltip="Background mode. A transparent background needs PNG or WEBP output.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=list(EXTENDED_BACKGROUNDS))},
            )
            ParameterString(
                name="moderation",
                default_value="auto",
                tooltip="Moderation level. This applies when no input image is connected.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=["auto", "low"])},
            )
        self.add_node_element(generation_settings_group)

    def _sync_model(self, model: str) -> None:
        line = line_of(model)
        official = line is GptLine.OFFICIAL
        self._show(["size", "quality"], visible=line is not GptLine.ALL)
        self._show(["output_format", "background", "moderation"], visible=official)
        self._show(["mask"], visible=official and _has_edits(model))
        self._show(["complexity"], visible=official and model.startswith(COMPLEXITY_MODEL_PREFIX))
        self._sync_compression_visibility(model)
        if line is GptLine.ALL:
            return

        first = model.startswith(FIRST_MODELS_PREFIX)
        newer = model.startswith(NEWER_MODEL_PREFIXES)
        self._offer_choices("size", _sizes(line, first=first))
        self._offer_choices("quality", EXTENDED_QUALITIES if newer else BASE_QUALITIES)
        if official:
            self._offer_choices("background", EXTENDED_BACKGROUNDS if newer or first else BASE_BACKGROUNDS)

    def _on_value_changed(self, name: str, value: Any) -> None:
        super()._on_value_changed(name, value)
        if name == "output_format":
            self._sync_compression_visibility(self._model())

    def _sync_compression_visibility(self, model: str) -> None:
        output_format = self.get_parameter_value("output_format")
        visible = line_of(model) is GptLine.OFFICIAL and output_format in FORMATS_WITH_COMPRESSION
        self._show(["output_compression"], visible=visible)

    def _input_image_limit(self, model: str) -> int:
        return self.MAX_INPUT_IMAGES if _has_edits(model) else 0

    def _validate_settings(self, model: str, image_count: int) -> list[Exception]:
        if line_of(model) is not GptLine.OFFICIAL:
            return []
        problems: list[Exception] = []
        output_format = self.get_parameter_value("output_format")
        if self.get_parameter_value("background") == "transparent" and output_format not in FORMATS_WITH_ALPHA:
            problems.append(
                NodeError(f"A transparent 'Background' needs PNG or WEBP. 'Output Format' is {output_format}.")
            )
        if _has_edits(model) and self.get_parameter_value("mask") and image_count == 0:
            problems.append(NodeError("'Mask' applies to the first input image. Connect an image to 'Input Images'."))
        return problems

    async def _build_request(self, model: str, prompt: str, images: list[Any]) -> ApiyiRequest:
        line = line_of(model)
        settings = self._shared_settings(line)
        if not images:
            payload = {"model": model, "prompt": prompt, **settings, **self._generation_settings(model, line)}
            return ApiyiRequest(path=GENERATIONS_PATH, json=payload)

        mask_value = self.get_parameter_value("mask") if line is GptLine.OFFICIAL else None
        # The mask must match the size of the first image exactly, so a mask turns compression off.
        files = await self._multipart_images(
            images,
            field_name="image[]" if line is GptLine.OFFICIAL else "image",
            compress=False if mask_value else None,
        )
        if mask_value:
            mask = await asyncio.to_thread(prepare_mask, await read_image(mask_value, "'Mask'"))
            files.append(("mask", ("mask.png", mask.content, mask.mime_type)))
        form = {"model": model, "prompt": prompt, **{key: str(value) for key, value in settings.items()}}
        return ApiyiRequest(path=EDITS_PATH, form=form, files=files)

    def _shared_settings(self, line: GptLine) -> dict[str, Any]:
        """Settings that generation and editing both accept. `n` is never sent: the -vip lines bill for it."""
        match line:
            case GptLine.ALL:
                return {"response_format": "b64_json"}
            case GptLine.VIP:
                return {
                    "size": self.get_parameter_value("size"),
                    "quality": self.get_parameter_value("quality"),
                    "response_format": "b64_json",
                }
            case GptLine.OFFICIAL:
                # Official models always answer in base64 and refuse `response_format` with a 400.
                settings = {
                    "size": self.get_parameter_value("size"),
                    "quality": self.get_parameter_value("quality"),
                    "output_format": self.get_parameter_value("output_format"),
                    "background": self.get_parameter_value("background"),
                }
                if settings["output_format"] in FORMATS_WITH_COMPRESSION:
                    settings["output_compression"] = self.get_parameter_value("output_compression")
                return settings
            case _:
                msg = f"Unknown GPT Image line: {line!r}"
                raise ValueError(msg)

    def _generation_settings(self, model: str, line: GptLine) -> dict[str, Any]:
        if line is not GptLine.OFFICIAL:
            return {}
        settings = {"moderation": self.get_parameter_value("moderation")}
        complexity = self.get_parameter_value("complexity")
        if model.startswith(COMPLEXITY_MODEL_PREFIX) and complexity != COMPLEXITIES[0]:
            settings["complexity"] = complexity
        return settings
