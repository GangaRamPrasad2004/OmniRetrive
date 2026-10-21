package metrics

import (
	"fmt"
	"math"
	"sort"
	"strings"
	"sync"
)

type LatencyStats struct {
	Count int     `json:"count"`
	Avg   float64 `json:"avg"`
	P50   float64 `json:"p50"`
	P95   float64 `json:"p95"`
	Max   float64 `json:"max"`
}

type MetricsSnapshot struct {
	HTTPRequests struct {
		Total               int64            `json:"total"`
		Errors              int64            `json:"errors"`
		ErrorRatePct        float64          `json:"error_rate_pct"`
		ByEndpointAndStatus map[string]int64 `json:"by_endpoint_and_status"`
		LatencyMs           LatencyStats     `json:"latency_ms"`
	} `json:"http_requests"`
	Ingestion struct {
		TotalJobs        int64            `json:"total_jobs"`
		Successful       int64            `json:"successful"`
		Failed           int64            `json:"failed"`
		FailuresByReason map[string]int64 `json:"failures_by_reason"`
		DurationMs       LatencyStats     `json:"duration_ms"`
	} `json:"ingestion"`
	Embedding struct {
		Requests             int64        `json:"requests"`
		Failures             int64        `json:"failures"`
		TotalChunksEmbedded  int64        `json:"total_chunks_embedded"`
		LatencyMs            LatencyStats `json:"latency_ms"`
	} `json:"embedding"`
	Retrieval struct {
		Queries              int64        `json:"queries"`
		TotalChunksRetrieved int64        `json:"total_chunks_retrieved"`
		AvgChunksPerQuery    float64      `json:"avg_chunks_per_query"`
		LatencyMs            LatencyStats `json:"latency_ms"`
	} `json:"retrieval"`
	LLM struct {
		Calls                 int64        `json:"calls"`
		Failures              int64        `json:"failures"`
		PromptTokensTotal     int64        `json:"prompt_tokens_total"`
		CompletionTokensTotal int64        `json:"completion_tokens_total"`
		TotalTokens           int64        `json:"total_tokens"`
		EstimatedCostUSD      float64      `json:"estimated_cost_usd"`
		LatencyMs             LatencyStats `json:"latency_ms"`
	} `json:"llm"`
}

type MetricsCollector struct {
	mu sync.RWMutex

	httpRequestsTotal   map[string]int64
	httpDurations       []float64

	ingestionTotal           int64
	ingestionSuccessTotal    int64
	ingestionFailuresTotal   int64
	ingestionDurations       []float64
	ingestionFailuresReason  map[string]int64

	embeddingRequestsTotal   int64
	embeddingFailuresTotal   int64
	embeddingChunksTotal     int64
	embeddingDurations       []float64

	retrievalRequestsTotal   int64
	retrievalChunksTotal     int64
	retrievalDurations       []float64

	llmRequestsTotal         int64
	llmFailuresTotal         int64
	llmDurations             []float64
	llmPromptTokensTotal     int64
	llmCompletionTokensTotal int64
	llmEstimatedCostUSD      float64
}

func NewMetricsCollector() *MetricsCollector {
	return &MetricsCollector{
		httpRequestsTotal:       make(map[string]int64),
		httpDurations:           make([]float64, 0, 1000),
		ingestionFailuresReason: make(map[string]int64),
		ingestionDurations:      make([]float64, 0, 500),
		embeddingDurations:      make([]float64, 0, 500),
		retrievalDurations:      make([]float64, 0, 500),
		llmDurations:            make([]float64, 0, 500),
	}
}

func (m *MetricsCollector) RecordHTTPRequest(method, endpoint string, statusCode int, durationMs float64) {
	key := fmt.Sprintf("%s:%s:%d", method, endpoint, statusCode)
	m.mu.Lock()
	defer m.mu.Unlock()

	m.httpRequestsTotal[key]++
	m.httpDurations = append(m.httpDurations, durationMs)
	if len(m.httpDurations) > 1000 {
		m.httpDurations = m.httpDurations[1:]
	}
}

func (m *MetricsCollector) RecordIngestion(success bool, durationMs float64, errorReason string) {
	m.mu.Lock()
	defer m.mu.Unlock()

	m.ingestionTotal++
	if success {
		m.ingestionSuccessTotal++
	} else {
		m.ingestionFailuresTotal++
		if errorReason == "" {
			errorReason = "unknown"
		}
		m.ingestionFailuresReason[errorReason]++
	}
	m.ingestionDurations = append(m.ingestionDurations, durationMs)
	if len(m.ingestionDurations) > 500 {
		m.ingestionDurations = m.ingestionDurations[1:]
	}
}

