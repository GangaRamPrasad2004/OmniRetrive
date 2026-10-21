package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"omniretrive/observability-service/metrics"
	"omniretrive/observability-service/probes"
	"omniretrive/observability-service/redactor"
)

func TestSecretRedaction(t *testing.T) {
	cases := []struct {
		name     string
		input    string
		expected string
		forbidden string
	}{
		{
			name:      "Bearer token",
			input:     "Authorization: Bearer my_secret_token_12345",
			expected:  "Authorization: Bearer [REDACTED]",
			forbidden: "my_secret_token_12345",
		},
		{
			name:      "Hugging Face token",
			input:     "Model token is hf_abcdef1234567890abcdef",
			expected:  "Model token is hf_[REDACTED]",
			forbidden: "hf_abcdef1234567890abcdef",
		},
		{
			name:      "OpenAI key",
			input:     "Calling API with sk-1234567890abcdef1234567890",
			expected:  "Calling API with sk-[REDACTED]",
			forbidden: "sk-1234567890abcdef1234567890",
		},
		{
			name:      "Database connection URL",
			input:     "postgresql://postgres:db_super_password@localhost:5432/omniretrive",
			expected:  "postgresql://postgres:[REDACTED]@localhost:5432/omniretrive",
			forbidden: "db_super_password",
		},
		{
			name:      "API Key param",
			input:     "api_key: secret_api_key_val",
			expected:  "api_key=[REDACTED]",
			forbidden: "secret_api_key_val",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			out := redactor.RedactSecrets(tc.input)
			if strings.Contains(out, tc.forbidden) {
				t.Errorf("Expected %q to be redacted from %q, got %q", tc.forbidden, tc.input, out)
			}
			if out != tc.expected {
				t.Errorf("Expected %q, got %q", tc.expected, out)
			}
		})
	}
}

func TestSanitizePayload(t *testing.T) {
	longDoc := strings.Repeat("Company proprietary document text. ", 15)
	payload := map[string]interface{}{
		"api_key":     "secret_val_123",
		"document_id": "doc-uuid-99",
		"text":        longDoc,
	}

	sanitized := redactor.SanitizePayload(payload, 50)
	if sanitized["api_key"] != "[REDACTED]" {
		t.Errorf("Expected api_key to be redacted, got %v", sanitized["api_key"])
	}
	if sanitized["document_id"] != "doc-uuid-99" {
		t.Errorf("Expected document_id to be preserved, got %v", sanitized["document_id"])
	}
	textVal, ok := sanitized["text"].(string)
	if !ok || !strings.Contains(textVal, "[truncated ") {
		t.Errorf("Expected text to be truncated, got %v", sanitized["text"])
	}
}

func TestMetricsCollectorAndPrometheus(t *testing.T) {
	col := metrics.NewMetricsCollector()

	// Record HTTP
	col.RecordHTTPRequest("GET", "/health", 200, 5.5)
	col.RecordHTTPRequest("POST", "/query", 200, 25.0)
	col.RecordHTTPRequest("POST", "/query", 500, 10.0)

	// Record Ingestion
	col.RecordIngestion(true, 150.0, "")
	col.RecordIngestion(false, 80.0, "pdf_extraction_error")

	// Record Embedding
	col.RecordEmbedding(16, 45.0, true)

	// Record Retrieval
	col.RecordRetrieval(5, 12.0)

	// Record LLM
	col.RecordLLM(220.0, 100, 50, true, 0.000045)

	snap := col.GetSnapshot()
	if snap.HTTPRequests.Total != 3 {
		t.Errorf("Expected 3 HTTP requests, got %d", snap.HTTPRequests.Total)
	}
	if snap.HTTPRequests.Errors != 1 {
		t.Errorf("Expected 1 HTTP error, got %d", snap.HTTPRequests.Errors)
	}
	if snap.Ingestion.TotalJobs != 2 {
		t.Errorf("Expected 2 ingestion jobs, got %d", snap.Ingestion.TotalJobs)
	}
	if snap.Ingestion.Failed != 1 {
		t.Errorf("Expected 1 failed ingestion job, got %d", snap.Ingestion.Failed)
	}
	if snap.Embedding.TotalChunksEmbedded != 16 {
		t.Errorf("Expected 16 chunks embedded, got %d", snap.Embedding.TotalChunksEmbedded)
	}
	if snap.Retrieval.Queries != 1 || snap.Retrieval.TotalChunksRetrieved != 5 {
		t.Errorf("Retrieval stats mismatch: %+v", snap.Retrieval)
	}
	if snap.LLM.Calls != 1 || snap.LLM.TotalTokens != 150 {
		t.Errorf("LLM stats mismatch: %+v", snap.LLM)
	}

	prom := col.ExportPrometheus()
	if !strings.Contains(prom, "omniretrive_http_requests_total") {
		t.Errorf("Prometheus output missing http_requests_total")
	}
	if !strings.Contains(prom, "omniretrive_ingestion_jobs_total 2") {
		t.Errorf("Prometheus output missing ingestion jobs count")
	}
}

