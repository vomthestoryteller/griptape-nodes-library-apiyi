"""What the image nodes and the video nodes share: the first inputs, the key, and the input images."""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, ClassVar

from griptape.artifacts import ImageArtifact
from griptape.artifacts.image_url_artifact import ImageUrlArtifact
from griptape_nodes.exe_types.core_types import NodeError, Parameter, ParameterGroup, ParameterMode
from griptape_nodes.exe_types.node_types import SuccessFailureNode
from griptape_nodes.exe_types.param_types.parameter_bool import ParameterBool
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.traits.options import Options
from griptape_nodes.utils.artifact_normalization import normalize_artifact_input, normalize_artifact_list

from griptape_nodes_library_apiyi.apiyi_client import ApiKey, find_key, missing_key_error
from griptape_nodes_library_apiyi.apiyi_images import ImageData, prepare_image, public_url, read_image

__all__ = ["IMAGE_INPUT_TYPES", "ApiyiNode", "to_image_artifact"]

IMAGE_INPUT_TYPES = [
    "ImageUrlArtifact",
    "ImageArtifact",
    "str",
    "list",
    "list[ImageArtifact]",
    "list[ImageUrlArtifact]",
]
TYPED_SIZE = re.compile(r"\d+x\d+")


def to_image_artifact(value: Any) -> Any:
    """Turn a path or a link in an image list into an image artifact. Each image list uses this converter."""
    return normalize_artifact_input(value, ImageUrlArtifact, accepted_types=(ImageArtifact,))


