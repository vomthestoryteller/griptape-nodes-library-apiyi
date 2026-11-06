# APIYI Library for Griptape Nodes

This library connects Griptape Nodes to [APIYI](https://docs.apiyi.com/), a gateway for AI models.
You can use APIYI chat models with the standard `Agent` node. You can also generate and edit images.

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

## Choose a Model

- Each `model` input is a dropdown with search.
- You can type a model ID that is not in the list.
- The APIYI model list is at <https://docs.apiyi.com/models/index>.
- The default chat model is the most capable Claude model. It is also one of the most expensive.
  Choose a smaller model for simple work.

## Costs and Time

APIYI image calls are synchronous. The node waits for the image and keeps the connection open.

- APIYI bills a request even if the connection stops. Keep `Timeout` high. The default is 600 seconds.
- The node sends a request again only after HTTP 429, 500, or 503. APIYI does not bill these statuses.
  After a 500, the node tries two more times at most.
- The node does not send a request again after a timeout, because APIYI can bill that request.
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

## Problems

| Message | Cause | Fix |
| --- | --- | --- |
| `Connection failed on type mismatch ... to "Agent.agent"` | The wire went to the `agent` port at the top of the node. That port does not accept this type. | Drag the wire again. Release it directly on the port of the `prompt model` row. |
| `Internal server error ... (request id: ...)` | APIYI or the model vendor had a server error. | Look for a notice at <https://docs.apiyi.com/live/index>. If there is no notice, run the node again. If the error continues, send the request ID to APIYI support. |
| `quota [...] preConsumedQuota [...] is not enough` | The APIYI balance is too low for this model. `quota` is the balance, and `preConsumedQuota` is the price. 500,000 quota is 1 USD. | Add credit, or choose a less expensive model. |

While you drag a wire, the ports that accept it stay bright, and other ports go dim.
A dim port can still catch the wire if you release it near that port.

## Use the Backup Host

To use the APIYI backup host, add a secret with the name `APIYI_BASE_URL` and the value
`https://b.apiyi.com`. All nodes in this library then use this host.

## Features Not Included

- GPT Image 2.6 SVG and GIF output, and `markup` editing.
- Seedream layer decomposition.
- Nano Banana thinking settings.
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
표준 `Agent` 노드에서 APIYI 챗 모델을 쓸 수 있고, 이미지를 생성하고 편집할 수도 있습니다.

## 노드

| 노드 | 용도 |
| --- | --- |
| `APIYI Prompt` | APIYI 챗 모델을 사용합니다. 이 노드를 `Agent` 노드에 연결합니다. |
| `APIYI GPT Image` | GPT Image 모델로 이미지를 생성하거나 편집합니다. 공식 모델은 마스크도 받습니다. |
| `APIYI Nano Banana` | Gemini 이미지 모델로 이미지를 생성하거나 편집합니다. |
| `APIYI Flux` | FLUX 모델로 이미지를 생성하거나 편집합니다. |
| `APIYI Seedream` | 이미지를 생성하거나 편집하고, 서로 이어지는 이미지 시퀀스를 만듭니다. |
| `APIYI MAI-Image` | MAI-Image 모델로 이미지를 생성하거나 편집합니다. |

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

## 모델 선택

- 각 `model` 입력은 검색이 되는 드롭다운입니다.
- 목록에 없는 모델 ID를 직접 입력할 수 있습니다.
- APIYI 모델 목록은 <https://docs.apiyi.com/models/index> 에 있습니다.
- 기본 챗 모델은 가장 성능이 높은 Claude 모델입니다. 가장 비싼 모델 중 하나이기도 합니다.
  간단한 작업에는 더 작은 모델을 선택하십시오.

## 비용과 시간

APIYI 이미지 호출은 동기 방식입니다. 노드는 연결을 유지한 채 이미지를 기다립니다.

- 연결이 끊겨도 APIYI는 요청에 과금합니다. `Timeout`을 높게 유지하십시오. 기본값은 600초입니다.
- 노드는 HTTP 429, 500, 503 이후에만 요청을 다시 보냅니다. APIYI는 이 상태들에 과금하지 않습니다.
  500 이후에는 최대 두 번만 다시 시도합니다.
- 시간 초과 후에는 요청을 다시 보내지 않습니다. APIYI가 그 요청에 과금할 수 있기 때문입니다.
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

## 문제 해결

| 메시지 | 원인 | 해결 방법 |
| --- | --- | --- |
| `Connection failed on type mismatch ... to "Agent.agent"` | 와이어가 노드 맨 위의 `agent` 포트로 갔습니다. 이 포트는 이 타입을 받지 않습니다. | 와이어를 다시 끌어서 `prompt model` 행의 포트 위에 정확히 놓습니다. |
| `Internal server error ... (request id: ...)` | APIYI 또는 모델 제공사에 서버 오류가 있었습니다. | <https://docs.apiyi.com/live/index> 에서 공지를 확인합니다. 공지가 없으면 노드를 다시 실행합니다. 오류가 계속되면 request ID를 APIYI 지원팀에 보냅니다. |
| `quota [...] preConsumedQuota [...] is not enough` | 이 모델을 쓰기에 APIYI 잔액이 부족합니다. `quota`는 잔액이고 `preConsumedQuota`는 가격입니다. 500,000 quota는 1 USD입니다. | 충전하거나 더 저렴한 모델을 선택합니다. |

와이어를 끄는 동안 받을 수 있는 포트는 밝게 유지되고 나머지 포트는 흐려집니다.
흐려진 포트도 그 근처에서 와이어를 놓으면 와이어를 잡을 수 있습니다.

## 백업 호스트 사용

APIYI 백업 호스트를 쓰려면 이름이 `APIYI_BASE_URL`이고 값이 `https://b.apiyi.com`인 시크릿을 추가합니다.
그러면 이 라이브러리의 모든 노드가 이 호스트를 사용합니다.

## 포함하지 않은 기능

- GPT Image 2.6의 SVG, GIF 출력과 `markup` 편집
- Seedream 레이어 분해
- Nano Banana 사고(thinking) 설정
- Claude 프롬프트 캐싱. 채팅은 APIYI의 OpenAI 호환 엔드포인트를 사용하며, 이 엔드포인트에는 Claude 캐시
  할인이 없습니다.

## 테스트 실행

테스트는 로컬 가짜 APIYI 서버를 사용합니다. API 키가 필요 없고 비용도 들지 않습니다.

```bash
pytest
```

`Agent` 테스트도 실행하려면 `GTN_STANDARD_LIBRARY`를 표준 라이브러리 폴더로 설정합니다.
