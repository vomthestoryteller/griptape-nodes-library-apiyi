"""The base class of the image nodes. Each model family supplies its parameters and its request."""

from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, ClassVar

from griptape.artifacts import ImageArtifact
from griptape.artifacts.image_url_artifact import ImageUrlArtifact
from griptape_nodes.exe_types.core_types import NodeError, Parameter, ParameterGroup, ParameterList, ParameterMode
from griptape_nodes.exe_types.node_types import SuccessFailureNode
from griptape_nodes.exe_types.param_components.project_file_parameter import ProjectFileParameter
from griptape_nodes.exe_types.param_types.parameter_bool import ParameterBool
from griptape_nodes.exe_types.param_types.parameter_dict import ParameterDict
from griptape_nodes.exe_types.param_types.parameter_image import ParameterImage
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.files.file import FileLoadError, FileWriteError
from griptape_nodes.traits.options import Options
from griptape_nodes.utils.artifact_normalization import normalize_artifact_list

from griptape_nodes_library_apiyi.apiyi_client import (
    API_KEY_NAME,
    ApiyiRequest,
    MultipartFile,
    download,
    missing_key_error,
    read_secret,
    send,
)
from griptape_nodes_library_apiyi.apiyi_images import (
    ImageData,
    decode_b64,
    prepare_image,
    public_url,
    read_image,
    without_image_data,
)

logger = logging.getLogger("griptape_nodes")

__all__ = ["GENERATIONS_PATH", "EDITS_PATH", "ApiyiImageNode"]

GENERATIONS_PATH = "/v1/images/generations"
EDITS_PATH = "/v1/images/edits"

# APIYI image calls are synchronous and a disconnect is still billed, so the default covers the
# slowest documented case: 600 seconds for a 4K Nano Banana Pro image.
DEFAULT_TIMEOUT_SECONDS = 600
MIN_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 3600
TYPED_SIZE = re.compile(r"\d+x\d+")


