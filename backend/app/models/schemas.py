from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

from app.config import PLANT_TIMEZONE


class DetectCyclesRequest(BaseModel):
    field: str
    tag_id: str
    start_time: str
    stop_time: str
    bucket: Optional[str] = None
    measurement: Optional[str] = None
    smooth_window: int = 9
    source_unit: str = "bar"


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
    detection_fingerprint: str


class SelectedCycleBoundary(BaseModel):
    start: str
    end: str


class BuildBenchmarkRequest(BaseModel):
    field: str
    tag_id: str
    start_time: str
    stop_time: str
    bucket: Optional[str] = None
    measurement: Optional[str] = None
    smooth_window: int = 9
    selected_cycle_indices: List[int] = Field(default_factory=list)
    selected_cycle_boundaries: List[SelectedCycleBoundary] = Field(default_factory=list)
    detection_fingerprint: str = ""
    normalized_points: int = 300
    benchmark_name: str = "default_benchmark"
    source_unit: str = "bar"


class BenchmarkResponse(BaseModel):
    benchmark: Dict[str, Any]
    normalized_cycles: List[List[float]]


class BenchmarkListItem(BaseModel):
    benchmark_id: Optional[str] = None
    database_id: Optional[str] = None
    file_name: str
    benchmark_name: Optional[str] = None
    id: Optional[str] = None
    tag_id: Optional[str] = None
    sterilizer_id: Optional[str] = None
    field: Optional[str] = None
    measurement: Optional[str] = None
    unit: Optional[str] = None
    source_unit: str = "bar"
    benchmark_unit: Optional[str] = None
    source_cycle_count: Optional[int] = None
    normalized_points: Optional[int] = None
    created_at: Optional[str] = None
    adjusted_from: Optional[str] = None
    adjusted_from_file_name: Optional[str] = None
    validation_status: str = "valid"
    validation_error: Optional[str] = None


class BenchmarkListResponse(BaseModel):
    benchmarks: List[BenchmarkListItem]


class BenchmarkLoadResponse(BaseModel):
    benchmark: Dict[str, Any]


class CompareCyclesRequest(BaseModel):
    field: str
    tag_id: str
    start_time: str
    stop_time: str
    bucket: Optional[str] = None
    measurement: Optional[str] = None
    source_unit: str = "bar"
    smooth_window: int = 9
    benchmark_file_name: Optional[str] = None


class LiveMonitorRequest(BaseModel):
    tag_id: str
    bucket: Optional[str] = None
    measurement: Optional[str] = None
    source_unit: str = "bar"
    smooth_window: int = 9

    # Moving recent time window
    last_n_hours: Optional[int] = 3

    # Optional custom range. When one value is supplied, both are required.
    start_time: Optional[str] = None
    stop_time: Optional[str] = None


class DailyReportShiftItem(BaseModel):
    label: str
    start: str
    end: str


class DailyReportShiftSettings(BaseModel):
    morning: DailyReportShiftItem
    night: DailyReportShiftItem


class DailyReportPlantSetting(BaseModel):
    data_id: str
    # ``display_name`` remains for older clients. New clients send the
    # operator-entered alias separately so an official name is never saved as
    # a custom value by accident.
    display_name: Optional[str] = None
    custom_display_name: Optional[str] = None
    official_name: Optional[str] = None


class DailyReportSiteSetting(BaseModel):
    site_code: str
    display_name: str
    shifts: DailyReportShiftSettings
    plants: List[DailyReportPlantSetting] = Field(default_factory=list)


class DailyReportSettingsRequest(BaseModel):
    version: int = 0
    timezone: str = PLANT_TIMEZONE
    sites: List[DailyReportSiteSetting] = Field(default_factory=list)
    active_benchmarks: Dict[str, Dict[str, str]] = Field(default_factory=dict)


class DailyReportGenerateRequest(BaseModel):
    site_code: str
    data_id: str
    report_date: str
    smooth_window: int = 9


class CompareCycleResult(BaseModel):
    cycle_no: int
    cycle_start: str
    cycle_end: str
    duration_seconds: float
    score: float
    classification: str
    score_band: Optional[str] = None
    feedback_messages: List[str]
    score_breakdown: Dict[str, Optional[float]]
    metrics: Dict[str, Any]
    visual_overlay_chart: List[Dict[str, Any]]
    overlay_chart: List[Dict[str, Any]]
    original_cycle_chart: List[Dict[str, Any]]
    rca_feedback: Optional[Dict[str, Any]] = None


class CompareCyclesResponse(BaseModel):
    benchmark_name: Optional[str] = None
    benchmark_file_name: Optional[str] = None
    benchmark_id: Optional[str] = None
    benchmark_field: Optional[str] = None
    benchmark_unit: Optional[str] = None
    source_measurement: Optional[str] = None
    source_unit: Optional[str] = None
    cycle_results: List[CompareCycleResult]
    continuous_overlay_chart: List[Dict[str, Any]]
