# Changelog

This file follows [Keep a Changelog 2.0.0](https://keepachangelog.com/en/2.0.0/). Each version is the
`library_version` in `griptape-nodes-library.json`.

## [0.2.0] - 2026-11-06

### Added

- `APIYI Oxygen`, `APIYI Veo`, `APIYI Grok Video`, and `APIYI Seedance` generate videos through APIYI.
- A video node keeps its task ID. `Refresh / Retrieve Result` collects a video after a timeout, after a failed
  download, and after a workflow load.
- The optional secrets `APIYI_GROK_API_KEY` and `APIYI_SEEDANCE_API_KEY` hold tokens in special groups.
- `scripts/sync_models.py` updates the model dropdowns from the APIYI model registry.
- `APIYI Prompt` offers all 188 chat models in the registry. The list starts with the same 32 models.
- `APIYI GPT Image` offers `gpt-image-1`, `gpt-image-1.5`, `gpt-image-1-mini`, and three dated models.
- `APIYI Nano Banana` offers the Gemini 2.5 image models and the aliases of the other models.

### Fixed

- Image lists use the item converters of the engine. The engine ignored the old value hook, and logged a warning
  for each new item.
- A status 200 with an unreadable answer no longer tells you to run the node again, because APIYI can bill that
  request.

## [0.1.2] - 2026-11-06

### Fixed

- `provider_response` no longer keeps long strings. A Gemini `thoughtSignature` of 2.5 MB made
  `APIYI Nano Banana` send large updates to the editor.

## [0.1.1] - 2026-11-06

### Fixed

- Image nodes send a request again after HTTP 500, two times at most. The APIYI guides advise this,
  and APIYI does not bill a 500.

## [0.1.0] - 2026-11-06

### Added

- `APIYI Prompt` connects the standard `Agent` node to the chat models on APIYI.
- `APIYI GPT Image`, `APIYI Nano Banana`, `APIYI Flux`, `APIYI Seedream`, and `APIYI MAI-Image` generate
  and edit images through APIYI.
