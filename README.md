# APIYI Library for Griptape Nodes

This library connects Griptape Nodes to [APIYI](https://docs.apiyi.com/), a gateway for AI models.
You can use APIYI chat models with the standard `Agent` node. You can also generate and edit images, and
generate videos.

한국어 번역은 [아래](#한국어-번역)에 있습니다.

## Nodes

| Node | Use |
| --- | --- |
| `APIYI Prompt` | Use an APIYI chat model. Connect this node to an `Agent` node. |
| `APIYI GPT Image` | Generate or edit images with GPT Image models. Official models also accept a mask. |
| `APIYI Nano Banana` | Generate or edit images with Gemini image models. |
| `APIYI Flux` | Generate or edit images with FLUX models. |
| `APIYI Seedream` | Generate or edit images, or make a sequence of related images. |
| `APIYI MAI-Image` | Generate or edit images with MAI-Image models. |
| `APIYI Oxygen` | Generate videos with Oxygen models. |
| `APIYI Veo` | Generate videos with Google Veo 3.1 models. |
| `APIYI Grok Video` | Generate videos with xAI Grok Imagine Video models. |
| `APIYI Seedance` | Generate videos with ByteDance Seedance models. |

## Set Up

1. Get an API key at <https://api.apiyi.com/token>.
2. Start Griptape Nodes. The `APIYI` library loads from this folder.
3. Open **Settings → API Keys & Secrets**.
4. Put your key in `APIYI_API_KEY`.

If the library does not load, add this folder in **Settings → Libraries**.

## Chat With an Agent

1. Add an `APIYI Prompt` node.
2. Add an `Agent` node from the standard library.
3. Drag a wire from `prompt model config` to the round port at the left of the `prompt model` row of `Agent`.
   This row is below `provider`. Release the wire directly on the port.
4. Choose a model in `APIYI Prompt`.
5. Type your message in the `prompt` input of `Agent`.
6. Run the workflow.

Tools, rulesets, and agent memory work the same as with other providers.
You do not need a Griptape Cloud account for this workflow.

To make an image from the answer, connect `output` of `Agent` to `prompt` of an image node.

## Make a Video

Each manufacturer has its own video node.

| Node | Models | Inputs | Token |
| --- | --- | --- | --- |
| `APIYI Oxygen` | Oxygen 1.0 | First and last frames, or reference images, videos, and audio | `default` or `svip` group, billing mode 按量优先 |
| `APIYI Veo` | Veo 3.1 and Veo 3.1 Fast | First frame | `Default` group, billing mode 按次计费 or 按量优先 |
| `APIYI Grok Video` | Grok Imagine Video 1.5, 1.5 Lite, and the first version | First frame | `GrokOfficial` group, billing mode not 按次计费 |
| `APIYI Seedance` | Seedance 2.5, 2.0, 2.0 Fast, and 2.0 Mini | First and last frames, or reference images, videos, and audio | `SeeDance2` group, billing mode 按量优先 or 按量计费 |

1. Add a video node.
2. Type what the video must show in `prompt`.
3. Run the node.
4. Watch `generation_status`. A video takes from one minute to many minutes.
5. Use the `video` output, or open the file from `output_file`.

### Use a Token in a Special Group

`APIYI Grok Video` and `APIYI Seedance` need a token in a special group. APIYI opens the `GrokOfficial` group
on request. Your other nodes can keep the token in `APIYI_API_KEY`.

1. Make a token in that group at <https://api.apiyi.com/token>.
2. Open **Settings → API Keys & Secrets**.
3. Put the token in `APIYI_GROK_API_KEY` or `APIYI_SEEDANCE_API_KEY`.

If these secrets are empty, the nodes use `APIYI_API_KEY`.

### If a Video Does Not Arrive

A video is a task on APIYI. The node sends the task one time, and then reads its state.

- The task continues on APIYI if the node stops waiting. APIYI can still bill the task.
- `Cancel` stops the wait only. APIYI has no way to stop a task.
- To collect the video, click `Refresh / Retrieve Result` in the status group. This starts no new task.
- This button works after a timeout, after a failed download, and after you open the workflow again.
- Do not run the node again to get the same video. Each run starts a new task, and APIYI can bill it.
- The node saves the video to your project. The links on APIYI work for about 24 hours.

### Frames and References

- You can use frames or references in one run, but not both.
- A last frame needs a first frame.
- Reference videos and audio need a public `https` link. APIYI cannot read files on your computer.
- Images can be local files. The node sends them as data in the request.

## Choose a Model

- Each `model` input is a dropdown with search.
- You can type a model ID that is not in the list.
- The APIYI model list is at <https://docs.apiyi.com/models/index>.
- The dropdowns come from the APIYI model registry. To update them, run `python scripts/sync_models.py`.
- The default video models are the least expensive ones. One video on a large model can cost more than 1 USD.
- The default chat model is the most capable Claude model. It is also one of the most expensive.
  Choose a smaller model for simple work.

## Costs and Time

APIYI image calls are synchronous. The node waits for the image and keeps the connection open.
Video calls are tasks. See [If a Video Does Not Arrive](#if-a-video-does-not-arrive).

- APIYI bills a request even if the connection stops. Keep `Timeout` high. The default is 600 seconds.
- The node sends a request again only after HTTP 429, 500, or 503. APIYI does not bill these statuses.
  After a 500, the node tries two more times at most.
- The node does not send a request again after a timeout, because APIYI can bill that request.
- A video node never sends a task again by itself. You decide after each error.
- Above 1.5 MB, an input image goes through compression. The long edge becomes 2048 pixels or less.
  To send the full resolution, turn off `compress_input_images`.
- A connected `mask` turns off compression, because the mask must match the first image exactly.

## Model Rules That the Nodes Apply

Each model line has different rules. The nodes apply these rules for you.

| Model line | Rule |
| --- | --- |
| GPT Image `-vip` | The node never sends `n`. APIYI bills for it and returns one image. |
| GPT Image `-all` | These models ignore `size`. Write the shape in the prompt, such as `16:9 wide image`. |
| Seedream 5.0 pro and flash | These models have no image sequences. The node hides `max_images`. |
| Seedream 4.x | These models make JPEG only. The node hides `output_format`. |
| MAI-Image | The node sends `width` and `height`. These models ignore `size` without an error. |
| FLUX | The link to the result expires in 10 minutes. The node downloads the image immediately. |
| GPT Image 1 models | These models have fewer sizes. Only `gpt-image-1` accepts input images and a mask. |
| Oxygen | Most settings go in the `input_reference` string. At the top level, APIYI ignores them and still bills the task. |
| Veo 3.1 | 1080p and 4k need 8 seconds. The node offers only this length at these resolutions. |
| Veo 3.1 | `negative_prompt` works only without `first_frame`. |
| Grok Imagine Video | Review can block a completed task. APIYI bills that task, and the node reports it. |
| Seedance 2.5 | A frame, an edit, and an extend task need the ratio `adaptive`. An edit needs the duration `-1`. |
| Seedance 2.0 | These models need an image or a video with reference audio. |

## Problems

| Message | Cause | Fix |
| --- | --- | --- |
| `Connection failed on type mismatch ... to "Agent.agent"` | The wire went to the `agent` port at the top of the node. That port does not accept this type. | Drag the wire again. Release it directly on the port of the `prompt model` row. |
| `Internal server error ... (request id: ...)` | APIYI or the model vendor had a server error. | Look for a notice at <https://docs.apiyi.com/live/index>. If there is no notice, run the node again. If the error continues, send the request ID to APIYI support. |
| `quota [...] preConsumedQuota [...] is not enough` | The APIYI balance is too low for this model. `quota` is the balance, and `preConsumedQuota` is the price. 500,000 quota is 1 USD. | Add credit, or choose a less expensive model. |
| `该模型无可用渠道`, `无可用渠道`, or HTTP 503 from a video node | The token cannot reach this model. A new attempt does not help. | Do the steps in the message. See the token column in [Make a Video](#make-a-video). |
| `The video did not finish in ...` | The task takes longer than `Timeout`. | Click `Refresh / Retrieve Result` later. Do not run the node again. |

While you drag a wire, the ports that accept it stay bright, and other ports go dim.
A dim port can still catch the wire if you release it near that port.

## Use the Backup Host

To use the APIYI backup host, add a secret with the name `APIYI_BASE_URL` and the value
`https://b.apiyi.com`. All nodes in this library then use this host.

## Features Not Included

- GPT Image 2.6 SVG and GIF output, and `markup` editing.
- Seedream layer decomposition.
- Nano Banana thinking settings.
- Seedance draft videos, `return_last_frame`, and `asset://` file IDs.
- Wan, HappyHorse, and MiniMax video models.
- Claude prompt caching. Chat goes through the OpenAI-compatible endpoint of APIYI, and that endpoint
  has no cache discount for Claude.

## Run the Tests

The tests use a local fake APIYI server. They do not need an API key, and they cost nothing.

```bash
pytest
```

To also run the `Agent` tests, set `GTN_STANDARD_LIBRARY` to the folder of the standard library.

---

# 한국어 번역

이 라이브러리는 Griptape Nodes를 AI 모델 게이트웨이인 [APIYI](https://docs.apiyi.com/)에 연결합니다.
표준 `Agent` 노드에서 APIYI 챗 모델을 쓸 수 있고, 이미지를 생성하고 편집하고, 비디오를 생성할 수도 있습니다.

## 노드

| 노드 | 용도 |
| --- | --- |
| `APIYI Prompt` | APIYI 챗 모델을 사용합니다. 이 노드를 `Agent` 노드에 연결합니다. |
| `APIYI GPT Image` | GPT Image 모델로 이미지를 생성하거나 편집합니다. 공식 모델은 마스크도 받습니다. |
| `APIYI Nano Banana` | Gemini 이미지 모델로 이미지를 생성하거나 편집합니다. |
| `APIYI Flux` | FLUX 모델로 이미지를 생성하거나 편집합니다. |
| `APIYI Seedream` | 이미지를 생성하거나 편집하고, 서로 이어지는 이미지 시퀀스를 만듭니다. |
| `APIYI MAI-Image` | MAI-Image 모델로 이미지를 생성하거나 편집합니다. |
| `APIYI Oxygen` | Oxygen 모델로 비디오를 생성합니다. |
| `APIYI Veo` | Google Veo 3.1 모델로 비디오를 생성합니다. |
| `APIYI Grok Video` | xAI Grok Imagine Video 모델로 비디오를 생성합니다. |
| `APIYI Seedance` | ByteDance Seedance 모델로 비디오를 생성합니다. |

## 설정

1. <https://api.apiyi.com/token> 에서 API 키를 받습니다.
2. Griptape Nodes를 시작합니다. `APIYI` 라이브러리가 이 폴더에서 로드됩니다.
3. **Settings → API Keys & Secrets** 를 엽니다.
4. `APIYI_API_KEY`에 키를 넣습니다.

라이브러리가 로드되지 않으면 **Settings → Libraries** 에서 이 폴더를 추가합니다.

## Agent로 채팅하기

1. `APIYI Prompt` 노드를 추가합니다.
2. 표준 라이브러리의 `Agent` 노드를 추가합니다.
3. `prompt model config`에서 `Agent`의 `prompt model` 행 왼쪽에 있는 둥근 포트로 와이어를 끌어옵니다.
   이 행은 `provider` 아래에 있습니다. 와이어는 포트 위에 정확히 놓습니다.
4. `APIYI Prompt`에서 모델을 선택합니다.
5. `Agent`의 `prompt` 입력에 메시지를 입력합니다.
6. 워크플로를 실행합니다.

도구, 룰셋, 에이전트 메모리는 다른 프로바이더와 똑같이 동작합니다.
이 워크플로에는 Griptape Cloud 계정이 필요하지 않습니다.

답변으로 이미지를 만들려면 `Agent`의 `output`을 이미지 노드의 `prompt`에 연결합니다.

## 비디오 만들기

제조사마다 비디오 노드가 따로 있습니다.

| 노드 | 모델 | 입력 | 토큰 |
| --- | --- | --- | --- |
| `APIYI Oxygen` | Oxygen 1.0 | 첫 프레임과 마지막 프레임, 또는 참조 이미지, 비디오, 오디오 | `default` 또는 `svip` 그룹, 과금 방식 按量优先 |
| `APIYI Veo` | Veo 3.1, Veo 3.1 Fast | 첫 프레임 | `Default` 그룹, 과금 방식 按次计费 또는 按量优先 |
| `APIYI Grok Video` | Grok Imagine Video 1.5, 1.5 Lite, 첫 버전 | 첫 프레임 | `GrokOfficial` 그룹, 과금 방식은 按次计费가 아니어야 함 |
| `APIYI Seedance` | Seedance 2.5, 2.0, 2.0 Fast, 2.0 Mini | 첫 프레임과 마지막 프레임, 또는 참조 이미지, 비디오, 오디오 | `SeeDance2` 그룹, 과금 방식 按量优先 또는 按量计费 |

1. 비디오 노드를 추가합니다.
2. `prompt`에 비디오가 보여 줄 내용을 입력합니다.
3. 노드를 실행합니다.
4. `generation_status`를 확인합니다. 비디오는 1분에서 여러 분까지 걸립니다.
5. `video` 출력을 쓰거나 `output_file`의 파일을 엽니다.

### 특수 그룹의 토큰 사용

`APIYI Grok Video`와 `APIYI Seedance`는 특수 그룹의 토큰이 필요합니다. `GrokOfficial` 그룹은 APIYI에 요청해야
열립니다. 다른 노드는 `APIYI_API_KEY`의 토큰을 그대로 쓸 수 있습니다.

1. <https://api.apiyi.com/token> 에서 해당 그룹의 토큰을 만듭니다.
2. **Settings → API Keys & Secrets** 를 엽니다.
3. 토큰을 `APIYI_GROK_API_KEY` 또는 `APIYI_SEEDANCE_API_KEY`에 넣습니다.

이 시크릿이 비어 있으면 노드는 `APIYI_API_KEY`를 사용합니다.

### 비디오가 오지 않을 때

비디오는 APIYI의 작업(task)입니다. 노드는 작업을 한 번 보낸 뒤 상태를 읽습니다.

- 노드가 기다리기를 멈춰도 작업은 APIYI에서 계속됩니다. APIYI는 이 작업에 과금할 수 있습니다.
- `Cancel`은 기다리기만 멈춥니다. APIYI에는 작업을 멈추는 방법이 없습니다.
- 비디오를 받으려면 상태 그룹의 `Refresh / Retrieve Result`를 클릭합니다. 새 작업은 시작되지 않습니다.
- 이 버튼은 시간 초과 후, 다운로드 실패 후, 워크플로를 다시 연 후에도 동작합니다.
- 같은 비디오를 받으려고 노드를 다시 실행하지 마십시오. 실행할 때마다 새 작업이 시작되고 과금될 수 있습니다.
- 노드는 비디오를 프로젝트에 저장합니다. APIYI의 링크는 약 24시간 동안 유효합니다.

### 프레임과 참조

- 한 번의 실행에 프레임 또는 참조를 쓸 수 있습니다. 둘을 함께 쓸 수는 없습니다.
- 마지막 프레임을 쓰려면 첫 프레임이 필요합니다.
- 참조 비디오와 오디오는 공개 `https` 링크가 필요합니다. APIYI는 사용자 컴퓨터의 파일을 읽을 수 없습니다.
- 이미지는 로컬 파일이어도 됩니다. 노드가 요청 안에 데이터로 넣어 보냅니다.

## 모델 선택

- 각 `model` 입력은 검색이 되는 드롭다운입니다.
- 목록에 없는 모델 ID를 직접 입력할 수 있습니다.
- APIYI 모델 목록은 <https://docs.apiyi.com/models/index> 에 있습니다.
- 드롭다운은 APIYI 모델 레지스트리에서 가져옵니다. 갱신하려면 `python scripts/sync_models.py`를 실행합니다.
- 기본 비디오 모델은 가장 저렴한 모델입니다. 큰 모델로 만든 비디오 한 개는 1 USD를 넘을 수 있습니다.
- 기본 챗 모델은 가장 성능이 높은 Claude 모델입니다. 가장 비싼 모델 중 하나이기도 합니다.
  간단한 작업에는 더 작은 모델을 선택하십시오.

## 비용과 시간

APIYI 이미지 호출은 동기 방식입니다. 노드는 연결을 유지한 채 이미지를 기다립니다.
비디오 호출은 작업입니다. [비디오가 오지 않을 때](#비디오가-오지-않을-때)를 참고합니다.

- 연결이 끊겨도 APIYI는 요청에 과금합니다. `Timeout`을 높게 유지하십시오. 기본값은 600초입니다.
- 노드는 HTTP 429, 500, 503 이후에만 요청을 다시 보냅니다. APIYI는 이 상태들에 과금하지 않습니다.
  500 이후에는 최대 두 번만 다시 시도합니다.
- 시간 초과 후에는 요청을 다시 보내지 않습니다. APIYI가 그 요청에 과금할 수 있기 때문입니다.
- 비디오 노드는 작업을 절대 스스로 다시 보내지 않습니다. 오류가 나면 사용자가 결정합니다.
- 1.5MB를 넘는 입력 이미지는 압축합니다. 긴 변은 2048픽셀 이하가 됩니다.
  원본 해상도를 보내려면 `compress_input_images`를 끕니다.
- `mask`를 연결하면 압축이 꺼집니다. 마스크가 첫 번째 이미지와 정확히 일치해야 하기 때문입니다.

## 노드가 적용하는 모델 규칙

모델 라인마다 규칙이 다릅니다. 노드가 이 규칙을 대신 적용합니다.

| 모델 라인 | 규칙 |
| --- | --- |
| GPT Image `-vip` | 노드는 `n`을 보내지 않습니다. APIYI는 이 값대로 과금하지만 이미지는 1장만 반환합니다. |
| GPT Image `-all` | 이 모델은 `size`를 무시합니다. `16:9 wide image`처럼 프롬프트에 형태를 쓰십시오. |
| Seedream 5.0 pro, flash | 이 모델에는 이미지 시퀀스가 없습니다. 노드는 `max_images`를 숨깁니다. |
| Seedream 4.x | 이 모델은 JPEG만 만듭니다. 노드는 `output_format`을 숨깁니다. |
| MAI-Image | 노드는 `width`와 `height`를 보냅니다. 이 모델은 오류 없이 `size`를 무시합니다. |
| FLUX | 결과 링크는 10분 후에 만료됩니다. 노드는 이미지를 즉시 내려받습니다. |
| GPT Image 1 모델 | 이 모델은 크기 선택지가 적습니다. `gpt-image-1`만 입력 이미지와 마스크를 받습니다. |
| Oxygen | 대부분의 설정은 `input_reference` 문자열에 들어갑니다. 최상위에 두면 APIYI가 무시하고 그대로 과금합니다. |
| Veo 3.1 | 1080p와 4k는 8초가 필요합니다. 노드는 이 해상도에서 이 길이만 제공합니다. |
| Veo 3.1 | `negative_prompt`는 `first_frame`이 없을 때만 동작합니다. |
| Grok Imagine Video | 심사가 완료된 작업을 막을 수 있습니다. APIYI는 그 작업에 과금하고, 노드는 이를 알립니다. |
| Seedance 2.5 | 프레임, edit, extend 작업은 ratio `adaptive`가 필요합니다. edit는 duration `-1`이 필요합니다. |
| Seedance 2.0 | 참조 오디오에는 이미지 또는 비디오가 함께 필요합니다. |

## 문제 해결

| 메시지 | 원인 | 해결 방법 |
| --- | --- | --- |
| `Connection failed on type mismatch ... to "Agent.agent"` | 와이어가 노드 맨 위의 `agent` 포트로 갔습니다. 이 포트는 이 타입을 받지 않습니다. | 와이어를 다시 끌어서 `prompt model` 행의 포트 위에 정확히 놓습니다. |
| `Internal server error ... (request id: ...)` | APIYI 또는 모델 제공사에 서버 오류가 있었습니다. | <https://docs.apiyi.com/live/index> 에서 공지를 확인합니다. 공지가 없으면 노드를 다시 실행합니다. 오류가 계속되면 request ID를 APIYI 지원팀에 보냅니다. |
| `quota [...] preConsumedQuota [...] is not enough` | 이 모델을 쓰기에 APIYI 잔액이 부족합니다. `quota`는 잔액이고 `preConsumedQuota`는 가격입니다. 500,000 quota는 1 USD입니다. | 충전하거나 더 저렴한 모델을 선택합니다. |
| 비디오 노드의 `该模型无可用渠道`, `无可用渠道`, HTTP 503 | 토큰이 이 모델에 닿지 못합니다. 다시 시도해도 소용없습니다. | 메시지의 단계를 따릅니다. [비디오 만들기](#비디오-만들기)의 토큰 열을 참고합니다. |
| `The video did not finish in ...` | 작업이 `Timeout`보다 오래 걸립니다. | 나중에 `Refresh / Retrieve Result`를 클릭합니다. 노드를 다시 실행하지 마십시오. |

와이어를 끄는 동안 받을 수 있는 포트는 밝게 유지되고 나머지 포트는 흐려집니다.
흐려진 포트도 그 근처에서 와이어를 놓으면 와이어를 잡을 수 있습니다.

## 백업 호스트 사용

APIYI 백업 호스트를 쓰려면 이름이 `APIYI_BASE_URL`이고 값이 `https://b.apiyi.com`인 시크릿을 추가합니다.
그러면 이 라이브러리의 모든 노드가 이 호스트를 사용합니다.

## 포함하지 않은 기능

- GPT Image 2.6의 SVG, GIF 출력과 `markup` 편집
- Seedream 레이어 분해
- Nano Banana 사고(thinking) 설정
- Seedance 초안 비디오, `return_last_frame`, `asset://` 파일 ID
- Wan, HappyHorse, MiniMax 비디오 모델
- Claude 프롬프트 캐싱. 채팅은 APIYI의 OpenAI 호환 엔드포인트를 사용하며, 이 엔드포인트에는 Claude 캐시
  할인이 없습니다.

## 테스트 실행

테스트는 로컬 가짜 APIYI 서버를 사용합니다. API 키가 필요 없고 비용도 들지 않습니다.

```bash
pytest
```

`Agent` 테스트도 실행하려면 `GTN_STANDARD_LIBRARY`를 표준 라이브러리 폴더로 설정합니다.