func (m *MetricsCollector) RecordEmbedding(numChunks int64, durationMs float64, success bool) {
	m.mu.Lock()
	defer m.mu.Unlock()

	m.embeddingRequestsTotal++
	if success {
		m.embeddingChunksTotal += numChunks
		m.embeddingDurations = append(m.embeddingDurations, durationMs)
		if len(m.embeddingDurations) > 500 {
			m.embeddingDurations = m.embeddingDurations[1:]
		}
	} else {
		m.embeddingFailuresTotal++
	}
}

func (m *MetricsCollector) RecordRetrieval(chunksRetrieved int64, durationMs float64) {
	m.mu.Lock()
	defer m.mu.Unlock()

	m.retrievalRequestsTotal++
	m.retrievalChunksTotal += chunksRetrieved
	m.retrievalDurations = append(m.retrievalDurations, durationMs)
	if len(m.retrievalDurations) > 500 {
		m.retrievalDurations = m.retrievalDurations[1:]
	}
}

func (m *MetricsCollector) RecordLLM(durationMs float64, promptTokens, completionTokens int64, success bool, costUSD float64) {
	m.mu.Lock()
	defer m.mu.Unlock()

	m.llmRequestsTotal++
	if success {
		m.llmDurations = append(m.llmDurations, durationMs)
		if len(m.llmDurations) > 500 {
			m.llmDurations = m.llmDurations[1:]
		}
		m.llmPromptTokensTotal += promptTokens
		m.llmCompletionTokensTotal += completionTokens
		m.llmEstimatedCostUSD += costUSD
	} else {
		m.llmFailuresTotal++
	}
}

func calcStats(values []float64) LatencyStats {
	n := len(values)
	if n == 0 {
		return LatencyStats{Count: 0, Avg: 0, P50: 0, P95: 0, Max: 0}
	}
	sorted := make([]float64, n)
	copy(sorted, values)
	sort.Float64s(sorted)

	sum := 0.0
	for _, v := range sorted {
		sum += v
	}
	avg := sum / float64(n)
	p50 := sorted[int(float64(n)*0.50)]
	p95Idx := int(float64(n) * 0.95)
	if p95Idx >= n {
		p95Idx = n - 1
	}
	p95 := sorted[p95Idx]
	maxVal := sorted[n-1]

	return LatencyStats{
		Count: n,
		Avg:   round2(avg),
		P50:   round2(p50),
		P95:   round2(p95),
		Max:   round2(maxVal),
	}
}

func round2(val float64) float64 {
	return math.Round(val*100) / 100
}

func (m *MetricsCollector) GetSnapshot() MetricsSnapshot {
	m.mu.RLock()
	defer m.mu.RUnlock()

	var totalReqs int64
	var errorReqs int64
	httpCopy := make(map[string]int64, len(m.httpRequestsTotal))
	for k, v := range m.httpRequestsTotal {
		httpCopy[k] = v
		totalReqs += v
		parts := strings.Split(k, ":")
		if len(parts) == 3 {
			var code int
			fmt.Sscanf(parts[2], "%d", &code)
			if code >= 400 {
				errorReqs += v
			}
		}
	}

	errorRate := 0.0
	if totalReqs > 0 {
		errorRate = round2(float64(errorReqs) / float64(totalReqs) * 100.0)
	}

	reasonCopy := make(map[string]int64, len(m.ingestionFailuresReason))
	for k, v := range m.ingestionFailuresReason {
		reasonCopy[k] = v
	}

	avgChunks := 0.0
	if m.retrievalRequestsTotal > 0 {
		avgChunks = round2(float64(m.retrievalChunksTotal) / float64(m.retrievalRequestsTotal))
	}

	var snap MetricsSnapshot
	snap.HTTPRequests.Total = totalReqs
	snap.HTTPRequests.Errors = errorReqs
	snap.HTTPRequests.ErrorRatePct = errorRate
	snap.HTTPRequests.ByEndpointAndStatus = httpCopy
	snap.HTTPRequests.LatencyMs = calcStats(m.httpDurations)

	snap.Ingestion.TotalJobs = m.ingestionTotal
	snap.Ingestion.Successful = m.ingestionSuccessTotal
	snap.Ingestion.Failed = m.ingestionFailuresTotal
	snap.Ingestion.FailuresByReason = reasonCopy
	snap.Ingestion.DurationMs = calcStats(m.ingestionDurations)

	snap.Embedding.Requests = m.embeddingRequestsTotal
	snap.Embedding.Failures = m.embeddingFailuresTotal
	snap.Embedding.TotalChunksEmbedded = m.embeddingChunksTotal
	snap.Embedding.LatencyMs = calcStats(m.embeddingDurations)

	snap.Retrieval.Queries = m.retrievalRequestsTotal
	snap.Retrieval.TotalChunksRetrieved = m.retrievalChunksTotal
	snap.Retrieval.AvgChunksPerQuery = avgChunks
	snap.Retrieval.LatencyMs = calcStats(m.retrievalDurations)

	snap.LLM.Calls = m.llmRequestsTotal
	snap.LLM.Failures = m.llmFailuresTotal
	snap.LLM.PromptTokensTotal = m.llmPromptTokensTotal
	snap.LLM.CompletionTokensTotal = m.llmCompletionTokensTotal
	snap.LLM.TotalTokens = m.llmPromptTokensTotal + m.llmCompletionTokensTotal
	snap.LLM.EstimatedCostUSD = math.Round(m.llmEstimatedCostUSD*1000000) / 1000000
	snap.LLM.LatencyMs = calcStats(m.llmDurations)

	return snap
}

