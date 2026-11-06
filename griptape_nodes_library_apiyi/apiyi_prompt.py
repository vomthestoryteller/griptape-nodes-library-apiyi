"""Connect the standard Agent node to the chat models on APIYI."""

from __future__ import annotations

from typing import Any

from griptape_nodes.exe_types.core_types import Parameter, ParameterMode
from griptape_nodes.exe_types.node_types import DataNode
from griptape_nodes.exe_types.param_types.parameter_float import ParameterFloat
from griptape_nodes.exe_types.param_types.parameter_int import ParameterInt
from griptape_nodes.exe_types.param_types.parameter_string import ParameterString
from griptape_nodes.traits.options import Options

from griptape_nodes_library_apiyi.apiyi_client import API_KEY_NAME, gateway_url, missing_key_error, read_secret
from griptape_nodes_library_apiyi.apiyi_model_catalog import CHAT_MODELS

__all__ = ["ApiyiPrompt"]

# The Agent node's `model` input accepts this type. Agent matches the parameter name too.
PROMPT_MODEL_CONFIG_TYPE = "Prompt Model Config"
PROMPT_MODEL_CONFIG_PARAMETER = "prompt_model_config"
# The Agent node reads this provider as an OpenAI-compatible host at `base_url`.
OPENAI_COMPATIBLE_PROVIDER = "openai_compatible"

DEFAULT_MODEL = "claude-fable-5-1"
# Below zero means "do not send a temperature". Newer Claude and GPT models refuse the field with a 400.
UNSET_TEMPERATURE = -1.0
# Agent checks for a Griptape Cloud credential before a workflow runs, which is before this node has
# run. It skips the check only if its `model` input already holds a config, and the engine copies this
# default there when the connection is made. An APIYI model must not need a Griptape account.
# The real config replaces this value when this node runs.
CONFIG_BEFORE_FIRST_RUN = {"provider": OPENAI_COMPATIBLE_PROVIDER, "model": DEFAULT_MODEL}


class ApiyiPrompt(DataNode):
    """Configure an APIYI chat model. Connect the output to the `prompt model` input of an Agent node.

    The output is the wire form of the standard library's `ModelConfig`: a plain dictionary. It holds the
    name of the secret and never its value. Agent reads the secret when it runs. A dictionary also keeps
    this library free of an import from another library's Python package.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.add_parameter(
            Parameter(
                name=PROMPT_MODEL_CONFIG_PARAMETER,
                type=PROMPT_MODEL_CONFIG_TYPE,
                output_type=PROMPT_MODEL_CONFIG_TYPE,
                default_value=CONFIG_BEFORE_FIRST_RUN,
                tooltip="Connect this to the 'prompt model' input of an Agent node.",
                allowed_modes={ParameterMode.OUTPUT},
                ui_options={"display_name": "prompt model config"},
            )
        )
        self.add_parameter(
            ParameterString(
                name="model",
                default_value=DEFAULT_MODEL,
                tooltip="The chat model. Choose one, or type a model ID from the APIYI model list.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                traits={Options(choices=CHAT_MODELS, allow_custom=True)},
            )
        )
        self.add_parameter(
            ParameterFloat(
                name="temperature",
                default_value=UNSET_TEMPERATURE,
                tooltip=(
                    "Creativity of the answers, from 0 to 2. Keep -1 to use the default of the model. "
                    "Some newer models refuse any other value."
                ),
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )
        )
        self.add_parameter(
            ParameterInt(
                name="max_tokens",
                default_value=-1,
                tooltip="The maximum length of an answer in tokens. Keep -1 to use the default of the model.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
            )
        )
        self.add_parameter(
            ParameterInt(
                name="max_attempts_on_fail",
                default_value=2,
                tooltip="How many times to send a request that fails.",
                allowed_modes={ParameterMode.INPUT, ParameterMode.PROPERTY},
                min_val=1,
                max_val=10,
            )
        )

    def validate_before_workflow_run(self) -> list[Exception] | None:
        return self._missing_key()

    def validate_before_node_run(self) -> list[Exception] | None:
        return self._missing_key()

    def process(self) -> None:
        self.parameter_output_values[PROMPT_MODEL_CONFIG_PARAMETER] = {
            "provider": OPENAI_COMPATIBLE_PROVIDER,
            "model": (self.get_parameter_value("model") or DEFAULT_MODEL).strip(),
            "base_url": f"{gateway_url()}/v1",
            "api_key_secret": API_KEY_NAME,
            "settings": self._settings(),
            "max_retries": max(int(self.get_parameter_value("max_attempts_on_fail") or 1) - 1, 0),
        }

    def _settings(self) -> dict[str, Any]:
        settings: dict[str, Any] = {}
        temperature = self.get_parameter_value("temperature")
        if temperature is not None and temperature >= 0:
            settings["temperature"] = temperature
        max_tokens = self.get_parameter_value("max_tokens")
        if max_tokens is not None and max_tokens > 0:
            settings["max_tokens"] = max_tokens
        return settings

    def _missing_key(self) -> list[Exception] | None:
        return None if read_secret(API_KEY_NAME) else [missing_key_error()]
