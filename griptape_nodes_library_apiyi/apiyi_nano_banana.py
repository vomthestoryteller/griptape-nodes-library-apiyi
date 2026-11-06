"""Nano Banana (Gemini image) models on APIYI. They use the Gemini native format, not OpenAI Images."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar
from urllib.parse import quote

from griptape_nodes.exe_types.core_types import NodeError, ParameterMode
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.traits.options import Options

from griptape_nodes_library_apiyi.apiyi_client import ApiyiRequest
from griptape_nodes_library_apiyi.apiyi_image_node import ApiyiImageNode
from griptape_nodes_library_apiyi.apiyi_images import decode_b64
from griptape_nodes_library_apiyi.apiyi_model_catalog import NANO_BANANA_MODELS

__all__ = ["ApiyiNanoBanana"]

RATIOS_10 = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
RATIOS_14 = ("1:1", "1:4", "4:1", "1:8", "8:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
# "auto" sends no ratio, and the model chooses one. That is useful when an input image sets the shape.
AUTO_RATIO = "auto"


@dataclass(frozen=True)
class NanoBananaSpec:
    image_sizes: tuple[str, ...]
    aspect_ratios: tuple[str, ...]


PRO_SPEC = NanoBananaSpec(("1K", "2K", "4K"), RATIOS_10)
FLASH_SPEC = NanoBananaSpec(("512", "1K", "2K", "4K"), RATIOS_14)
FIRST_GENERATION_SPEC = NanoBananaSpec(("1K",), RATIOS_10)

MODEL_SPECS = {
    "gemini-nano-banana-2.1": NanoBananaSpec(("1K", "2K", "4K"), RATIOS_14),
    "gemini-3-pro-image": PRO_SPEC,
    "gemini-3.1-flash-image": FLASH_SPEC,
    "gemini-3.1-flash-lite-image": NanoBananaSpec(("1K",), RATIOS_14),
    "gemini-2.5-flash-image": FIRST_GENERATION_SPEC,
    "gemini-2.5-flash-image-preview": FIRST_GENERATION_SPEC,
    # Aliases in the APIYI documents.
    "gemini-3-pro-image-preview": PRO_SPEC,
    "gemini-3.1-flash-image-preview": FLASH_SPEC,
    # The documents do not describe the models below. These entries come from their names and their prices.
    "gemini-3-pro-image-preview-1k": NanoBananaSpec(("1K",), RATIOS_10),
    "gemini-3-pro-image-preview-2k": NanoBananaSpec(("2K",), RATIOS_10),
    "gemini-3-pro-image-preview-4k": NanoBananaSpec(("4K",), RATIOS_10),
    "gemini-3.1-flash-image-4k": NanoBananaSpec(("4K",), RATIOS_14),
    "gemini-3.1-flash-image-preview-4k": NanoBananaSpec(("4K",), RATIOS_14),
    "nano-banana": FIRST_GENERATION_SPEC,
    "nano-banana-2": FLASH_SPEC,
    "nano-banana-pro": PRO_SPEC,
}
DEFAULT_MODEL = "gemini-nano-banana-2.1"


def _spec(model: str) -> NanoBananaSpec:
    return MODEL_SPECS.get(model, MODEL_SPECS[DEFAULT_MODEL])


def _text_of(parts: list[dict[str, Any]]) -> str:
    return " ".join(part["text"].strip() for part in parts if isinstance(part.get("text"), str)).strip()


def _no_image_error(body: dict[str, Any]) -> NodeError:
    """Explain an answer with no image. Google blocks content with HTTP 200, so the reason is in the body."""
    candidates = body.get("candidates") or []
    candidate = candidates[0] if candidates else {}
    parts = (candidate.get("content") or {}).get("parts") or []
    block_reason = (body.get("promptFeedback") or {}).get("blockReason")
    finish_reason = candidate.get("finishReason")
    text = _text_of(parts)

    if block_reason:
        message = (
            f"Google blocked the request before generation (reason: {block_reason}). "
            "Change the prompt or remove an input image, then run the node again."
        )
    elif text:
        message = f"The model returned text and no image: {text}"
    elif finish_reason and finish_reason != "STOP":
        message = (
            f"Google stopped the generation (reason: {finish_reason}). "
            "Change the prompt and run the node again. A safety stop can also pass on a second run."
        )
    else:
        message = "The answer from APIYI has no image. Run the node again."

    fields = {"finish_reason": finish_reason, "block_reason": block_reason}
    return NodeError(message, fields={key: value for key, value in fields.items() if value}, response=body)


class ApiyiNanoBanana(ApiyiImageNode):
    """Generate or edit images with Nano Banana models through APIYI."""

    MODEL_CHOICES: ClassVar[list[str]] = NANO_BANANA_MODELS
    DEFAULT_MODEL: ClassVar[str] = DEFAULT_MODEL
    MAX_INPUT_IMAGES: ClassVar[int] = 14
    DEFAULT_FILENAME: ClassVar[str] = "apiyi_nano_banana.png"
    # The Gemini format takes only these two types in `inlineData`.
    INPUT_FORMATS: ClassVar[frozenset[str]] = frozenset({"PNG", "JPEG"})

    def _add_model_parameters(self) -> None:
        spec = _spec(self.DEFAULT_MODEL)
        self.add_parameter(
            ParameterString(
                name="aspect_ratio",
                default_value=spec.aspect_ratios[0],
                tooltip="Aspect ratio of the output. Use 'auto' to let the model choose from the prompt and images.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=[*spec.aspect_ratios, AUTO_RATIO])},
            )
        )
        self.add_parameter(
            ParameterString(
                name="image_size",
                default_value=spec.image_sizes[0],
                tooltip="Output resolution. The available values depend on the model.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=list(spec.image_sizes))},
            )
        )

    def _sync_model(self, model: str) -> None:
        spec = _spec(model)
        self._offer_choices("aspect_ratio", [*spec.aspect_ratios, AUTO_RATIO])
        self._offer_choices("image_size", spec.image_sizes)

    async def _build_request(self, model: str, prompt: str, images: list[Any]) -> ApiyiRequest:
        # One text part, then one part for each image. A part must not hold both.
        parts: list[dict[str, Any]] = [{"text": prompt}]
        for index, value in enumerate(images, start=1):
            image = await self._prepare(value, index)
            parts.append({"inlineData": {"mimeType": image.mime_type, "data": image.base64}})

        image_config = {"imageSize": self.get_parameter_value("image_size")}
        aspect_ratio = self.get_parameter_value("aspect_ratio")
        if aspect_ratio != AUTO_RATIO:
            image_config["aspectRatio"] = aspect_ratio

        return ApiyiRequest(
            path=f"/v1beta/models/{quote(model, safe='')}:generateContent",
            json={
                "contents": [{"parts": parts}],
                "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": image_config},
            },
        )

    async def _read_results(self, body: dict[str, Any]) -> list[bytes]:
        candidates = body.get("candidates") or []
        parts = (candidates[0].get("content") or {}).get("parts") or [] if candidates else []
        images = [part["inlineData"] for part in parts if isinstance(part.get("inlineData"), dict)]
        if not images:
            raise _no_image_error(body)
        # The parts have no fixed order, and a complex task can return drafts. The last image is the final one.
        return [decode_b64(images[-1]["data"])]