class ApiyiImageNode(SuccessFailureNode, ABC):
    """Generate images through APIYI.

    Subclasses set the class values and implement:
    - _add_model_parameters(): Add the parameters of the model family.
    - _sync_model(): Match the dropdowns and the visible parameters to the selected model.
    - _build_request(): Build the request for the selected model.

    A subclass overrides _read_results() when the answer is not in the OpenAI Images shape.
    """

    MODEL_CHOICES: ClassVar[list[str]]
    DEFAULT_MODEL: ClassVar[str]
    MAX_INPUT_IMAGES: ClassVar[int]
    DEFAULT_FILENAME: ClassVar[str]
    # Pillow format names the endpoints of the family accept for input images.
    INPUT_FORMATS: ClassVar[frozenset[str]] = frozenset({"PNG", "JPEG", "WEBP"})

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.add_parameter(
            ParameterString(
                name="model",
                default_value=self.DEFAULT_MODEL,
                tooltip="The model to use. Choose one, or type a model ID from the APIYI model list.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=list(self.MODEL_CHOICES), allow_custom=True)},
            )
        )
        self.add_parameter(
            ParameterString(
                name="prompt",
                default_value="",
                tooltip="What to generate, or how to change the input images.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                multiline=True,
                placeholder_text="Describe the image you want...",
            )
        )
        self.add_parameter(
            ParameterList(
                name="input_images",
                input_types=[
                    "ImageUrlArtifact",
                    "ImageArtifact",
                    "str",
                    "list",
                    "list[ImageArtifact]",
                    "list[ImageUrlArtifact]",
                ],
                default_value=[],
                tooltip=(
                    f"Optional reference images, up to {self.MAX_INPUT_IMAGES}. "
                    "The prompt can name them by order: image 1, image 2."
                ),
                allowed_modes={ParameterMode.INPUT},
                max_items=self.MAX_INPUT_IMAGES,
                ui_options={"display_name": "Input Images", "expander": True, "hide_property": True},
            )
        )

        self._add_model_parameters()

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
                default_value=DEFAULT_TIMEOUT_SECONDS,
                tooltip=(
                    "How many seconds to wait for the image. APIYI bills a request even if the node stops "
                    "waiting, so keep this value high."
                ),
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                min_val=MIN_TIMEOUT_SECONDS,
                max_val=MAX_TIMEOUT_SECONDS,
            )
        self.add_node_element(request_settings_group)

        self.add_parameter(
            ParameterDict(
                name="provider_response",
                tooltip="The answer from APIYI, with image data removed.",
                allowed_modes={ParameterMode.OUTPUT},
                hide_property=True,
                hide=True,
            )
        )
        self.add_parameter(
            ParameterImage(
                name="image",
                tooltip="The generated image. It is the first image if the model returned more than one.",
                allowed_modes={ParameterMode.OUTPUT, ParameterMode.PROPERTY},
                settable=False,
                ui_options={"pulse_on_run": True},
            )
        )
        self.add_parameter(
            Parameter(
                name="all_images",
                output_type="list[ImageUrlArtifact]",
                type="list[ImageUrlArtifact]",
                tooltip="All generated images.",
                allowed_modes={ParameterMode.OUTPUT, ParameterMode.PROPERTY},
                settable=False,
                hide=True,
                ui_options={"pulse_on_run": True},
            )
        )

        self._output_file = ProjectFileParameter(node=self, name="output_file", default_filename=self.DEFAULT_FILENAME)
        self._output_file.add_parameter()

        self._create_status_parameters(
            result_details_tooltip="Details about the image generation result or any errors",
            result_details_placeholder="Generation status and details will appear here.",
            parameter_group_initially_collapsed=True,
        )

        self._sync_model(self.DEFAULT_MODEL)

    # ---- What each model family supplies ----

    @abstractmethod
    def _add_model_parameters(self) -> None:
        """Add the parameters of this model family."""

    @abstractmethod
    def _sync_model(self, model: str) -> None:
        """Match the dropdowns and the visible parameters to `model`."""

    @abstractmethod
    async def _build_request(self, model: str, prompt: str, images: list[Any]) -> ApiyiRequest:
        """Build the request for `model`. `images` holds the connected input images."""

    def _input_image_limit(self, model: str) -> int:  # noqa: ARG002
        return self.MAX_INPUT_IMAGES

    def _validate_settings(self, model: str, image_count: int) -> list[Exception]:  # noqa: ARG002
        """Problems that only this model family can see."""
        return []

    def _on_value_changed(self, name: str, value: Any) -> None:
        """Keep the node in step with a value. This runs after an edit and during a workflow load."""
        if name == "model" and isinstance(value, str):
            self._sync_model(value)

    def _before_generation(self) -> None:  # noqa: B027
        """Work to do before the request is built."""

    async def _read_results(self, body: dict[str, Any]) -> list[bytes]:
        """Read the images from an answer in the OpenAI Images shape: `data[].b64_json` or `data[].url`."""
        items = body.get("data")
        results: list[bytes] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("b64_json"):
                results.append(decode_b64(item["b64_json"]))
            elif item.get("url"):
                results.append(await download(item["url"]))
        if not results:
            error = body.get("error")
            reason = error.get("message") if isinstance(error, dict) else error
            message = reason if isinstance(reason, str) and reason else "The answer from APIYI has no image."
            raise NodeError(message, response=body)
        return results

    # ---- Node hooks ----

    def set_parameter_value(self, param_name: str, value: Any, **kwargs: Any) -> None:
        super().set_parameter_value(param_name, value, **kwargs)
        # A workflow load skips after_value_set, but the dropdowns must still match the saved model.
        if kwargs.get("initial_setup", False):
            self._on_value_changed(param_name, self.get_parameter_value(param_name))

    def after_value_set(self, parameter: Parameter, value: Any) -> None:
        if parameter.name == "input_images" and isinstance(value, list):
            updated = normalize_artifact_list(value, ImageUrlArtifact, accepted_types=(ImageArtifact,))
            if updated != value:
                self.set_parameter_value("input_images", updated)
        self._on_value_changed(parameter.name, value)
        return super().after_value_set(parameter, value)

    def validate_before_workflow_run(self) -> list[Exception] | None:
        if not read_secret(API_KEY_NAME):
            return [missing_key_error()]
        return super().validate_before_workflow_run()

    def validate_before_node_run(self) -> list[Exception] | None:
        # Nothing can run without a key, so report it first and alone.
        if not read_secret(API_KEY_NAME):
            return [missing_key_error()]

        exceptions = super().validate_before_node_run() or []
        if not self._prompt():
            exceptions.append(NodeError("'Prompt' is empty. Describe the image you want."))

        model = self._model()
        if not model:
            exceptions.append(NodeError("'Model' is empty. Choose a model."))

        image_count = len(self._input_images())
        limit = self._input_image_limit(model)
        if image_count > limit:
            exceptions.append(
                NodeError(
                    f"{image_count} images are connected to 'Input Images', and {model} accepts {limit}. "
                    "Remove some images."
                )
            )
        exceptions.extend(self._validate_settings(model, image_count))
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

    # ---- Generation ----

    async def _generate(self) -> str:
        api_key = read_secret(API_KEY_NAME)
        if not api_key:
            raise missing_key_error()

        self._before_generation()
        model = self._model()
        request = await self._build_request(model, self._prompt(), self._input_images())
        body = await send(request, api_key=api_key, timeout_seconds=self._timeout_seconds())
        self.parameter_output_values["provider_response"] = without_image_data(body)

        artifacts = await self._save(await self._read_results(body))
        self.parameter_output_values["image"] = artifacts[0]
        self.parameter_output_values["all_images"] = artifacts

        names = ", ".join(artifact.name for artifact in artifacts)
        return f"{model} generated {len(artifacts)} image(s). Saved as {names}."

    async def _save(self, results: list[bytes]) -> list[ImageUrlArtifact]:
        artifacts: list[ImageUrlArtifact] = []
        for index, content in enumerate(results, start=1):
            # Several images in one run need different names. The macro omits _index when it is not given.
            extra_vars = {"_index": index} if len(results) > 1 else {}
            try:
                saved = await self._output_file.build_file(**extra_vars).awrite_bytes(content)
            except (FileWriteError, FileLoadError, ValueError) as e:
                raise NodeError(
                    f"The image was generated, but it could not be saved: {e}. Check 'output_file' and run the node again."
                ) from e
            artifacts.append(ImageUrlArtifact(value=saved.location, name=saved.name))
            logger.info("%s saved %s", self.name, saved.name)
        return artifacts

    def _clear_outputs(self) -> None:
        self.parameter_output_values["image"] = None
        self.parameter_output_values["all_images"] = []
        self.parameter_output_values["provider_response"] = None

    # ---- Helpers for the subclasses ----

    def _model(self) -> str:
        return (self.get_parameter_value("model") or "").strip()

    def _prompt(self) -> str:
        return (self.get_parameter_value("prompt") or "").strip()

    def _input_images(self) -> list[Any]:
        images = self.get_parameter_list_value("input_images") or []
        return normalize_artifact_list(images, ImageUrlArtifact, accepted_types=(ImageArtifact,))

    def _timeout_seconds(self) -> int:
        return max(MIN_TIMEOUT_SECONDS, int(self.get_parameter_value("timeout") or DEFAULT_TIMEOUT_SECONDS))

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

    async def _prepare(self, value: Any, index: int, *, compress: bool | None = None) -> ImageData:
        """Read input image `index` and make it ready to upload."""
        label = f"'Input Images' item {index}"
        content = await read_image(value, label)
        if compress is None:
            compress = bool(self.get_parameter_value("compress_input_images"))
        # Pillow work is CPU-bound, so it must not block the event loop.
        return await asyncio.to_thread(
            prepare_image, content, compress=compress, formats=self.INPUT_FORMATS, label=label
        )

    async def _image_references(self, images: list[Any]) -> list[str]:
        """Each input image as a URL or data URI. APIYI fetches a public URL by itself, which is faster."""
        return [
            public_url(value) or (await self._prepare(value, index)).data_uri
            for index, value in enumerate(images, start=1)
        ]

    async def _multipart_images(
        self, images: list[Any], *, field_name: str, compress: bool | None = None
    ) -> list[MultipartFile]:
        """Each input image as a file in a repeated multipart field."""
        files: list[MultipartFile] = []
        for index, value in enumerate(images, start=1):
            image = await self._prepare(value, index, compress=compress)
            files.append((field_name, (image.filename(index), image.content, image.mime_type)))
        return files
