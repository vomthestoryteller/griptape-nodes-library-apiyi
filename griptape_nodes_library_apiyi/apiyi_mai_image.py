"""MAI-Image models on APIYI.

These models refuse `response_format`, `seed`, and `negative_prompt` with a 400. They also ignore `size`
on generation without any error, so the node always sends `width` and `height`.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from griptape_nodes.exe_types.core_types import NodeError, ParameterMode
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.traits.options import Options

from griptape_nodes_library_apiyi.apiyi_client import ApiyiRequest
from griptape_nodes_library_apiyi.apiyi_image_node import EDITS_PATH, GENERATIONS_PATH, ApiyiImageNode
from griptape_nodes_library_apiyi.apiyi_model_catalog import MAI_IMAGE_MODELS

__all__ = ["ApiyiMaiImage"]

SIZES = ["1024x1024", "1536x1536", "1536x1024", "1024x1536", "1792x1008", "1008x1792", "3072x768"]
SIZE_PATTERN = re.compile(r"(\d+)x(\d+)")
MIN_EDGE = 768
MAX_PIXELS = 2_359_296


def _dimensions(size: Any) -> tuple[int, int] | None:
    match = SIZE_PATTERN.fullmatch(size.strip()) if isinstance(size, str) else None
    return (int(match.group(1)), int(match.group(2))) if match else None


class ApiyiMaiImage(ApiyiImageNode):
    """Generate or edit images with MAI-Image models through APIYI."""

    MODEL_CHOICES: ClassVar[list[str]] = MAI_IMAGE_MODELS
    DEFAULT_MODEL: ClassVar[str] = "MAI-Image-2.6"
    MAX_INPUT_IMAGES: ClassVar[int] = 5
    DEFAULT_FILENAME: ClassVar[str] = "apiyi_mai_image.png"

    def _add_model_parameters(self) -> None:
        self.add_parameter(
            ParameterString(
                name="size",
                default_value=SIZES[0],
                tooltip=(
                    f"Output size. You can also type WIDTHxHEIGHT: each edge needs {MIN_EDGE} pixels or more, "
                    f"and width times height can be {MAX_PIXELS:,} at most."
                ),
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=SIZES, allow_custom=True)},
            )
        )

    def _sync_model(self, model: str) -> None:
        """All MAI-Image models have the same parameters."""

    def _validate_settings(self, model: str, image_count: int) -> list[Exception]:  # noqa: ARG002
        size = self.get_parameter_value("size")
        dimensions = _dimensions(size)
        if dimensions is None:
            return [NodeError(f"'Size' must look like 1536x1024. It is set to '{size}'.")]
        width, height = dimensions
        if min(width, height) < MIN_EDGE or width * height > MAX_PIXELS:
            return [
                NodeError(
                    f"'Size' is {width}x{height}. Each edge needs {MIN_EDGE} pixels or more, "
                    f"and width times height can be {MAX_PIXELS:,} at most."
                )
            ]
        return []

    async def _build_request(self, model: str, prompt: str, images: list[Any]) -> ApiyiRequest:
        dimensions = _dimensions(self.get_parameter_value("size"))
        if dimensions is None:
            raise NodeError("'Size' must look like 1536x1024.")
        width, height = dimensions
        if not images:
            payload = {"model": model, "prompt": prompt, "width": width, "height": height}
            return ApiyiRequest(path=GENERATIONS_PATH, json=payload)

        form = {"model": model, "prompt": prompt, "width": str(width), "height": str(height)}
        return ApiyiRequest(
            path=EDITS_PATH, form=form, files=await self._multipart_images(images, field_name="image[]")
        )