func (m *MetricsCollector) ExportPrometheus() string {
	snap := m.GetSnapshot()

	var sb strings.Builder
	sb.WriteString("# HELP omniretrive_http_requests_total Total number of HTTP requests processed\n")
	sb.WriteString("# TYPE omniretrive_http_requests_total counter\n")
	for k, count := range snap.HTTPRequests.ByEndpointAndStatus {
		parts := strings.Split(k, ":")
		if len(parts) == 3 {
			sb.WriteString(fmt.Sprintf("omniretrive_http_requests_total{method=\"%s\",endpoint=\"%s\",status=\"%s\"} %d\n", parts[0], parts[1], parts[2], count))
		}
	}
	sb.WriteString(fmt.Sprintf("omniretrive_http_error_rate_percent %f\n", snap.HTTPRequests.ErrorRatePct))
	sb.WriteString(fmt.Sprintf("omniretrive_http_request_latency_ms_avg %f\n", snap.HTTPRequests.LatencyMs.Avg))
	sb.WriteString(fmt.Sprintf("omniretrive_http_request_latency_ms_p95 %f\n\n", snap.HTTPRequests.LatencyMs.P95))

	sb.WriteString("# HELP omniretrive_ingestion_jobs_total Total document ingestion jobs\n")
	sb.WriteString("# TYPE omniretrive_ingestion_jobs_total counter\n")
	sb.WriteString(fmt.Sprintf("omniretrive_ingestion_jobs_total %d\n", snap.Ingestion.TotalJobs))
	sb.WriteString(fmt.Sprintf("omniretrive_ingestion_jobs_success %d\n", snap.Ingestion.Successful))
	sb.WriteString(fmt.Sprintf("omniretrive_ingestion_jobs_failed %d\n", snap.Ingestion.Failed))
	sb.WriteString(fmt.Sprintf("omniretrive_ingestion_duration_ms_avg %f\n\n", snap.Ingestion.DurationMs.Avg))

	sb.WriteString("# HELP omniretrive_embedding_requests_total Total calls to embedding provider\n")
	sb.WriteString("# TYPE omniretrive_embedding_requests_total counter\n")
	sb.WriteString(fmt.Sprintf("omniretrive_embedding_requests_total %d\n", snap.Embedding.Requests))
	sb.WriteString(fmt.Sprintf("omniretrive_embedding_failures_total %d\n", snap.Embedding.Failures))
	sb.WriteString(fmt.Sprintf("omniretrive_embedding_chunks_total %d\n", snap.Embedding.TotalChunksEmbedded))
	sb.WriteString(fmt.Sprintf("omniretrive_embedding_latency_ms_avg %f\n\n", snap.Embedding.LatencyMs.Avg))

	sb.WriteString("# HELP omniretrive_retrieval_queries_total Total vector retrieval queries\n")
	sb.WriteString("# TYPE omniretrive_retrieval_queries_total counter\n")
	sb.WriteString(fmt.Sprintf("omniretrive_retrieval_queries_total %d\n", snap.Retrieval.Queries))
	sb.WriteString(fmt.Sprintf("omniretrive_retrieval_chunks_total %d\n", snap.Retrieval.TotalChunksRetrieved))
	sb.WriteString(fmt.Sprintf("omniretrive_retrieval_latency_ms_avg %f\n\n", snap.Retrieval.LatencyMs.Avg))

	sb.WriteString("# HELP omniretrive_llm_calls_total Total LLM question-answering inferences\n")
	sb.WriteString("# TYPE omniretrive_llm_calls_total counter\n")
	sb.WriteString(fmt.Sprintf("omniretrive_llm_calls_total %d\n", snap.LLM.Calls))
	sb.WriteString(fmt.Sprintf("omniretrive_llm_failures_total %d\n", snap.LLM.Failures))
	sb.WriteString(fmt.Sprintf("omniretrive_llm_tokens_total %d\n", snap.LLM.TotalTokens))
	sb.WriteString(fmt.Sprintf("omniretrive_llm_estimated_cost_usd %f\n", snap.LLM.EstimatedCostUSD))
	sb.WriteString(fmt.Sprintf("omniretrive_llm_latency_ms_avg %f\n", snap.LLM.LatencyMs.Avg))

	return sb.String()
}
