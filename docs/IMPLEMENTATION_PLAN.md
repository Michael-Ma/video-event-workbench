# Implementation plan

Source design: https://chatgpt.com/space/page_c6399f3467e88191aaf8beb5a250cb29

Scope: video + natural-language query → automatic event localization, clips, state,
and debug records. No annotation editing, phase labels, summaries, or training UI.

Dependency order:

1. Root: shared Pydantic contracts, SQLite repository, API surface and configuration.
2. In parallel after contracts exist:
   - Media: probe, original frame index, samples, accurate cuts, synthetic demo.
   - Pipeline: Gemini/fixture adapters, window planning, candidate groups, refinement,
     conservative reconciliation, cancellation and durable state.
   - Web: input, run progress, read-only results, clips and debug view.
3. Root: integrate, run unit/media/API/browser checks, document actual validation,
   append implementation decisions to the source design, push private GitHub repository.

The deterministic fixture provider is only an engineering test. It must be labeled
in every UI/result and never presented as measured VLM accuracy.

