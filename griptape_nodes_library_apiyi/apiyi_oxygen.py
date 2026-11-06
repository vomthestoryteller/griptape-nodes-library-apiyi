"""Oxygen video models on APIYI.

Only five fields work at the top level of the request. Every other setting goes into `input_reference`,
as a JSON string that APIYI calls the envelope. At the top level, APIYI drops these settings without an
error and still bills the task.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from griptape_nodes.exe_types.core_types import ParameterGroup, ParameterMode
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.traits.options import Options

from griptape_nodes_library_apiyi.apiyi_client import ApiyiRequest
from griptape_nodes_library_apiyi.apiyi_model_catalog import OXYGEN_MODELS
from griptape_nodes_library_apiyi.apiyi_video_node import VIDEOS_PATH, ApiyiVideoNode, ReferenceLimits

__all__ = ["ApiyiOxygen"]

MIN_SECONDS = 4
MAX_SECONDS = 15
# The two resolutions have the same price, so the higher one is the default.
RESOLUTIONS = ["768p", "480p"]
ASPECT_RATIOS = ["16:9", "9:16", "1:1"]
SQUARE = "1:1"
# `size` must always go in the request. It sets the resolution and the shape. A square has no `size`, so the
# envelope sets the shape, and it wins over `size`.
SIZES = {
    ("768p", "16:9"): "1792x1024",
    ("768p", "9:16"): "1024x1792",
    ("768p", SQUARE): "1792x1024",
    ("480p", "16:9"): "1280x720",
    ("480p", "9:16"): "720x1280",
    ("480p", SQUARE): "1280x720",
}
QUALITIES = ["high", "normal"]
PROMPT_ENHANCEMENTS = ["refine", "format", "none"]
REFERENCE_LIMITS = ReferenceLimits(images=9, videos=3, audio=3)


class ApiyiOxygen(ApiyiVideoNode):
    """Generate videos with Oxygen models through APIYI."""

    MODEL_CHOICES: ClassVar[list[str]] = OXYGEN_MODELS
    DEFAULT_MODEL: ClassVar[str] = "oxygen-1.0"
    DEFAULT_FILENAME: ClassVar[str] = "apiyi_oxygen.mp4"
    POLL_SECONDS: ClassVar[int] = 5
    FIRST_POLL_SECONDS: ClassVar[int] = 5
    TOKEN_HELP: ClassVar[str] = (
        "Oxygen needs a token in the default or svip group, with the billing mode 按量优先 "
        "(pay-as-you-go priority). Check the token at https://api.apiyi.com/token."
    )

    def _add_model_parameters(self) -> None:
        self._add_frame_input(
            "first_frame", "Optional image for the start of the video. It sets the shape of the video."
        )
        self._add_frame_input("last_frame", "Optional image for the end of the video. It needs 'First Frame'.")
        self._add_reference_inputs(REFERENCE_LIMITS)

        self.add_parameter(
            ParameterInt(
                name="seconds",
                default_value=5,
                tooltip=f"Video length, from {MIN_SECONDS} to {MAX_SECONDS} seconds. APIYI bills each second.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                min_val=MIN_SECONDS,
                max_val=MAX_SECONDS,
                slider=True,
            )
        )
        self.add_parameter(
            ParameterString(
                name="resolution",
                default_value=RESOLUTIONS[0],
                tooltip="Output resolution. The two values have the same price.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=RESOLUTIONS)},
            )
        )
        self.add_parameter(
            ParameterString(
                name="aspect_ratio",
                default_value=ASPECT_RATIOS[0],
                tooltip="Shape of the video. With 'First Frame', the video has the shape of that image.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=ASPECT_RATIOS)},
            )
        )

        with ParameterGroup(name="Generation Settings", ui_options={"collapsed": True}) as generation_settings_group:
            ParameterString(
                name="quality",
                default_value=QUALITIES[0],
                tooltip="Quality level. It does not change the price.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=QUALITIES)},
            )
            ParameterString(
                name="prompt_enhance",
                default_value=PROMPT_ENHANCEMENTS[0],
                tooltip="What the model does with the prompt: 'refine' extends it, 'format' tidies it, 'none' keeps it.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=PROMPT_ENHANCEMENTS)},
            )
        self.add_node_element(generation_settings_group)

    def _sync_model(self, model: str) -> None:
        """All Oxygen models have the same rules."""

    def _find_problems(self, model: str) -> list[Exception]:
        return self._frame_and_reference_problems(model, REFERENCE_LIMITS)

    def _video_link(self, task: dict[str, Any]) -> str | None:
        # `/content` can answer 400 just after a task completes, so the documents advise this link.
        return task.get("video_url")

    async def _build_request(self, model: str, prompt: str) -> ApiyiRequest:
        resolution = self.get_parameter_value("resolution")
        aspect_ratio = self.get_parameter_value("aspect_ratio")
        payload = {
            "model": model,
            "prompt": prompt,
            "seconds": str(self.get_parameter_value("seconds")),
            "size": SIZES[(resolution, aspect_ratio)],
        }
        envelope = await self._envelope(resolution, aspect_ratio)
        if envelope:
            # APIYI needs a string here. It refuses a JSON object with a 400.
            payload["input_reference"] = json.dumps(envelope)
        return ApiyiRequest(path=VIDEOS_PATH, json=payload)

    async def _envelope(self, resolution: str, aspect_ratio: str) -> dict[str, Any]:
        """The settings that do not work at the top level. Each key has a value that differs from the default."""
        envelope: dict[str, Any] = {}
        first_frame = self.get_parameter_value("first_frame")
        if first_frame:
            envelope["images"] = [await self._image_reference(first_frame, "'First Frame'")]
        last_frame = self.get_parameter_value("last_frame")
        if last_frame:
            envelope["last_image"] = await self._image_reference(last_frame, "'Last Frame'")

        images = self._images_in("reference_images")
        if images:
            envelope["reference_images"] = [
                await self._image_reference(value, f"'Reference Images' item {index}")
                for index, value in enumerate(images, start=1)
            ]
        videos = self._files_in("reference_videos", "Reference Videos")
        if videos:
            envelope["reference_videos"] = videos
        audio = self._files_in("reference_audio", "Reference Audio")
        if audio:
            envelope["reference_audios"] = audio

        if aspect_ratio == SQUARE and not first_frame:
            envelope["resolution"] = resolution
            envelope["aspect_ratio"] = aspect_ratio
        quality = self.get_parameter_value("quality")
        if quality != QUALITIES[0]:
            envelope["quality"] = quality
        prompt_enhance = self.get_parameter_value("prompt_enhance")
        if prompt_enhance != PROMPT_ENHANCEMENTS[0]:
            envelope["prompt_enhance"] = prompt_enhance
        return envelope
