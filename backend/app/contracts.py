from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EventKind = Literal["point", "interval"]
Decision = Literal["supported", "rejected", "unresolved"]
InputMode = Literal["images", "video"]


class QuerySpec(BaseModel):
    raw_query: str
    event_kind: EventKind = "interval"
    target_description: str = ""
    required_evidence: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    occurrence_policy: Literal["all", "first", "last"] = "all"
    anchor_rule: str | None = None
    start_rule: str | None = None
    end_rule: str | None = None
    defaults_used: list[str] = Field(default_factory=list)


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["gemini", "fixture"] = "gemini"
    model_id: str = "gemini-3.8-flash"
    profile: Literal["auto", "action", "point"] = "auto"
    scan_input_mode: InputMode = "images"
    refine_input_mode: InputMode = "images"
    model_concurrency: int = Field(default=2, ge=1, le=8)
    clip_concurrency: int = Field(default=2, ge=1, le=4)
    core_window_s: float | None = Field(default=None, gt=0, le=120)
    context_s: float | None = Field(default=None, ge=0, le=20)
    scan_fps: float | None = Field(default=None, gt=0, le=30)
    refine_fps: float = Field(default=6, gt=0, le=60)
    max_input_frames: int = Field(default=160, ge=16, le=512)
    max_frame_width: int = Field(default=768, ge=256, le=1920)
    max_calls: int = Field(default=500, ge=1, le=5000)
    max_output_tokens: int = Field(default=16384, ge=512, le=32768)
    thinking_level: Literal["low", "medium", "high", "default"] = "low"
    temperature: float = Field(default=1, ge=0, le=2)
    request_timeout_s: int = Field(default=1200, ge=10, le=3600)
    max_refinement_expansions: int = Field(default=2, ge=0, le=4)
    refine_padding_s: float = Field(default=2, ge=0, le=15)
    max_refine_window_s: float = Field(default=120, gt=0, le=600)
    boundary_tolerance_ms: int = Field(default=1000, ge=1, le=10000)
    clip_before_s: float = Field(default=0.5, ge=0, le=15)
    clip_after_s: float = Field(default=0.5, ge=0, le=15)
    point_before_s: float = Field(default=1, ge=0, le=15)
    point_after_s: float = Field(default=1, ge=0, le=15)
    export_uncertain: bool = True
    max_events_per_window: int = Field(default=64, ge=1, le=256)

    @model_validator(mode="after")
    def check_video_sampling(self) -> "RunConfig":
        if self.scan_input_mode == "video" and self.scan_fps is not None and self.scan_fps > 24:
            raise ValueError("Native-video scan FPS must be at most 24")
        if self.refine_input_mode == "video" and self.refine_fps > 24:
            raise ValueError("Native-video refine FPS must be at most 24")
        if (self.provider == "gemini" and self.thinking_level != "default" and
                not self.model_id.removeprefix("models/").startswith("gemini-3")):
            raise ValueError("Choose provider-default thinking for models outside the Gemini 3 family")
        return self


class CreateRunRequest(BaseModel):
    media_id: str
    query: str = Field(min_length=2, max_length=4000)
    config: RunConfig = Field(default_factory=RunConfig)


class Window(BaseModel):
    window_id: str
    core_start_us: int
    core_end_us: int
    read_start_us: int
    read_end_us: int
    sample_fps: float


class Location(BaseModel):
    kind: EventKind
    start_us: int | None = None
    end_us: int | None = None
    start_range_us: tuple[int, int] | None = None
    end_range_us: tuple[int, int] | None = None
    anchor_us: int | None = None
    anchor_range_us: tuple[int, int] | None = None
    open_left: bool = False
    open_right: bool = False

    @model_validator(mode="after")
    def check_shape(self) -> "Location":
        for key in ("start_us", "end_us", "anchor_us"):
            value = getattr(self, key)
            if value is not None and value < 0:
                raise ValueError(f"{key} must not be negative")
        for key in ("start_range_us", "end_range_us", "anchor_range_us"):
            value = getattr(self, key)
            if value is not None and (value[0] < 0 or value[0] > value[1]):
                raise ValueError(f"invalid {key}")
        if self.kind == "point":
            if self.start_us is not None or self.end_us is not None:
                raise ValueError("a point must not be represented as an interval")
        elif self.start_us is not None and self.end_us is not None:
            if self.start_us >= self.end_us:
                raise ValueError("start must be earlier than end")
        return self


class Candidate(BaseModel):
    candidate_id: str
    window_id: str
    location: Location
    entity_key: str = "unknown"
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str = ""


class ModelEvent(BaseModel):
    """Model times are seconds relative to the actual submitted input timeline."""
    kind: EventKind
    decision: Decision = "supported"
    start_s: float | None = None
    end_s: float | None = None
    start_range_s: tuple[float, float] | None = None
    end_range_s: tuple[float, float] | None = None
    anchor_s: float | None = None
    anchor_range_s: tuple[float, float] | None = None
    open_left: bool = False
    open_right: bool = False
    entity_key: str = "unknown"
    evidence_frame_ids: list[str] = Field(default_factory=list)
    evidence_times_s: list[float] = Field(default_factory=list)
    reason: str = ""
    uncertainty_reasons: list[str] = Field(default_factory=list)


class CandidateDisposition(BaseModel):
    candidate_id: str
    disposition: Literal["mapped_to_event", "rejected", "unresolved"]
    event_indices: list[int] = Field(default_factory=list)
    reason: str = ""


class ModelResponse(BaseModel):
    complete: bool = True
    events: list[ModelEvent] = Field(default_factory=list)
    candidate_dispositions: list[CandidateDisposition] = Field(default_factory=list)


class ClipResult(BaseModel):
    path: str
    url: str | None = None
    cut_range_us: tuple[int, int]
    actual_range_us: tuple[int, int] | None = None
    kind: Literal["event_with_context", "context_fallback"]
    metadata: dict = Field(default_factory=dict)


class EventResult(BaseModel):
    event_id: str
    source_candidate_ids: list[str] = Field(default_factory=list)
    decision: Decision
    boundary_status: Literal["bounded", "open", "unknown"]
    result_bucket: Literal["matched", "uncertain", "rejected"]
    location: Location
    entity_key: str = "unknown"
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str = ""
    uncertainty_reasons: list[str] = Field(default_factory=list)
    duplicate_of: str | None = None
    clip: ClipResult | None = None
    clip_status: Literal["pending", "succeeded", "failed", "not_required"] = "pending"
