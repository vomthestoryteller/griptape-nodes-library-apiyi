"""The base class of the video nodes. Each manufacturer supplies its parameters and its request.

A video is a task on APIYI. The node submits the task, reads its state until it ends, downloads the
video, and saves it. APIYI can bill a task even if the node stops waiting, so the node never submits a
task twice by itself. The task ID stays on the node, and the refresh button collects a video that a run
did not collect.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import re
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from griptape.artifacts.video_url_artifact import VideoUrlArtifact
from griptape_nodes.exe_types.core_types import NodeError, ParameterList, ParameterMode
from griptape_nodes.exe_types.param_components.project_file_parameter import ProjectFileParameter
from griptape_nodes.exe_types.param_types.parameter_button import ParameterButton
from griptape_nodes.exe_types.param_types.parameter_dict import ParameterDict
from griptape_nodes.exe_types.param_types.parameter_image import ParameterImage
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.exe_types.param_types.parameter_video import ParameterVideo
from griptape_nodes.files.file import FileLoadError, FileWriteError

from griptape_nodes_library_apiyi.apiyi_client import (
    ApiKey,
    ApiyiRequest,
    BriefFailure,
    download_video,
    find_key,
    gateway_url,
    missing_key_error,
    read_task,
    send,
)
from griptape_nodes_library_apiyi.apiyi_images import public_url, without_image_data
from griptape_nodes_library_apiyi.apiyi_node import IMAGE_INPUT_TYPES, ApiyiNode, to_image_artifact

logger = logging.getLogger("griptape_nodes")

__all__ = ["VIDEOS_PATH", "ApiyiVideoNode", "ReferenceLimits"]

VIDEOS_PATH = "/v1/videos"
REFRESH_LABEL = "Refresh / Retrieve Result"

HTTP_SERVICE_UNAVAILABLE = 503
# The gateway answers with these words when the token cannot reach the model. A new attempt cannot help.
NO_CHANNEL = re.compile(r"无可用渠道|没有可用渠道|no available channel", re.IGNORECASE)
# The APIYI documents advise a retry every 4 seconds, for about 30 seconds, after `/content` answers 400.
DOWNLOAD_ATTEMPTS = 8
DOWNLOAD_RETRY_SECONDS = 4


@dataclass(frozen=True)
class ReferenceLimits:
    """How many reference files of each kind a model accepts."""

    images: int
    videos: int
    audio: int


async def _pause(seconds: int) -> None:
    """Wait between two requests. The tests replace this function, so that they run at once."""
    await asyncio.sleep(seconds)


class ApiyiVideoNode(ApiyiNode, ABC):
    """Generate a video through an APIYI task.

    Subclasses set the class values and implement:
    - _add_model_parameters(): Add the parameters of the manufacturer.
    - _sync_model(): Match the dropdowns and the visible parameters to the selected model.
    - _build_request(): Build the request that submits the task.

    A subclass overrides _video_link() when the task holds a link to the video, and _check_result()
    when a completed task can have no video.
    """

    MEDIA: ClassVar[str] = "video"
    # The APIYI documents advise 15 minutes for a task. A task can take longer in busy hours.
    DEFAULT_TIMEOUT_SECONDS: ClassVar[int] = 900
    MIN_TIMEOUT_SECONDS: ClassVar[int] = 60
    DEFAULT_FILENAME: ClassVar[str]
    TASKS_PATH: ClassVar[str] = VIDEOS_PATH
    TASK_HEADERS: ClassVar[dict[str, str]] = {}
    POLL_SECONDS: ClassVar[int] = 10
    FIRST_POLL_SECONDS: ClassVar[int] = 10
    DONE_STATUSES: ClassVar[frozenset[str]] = frozenset({"completed"})
    FAILED_STATUSES: ClassVar[frozenset[str]] = frozenset({"failed"})
    # What the token needs for this manufacturer. A refused submit shows this text.
    TOKEN_HELP: ClassVar[str]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self._add_model_input()
        self._add_prompt_input("What the video shows, and how the camera moves.")
        self._add_model_parameters()
        self._add_request_settings(
            timeout_tooltip=(
                "How many seconds to wait for the video. A task continues on APIYI if the node stops "
                f"waiting. Then use '{REFRESH_LABEL}' to collect the video."
            )
        )

        self.add_parameter(
            ParameterDict(
                name="provider_response",
                tooltip="The last task state from APIYI.",
                allowed_modes={ParameterMode.OUTPUT},
                hide_property=True,
                hide=True,
            )
        )
        self.add_parameter(
            ParameterVideo(
                name="video",
                tooltip="The generated video.",
                allowed_modes={ParameterMode.OUTPUT, ParameterMode.PROPERTY},
                settable=False,
                ui_options={"pulse_on_run": True},
            )
        )

        self._output_file = ProjectFileParameter(node=self, name="output_file", default_filename=self.DEFAULT_FILENAME)
        self._output_file.add_parameter()

        self._create_status_parameters(
            result_details_tooltip="Details about the video generation result or any errors",
            result_details_placeholder="Generation status and details will appear here.",
            parameter_group_initially_collapsed=False,
        )

        self._sync_model(self.DEFAULT_MODEL)

    def _create_status_parameters(
        self,
        *,
        result_details_tooltip: str = "Details about the operation result",
        result_details_placeholder: str = "Details on the operation will be presented here.",
        parameter_group_initially_collapsed: bool = True,
    ) -> None:
        super()._create_status_parameters(
            result_details_tooltip=result_details_tooltip,
            result_details_placeholder=result_details_placeholder,
            parameter_group_initially_collapsed=parameter_group_initially_collapsed,
        )
        status_group = self.status_component.get_parameter_group()
        status_group.add_child(
            ParameterString(
                name="generation_id",
                default_value="",
                tooltip=f"The task ID on APIYI. It stays after a failure, so that '{REFRESH_LABEL}' can find the task.",
                allowed_modes={ParameterMode.OUTPUT},
                settable=False,
                placeholder_text="The task ID will appear here.",
            )
        )
        status_group.add_child(
            ParameterString(
                name="generation_status",
                default_value="",
                tooltip="The last known state of the task.",
                allowed_modes={ParameterMode.OUTPUT},
                settable=False,
                placeholder_text="The task state will appear here.",
            )
        )
        status_group.add_child(
            ParameterButton(
                name="generation_refresh",
                label=REFRESH_LABEL,
                icon="refresh-cw",
                variant="secondary",
                full_width=True,
                tooltip="Read the task again, and save the video if the task is complete. This starts no new task.",
                on_click=self._on_refresh_clicked,
            )
        )

    # ---- Parameters for the subclasses to add ----

    def _add_frame_input(self, name: str, tooltip: str) -> None:
        self.add_parameter(
            ParameterImage(name=name, tooltip=tooltip, allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY})
        )

    def _add_reference_inputs(self, limits: ReferenceLimits) -> None:
        """Add the reference lists. Each list is as long as the largest limit of the manufacturer."""
        lists = (
            ("reference_images", "Reference Images", IMAGE_INPUT_TYPES, limits.images, "images", [to_image_artifact]),
            ("reference_videos", "Reference Videos", ["VideoUrlArtifact", "str"], limits.videos, "videos", []),
            ("reference_audio", "Reference Audio", ["AudioUrlArtifact", "str"], limits.audio, "audio clips", []),
        )
        for name, display_name, input_types, max_items, noun, item_converters in lists:
            self.add_parameter(
                ParameterList(
                    name=name,
                    input_types=input_types,
                    default_value=[],
                    tooltip=(
                        f"Optional reference {noun}, up to {max_items}. The limit can depend on the model. "
                        "You cannot use these with 'First Frame' or 'Last Frame'."
                    ),
                    allowed_modes={ParameterMode.INPUT},
                    max_items=max_items,
                    item_converters=item_converters,
                    ui_options={"display_name": display_name, "expander": True, "hide_property": True},
                )
            )

    # ---- What each manufacturer supplies ----

    @abstractmethod
    def _add_model_parameters(self) -> None:
        """Add the parameters of this manufacturer."""

    @abstractmethod
    async def _build_request(self, model: str, prompt: str) -> ApiyiRequest:
        """Build the request that submits the task."""

    def _video_link(self, task: dict[str, Any]) -> str | None:  # noqa: ARG002
        """A public link to the video in a finished task. None means the `/content` path of the task."""
        return None

    def _check_result(self, task_id: str, task: dict[str, Any]) -> None:
        """Raise a NodeError if a completed task has no video."""

    # ---- Generation ----

    async def _generate(self) -> str:
        key = self._key()
        self._before_generation()
        model = self._model()
        request = await self._build_request(model, self._prompt())
        answer = await self._submit(request, key)
        self.parameter_output_values["provider_response"] = without_image_data(answer)

        task_id = _task_id(answer)
        self.parameter_output_values["generation_id"] = task_id
        self.parameter_output_values["generation_status"] = "submitted"

        task = await self._wait_for(task_id, key)
        name = await self._collect(task_id, task, key)
        return f"{model} generated a video. Saved as {name}."

    async def _submit(self, request: ApiyiRequest, key: ApiKey) -> dict[str, Any]:
        """Create the task. A person decides about a new attempt, because the gateway can bill a task."""
        try:
            return await send(request, key=key, timeout_seconds=self._timeout_seconds(), retry=False)
        except NodeError as error:
            if error.fields.get("status_code") == HTTP_SERVICE_UNAVAILABLE or NO_CHANNEL.search(str(error)):
                raise NodeError(
                    f"{error} {self.TOKEN_HELP}", fields=error.fields, response=error.response, links=error.links
                ) from error
            raise

    async def _wait_for(self, task_id: str, key: ApiKey) -> dict[str, Any]:
        """Read the task until it ends. A brief failure does not stop the wait: the task is still paid for."""
        timeout = self._timeout_seconds()
        await _pause(self.FIRST_POLL_SECONDS)
        waited = self.FIRST_POLL_SECONDS
        last_failure: BriefFailure | None = None
        while True:
            try:
                task = await read_task(self._task_path(task_id), key=key, headers=self.TASK_HEADERS)
            except BriefFailure as failure:
                logger.warning("%s could not read task %s: %s", self.name, task_id, failure)
                self.parameter_output_values["generation_status"] = "state not known"
                last_failure = failure
            else:
                last_failure = None
                self._show_state(task)
                if task.get("status") in self.DONE_STATUSES:
                    return task
                if task.get("status") in self.FAILED_STATUSES:
                    raise _task_failed(task_id, task)
            if waited >= timeout:
                raise _not_finished(task_id, timeout, last_failure)
            # The engine usually cancels the running task during the pause. This covers a cancel by flag only.
            if self.is_cancellation_requested:
                raise _stopped_waiting(task_id)
            await _pause(self.POLL_SECONDS)
            waited += self.POLL_SECONDS

    async def _collect(self, task_id: str, task: dict[str, Any], key: ApiKey) -> str:
        """Save the video of a finished task, and return the name of the file."""
        self.parameter_output_values["provider_response"] = without_image_data(task)
        self._check_result(task_id, task)
        content = await self._read_video(task_id, task, key)
        try:
            saved = await self._output_file.build_file().awrite_bytes(content)
        except (FileWriteError, FileLoadError, ValueError) as e:
            raise NodeError(
                f"The video was generated, but it could not be saved: {e}. "
                f"Check 'output_file', then click '{REFRESH_LABEL}'.",
                fields={"generation_id": task_id},
            ) from e
        self.parameter_output_values["video"] = VideoUrlArtifact(value=saved.location, name=saved.name)
        logger.info("%s saved %s", self.name, saved.name)
        return saved.name

    async def _read_video(self, task_id: str, task: dict[str, Any], key: ApiKey) -> bytes:
        link = self._video_link(task)
        url = link or f"{gateway_url()}{self._task_path(task_id)}/content"
        attempts = 0
        while True:
            attempts += 1
            try:
                return await download_video(url, key=None if link else key)
            except BriefFailure as failure:
                if attempts >= DOWNLOAD_ATTEMPTS:
                    raise NodeError(
                        f"The video was generated, but the download failed: {failure} "
                        f"Click '{REFRESH_LABEL}' to try again. Do not run the node again, "
                        "because that starts a new task.",
                        fields={**failure.fields, "generation_id": task_id},
                    ) from failure
            await _pause(DOWNLOAD_RETRY_SECONDS)

    def _task_path(self, task_id: str) -> str:
        return f"{self.TASKS_PATH}/{task_id}"

    def _show_state(self, task: dict[str, Any]) -> None:
        status = task.get("status") or "unknown"
        progress = task.get("progress")
        state = f"{status} ({progress}%)" if isinstance(progress, int | float) else status
        self.parameter_output_values["generation_status"] = state

    def _clear_outputs(self) -> None:
        self.parameter_output_values["video"] = None
        self.parameter_output_values["provider_response"] = None
        self.parameter_output_values["generation_id"] = ""
        self.parameter_output_values["generation_status"] = ""

    # ---- The refresh button ----

    def _on_refresh_clicked(self, _button: Any, _details: Any) -> None:
        """Collect a video without a run.

        The caller can have a running event loop on this thread, so the work needs its own thread. The
        copied context keeps the engine of this node on that thread.
        """
        worker = threading.Thread(
            target=contextvars.copy_context().run,
            args=(asyncio.run, self._refresh()),
            name=f"{self.name}-refresh",
            daemon=True,
        )
        worker.start()
        worker.join()

    async def _refresh(self) -> None:
        """Read the task one time, and save the video if the task is complete. A button cannot raise an error."""
        task_id = (self.parameter_output_values.get("generation_id") or "").strip()
        if not task_id:
            self._set_status_results(was_successful=False, result_details="This node has no task. Run the node first.")
            return
        key = find_key(self.GROUP_KEY_NAME)
        if key is None:
            self._set_status_results(was_successful=False, result_details=str(missing_key_error()))
            return

        try:
            task = await read_task(self._task_path(task_id), key=key, headers=self.TASK_HEADERS)
            self._show_state(task)
            status = task.get("status")
            if status in self.FAILED_STATUSES:
                raise _task_failed(task_id, task)
            if status not in self.DONE_STATUSES:
                self._set_status_results(
                    was_successful=False,
                    result_details=f"Task {task_id} has not finished (status: {status}). Try again later.",
                )
                return
            name = await self._collect(task_id, task, key)
        except NodeError as error:
            self._set_status_results(was_successful=False, result_details=str(error))
            return
        self._set_status_results(was_successful=True, result_details=f"Task {task_id} is complete. Saved as {name}.")

    # ---- Helpers for the subclasses ----

    def _files_in(self, name: str, label: str) -> list[str]:
        """The links in a list of videos or audio clips. APIYI fetches these files by itself."""
        links: list[str] = []
        for index, value in enumerate(self.get_parameter_list_value(name) or [], start=1):
            link = public_url(value)
            if link is None:
                raise NodeError(
                    f"'{label}' item {index} is not a public link. APIYI can only fetch public https links. "
                    "Upload the file, then connect its link."
                )
            links.append(link)
        return links

    def _frame_and_reference_problems(self, model: str, limits: ReferenceLimits) -> list[Exception]:
        """The rules for the frame inputs and the reference lists, which are the same for each manufacturer."""
        problems: list[Exception] = []
        first_frame = self.get_parameter_value("first_frame")
        last_frame = self.get_parameter_value("last_frame")
        counts = {
            "Reference Images": (len(self.get_parameter_list_value("reference_images") or []), limits.images),
            "Reference Videos": (len(self.get_parameter_list_value("reference_videos") or []), limits.videos),
            "Reference Audio": (len(self.get_parameter_list_value("reference_audio") or []), limits.audio),
        }
        if last_frame and not first_frame:
            problems.append(NodeError("'Last Frame' needs 'First Frame'. Connect a first frame."))
        if (first_frame or last_frame) and any(count for count, _ in counts.values()):
            problems.append(
                NodeError(
                    "Use frames or references, not both. Remove 'First Frame' and 'Last Frame', or the references."
                )
            )
        for label, (count, limit) in counts.items():
            if count > limit:
                problems.append(NodeError(f"{count} items are in '{label}', and {model} accepts {limit}."))
        return problems


def _task_id(answer: dict[str, Any]) -> str:
    task_id = answer.get("task_id") or answer.get("id")
    if not isinstance(task_id, str) or not task_id:
        error = answer.get("error")
        reason = error.get("message") if isinstance(error, dict) else error
        if isinstance(reason, str) and reason:
            raise NodeError(reason, response=answer)
        raise NodeError(
            "The answer from APIYI has no task ID, so the node cannot follow the task. "
            "Look at the APIYI logs before you run the node again, because APIYI can bill this request.",
            response=answer,
        )
    return task_id


def _task_failed(task_id: str, task: dict[str, Any]) -> NodeError:
    error = task.get("error")
    reason = error.get("message") if isinstance(error, dict) else error
    code = error.get("code") if isinstance(error, dict) else None
    if not isinstance(reason, str) or not reason:
        reason = f"The task ended with the status {task.get('status')}."
    fields = {"generation_id": task_id}
    if isinstance(code, str) and code:
        fields["error_code"] = code
    return NodeError(f"The video failed: {reason}", fields=fields, response=without_image_data(task))


def _stopped_waiting(task_id: str) -> NodeError:
    return NodeError(
        f"The node stopped waiting, but APIYI has no way to stop a task. The task can still run, and APIYI can "
        f"bill it. Click '{REFRESH_LABEL}' later to collect the video.",
        fields={"generation_id": task_id},
    )


def _not_finished(task_id: str, timeout: int, last_failure: BriefFailure | None) -> NodeError:
    reason = f" The last read of the task failed: {last_failure}" if last_failure else ""
    return NodeError(
        f"The video did not finish in {timeout} seconds.{reason} The task can still be running, and APIYI can "
        f"bill it. Click '{REFRESH_LABEL}' to collect the video. Do not run the node again, because that "
        "starts a new task.",
        fields={"generation_id": task_id},
    )
