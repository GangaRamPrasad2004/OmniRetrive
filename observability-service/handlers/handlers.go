package handlers

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"time"

	"omniretrive/observability-service/metrics"
	"omniretrive/observability-service/middleware"
	"omniretrive/observability-service/probes"
	"omniretrive/observability-service/redactor"
)

type ObservabilityHandler struct {
	Collector *metrics.MetricsCollector
	ProbeCfg  probes.ProbeConfig
	StartTime time.Time
}

func NewObservabilityHandler(collector *metrics.MetricsCollector, probeCfg probes.ProbeConfig) *ObservabilityHandler {
	return &ObservabilityHandler{
		Collector: collector,
		ProbeCfg:  probeCfg,
		StartTime: time.Now(),
	}
}

// Liveness handles GET /health and GET /healthz
func (h *ObservabilityHandler) Liveness(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"status":    "ok",
		"service":   "OmniRetrive Observability Microservice (Go)",
		"timestamp": time.Now().UTC().Format(time.RFC3339),
		"uptime_s":  int64(time.Since(h.StartTime).Seconds()),
	})
}

// Readiness handles GET /ready and GET /readyz
func (h *ObservabilityHandler) Readiness(w http.ResponseWriter, r *http.Request) {
	report := probes.CheckAll(h.ProbeCfg)
	w.Header().Set("Content-Type", "application/json")
	if report.Status == "ready" {
		w.WriteHeader(http.StatusOK)
	} else {
		w.WriteHeader(http.StatusServiceUnavailable)
	}
	_ = json.NewEncoder(w).Encode(report)
}

// PrometheusMetrics handles GET /metrics
func (h *ObservabilityHandler) PrometheusMetrics(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(h.Collector.ExportPrometheus()))
}

// JSONMetrics handles GET /metrics/json
func (h *ObservabilityHandler) JSONMetrics(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(h.Collector.GetSnapshot())
}

// LogEntryRequest represents incoming structured log data from peer microservices
type LogEntryRequest struct {
	Service       string                 `json:"service"`
	Level         string                 `json:"level"`
	Message       string                 `json:"message"`
	RequestID     string                 `json:"request_id"`
	CorrelationID string                 `json:"correlation_id"`
	WorkspaceID   string                 `json:"workspace_id"`
	DocumentID    string                 `json:"document_id"`
	JobID         string                 `json:"job_id"`
	Extra         map[string]interface{} `json:"extra"`
}

// IngestLog handles POST /telemetry/logs
func (h *ObservabilityHandler) IngestLog(w http.ResponseWriter, r *http.Request) {
	var entry LogEntryRequest
	if err := json.NewDecoder(r.Body).Decode(&entry); err != nil {
		http.Error(w, `{"error":"Invalid JSON log payload"}`, http.StatusBadRequest)
		return
	}

	if entry.Level == "" {
		entry.Level = "INFO"
	}
	if entry.Service == "" {
		entry.Service = "remote-service"
	}
	if entry.RequestID == "" {
		entry.RequestID = middleware.GetRequestID(r.Context())
	}
	if entry.CorrelationID == "" {
		entry.CorrelationID = middleware.GetCorrelationID(r.Context())
	}

	sanitizedMsg := redactor.RedactSecrets(entry.Message)
	sanitizedExtra := redactor.SanitizePayload(entry.Extra, 150)

	logOutput := map[string]interface{}{
		"timestamp":      time.Now().UTC().Format(time.RFC3339),
		"level":          entry.Level,
		"service":        entry.Service,
		"message":        sanitizedMsg,
		"request_id":     entry.RequestID,
		"correlation_id": entry.CorrelationID,
	}
	if entry.WorkspaceID != "" {
		logOutput["workspace_id"] = entry.WorkspaceID
	}
	if entry.DocumentID != "" {
		logOutput["document_id"] = entry.DocumentID
	}
	if entry.JobID != "" {
		logOutput["job_id"] = entry.JobID
	}
	if len(sanitizedExtra) > 0 {
		logOutput["extra"] = sanitizedExtra
	}

	jsonBytes, _ := json.Marshal(logOutput)
	log.Println(string(jsonBytes))

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"status":     "ingested",
		"request_id": entry.RequestID,
	})
}

// MetricEventRequest represents incoming operational metric events from peer microservices
type MetricEventRequest struct {
	Type             string  `json:"type"` // "http", "ingestion", "embedding", "retrieval", "llm"
	Method           string  `json:"method,omitempty"`
	Endpoint         string  `json:"endpoint,omitempty"`
	StatusCode       int     `json:"status_code,omitempty"`
	DurationMs       float64 `json:"duration_ms"`
	Success          bool    `json:"success"`
	ErrorReason      string  `json:"error_reason,omitempty"`
	Chunks           int64   `json:"chunks,omitempty"`
	PromptTokens     int64   `json:"prompt_tokens,omitempty"`
	CompletionTokens int64   `json:"completion_tokens,omitempty"`
	CostUSD          float64 `json:"cost_usd,omitempty"`
}

// RecordMetric handles POST /telemetry/metrics
func (h *ObservabilityHandler) RecordMetric(w http.ResponseWriter, r *http.Request) {
	var ev MetricEventRequest
	if err := json.NewDecoder(r.Body).Decode(&ev); err != nil {
		http.Error(w, `{"error":"Invalid JSON metric payload"}`, http.StatusBadRequest)
		return
	}

	switch ev.Type {
	case "http":
		h.Collector.RecordHTTPRequest(ev.Method, ev.Endpoint, ev.StatusCode, ev.DurationMs)
	case "ingestion":
		h.Collector.RecordIngestion(ev.Success, ev.DurationMs, ev.ErrorReason)
	case "embedding":
		h.Collector.RecordEmbedding(ev.Chunks, ev.DurationMs, ev.Success)
	case "retrieval":
		h.Collector.RecordRetrieval(ev.Chunks, ev.DurationMs)
	case "llm":
		h.Collector.RecordLLM(ev.DurationMs, ev.PromptTokens, ev.CompletionTokens, ev.Success, ev.CostUSD)
	default:
		http.Error(w, fmt.Sprintf(`{"error":"Unknown metric type: %s"}`, ev.Type), http.StatusBadRequest)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"status": "recorded",
		"type":   ev.Type,
	})
}

// Summary handles GET /telemetry/summary
func (h *ObservabilityHandler) Summary(w http.ResponseWriter, r *http.Request) {
	snap := h.Collector.GetSnapshot()
	readiness := probes.CheckAll(h.ProbeCfg)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"service":          "OmniRetrive Observability Microservice (Go)",
		"status":           readiness.Status,
		"uptime_seconds":   int64(time.Since(h.StartTime).Seconds()),
		"total_requests":   snap.HTTPRequests.Total,
		"error_rate_pct":   snap.HTTPRequests.ErrorRatePct,
		"avg_latency_ms":   snap.HTTPRequests.LatencyMs.Avg,
		"p95_latency_ms":   snap.HTTPRequests.LatencyMs.P95,
		"ingestion_jobs":   snap.Ingestion.TotalJobs,
		"ingestion_failed": snap.Ingestion.Failed,
		"llm_tokens":       snap.LLM.TotalTokens,
		"llm_cost_usd":     snap.LLM.EstimatedCostUSD,
		"dependencies":     readiness.Dependencies,
	})
}
