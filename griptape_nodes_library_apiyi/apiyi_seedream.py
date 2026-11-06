"""Seedream models on APIYI. Generation, editing, and image sequences all use one endpoint."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from griptape_nodes.exe_types.core_types import NodeError, ParameterMode
from griptape_nodes.exe_types.param_types.parameter_bool import ParameterBool
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.traits.options import Options

from griptape_nodes_library_apiyi.apiyi_client import ApiyiRequest
from griptape_nodes_library_apiyi.apiyi_image_node import GENERATIONS_PATH, ApiyiImageNode

__all__ = ["ApiyiSeedream"]

# The API limit for one request: input images plus output images.
MAX_TOTAL_IMAGES = 15
MAX_OUTPUT_IMAGES = 15


@dataclass(frozen=True)
class SeedreamSpec:
    sizes: tuple[str, ...]
    output_formats: tuple[str, ...]
    # The pro and flash models answer 400 to any `sequential_image_generation` value, even "disabled".
    sequences: bool


MODEL_SPECS = {
    "seedream-5-0-260128": SeedreamSpec(("2K", "3K"), ("png", "jpeg"), sequences=True),
    "seedream-5-0-flash-260915": SeedreamSpec(("1K", "1.5K", "2K"), ("png", "jpeg"), sequences=False),
    "seedream-5-0-pro-260628": SeedreamSpec(("1K", "2K"), ("png", "jpeg"), sequences=False),
    "seedream-4-5-251128": SeedreamSpec(("2K", "4K"), ("jpeg",), sequences=True),
    "seedream-4-0-250828": SeedreamSpec(("1K", "2K", "4K"), ("jpeg",), sequences=True),
}
DEFAULT_MODEL = "seedream-5-0-260128"
# For a model ID that a user types: send only the fields that every model accepts.
UNKNOWN_MODEL_SPEC = SeedreamSpec(("2K",), ("jpeg",), sequences=False)


def _spec(model: str) -> SeedreamSpec:
    return MODEL_SPECS.get(model, UNKNOWN_MODEL_SPEC)


class ApiyiSeedream(ApiyiImageNode):
    """Generate or edit images with Seedream models through APIYI."""

    MODEL_CHOICES: ClassVar[list[str]] = list(MODEL_SPECS)
    DEFAULT_MODEL: ClassVar[str] = DEFAULT_MODEL
    MAX_INPUT_IMAGES: ClassVar[int] = 10
    DEFAULT_FILENAME: ClassVar[str] = "apiyi_seedream.jpg"

    def _add_model_parameters(self) -> None:
        spec = _spec(self.DEFAULT_MODEL)
        self.add_parameter(
            ParameterString(
                name="size",
                default_value=spec.sizes[0],
                tooltip="Output size. The presets depend on the model. You can also type WIDTHxHEIGHT.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=list(spec.sizes), allow_custom=True)},
            )
        )
        self.add_parameter(
            ParameterInt(
                name="max_images",
                default_value=1,
                tooltip=(
                    f"Above 1, the model makes a sequence of related images. Input and output images "
                    f"together can be {MAX_TOTAL_IMAGES} at most. APIYI bills each image."
                ),
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                min_val=1,
                max_val=MAX_OUTPUT_IMAGES,
                slider=True,
            )
        )
        self.add_parameter(
            ParameterString(
                name="output_format",
                default_value=spec.output_formats[0],
                tooltip="Output image format.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=list(spec.output_formats))},
            )
        )
        self.add_parameter(
            ParameterBool(
                name="watermark",
                default_value=False,
                tooltip="Add a watermark to the output.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )
        )

    def _sync_model(self, model: str) -> None:
        spec = _spec(model)
        self._offer_choices("size", spec.sizes)
        self._offer_choices("output_format", spec.output_formats)
        self._show(["output_format"], visible=len(spec.output_formats) > 1)
        self._show(["max_images"], visible=spec.sequences)
        self._show(["all_images"], visible=spec.sequences)

    def _output_count(self, model: str) -> int:
        return int(self.get_parameter_value("max_images") or 1) if _spec(model).sequences else 1

    def _validate_settings(self, model: str, image_count: int) -> list[Exception]:
        total = image_count + self._output_count(model)
        if total <= MAX_TOTAL_IMAGES:
            return []
        return [
            NodeError(
                f"This request has {total} images in total, and the limit is {MAX_TOTAL_IMAGES}. "
                "Remove input images or lower 'Max Images'."
            )
        ]

    async def _build_request(self, model: str, prompt: str, images: list[Any]) -> ApiyiRequest:
        spec = _spec(model)
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": self.get_parameter_value("size"),
            # The default is a URL, but base64 removes a second download from the vendor storage.
            "response_format": "b64_json",
            "watermark": bool(self.get_parameter_value("watermark")),
        }
        if len(spec.output_formats) > 1:
            payload["output_format"] = self.get_parameter_value("output_format")
        if images:
            payload["image"] = await self._image_references(images)
        if spec.sequences:
            output_count = self._output_count(model)
            payload["sequential_image_generation"] = "auto" if output_count > 1 else "disabled"
            if output_count > 1:
                payload["sequential_image_generation_options"] = {"max_images": output_count}
        return ApiyiRequest(path=GENERATIONS_PATH, json=payload)
