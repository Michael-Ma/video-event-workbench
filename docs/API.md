# Shared API contract

Base path: `/api`. All times are integer microseconds relative to the first source
display frame. Video/model local times are converted by backend code.

- `GET /health`: `{status, api_key_configured, default_model, ffmpeg_available,
  ffprobe_available, worker_alive}`. No secret values.
- `GET /media`: media records (id, filename, duration_us, status, metadata, is_demo).
- `POST /media` multipart field `file`: streamed upload and probe, returns media.
- `POST /demo`: generate/load a clearly labeled deterministic test video and return media.
- `GET /media/{id}`: media plus original_url/preview_url when available.
- `POST /runs` body `{media_id, query, config}`: CreateRunRequest from contracts.py;
  optional `Idempotency-Key`, returns run (HTTP 202).
- `GET /runs`: latest runs.
- `GET /runs/{id}`: run plus tasks. Run: id, media_id, query, config, query_spec,
  status, stage, progress, results, error, created_at, updated_at.
- `GET /runs/{id}/logs?after=0&limit=200`: `{items:[{seq,time,stage,level,code,message,details}]}`.
- `POST /runs/{id}/cancel`: returns run; late replies cannot revive cancellation.
- `GET /runs/{id}/results`: results (JSON), 409 until available.
- `GET /artifacts/{relative_path}`: media/clip/debug file under the configured data root.

Media records contain original_path/preview_path internally. The API adds URLs;
the browser never submits arbitrary filesystem paths. Artifact paths are relative
to the data directory and must not traverse outside it.

Result envelope: `{run_id, provider, model_id, query_spec, events:EventResult[],
scan_complete, coverage:{duration_us,covered_us,gaps}, stats, limitations}`.
Pydantic `EventResult` is authoritative. Its clip URL is exposed by the API.

Errors: `{error:{code,message,retryable?,details?,technical_message?}}`. Missing key is a settings
error, not an empty event list. Fixture mode is allowed only on the built-in demo.

Run detail includes `diagnostics`: issues with stage, code, human-readable title/message,
action, source range, safe details and artifact refs, plus stopped_stage and available
event/clip counts. Older journals are interpreted at read time without mutation.
Task errors can expose `error_details` and `error_path`.

RunConfig freezes independent `scan_input_mode` / `refine_input_mode` (`images` or `video`),
`model_concurrency` (default 2, 1..8), and `clip_concurrency` (default 2, 1..4).
`request_timeout_s` defaults to 1200 (10..3600), refinement defaults to 6 FPS, and scan
presets use 2 FPS for intervals or 6 FPS for points. Native video FPS cannot exceed 24.
Thinking defaults to `low` with `max_output_tokens=16384` and `temperature=1`;
`thinking_level=default` delegates to the provider for other model families. Output
budgets include thinking. `started_at` and optional `processing_elapsed_s` separate
processing duration from queued or offline reprocessing wait.
Native inputs are normalized local MP4 windows with original-frame mapping. Model events
use `evidence_times_s` for video and `evidence_frame_ids` for images; published video evidence
references have the shape `video:<media_id>:<source_time_us>` and are not JPEG observations.
Native `duration_validation` records explicit container duration quantization without
relaxing the two-microsecond decoded-frame timestamp checks. Candidate dispositions with
indices are preferred as `mapped_to_event`; rejected/unresolved variants are accepted only
when every referenced event has that same matching decision. Contradictions still fail.

Model tasks also expose `cost`: status (`estimated`, `unknown`, `not_billable`),
`estimated_usd` (null when unknown), currency/basis, pricing version/source/effective rates,
and token counts with input/cached-input/output components. The `model_call_cost` log
entry includes the operation, task/attempt IDs, cost, original usage, and request metrics.
The same cost record is saved in each response/error artifact. A pricing snapshot is saved
before submission. Estimates use paid Standard list rates, including output thinking tokens;
they do not assert the account's actual billed amount. Cancelled or invalid responses still
retain available usage/cost; persisted-response reuse does not duplicate a cost log.
Run detail adds `cost_summary` with `estimated_usd` (known subtotal, null if none),
`estimated_calls`, `unknown_calls`, `pending_calls`, `local_calls`, `total_calls`, and
`complete`. Only tasks with request intents count as calls. Each model task exposes
`input_mode`, `usage` and `request_metrics` as well as cost for the visible UI ledger.

Results may be available while running. `provisional:true` marks saved intermediate
events awaiting global reconciliation; their matched bucket is exposed as uncertain.
After reconciliation, available clips are published incrementally. Cancelled or failed
runs retain the last published snapshot. A zero-event partial result is not absence proof.

## System language

Send `Accept-Language: en` or `zh` to localize known API errors and run diagnostics.
Supported language variants and quality weights are recognized; the API defaults to Chinese
when no supported language is requested. Localized error and run-detail responses include
`Content-Language` and `Vary: Accept-Language`.

Translation is generated at read time. Stored queries, model judgments, task journals,
raw logs, timestamps, technical messages/details, and costs retain their original values.
Unknown external errors remain verbatim. Upload-limit errors include `details.max_upload_mb`.