func TestObservabilityServiceEndpoints(t *testing.T) {
	col := metrics.NewMetricsCollector()
	probeCfg := probes.ProbeConfig{
		DatabasePath: "test_temp.db",
		UploadsDir:   "test_uploads",
	}
	router := SetupRouter(col, probeCfg)

	// 1. Test /health
	req := httptest.NewRequest("GET", "/health", nil)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("Expected 200 from /health, got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), "OmniRetrive Observability Microservice") {
		t.Errorf("Unexpected body from /health: %s", w.Body.String())
	}
	if w.Header().Get("X-Request-ID") == "" {
		t.Errorf("Expected X-Request-ID header in response")
	}

	// 2. Test /ready
	req = httptest.NewRequest("GET", "/ready", nil)
	w = httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusOK && w.Code != http.StatusServiceUnavailable {
		t.Fatalf("Unexpected status from /ready: %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), "dependencies") {
		t.Errorf("Expected dependencies in readiness body: %s", w.Body.String())
	}

	// 3. Test /metrics
	req = httptest.NewRequest("GET", "/metrics", nil)
	w = httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("Expected 200 from /metrics, got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), "# HELP omniretrive_http_requests_total") {
		t.Errorf("Expected Prometheus format from /metrics")
	}

	// 4. Test /metrics/json
	req = httptest.NewRequest("GET", "/metrics/json", nil)
	w = httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("Expected 200 from /metrics/json, got %d", w.Code)
	}
	var jsonSnap map[string]interface{}
	if err := json.Unmarshal(w.Body.Bytes(), &jsonSnap); err != nil {
		t.Fatalf("Failed to parse /metrics/json output: %v", err)
	}

	// 5. Test POST /telemetry/logs with secret redaction
	logPayload := map[string]interface{}{
		"service": "python-backend",
		"level":   "INFO",
		"message": "User query processed using Bearer secret_token_xyz999",
		"extra": map[string]interface{}{
			"api_key": "raw_super_secret_key",
			"text":    "Confidential trade secrets and proprietary data",
		},
	}
	logBytes, _ := json.Marshal(logPayload)
	req = httptest.NewRequest("POST", "/telemetry/logs", bytes.NewReader(logBytes))
	req.Header.Set("Content-Type", "application/json")
	w = httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusAccepted {
		t.Fatalf("Expected 202 from /telemetry/logs, got %d", w.Code)
	}

	// 6. Test POST /telemetry/metrics
	metricPayload := map[string]interface{}{
		"type":        "ingestion",
		"duration_ms": 110.5,
		"success":     true,
	}
	metricBytes, _ := json.Marshal(metricPayload)
	req = httptest.NewRequest("POST", "/telemetry/metrics", bytes.NewReader(metricBytes))
	req.Header.Set("Content-Type", "application/json")
	w = httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusCreated {
		t.Fatalf("Expected 201 from /telemetry/metrics, got %d", w.Code)
	}

	// 7. Test GET /telemetry/summary
	req = httptest.NewRequest("GET", "/telemetry/summary", nil)
	w = httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("Expected 200 from /telemetry/summary, got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), "uptime_seconds") {
		t.Errorf("Expected uptime_seconds in summary: %s", w.Body.String())
	}
}
