"""Seedance video models on APIYI.

These models have their own task endpoint. A token needs the SeeDance2 group, and the content of the
request sets the mode: text, frames, or references. The modes cannot mix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from griptape_nodes.exe_types.core_types import NodeError, Parameter, ParameterGroup, ParameterMode
from griptape_nodes.exe_types.param_components.seed_parameter import SeedParameter
from griptape_nodes.exe_types.param_types.parameter_bool import ParameterBool
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.traits.options import Options

from griptape_nodes_library_apiyi.apiyi_client import ApiyiRequest
from griptape_nodes_library_apiyi.apiyi_model_catalog import SEEDANCE_MODELS
from griptape_nodes_library_apiyi.apiyi_video_node import ApiyiVideoNode, ReferenceLimits

__all__ = ["ApiyiSeedance"]

TASKS_PATH = "/seedance/api/v3/contents/generations/tasks"
# The gateway marks the answer as gzip, but the body is plain. Without this header, the client fails to decode it.
PLAIN_ANSWER_HEADERS = {"Accept-Encoding": "identity"}

MIN_DURATION = 4
# The model chooses the length, and APIYI bills what the model makes.
AUTO_DURATION = -1
RESOLUTIONS = ("1080p", "720p", "480p")
RESOLUTIONS_WITHOUT_1080P = ("720p", "480p")
RATIOS = ["adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"]
ADAPTIVE_RATIO = RATIOS[0]
OUTPUT_FORMATS = ["mp4", "mov"]
AUTO_TASK = "auto"
EDIT_TASK = "edit"
REFERENCE_TASKS = [AUTO_TASK, EDIT_TASK, "extend"]


@dataclass(frozen=True)
class SeedanceSpec:
    resolutions: tuple[str, ...]
    max_duration: int
    reference_limits: ReferenceLimits
    # Seedance 2.5 only: audio as the only reference, `output_format`, `omni_reference_task_type`, and
    # the rule that a task with frames, or an edit or extend task, needs the adaptive ratio.
    newest: bool


LIMITS_2_0 = ReferenceLimits(images=9, videos=3, audio=3)
LIMITS_2_5 = ReferenceLimits(images=30, videos=10, audio=10)
MODEL_SPECS = {
    "doubao-seedance-2-5-260628": SeedanceSpec(RESOLUTIONS, 30, LIMITS_2_5, newest=True),
    "doubao-seedance-2-0-260128": SeedanceSpec(RESOLUTIONS, 15, LIMITS_2_0, newest=False),
    "doubao-seedance-2-0-fast-260128": SeedanceSpec(RESOLUTIONS_WITHOUT_1080P, 15, LIMITS_2_0, newest=False),
    "doubao-seedance-2-0-mini-260615": SeedanceSpec(RESOLUTIONS_WITHOUT_1080P, 15, LIMITS_2_0, newest=False),
}
# The least expensive model. One video can cost more than one USD on the others.
DEFAULT_MODEL = "doubao-seedance-2-0-mini-260615"


def _spec(model: str) -> SeedanceSpec:
    return MODEL_SPECS.get(model, MODEL_SPECS[DEFAULT_MODEL])


class ApiyiSeedance(ApiyiVideoNode):
    """Generate videos with Seedance models through APIYI."""

    MODEL_CHOICES: ClassVar[list[str]] = SEEDANCE_MODELS
    DEFAULT_MODEL: ClassVar[str] = DEFAULT_MODEL
    DEFAULT_FILENAME: ClassVar[str] = "apiyi_seedance.mp4"
    GROUP_KEY_NAME: ClassVar[str | None] = "APIYI_SEEDANCE_API_KEY"
    TASKS_PATH: ClassVar[str] = TASKS_PATH
    TASK_HEADERS: ClassVar[dict[str, str]] = PLAIN_ANSWER_HEADERS
    # The APIYI documents: the first read after 20 to 30 seconds, then one read each 10 to 20 seconds.
    FIRST_POLL_SECONDS: ClassVar[int] = 25
    POLL_SECONDS: ClassVar[int] = 15
    DONE_STATUSES: ClassVar[frozenset[str]] = frozenset({"succeeded"})
    FAILED_STATUSES: ClassVar[frozenset[str]] = frozenset({"failed", "expired"})
    TOKEN_HELP: ClassVar[str] = (
        "Seedance needs a token in the SeeDance2 group, with the billing mode 按量优先 (pay-as-you-go priority) "
        "or 按量计费 (pay-as-you-go). Put the token in APIYI_SEEDANCE_API_KEY."
    )

    def _add_model_parameters(self) -> None:
        self._add_frame_input("first_frame", "Optional image for the start of the video.")
        self._add_frame_input("last_frame", "Optional image for the end of the video. It needs 'First Frame'.")
        self._add_reference_inputs(LIMITS_2_5)

        spec = _spec(self.DEFAULT_MODEL)
        self.add_parameter(
            ParameterString(
                name="resolution",
                default_value=spec.resolutions[0],
                tooltip="Output resolution. 1080p needs Seedance 2.5 or the 2.0 standard model.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=list(spec.resolutions))},
            )
        )
        self.add_parameter(
            ParameterString(
                name="ratio",
                default_value=ADAPTIVE_RATIO,
                tooltip="Shape of the video. Seedance 2.5 needs 'adaptive' with a frame, and for an edit or extend task.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=RATIOS)},
            )
        )
        self.add_parameter(
            ParameterInt(
                name="duration",
                default_value=5,
                tooltip=(
                    f"Video length in seconds: from {MIN_DURATION} to 15, or to 30 on Seedance 2.5. "
                    f"Use {AUTO_DURATION} to let the model choose. APIYI then bills what the model makes."
                ),
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )
        )
        self.add_parameter(
            ParameterBool(
                name="generate_audio",
                default_value=True,
                tooltip="Make sound for the video: voices, effects, and music.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )
        )

        self._seed_parameter = SeedParameter(self)
        with ParameterGroup(name="Generation Settings", ui_options={"collapsed": True}) as generation_settings_group:
            self._seed_parameter.add_input_parameters(inside_param_group=True)
            ParameterBool(
                name="watermark",
                default_value=False,
                tooltip="Add an 'AI generated' mark to the video.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )
            ParameterString(
                name="output_format",
                default_value=OUTPUT_FORMATS[0],
                tooltip="File format of the video. Some players cannot open mov.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=OUTPUT_FORMATS)},
            )
            ParameterString(
                name="reference_task",
                default_value=AUTO_TASK,
                tooltip=(
                    "What to do with the reference videos. 'edit' changes the video, and 'extend' makes it longer. "
                    "The prompt must also say this, for example: replace the car in video 1."
                ),
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=REFERENCE_TASKS)},
            )
        self.add_node_element(generation_settings_group)

    def _sync_model(self, model: str) -> None:
        spec = _spec(model)
        self._offer_choices("resolution", spec.resolutions)
        self._show(["output_format", "reference_task"], visible=spec.newest)

    def after_value_set(self, parameter: Parameter, value: Any) -> None:
        self._seed_parameter.after_value_set(parameter, value)
        return super().after_value_set(parameter, value)

    def _before_generation(self) -> None:
        self._seed_parameter.preprocess()

    def _find_problems(self, model: str) -> list[Exception]:
        spec = _spec(model)
        problems = self._frame_and_reference_problems(model, spec.reference_limits)

        duration = self.get_parameter_value("duration")
        if duration != AUTO_DURATION and not MIN_DURATION <= duration <= spec.max_duration:
            problems.append(
                NodeError(
                    f"'Duration' is {duration}. {model} needs {MIN_DURATION} to {spec.max_duration}, or {AUTO_DURATION}."
                )
            )

        images = self.get_parameter_list_value("reference_images")
        videos = self.get_parameter_list_value("reference_videos")
        audio = self.get_parameter_list_value("reference_audio")
        if audio and not (images or videos) and not spec.newest:
            problems.append(NodeError(f"{model} needs a reference image or video with 'Reference Audio'."))
        if spec.newest:
            problems.extend(self._newest_model_problems(model, duration))
        return problems

    def _newest_model_problems(self, model: str, duration: int) -> list[Exception]:
        problems: list[Exception] = []
        ratio = self.get_parameter_value("ratio")
        task = self.get_parameter_value("reference_task")
        has_frame = self.get_parameter_value("first_frame") or self.get_parameter_value("last_frame")
        if (has_frame or task != AUTO_TASK) and ratio != ADAPTIVE_RATIO:
            problems.append(
                NodeError(f"{model} needs the ratio 'adaptive' with a frame, or for an edit or extend task.")
            )
        if task != AUTO_TASK and not self.get_parameter_list_value("reference_videos"):
            problems.append(NodeError(f"The reference task '{task}' needs a video in 'Reference Videos'."))
        if task == EDIT_TASK and duration != AUTO_DURATION:
            problems.append(NodeError(f"The reference task '{task}' needs the duration {AUTO_DURATION}."))
        return problems

    def _video_link(self, task: dict[str, Any]) -> str | None:
        content = task.get("content")
        link = content.get("video_url") if isinstance(content, dict) else None
        if not link:
            raise NodeError("The task succeeded, but its answer has no link to the video.", response=task)
        return link

    async def _build_request(self, model: str, prompt: str) -> ApiyiRequest:
        body: dict[str, Any] = {
            "model": model,
            "content": [{"type": "text", "text": prompt}, *await self._media_content()],
            "resolution": self.get_parameter_value("resolution"),
            "ratio": self.get_parameter_value("ratio"),
            # Always sent: without it, 2.5 chooses the length, and the cost changes with that length.
            "duration": self.get_parameter_value("duration"),
            "generate_audio": bool(self.get_parameter_value("generate_audio")),
            "watermark": bool(self.get_parameter_value("watermark")),
            "seed": self._seed_parameter.get_seed(),
        }
        if _spec(model).newest:
            body["output_format"] = self.get_parameter_value("output_format")
            task = self.get_parameter_value("reference_task")
            if task != AUTO_TASK:
                body["omni_reference_task_type"] = task
        return ApiyiRequest(path=TASKS_PATH, json=body, headers=PLAIN_ANSWER_HEADERS)

    async def _media_content(self) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = []
        for name, label, role in (
            ("first_frame", "'First Frame'", "first_frame"),
            ("last_frame", "'Last Frame'", "last_frame"),
        ):
            value = self.get_parameter_value(name)
            if value:
                content.append(_image_item(await self._image_reference(value, label), role))
        for index, value in enumerate(self._images_in("reference_images"), start=1):
            reference = await self._image_reference(value, f"'Reference Images' item {index}")
            content.append(_image_item(reference, "reference_image"))
        for link in self._files_in("reference_videos", "Reference Videos"):
            content.append({"type": "video_url", "video_url": {"url": link}, "role": "reference_video"})
        for link in self._files_in("reference_audio", "Reference Audio"):
            content.append({"type": "audio_url", "audio_url": {"url": link}, "role": "reference_audio"})
        return content


def _image_item(url: str, role: str) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": url}, "role": role}
