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

Errors: `{error:{code,message,retryable?,details?}}`. Missing key is a settings
error, not an empty event list. Fixture mode is allowed only on the built-in demo.

Run detail includes `diagnostics`: issues with stage, code, human-readable title/message,
action, source range, safe details and artifact refs, plus stopped_stage and available
event/clip counts. Older journals are interpreted at read time without mutation.
Task errors can expose `error_details` and `error_path`.

Results may be available while running. `provisional:true` marks saved intermediate
events awaiting global reconciliation; their matched bucket is exposed as uncertain.
After reconciliation, available clips are published incrementally. Cancelled or failed
runs retain the last published snapshot. A zero-event partial result is not absence proof.
