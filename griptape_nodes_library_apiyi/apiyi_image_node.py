"""The base class of the image nodes. Each model family supplies its parameters and its request."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from griptape.artifacts.image_url_artifact import ImageUrlArtifact
from griptape_nodes.exe_types.core_types import NodeError, Parameter, ParameterList, ParameterMode
from griptape_nodes.exe_types.param_components.project_file_parameter import ProjectFileParameter
from griptape_nodes.exe_types.param_types.parameter_dict import ParameterDict
from griptape_nodes.exe_types.param_types.parameter_image import ParameterImage
from griptape_nodes.files.file import FileLoadError, FileWriteError

from griptape_nodes_library_apiyi.apiyi_client import ApiyiRequest, MultipartFile, download, send
from griptape_nodes_library_apiyi.apiyi_images import ImageData, decode_b64, without_image_data
from griptape_nodes_library_apiyi.apiyi_node import IMAGE_INPUT_TYPES, ApiyiNode, to_image_artifact

logger = logging.getLogger("griptape_nodes")

__all__ = ["GENERATIONS_PATH", "EDITS_PATH", "ApiyiImageNode"]

GENERATIONS_PATH = "/v1/images/generations"
EDITS_PATH = "/v1/images/edits"


class ApiyiImageNode(ApiyiNode, ABC):
    """Generate images through APIYI.

    Subclasses set the class values and implement:
    - _add_model_parameters(): Add the parameters of the model family.
    - _sync_model(): Match the dropdowns and the visible parameters to the selected model.
    - _build_request(): Build the request for the selected model.

    A subclass overrides _read_results() when the answer is not in the OpenAI Images shape.
    """

    MEDIA: ClassVar[str] = "image"
    # APIYI image calls are synchronous and a disconnect is still billed, so the default covers the
    # slowest documented case: 600 seconds for a 4K Nano Banana Pro image.
    DEFAULT_TIMEOUT_SECONDS: ClassVar[int] = 600
    MIN_TIMEOUT_SECONDS: ClassVar[int] = 30
    MAX_INPUT_IMAGES: ClassVar[int]
    DEFAULT_FILENAME: ClassVar[str]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self._add_model_input()
        self._add_prompt_input("What to generate, or how to change the input images.")
        self.add_parameter(
            ParameterList(
                name="input_images",
                input_types=IMAGE_INPUT_TYPES,
                default_value=[],
                tooltip=(
                    f"Optional reference images, up to {self.MAX_INPUT_IMAGES}. "
                    "The prompt can name them by order: image 1, image 2."
                ),
                allowed_modes={ParameterMode.INPUT},
                max_items=self.MAX_INPUT_IMAGES,
                item_converters=[to_image_artifact],
                ui_options={"display_name": "Input Images", "expander": True, "hide_property": True},
            )
        )

        self._add_model_parameters()

        self._add_request_settings(
            timeout_tooltip=(
                "How many seconds to wait for the image. APIYI bills a request even if the node stops "
                "waiting, so keep this value high."
            )
        )

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
    async def _build_request(self, model: str, prompt: str, images: list[Any]) -> ApiyiRequest:
        """Build the request for `model`. `images` holds the connected input images."""

    def _input_image_limit(self, model: str) -> int:  # noqa: ARG002
        return self.MAX_INPUT_IMAGES

    def _validate_settings(self, model: str, image_count: int) -> list[Exception]:  # noqa: ARG002
        """Problems that only this model family can see."""
        return []

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

    def _find_problems(self, model: str) -> list[Exception]:
        problems: list[Exception] = []
        image_count = len(self._input_images())
        limit = self._input_image_limit(model)
        if image_count > limit:
            problems.append(
                NodeError(
                    f"{image_count} images are connected to 'Input Images', and {model} accepts {limit}. "
                    "Remove some images."
                )
            )
        problems.extend(self._validate_settings(model, image_count))
        return problems

    # ---- Generation ----

    async def _generate(self) -> str:
        key = self._key()
        self._before_generation()
        model = self._model()
        request = await self._build_request(model, self._prompt(), self._input_images())
        body = await send(request, key=key, timeout_seconds=self._timeout_seconds())
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

    def _input_images(self) -> list[Any]:
        return self._images_in("input_images")

    async def _prepare(self, value: Any, index: int, *, compress: bool | None = None) -> ImageData:
        """Read input image `index` and make it ready to upload."""
        return await self._prepare_image(value, f"'Input Images' item {index}", compress=compress)

    async def _image_references(self, images: list[Any]) -> list[str]:
        """Each input image as a URL or data URI."""
        return [
            await self._image_reference(value, f"'Input Images' item {index}")
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
