# Changelog

This file follows [Keep a Changelog 2.0.0](https://keepachangelog.com/en/2.0.0/). Each version is the
`library_version` in `griptape-nodes-library.json`.

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
