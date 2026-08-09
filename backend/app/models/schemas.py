from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any


class DetectCyclesRequest(BaseModel):
    bucket: str
    measurement: str
    field: str
    tag_id: str
    start_time: str
    stop_time: str
    smooth_window: int = 9
    source_unit: Optional[str] = None


class CycleInfo(BaseModel):
    cycle_no: int
    start: str
    end: str
    num_points_original: int
    duration_seconds: float
    max_value: float
    min_value: float
    source_unit: Optional[str] = None
    benchmark_unit: Optional[str] = None


class DetectCyclesResponse(BaseModel):
    times: List[str]
    values: List[float]
    smooth: List[float]
    baseline_value: float
    baseline_margin: float
    threshold: float
    cycles: List[CycleInfo]
    source_unit: Optional[str] = None
    benchmark_unit: Optional[str] = None


class BuildBenchmarkRequest(BaseModel):
    bucket: str
    measurement: str
    field: str
    tag_id: str
    start_time: str
    stop_time: str
    smooth_window: int = 9
    selected_cycle_indices: List[int] = Field(default_factory=list)
    normalized_points: int = 300
    benchmark_name: str = "default_benchmark"
    source_unit: Optional[str] = None


class BenchmarkResponse(BaseModel):
    benchmark: Dict[str, Any]
    normalized_cycles: List[List[float]]


class BenchmarkListItem(BaseModel):
    file_name: str
    benchmark_name: Optional[str] = None
    id: Optional[str] = None
    field: Optional[str] = None
    measurement: Optional[str] = None
    unit: Optional[str] = None
    source_cycle_count: Optional[int] = None
    normalized_points: Optional[int] = None
    created_at: Optional[str] = None


class BenchmarkListResponse(BaseModel):
    benchmarks: List[BenchmarkListItem]


class BenchmarkLoadResponse(BaseModel):
    benchmark: Dict[str, Any]


class CompareCyclesRequest(BaseModel):
    bucket: str
    measurement: str
    field: str
    tag_id: str
    source_unit: Optional[str] = None
    start_time: str
    stop_time: str
    smooth_window: int = 9
    benchmark_file_name: str


class LiveMonitorRequest(BaseModel):
    bucket: str = "Mill"
    measurement: str = "PSTR"
    tag_id: str
    source_unit: str = "bar"
    smooth_window: int = 9

    # Moving recent time window
    last_n_hours: Optional[int] = 3

    # Optional custom datetime range, kept for future use
    start_time: Optional[str] = None
    stop_time: Optional[str] = None


class CompareCycleResult(BaseModel):
    cycle_no: int
    cycle_start: str
    cycle_end: str
    duration_seconds: float
    score: float
    classification: str
    score_band: Optional[str] = None
    feedback_messages: List[str]
    score_breakdown: Dict[str, float]
    metrics: Dict[str, Any]
    visual_overlay_chart: List[Dict[str, Any]]
    overlay_chart: List[Dict[str, Any]]
    original_cycle_chart: List[Dict[str, Any]]
    rca_feedback: Optional[Dict[str, Any]] = None


class CompareCyclesResponse(BaseModel):
    benchmark_name: Optional[str] = None
    benchmark_id: Optional[str] = None
    benchmark_field: Optional[str] = None
    benchmark_unit: Optional[str] = None
    cycle_results: List[CompareCycleResult]
    continuous_overlay_chart: List[Dict[str, Any]]
