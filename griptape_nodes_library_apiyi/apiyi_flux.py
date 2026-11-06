"""FLUX models on APIYI. The answer is a URL that works for 10 minutes, so the node downloads it at once."""

from __future__ import annotations

from typing import Any, ClassVar

from griptape_nodes.exe_types.core_types import Parameter, ParameterGroup, ParameterMode
from griptape_nodes.exe_types.param_components.seed_parameter import SeedParameter
from griptape_nodes.exe_types.param_types.parameter_bool import ParameterBool
from griptape_nodes.exe_types.param_types.parameter_float import ParameterFloat
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.traits.options import Options

from griptape_nodes_library_apiyi.apiyi_client import ApiyiRequest
from griptape_nodes_library_apiyi.apiyi_image_node import GENERATIONS_PATH, ApiyiImageNode
from griptape_nodes_library_apiyi.apiyi_model_catalog import FLUX_MODELS

__all__ = ["ApiyiFlux"]

# Model ID -> how many input images it accepts.
MODEL_INPUT_LIMITS = {
    "flux-2-pro": 8,
    "flux-2-max": 8,
    "flux-2-flex": 8,
    "flux-2-klein-9b": 4,
    "flux-2-klein-4b": 4,
    "flux-kontext-max": 1,
    "flux-kontext-pro": 1,
    "flux-dev": 1,
}
FLEX_MODEL = "flux-2-flex"
KLEIN_MODEL_PREFIX = "flux-2-klein"

SIZES = ["1024x1024", "1536x1024", "1024x1536", "1920x1080", "1440x2048", "2048x2048"]
ASPECT_RATIOS = ["auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"]
AUTO_RATIO = "auto"


class ApiyiFlux(ApiyiImageNode):
    """Generate or edit images with FLUX models through APIYI."""

    MODEL_CHOICES: ClassVar[list[str]] = FLUX_MODELS
    DEFAULT_MODEL: ClassVar[str] = "flux-2-pro"
    MAX_INPUT_IMAGES: ClassVar[int] = 8
    DEFAULT_FILENAME: ClassVar[str] = "apiyi_flux.jpg"

    def _add_model_parameters(self) -> None:
        self.add_parameter(
            ParameterString(
                name="size",
                default_value=SIZES[0],
                tooltip=(
                    "Output size when no input image is connected. You can also type WIDTHxHEIGHT: "
                    "use multiples of 16, up to 4 megapixels."
                ),
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=SIZES, allow_custom=True)},
            )
        )
        self.add_parameter(
            ParameterString(
                name="aspect_ratio",
                default_value=AUTO_RATIO,
                tooltip="Aspect ratio when an input image is connected. 'auto' follows the first input image.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=ASPECT_RATIOS, allow_custom=True)},
            )
        )

        self._seed_parameter = SeedParameter(self)
        with ParameterGroup(name="Generation Settings", ui_options={"collapsed": True}) as generation_settings_group:
            self._seed_parameter.add_input_parameters(inside_param_group=True)
            ParameterString(
                name="output_format",
                default_value="jpeg",
                tooltip="Output image format.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=["jpeg", "png"])},
            )
            ParameterInt(
                name="safety_tolerance",
                default_value=2,
                tooltip="Moderation level, from 0 (strict) to 6 (permissive).",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                min_val=0,
                max_val=6,
                slider=True,
            )
            ParameterBool(
                name="prompt_upsampling",
                default_value=False,
                tooltip="Let the model extend the prompt. FLUX.2 klein models do not have this feature.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )
            ParameterInt(
                name="steps",
                default_value=50,
                tooltip="Inference steps, up to 50.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                min_val=1,
                max_val=50,
                slider=True,
            )
            ParameterFloat(
                name="guidance",
                default_value=4.5,
                tooltip="How closely the image follows the prompt, from 1.5 to 10.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                min_val=1.5,
                max_val=10.0,
                slider=True,
                step=0.1,
            )
        self.add_node_element(generation_settings_group)

    def _sync_model(self, model: str) -> None:
        self._show(["steps", "guidance"], visible=model == FLEX_MODEL)
        self._show(["prompt_upsampling"], visible=not model.startswith(KLEIN_MODEL_PREFIX))

    def after_value_set(self, parameter: Parameter, value: Any) -> None:
        self._seed_parameter.after_value_set(parameter, value)
        return super().after_value_set(parameter, value)

    def _input_image_limit(self, model: str) -> int:
        return MODEL_INPUT_LIMITS.get(model, self.MAX_INPUT_IMAGES)

    def _before_generation(self) -> None:
        self._seed_parameter.preprocess()

    async def _build_request(self, model: str, prompt: str, images: list[Any]) -> ApiyiRequest:
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "seed": self._seed_parameter.get_seed(),
            "output_format": self.get_parameter_value("output_format"),
            "safety_tolerance": self.get_parameter_value("safety_tolerance"),
        }
        if images:
            # An input image turns the same endpoint into edit mode. Edit mode has no `size` field.
            for index, reference in enumerate(await self._image_references(images), start=1):
                payload["input_image" if index == 1 else f"input_image_{index}"] = reference
            aspect_ratio = self.get_parameter_value("aspect_ratio")
            if aspect_ratio != AUTO_RATIO:
                payload["aspect_ratio"] = aspect_ratio
        else:
            payload["size"] = self.get_parameter_value("size")

        if self.get_parameter_value("prompt_upsampling") and not model.startswith(KLEIN_MODEL_PREFIX):
            payload["prompt_upsampling"] = True
        if model == FLEX_MODEL:
            payload["steps"] = self.get_parameter_value("steps")
            payload["guidance"] = self.get_parameter_value("guidance")
        return ApiyiRequest(path=GENERATIONS_PATH, json=payload)
