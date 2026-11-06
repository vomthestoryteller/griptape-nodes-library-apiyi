"""Veo 3.1 video models on APIYI.

A request with no image is JSON, and the settings go in `metadata`. A request with an image must be
multipart, and the settings are plain form fields. APIYI bills each call, for the same price at each
length and resolution, and only a completed task is billed.
"""

from __future__ import annotations

from typing import Any, ClassVar

from griptape_nodes.exe_types.core_types import NodeError, Parameter, ParameterGroup, ParameterMode
from griptape_nodes.exe_types.param_components.seed_parameter import SeedParameter
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.traits.options import Options

from griptape_nodes_library_apiyi.apiyi_client import ApiyiRequest
from griptape_nodes_library_apiyi.apiyi_video_node import VIDEOS_PATH, ApiyiVideoNode

__all__ = ["ApiyiVeo"]

# The APIYI model registry does not list these models, so they are not in the model catalog.
MODEL_CHOICES = ["veo-3.1-fast-generate-preview", "veo-3.1-generate-preview"]
RESOLUTIONS = ["720p", "1080p", "4k"]
ASPECT_RATIOS = ["16:9", "9:16"]
SIZES = {
    ("720p", "16:9"): "1280x720",
    ("720p", "9:16"): "720x1280",
    ("1080p", "16:9"): "1920x1080",
    ("1080p", "9:16"): "1080x1920",
    ("4k", "16:9"): "3840x2160",
    ("4k", "9:16"): "2160x3840",
}
# Strings: the gateway refuses a number here with `parse_request_failed`.
ALL_SECONDS = ["8", "6", "4"]
# 1080p and 4k are available only at this length.
HIGH_RESOLUTION_SECONDS = ["8"]
BASE_RESOLUTION = RESOLUTIONS[0]


def _seconds_for(resolution: Any) -> list[str]:
    return ALL_SECONDS if resolution == BASE_RESOLUTION else HIGH_RESOLUTION_SECONDS


class ApiyiVeo(ApiyiVideoNode):
    """Generate videos with Veo 3.1 models through APIYI."""

    MODEL_CHOICES: ClassVar[list[str]] = MODEL_CHOICES
    DEFAULT_MODEL: ClassVar[str] = MODEL_CHOICES[0]
    DEFAULT_FILENAME: ClassVar[str] = "apiyi_veo.mp4"
    TOKEN_HELP: ClassVar[str] = (
        "Veo needs a token in the Default group, with the billing mode 按次计费 (per call) or 按量优先 "
        "(pay-as-you-go priority). Check the token at https://api.apiyi.com/token."
    )

    def _add_model_parameters(self) -> None:
        self._add_frame_input("first_frame", "Optional image for the start of the video. JPEG, PNG, or WEBP.")
        self.add_parameter(
            ParameterString(
                name="negative_prompt",
                default_value="",
                tooltip="What the video must not show. This applies when 'First Frame' is empty.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                multiline=True,
            )
        )
        self.add_parameter(
            ParameterString(
                name="resolution",
                default_value=BASE_RESOLUTION,
                tooltip="Output resolution. 1080p and 4k need a length of 8 seconds. 4k takes 4 to 6 times longer.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=RESOLUTIONS)},
            )
        )
        self.add_parameter(
            ParameterString(
                name="aspect_ratio",
                default_value=ASPECT_RATIOS[0],
                tooltip="Shape of the video.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=ASPECT_RATIOS)},
            )
        )
        self.add_parameter(
            ParameterString(
                name="seconds",
                default_value=ALL_SECONDS[0],
                tooltip="Video length. The price is the same for each length.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=list(ALL_SECONDS))},
            )
        )

        self._seed_parameter = SeedParameter(self)
        with ParameterGroup(name="Generation Settings", ui_options={"collapsed": True}) as generation_settings_group:
            self._seed_parameter.add_input_parameters(inside_param_group=True)
        self.add_node_element(generation_settings_group)

    def _sync_model(self, model: str) -> None:
        """Both Veo models have the same rules."""

    def _on_value_changed(self, name: str, value: Any) -> None:
        super()._on_value_changed(name, value)
        if name == "resolution":
            self._offer_choices("seconds", _seconds_for(value))

    def after_value_set(self, parameter: Parameter, value: Any) -> None:
        self._seed_parameter.after_value_set(parameter, value)
        return super().after_value_set(parameter, value)

    def _find_problems(self, model: str) -> list[Exception]:  # noqa: ARG002
        resolution = self.get_parameter_value("resolution")
        seconds = self.get_parameter_value("seconds")
        if seconds not in _seconds_for(resolution):
            return [NodeError(f"'Resolution' {resolution} needs a length of 8 seconds. 'Seconds' is {seconds}.")]
        return []

    def _before_generation(self) -> None:
        self._seed_parameter.preprocess()

    async def _build_request(self, model: str, prompt: str) -> ApiyiRequest:
        resolution = self.get_parameter_value("resolution")
        aspect_ratio = self.get_parameter_value("aspect_ratio")
        seed = self._seed_parameter.get_seed()
        fields = {
            "model": model,
            "prompt": prompt,
            "seconds": self.get_parameter_value("seconds"),
            "size": SIZES[(resolution, aspect_ratio)],
        }

        first_frame = self.get_parameter_value("first_frame")
        if first_frame:
            image = await self._prepare_image(first_frame, "'First Frame'")
            form = {**fields, "resolution": resolution, "aspectRatio": aspect_ratio, "seed": str(seed)}
            files = [("input_reference", (image.filename(1), image.content, image.mime_type))]
            return ApiyiRequest(path=VIDEOS_PATH, form=form, files=files)

        metadata: dict[str, Any] = {"resolution": resolution, "aspectRatio": aspect_ratio, "seed": seed}
        negative_prompt = (self.get_parameter_value("negative_prompt") or "").strip()
        if negative_prompt:
            metadata["negativePrompt"] = negative_prompt
        return ApiyiRequest(path=VIDEOS_PATH, json={**fields, "metadata": metadata})