class ApiyiNode(SuccessFailureNode, ABC):
    """A node that generates media through APIYI.

    Subclasses set the class values, add their parameters, and implement:
    - _sync_model(): Match the dropdowns and the visible parameters to the selected model.
    - _generate(): Do the work, and return the text for the result details.
    - _clear_outputs(): Remove the outputs of the last run.
    """

    MODEL_CHOICES: ClassVar[list[str]]
    DEFAULT_MODEL: ClassVar[str]
    # What the node makes, for messages: "image" or "video".
    MEDIA: ClassVar[str]
    DEFAULT_TIMEOUT_SECONDS: ClassVar[int]
    MIN_TIMEOUT_SECONDS: ClassVar[int]
    MAX_TIMEOUT_SECONDS: ClassVar[int] = 3600
    # Pillow format names the endpoints of the family accept for input images.
    INPUT_FORMATS: ClassVar[frozenset[str]] = frozenset({"PNG", "JPEG", "WEBP"})
    # A secret for a token in a special group. The node uses APIYI_API_KEY when this secret is empty.
    GROUP_KEY_NAME: ClassVar[str | None] = None

    # ---- Parameters for the subclasses to add ----

    def _add_model_input(self) -> None:
        self.add_parameter(
            ParameterString(
                name="model",
                default_value=self.DEFAULT_MODEL,
                tooltip="The model to use. Choose one, or type a model ID from the APIYI model list.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=list(self.MODEL_CHOICES), allow_custom=True)},
            )
        )

    def _add_prompt_input(self, tooltip: str) -> None:
        self.add_parameter(
            ParameterString(
                name="prompt",
                default_value="",
                tooltip=tooltip,
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                multiline=True,
                placeholder_text=f"Describe the {self.MEDIA} you want...",
            )
        )

    def _add_request_settings(self, *, timeout_tooltip: str) -> None:
        with ParameterGroup(name="Request Settings", ui_options={"collapsed": True}) as request_settings_group:
            ParameterBool(
                name="compress_input_images",
                default_value=True,
                tooltip=(
                    "Make input images above 1.5 MB smaller before the upload: long edge up to 2048 pixels. "
                    "APIYI advises this. Turn it off to send the full resolution."
                ),
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )
            ParameterInt(
                name="timeout",
                default_value=self.DEFAULT_TIMEOUT_SECONDS,
                tooltip=timeout_tooltip,
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                min_val=self.MIN_TIMEOUT_SECONDS,
                max_val=self.MAX_TIMEOUT_SECONDS,
            )
        self.add_node_element(request_settings_group)

    # ---- What each subclass supplies ----

    @abstractmethod
    def _sync_model(self, model: str) -> None:
        """Match the dropdowns and the visible parameters to `model`."""

    @abstractmethod
    async def _generate(self) -> str:
        """Generate the media and set the outputs. Return the text for the result details."""

    @abstractmethod
    def _clear_outputs(self) -> None:
        """Remove the outputs of the last run."""

    def _find_problems(self, model: str) -> list[Exception]:  # noqa: ARG002
        """Problems that only this kind of node can see."""
        return []

    def _on_value_changed(self, name: str, value: Any) -> None:
        """Keep the node in step with a value. This runs after an edit and during a workflow load."""
        if name == "model" and isinstance(value, str):
            self._sync_model(value)

    def _before_generation(self) -> None:  # noqa: B027
        """Work to do before the request is built."""

    # ---- Node hooks ----

    def set_parameter_value(self, param_name: str, value: Any, **kwargs: Any) -> None:
        super().set_parameter_value(param_name, value, **kwargs)
        # A workflow load skips after_value_set, but the dropdowns must still match the saved model.
        if kwargs.get("initial_setup", False):
            self._on_value_changed(param_name, self.get_parameter_value(param_name))

    def after_value_set(self, parameter: Parameter, value: Any) -> None:
        self._on_value_changed(parameter.name, value)
        return super().after_value_set(parameter, value)

    def validate_before_workflow_run(self) -> list[Exception] | None:
        if find_key(self.GROUP_KEY_NAME) is None:
            return [missing_key_error()]
        return super().validate_before_workflow_run()

    def validate_before_node_run(self) -> list[Exception] | None:
        # Nothing can run without a key, so report it first and alone.
        if find_key(self.GROUP_KEY_NAME) is None:
            return [missing_key_error()]

        exceptions = super().validate_before_node_run() or []
        if not self._prompt():
            exceptions.append(NodeError(f"'Prompt' is empty. Describe the {self.MEDIA} you want."))

        model = self._model()
        if not model:
            exceptions.append(NodeError("'Model' is empty. Choose a model."))
        exceptions.extend(self._find_problems(model))
        return exceptions or None

    async def aprocess(self) -> None:
        self._clear_execution_status()
        self._clear_outputs()
        try:
            details = await self._generate()
        except NodeError as error:
            self._set_status_results(was_successful=False, result_details=str(error))
            self._handle_failure_exception(error)
            return
        self._set_status_results(was_successful=True, result_details=details)

    # ---- Helpers for the subclasses ----

    def _key(self) -> ApiKey:
        key = find_key(self.GROUP_KEY_NAME)
        if key is None:
            raise missing_key_error()
        return key

    def _model(self) -> str:
        return (self.get_parameter_value("model") or "").strip()

    def _prompt(self) -> str:
        return (self.get_parameter_value("prompt") or "").strip()

    def _images_in(self, name: str) -> list[Any]:
        images = self.get_parameter_list_value(name) or []
        return normalize_artifact_list(images, ImageUrlArtifact, accepted_types=(ImageArtifact,))

    def _timeout_seconds(self) -> int:
        value = self.get_parameter_value("timeout") or self.DEFAULT_TIMEOUT_SECONDS
        return max(self.MIN_TIMEOUT_SECONDS, int(value))

    def _show(self, names: list[str], *, visible: bool) -> None:
        if visible:
            self.show_parameter_by_name(names)
        else:
            self.hide_parameter_by_name(names)

    def _offer_choices(self, name: str, choices: Sequence[str]) -> None:
        """Limit a dropdown to `choices`. The current value stays if it is valid or a typed WIDTHxHEIGHT."""
        parameter = self.get_parameter_by_name(name)
        if parameter is None:
            msg = f"Parameter '{name}' not found."
            raise ValueError(msg)
        current = self.get_parameter_value(name)
        offered = list(choices)
        # A preset of another model, such as "3K", must not survive: this model refuses it with a 400.
        if isinstance(current, str) and TYPED_SIZE.fullmatch(current) and current not in offered:
            offered.append(current)
        self._update_option_choices(name, offered, current if current in offered else offered[0])

    async def _prepare_image(self, value: Any, label: str, *, compress: bool | None = None) -> ImageData:
        """Read a connected image and make it ready to upload. `label` names the image in an error."""
        content = await read_image(value, label)
        if compress is None:
            compress = bool(self.get_parameter_value("compress_input_images"))
        # Pillow work is CPU-bound, so it must not block the event loop.
        return await asyncio.to_thread(
            prepare_image, content, compress=compress, formats=self.INPUT_FORMATS, label=label
        )

    async def _image_reference(self, value: Any, label: str) -> str:
        """A connected image as a URL or data URI. APIYI fetches a public URL by itself, which is faster."""
        return public_url(value) or (await self._prepare_image(value, label)).data_uri
