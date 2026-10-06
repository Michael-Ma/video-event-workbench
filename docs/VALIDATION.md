# Validation record

Validated on 2026-10-05 using Python 3.13.12, PyAV 19.0.1, FFmpeg 8.1.1,
Node.js 24.15.0, the locked Python environment and web package lock.

## Automated checks

- Python: **54 tests passed**. One dependency deprecation warning from Starlette's
  httpx test client; no failed or skipped checks in this environment.
- Python lint: passed with the repository's explicit Ruff configuration.
- Frontend: TypeScript and Vite production build passed; **4 tests passed**.

The suite covers exhaustive core ranges and failed-window gaps; observed PTS mapping;
actual VFR input with a 5-second source origin; unique frame sampling and frame caps;
rotation; source content and timestamps after real FFmpeg cuts; 0/1/N refinement;
missing candidate dispositions; conservative duplicate/conflict handling; global first/last;
request intent recovery; no automatic retry of unknown submissions; cancellation and late
responses; concurrent SQLite claims/idempotency; API upload errors and traversal denial;
HTTP video range serving; prepared-media reuse; and call-budget interruption.

Three integration cases use the real API, SQLite, synthetic video, PyAV and FFmpeg:
all intervals spanning three core windows, first point, and last point. Exported videos
are decoded and checked against their frame counts and original time metadata.
Gemini adapter checks use a mocked SDK boundary. No API key or paid call was used.

## Browser checks

The combined launcher starts all three services and shuts them down together.
The in-app browser verified demo import, submission, queue/cancellation, restored history,
two point events at 3 and 9 seconds, two intervals at 3–5 and 8–10 seconds, three-window
coverage, and clips ready for playback/download. Both source and clip video elements
loaded with no media error. Source positioning jumped to the expected 3-second location.

A one-call fixture budget produced a partial run, zero completed coverage, and the
visible 0–12-second gap. Debug records showed request, response, window plan, candidate
groups and result links. The final page had no browser error/warning messages.
Desktop and 390-pixel layouts were inspected; the narrow layout had no horizontal overflow.

![Prototype result view](screenshots/prototype-desktop.jpg)

## Pending real-video validation

The deterministic fixture has declared labels. It does **not** validate natural-language
understanding, semantic recall, real-world instance identity or boundary accuracy.
The next test requires a Gemini key and complete user videos, including repeated and
short events. Hour-long decode performance, actual provider acceptance/latency/cost,
occlusion and real camera conditions remain unmeasured. Sampled visual input and exported
clips contain no audio. These limits are also shown in the app and source design.

## Startup script follow-up

The one-command `./start.sh` was exercised on this macOS environment. It synchronized
the Python environment, installed frontend dependencies on its first run, reused them
on subsequent runs, created a missing `.env` with permissions 0600, and passed preflight.
Actual API, worker and frontend readiness was confirmed. Ctrl+C returned success and
released both listening ports; the next launch reached ready again. A duplicate startup
failed on port 8000 without stopping the existing app. Strict key mode failed clearly
because no key was configured. No model call was made.

Eight startup tests cover preserving `.env`, exported-value precedence, secret-safe
output, optional/required key behavior, invalid configuration, occupied ports and
requiring both worker and frontend readiness. The full backend suite now has **62 tests
passing**; Ruff and Bash syntax checks pass. Installing missing system runtimes from a
fresh machine was not exercised because uv, Node and FFmpeg were already installed.

## Partial-run and UI fixes 2026-10-05

The user's 54.8-second real-video run parsed the query successfully. Its first scan
attempt ended after 121.2 seconds with an unknown outcome; the configured timeout was
120 seconds. No scan core completed and no event was returned. The next window was not
submitted. The historical adapter discarded the exception type, so timeout or connection
interruption is an inference from timing, not a confirmed underlying exception.

The API now derives visible diagnostics for old and new runs without rewriting the old
journal. New provider failures retain exception type, elapsed time, timeout, frame count
and input bytes in an error artifact, without SDK exception strings that may contain keys.
The pipeline publishes snapshots after scan/refinement progress and each clip; interim
events remain uncertain until global reconciliation. Cancellation/failure retains snapshots.

Validation: **68 backend tests**, **10 frontend tests**, Ruff, TypeScript and Vite build
passed. Regression cases verify preserving the first real FFmpeg clip when a later clip
fails or the run is cancelled, historical zero-result diagnostics, blocked windows,
secret-safe timeout details, file drops, rejected files, busy uploads and cancel controls.

In the browser, the historical failure showed its 121.2-second wait and unsubmitted window.
A synthetic file was uploaded through the native chooser. An actual fixture queue showed
the rotating status indicator and a solid red cancel button; cancel changed the persisted
run to cancelled. A subsequent fixture run completed with two playable clips. Both video
elements had readyState 4 and no media error. The 390-pixel view was inspected; a hidden
file-input overflow was corrected and the document width became 375 pixels.

No historical unknown request was resubmitted and no new paid model generation was made
by this verification. Physical OS file dragging was covered through DOM drag-event tests;
the browser exercised the shared upload path through its native file chooser.
