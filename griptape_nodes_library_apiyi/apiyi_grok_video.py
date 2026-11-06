"""Grok Imagine Video models on APIYI.

These models are not in the default group. A token needs the GrokOfficial group, and APIYI opens that
group on request. A completed task can have no video: xAI review blocks it, and APIYI still bills it.
"""

from __future__ import annotations

from typing import Any, ClassVar

from griptape_nodes.exe_types.core_types import NodeError, ParameterMode
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.traits.options import Options

from griptape_nodes_library_apiyi.apiyi_client import ApiyiRequest
from griptape_nodes_library_apiyi.apiyi_images import without_image_data
from griptape_nodes_library_apiyi.apiyi_video_node import VIDEOS_PATH, ApiyiVideoNode

__all__ = ["ApiyiGrokVideo"]

# The APIYI model registry does not list these models, so they are not in the model catalog.
MODEL_CHOICES = ["grok-imagine-video-1.5-lite", "grok-imagine-video-1.5", "grok-imagine-video"]
MODEL_WITHOUT_FULL_HD = "grok-imagine-video"
MIN_SECONDS = 1
MAX_SECONDS = 15
STANDARD_SIZES = ["1280x720", "720x1280", "854x480", "480x854"]
FULL_HD_SIZES = ["1920x1080", "1080x1920"]
MODERATION_BLOCKED = "moderation_blocked"


class ApiyiGrokVideo(ApiyiVideoNode):
    """Generate videos with Grok Imagine Video models through APIYI."""

    MODEL_CHOICES: ClassVar[list[str]] = MODEL_CHOICES
    DEFAULT_MODEL: ClassVar[str] = MODEL_CHOICES[0]
    DEFAULT_FILENAME: ClassVar[str] = "apiyi_grok_video.mp4"
    GROUP_KEY_NAME: ClassVar[str | None] = "APIYI_GROK_API_KEY"
    TOKEN_HELP: ClassVar[str] = (
        "Grok Imagine Video needs a token in the GrokOfficial group, and APIYI opens that group on request. "
        "The billing mode must not be 按次计费 (per call). Put the token in APIYI_GROK_API_KEY. "
        "A new attempt does not help."
    )

    def _add_model_parameters(self) -> None:
        self._add_frame_input(
            "first_frame", "Optional image for the start of the video. It sets the shape of the video."
        )
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
                name="size",
                default_value=STANDARD_SIZES[0],
                tooltip=(
                    "Output size. The short edge sets the resolution, and the shape moves to the closest "
                    "of 1:1, 16:9, 9:16, 4:3, 3:4, 3:2, and 2:3. You can also type WIDTHxHEIGHT."
                ),
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=[*STANDARD_SIZES, *FULL_HD_SIZES], allow_custom=True)},
            )
        )

    def _sync_model(self, model: str) -> None:
        full_hd = model != MODEL_WITHOUT_FULL_HD
        self._offer_choices("size", [*STANDARD_SIZES, *FULL_HD_SIZES] if full_hd else STANDARD_SIZES)

    def _find_problems(self, model: str) -> list[Exception]:
        size = self.get_parameter_value("size")
        if model == MODEL_WITHOUT_FULL_HD and size in FULL_HD_SIZES:
            return [NodeError(f"{model} has no 1080p output. 'Size' is {size}. Choose a smaller size.")]
        return []

    def _check_result(self, task_id: str, task: dict[str, Any]) -> None:
        error = task.get("error")
        if isinstance(error, dict) and error.get("code") == MODERATION_BLOCKED:
            raise NodeError(
                "xAI review blocked this video, so there is no video. APIYI billed the task. "
                "Change the prompt or the image, then run the node again.",
                fields={"generation_id": task_id, "error_code": MODERATION_BLOCKED},
                response=without_image_data(task),
            )

    async def _build_request(self, model: str, prompt: str) -> ApiyiRequest:
        payload = {
            "model": model,
            "prompt": prompt,
            "seconds": str(self.get_parameter_value("seconds")),
            "size": self.get_parameter_value("size"),
        }
        first_frame = self.get_parameter_value("first_frame")
        if first_frame:
            payload["input_reference"] = await self._image_reference(first_frame, "'First Frame'")
        return ApiyiRequest(path=VIDEOS_PATH, json=payload)
