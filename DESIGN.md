# Video event workbench — UI design

## Scope and source of truth

The functional prototype serves an engineer inspecting automatic event extraction from long videos. The authoritative product scope is `docs/design.md`; wire contracts are `docs/API.md` and `backend/app/contracts.py`.

The primary path is import video → enter a natural-language event query → run systematic processing → inspect coverage and matched/uncertain/rejected events → play/download clips and inspect diagnostic artifacts. There are no manual annotation, scoring, phase-editing, training, or per-clip summary controls in this version.

All media, histories, task states, results, and logs come from the API. The UI contains no fabricated example results. The built-in demo is explicitly selected and labeled. Fixture mode uses deterministic engineering responses, is only available for demo media, and is prominently identified as non-AI recognition. Gemini mode says sampled video content is sent to the model API. Credentials are configured on the backend; the UI only receives a boolean status.

## Direction

A restrained video processing desk: persistent inputs on the left; source footage, responsible-window coverage, and result evidence on the right. The footage is the largest visual element. Sparse green accents identify actions and completed processing, amber identifies unresolved evidence, and red identifies infrastructure failures. Status names always accompany color.

No visual reference was supplied. The layout is original and follows familiar media tooling conventions. Rejected alternatives: a dashboard of fabricated aggregate metrics; a full annotation timeline beyond scope; decorative model-confidence percentages that imply calibration.

## Tokens and responsive behavior

- Canvas `#f4f5f3`, surface white, text `#24322e`, muted text `#6c7770`, borders `#dde3dc`.
- Action green `#28634d`, amber text `#916219` on `#fff6e5`, error text `#ac493a`.
- System sans stack with PingFang SC/Microsoft YaHei; system monospace for source times and artifact IDs. No font downloads.
- Main spacing: 8/12/18/24/32 px; modest 6–10 px corner radii; minimum decorative shadow.
- Desktop: 340 px input rail and fluid content. Intermediate: result cards become a compact grid above detail. Mobile: input precedes workspace, controls expand, results remain readable without page-level horizontal scrolling.

## Interaction states

Empty, media upload, service unavailable, missing API key, queued, running, completed with/without events, partial, failed, and cancelled are explicit. Uncertain results remain in the default all-events view. Rejected candidates remain inspectable but do not masquerade as playable clips. Context-fallback clips have a visible warning. Scan completion is never labeled as complete event recall.

Run selections survive reload via a local run ID; canonical records remain on the backend. Each run displays its frozen query and model. Draft input edits do not rewrite historical runs. Incremental logs include request/response artifact links where provided by the API. Cancellation stops the run via the API and discloses that in-flight calls may still incur cost.

## Verification plan

- Build and focused tests for microsecond formatting, point-vs-interval semantics, responsible-window ranges, and artifact URL handling.
- Browser integration with actual backend: built-in demo → fixture run → coverage → filters → clip playback/download → raw artifacts; missing-key and unavailable-backend states.
- Desktop and narrow mobile visual review, keyboard focus, long query/file-name wrapping, and no fake live content.

Browser integration and rendered visual review are owned by the root integration pass; this document does not claim those checks passed before they run.

## Run status and recovery refinement

The working area uses neutral gray before a run, blue during queued/running work, green
on completion, amber for partial work, red for failure and gray for cancellation. Labels,
phase markers and a motion indicator accompany color. The top status panel states the
current/stopped phase, responsible windows, saved events/clips and scan coverage. It shows
the primary failure and blocked windows directly, with raw diagnostics expandable. Zero
events after interruption is described as no available result, not a successful empty scan.

Starting or selecting a run brings the panel into view; saved results have a direct jump
link. Cancellation is a solid red 44-pixel control. Motion and scrolling respect reduced
motion. The upload area accepts actual file drops, changes state during drag/upload, shares
validation with the picker and rejects multiple/non-video files. Existing framework,
semantic event buckets and original-video time mapping are preserved.

## Independent model inputs and per-call cost visibility

The existing workbench now exposes independent `scan / propose` and
`refine / verify_refine` input selectors. Both default to `images`; `video` is a
locally cropped, silent input with configured static FPS sampling, not a promise
of continuous frame-by-frame inference. The draft displays the action/point scan
defaults (2/6 FPS), while a separate read-only strip shows each run's frozen
combination and actual planned FPS when available. Old records without input-mode
fields say “未记录” rather than receiving today's defaults. Advanced settings add
model/clip concurrency (2/2), with refine FPS 6 and request timeout 1200 seconds.

The visible cost panel follows coverage, keeping the existing video-first layout.
It separates a known estimated subtotal from unknown/unrecorded, in-flight, and
local Fixture calls. A per-call table retains operation, input mode, request
status, latency, four token counters, estimate, and expandable price provenance.
Cached input is identified as a subset of input; thinking is separate from output.
Unknown and legacy values are never represented as zero. Cancelling a run does
not erase recorded charges; polling continues while submitted requests are still
waiting for a receipt. Canonical task receipts take precedence, and log fallbacks
are deduplicated by attempt. Estimates are explicitly standard-list estimates,
not invoices or predictions of an account's free-tier treatment.

At narrow widths the same cost table becomes labeled stacked rows, retaining all
fields and accessible details controls. No new media, annotations, or model
outputs are fabricated by the application.

### Verification for this refinement (2026-10-05)

- RTL exercises independent selections, the actual POST configuration, frozen
  display after draft changes, historical defaults, cost states, and late cost
  receipts after cancellation. Pure logic tests cover task/log deduplication,
  known/unknown/missing/Fixture accounting and token preservation.
- A separate real Chrome check used intercepted local API fixtures at 1440×1000
  and 390×844: no page overflow, mobile cost cells inside the viewport, 45px select
  targets, editable independent inputs, preserved frozen run, expandable price
  provenance, and no page errors. The desktop/mobile screenshots were actually
  inspected. This checks presentation and interaction, not a live model/backend.
- Live provider calls and final backend integration remain the root integration
  pass. This frontend work did not call Gemini or change backend contracts.
## Final comparison controls and runtime 2026 10 05

Advanced controls expose thinking low, a 16384-token output budget and temperature 1.
Frozen configuration and recent-run labels distinguish input combinations; timing separates
queue wait from processing. The per-call cost ledger excludes local clip attempts. Actual
Gemini outputs and playable clips were checked at the default desktop width and 390px;
there was no horizontal document overflow. Cost estimates remain separate from account bills.
