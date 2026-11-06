"""A node must survive a copy, a paste, and a workflow load, and must pass the engine's strict-mode rules."""

from __future__ import annotations

import pytest
from griptape_nodes.exe_types.node_types import BaseNode
from griptape_nodes.retained_mode.engine import current_engine
from griptape_nodes.retained_mode.events.base_events import ResultDetails, StrictModeViolationDetail
from griptape_nodes.retained_mode.events.execution_events import ExecuteNodeResultSuccess
from griptape_nodes.retained_mode.events.node_events import (
    DeserializeSelectedNodesFromCommandsRequest,
    DeserializeSelectedNodesFromCommandsResultSuccess,
    SerializeSelectedNodesToCommandsRequest,
    SerializeSelectedNodesToCommandsResultSuccess,
)
from griptape_nodes.retained_mode.events.parameter_events import SetParameterValueRequest
from griptape_nodes.traits.options import Options

from .conftest import FLOW_NAME, create_node, run_node

ALL_NODES = [
    "ApiyiPrompt",
    "ApiyiGptImage",
    "ApiyiNanoBanana",
    "ApiyiFlux",
    "ApiyiSeedream",
    "ApiyiMaiImage",
    "ApiyiOxygen",
    "ApiyiVeo",
    "ApiyiGrokVideo",
    "ApiyiSeedance",
]
# A model that changes the dropdowns, and a parameter value that only this model accepts.
MODEL_ONLY_VALUES = [
    ("ApiyiGptImage", "gpt-image-2-vip", "size", "2048x1360"),
    ("ApiyiNanoBanana", "gemini-3.1-flash-image", "image_size", "512"),
    ("ApiyiNanoBanana", "gemini-3.1-flash-image", "aspect_ratio", "8:1"),
    ("ApiyiSeedream", "seedream-4-5-251128", "size", "4K"),
    ("ApiyiGptImage", "gpt-image-1", "background", "transparent"),
    ("ApiyiNanoBanana", "gemini-3-pro-image-preview-4k", "image_size", "4K"),
    ("ApiyiSeedance", "doubao-seedance-2-5-260628", "resolution", "1080p"),
]


def _set(node: BaseNode, name: str, value: str) -> None:
    result = current_engine().handle_request(
        SetParameterValueRequest(node_name=node.name, parameter_name=name, value=value)
    )
    assert result.succeeded(), result.result_details


def _paste(node: BaseNode) -> BaseNode:
    """Copy and paste a node. This applies its saved values the same way as opening a workflow."""
    engine = current_engine()
    before = set(engine.object_manager.get_filtered_subset(type=BaseNode))
    with engine.context_manager.flow(FLOW_NAME):
        copied = engine.handle_request(SerializeSelectedNodesToCommandsRequest(nodes_to_serialize=[[node.name, "0"]]))
        assert isinstance(copied, SerializeSelectedNodesToCommandsResultSuccess), copied.result_details
        pasted = engine.handle_request(
            DeserializeSelectedNodesFromCommandsRequest(
                deserialize_commands=copied.serialized_selected_node_commands,
                pickled_values=copied.pickled_values,
            )
        )
    assert isinstance(pasted, DeserializeSelectedNodesFromCommandsResultSuccess), pasted.result_details
    (pasted_name,) = set(engine.object_manager.get_filtered_subset(type=BaseNode)) - before
    return engine.node_manager.get_node_by_name(pasted_name)


@pytest.mark.parametrize(("node_type", "model", "name", "value"), MODEL_ONLY_VALUES)
def test_a_pasted_node_keeps_values_that_only_its_model_accepts(
    node_type: str, model: str, name: str, value: str
) -> None:
    node = create_node(node_type)
    _set(node, "model", model)
    _set(node, name, value)

    pasted = _paste(node)

    assert pasted.get_parameter_value("model") == model
    assert pasted.get_parameter_value(name) == value
    assert value in pasted.get_parameter_by_name(name).find_elements_by_type(Options)[0].choices


def test_a_pasted_node_keeps_hidden_parameters_hidden() -> None:
    node = create_node("ApiyiGptImage")
    _set(node, "model", "gpt-image-2.5-all")

    pasted = _paste(node)

    assert pasted.get_parameter_by_name("size").ui_options.get("hide") is True


@pytest.mark.parametrize("node_type", ALL_NODES)
async def test_a_node_reports_no_strict_mode_rule(node_type: str) -> None:
    node = create_node(node_type)
    _set(node, "model", node.get_parameter_by_name("model").find_elements_by_type(Options)[0].choices[-1])
    if node_type != "ApiyiPrompt":
        _set(node, "prompt", "a red fox")

    result = await run_node(node)

    assert isinstance(result, ExecuteNodeResultSuccess), result.result_details
    details = result.result_details
    assert isinstance(details, ResultDetails), details
    assert [detail for detail in details.result_details if isinstance(detail, StrictModeViolationDetail)] == []
