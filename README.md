# Video Event Workbench

A local prototype for finding every matching event in a long video from a natural-language
query, refining its boundaries, and exporting clips with inspectable state and debug records.

Private repository: https://github.com/Michael-Ma/video-event-workbench

The first version uses a React frontend, FastAPI, one Python worker, SQLite, FFmpeg/PyAV,
and a Gemini provider. A labeled deterministic demo can exercise the engineering flow
without an API key; it is not a video-recognition accuracy demonstration.

## Start locally

From the repository directory:

```sh
./start.sh
```

The script installs missing uv and Node.js into the ignored `.tools/` directory,
uses uv to prepare Python 3.13 and locked backend dependencies, and installs locked
frontend dependencies when their manifest changes. FFmpeg/ffprobe are installed with
Homebrew on macOS or apt on Debian/Ubuntu; system installation may request an administrator
password. If Homebrew is missing on macOS, its official installer runs first. Other Linux
distributions need FFmpeg preinstalled. Bootstrap downloads require curl or wget.

It creates `.env` from the template only when absent, preserves existing values, gives
exported environment variables priority, and checks the model, upload limit, writable
data directory, video encoders and ports. The API key is never printed or passed to the
frontend process. Key checking is local presence only; it makes no model request.

Open [http://127.0.0.1:5173](http://127.0.0.1:5173) after the script prints **[就绪]**.
That message requires the API, worker heartbeat and frontend to be ready. Ctrl+C stops
all three, including child processes. A port conflict fails clearly without killing the
service already using it.

```sh
./start.sh --check              # Install/check only
./start.sh --require-api-key    # Require a key for real-model testing
```

Use **内置示例** with **流程测试** first. The generated 12-second video has declared
events and requires no model key. Its results validate state, time mapping and clipping;
they do not demonstrate video-recognition accuracy.

For real video recognition, set `GEMINI_API_KEY` in the generated `.env`,
and restart with `./start.sh`. The key stays in the backend. Select Gemini, upload a video,
write the query, and start. The configured default is `gemini-3.8-flash`; it can be
changed through `GEMINI_MODEL` or the run's model field. Sampled images and the query
are sent to Google's Gemini API. No live paid model calls are made by the test suite.

Example queries:

- 找到小球每次触地的时刻，并截取前后各一秒。
- 找到机械臂每次抓取物体的过程，从开始靠近到抓取动作结束。

## Inspect a run

The UI shows each processing stage, successful core-window coverage, gaps, task state,
matched/uncertain/rejected events, playable clips, and incremental debug logs.
Point and interval locations use original-video microseconds. An uncertain clip is
explicitly a context fallback; rejected events and duplicates do not produce formal clips.
The raw query remains authoritative, and first/last selection is applied globally.

Each model call records `model_call_cost` in the processing/debug log, with token usage,
elapsed request time, input frame count/bytes, and an estimated USD cost. Estimates use a
versioned Gemini paid Standard price snapshot, including thinking tokens and discounted
cached input. The current price table covers `gemini-3.8-flash`; other models, missing usage,
and unknown request outcomes report unknown cost rather than zero. These are list-price
estimates, not billing receipts; free-tier use or account discounts can differ. Fixture calls
are explicitly local and cost zero. Reusing a saved response does not log another paid call.

State and artifacts are local in `.data/`: SQLite journal, immutable original-frame
indexes, navigation previews, sampled JPEGs, model request/response records, results,
and exported clips. `.env`, videos and runtime data are excluded from Git.
The preview and exported clips currently contain video only; the original upload retains
any audio. This prototype supports one local user and one worker on macOS/Linux.

Interrupted model submissions become `request_unknown` and are never automatically
resubmitted. Start a new run only after inspecting the previous state. Cancellation stops
new work, but an already-submitted remote request may finish. Call and input limits are
configurable; a processing gap is retained when a limit or failure prevents full scanning.

## Development and validation

```sh
uv run ruff check --config pyproject.toml backend scripts
uv run pytest -q
npm --prefix web run build
```

See [the API contract](docs/API.md), [dependency plan](docs/IMPLEMENTATION_PLAN.md),
[validation record](docs/VALIDATION.md), and [design snapshot](docs/design.md).
The source [design Page](https://chatgpt.com/space/page_c6399f3467e88191aaf8beb5a250cb29)
contains the implementation decisions appended after the original design.

The tests establish engineering behavior on deterministic inputs. Semantic recall,
boundary accuracy and cost on real basketball/robot videos require the user's API key
and complete test videos; successful processing is not evidence of complete event recall.
