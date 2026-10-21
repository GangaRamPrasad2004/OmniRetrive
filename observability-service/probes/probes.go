package probes

import (
	"context"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"omniretrive/observability-service/redactor"
)

type DependencyStatus struct {
	Status    string  `json:"status"`
	LatencyMs float64 `json:"latency_ms"`
	Details   string  `json:"details,omitempty"`
	Error     string  `json:"error,omitempty"`
}

type ReadinessReport struct {
	Status       string                      `json:"status"`
	Timestamp    string                      `json:"timestamp"`
	Dependencies map[string]DependencyStatus `json:"dependencies"`
}

type ProbeConfig struct {
	DatabasePath string
	QdrantURL    string
	BackendURL   string
	GatewayURL   string
	UploadsDir   string
}

func CheckHTTPService(url string, timeout time.Duration) DependencyStatus {
	start := time.Now()
	client := &http.Client{Timeout: timeout}

	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return DependencyStatus{
			Status:    "unhealthy",
			LatencyMs: roundMs(time.Since(start)),
			Error:     redactor.RedactSecrets(err.Error()),
		}
	}

	resp, err := client.Do(req)
	lat := roundMs(time.Since(start))
	if err != nil {
		return DependencyStatus{
			Status:    "degraded",
			LatencyMs: lat,
			Error:     redactor.RedactSecrets(err.Error()),
		}
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 200 && resp.StatusCode < 400 {
		return DependencyStatus{
			Status:    "healthy",
			LatencyMs: lat,
			Details:   fmt.Sprintf("HTTP %d", resp.StatusCode),
		}
	}

	return DependencyStatus{
		Status:    "degraded",
		LatencyMs: lat,
		Details:   fmt.Sprintf("HTTP %d", resp.StatusCode),
	}
}

func CheckStorage(dirPath string) DependencyStatus {
	start := time.Now()
	if dirPath == "" {
		dirPath = "uploads"
	}
	if err := os.MkdirAll(dirPath, 0755); err != nil {
		return DependencyStatus{
			Status:    "unhealthy",
			LatencyMs: roundMs(time.Since(start)),
			Error:     redactor.RedactSecrets(err.Error()),
		}
	}

	testFile := filepath.Join(dirPath, fmt.Sprintf(".probe_%d.tmp", time.Now().UnixNano()))
	if err := os.WriteFile(testFile, []byte("ok"), 0644); err != nil {
		return DependencyStatus{
			Status:    "unhealthy",
			LatencyMs: roundMs(time.Since(start)),
			Error:     redactor.RedactSecrets(err.Error()),
		}
	}
	_ = os.Remove(testFile)

	return DependencyStatus{
		Status:    "healthy",
		LatencyMs: roundMs(time.Since(start)),
		Details:   dirPath,
	}
}

func CheckDatabase(dbPath string) DependencyStatus {
	start := time.Now()
	if dbPath == "" {
		dbPath = "omniretrive.db"
	}
	info, err := os.Stat(dbPath)
	lat := roundMs(time.Since(start))
	if err != nil {
		// If sqlite db file doesn't exist yet, we check if directory is writable
		dir := filepath.Dir(dbPath)
		if dirErr := os.MkdirAll(dir, 0755); dirErr != nil {
			return DependencyStatus{
				Status:    "unhealthy",
				LatencyMs: lat,
				Error:     redactor.RedactSecrets(dirErr.Error()),
			}
		}
		return DependencyStatus{
			Status:    "healthy",
			LatencyMs: lat,
			Details:   "initialized/ready",
		}
	}

	return DependencyStatus{
		Status:    "healthy",
		LatencyMs: lat,
		Details:   fmt.Sprintf("size: %d bytes", info.Size()),
	}
}

func CheckAll(cfg ProbeConfig) ReadinessReport {
	deps := make(map[string]DependencyStatus)

	deps["database"] = CheckDatabase(cfg.DatabasePath)
	deps["storage"] = CheckStorage(cfg.UploadsDir)

	if cfg.QdrantURL != "" {
		qdrantHealthURL := fmt.Sprintf("%s/collections", cfg.QdrantURL)
		deps["qdrant"] = CheckHTTPService(qdrantHealthURL, 2*time.Second)
	}

	if cfg.BackendURL != "" {
		backendHealthURL := fmt.Sprintf("%s/health", cfg.BackendURL)
		deps["backend"] = CheckHTTPService(backendHealthURL, 2*time.Second)
	}

	if cfg.GatewayURL != "" {
		gatewayHealthURL := fmt.Sprintf("%s/health", cfg.GatewayURL)
		deps["gateway"] = CheckHTTPService(gatewayHealthURL, 2*time.Second)
	}

	// Overall readiness: database and storage must be healthy
	isReady := deps["database"].Status == "healthy" && deps["storage"].Status == "healthy"

	overall := "ready"
	if !isReady {
		overall = "not_ready"
	}

	return ReadinessReport{
		Status:       overall,
		Timestamp:    time.Now().UTC().Format(time.RFC3339),
		Dependencies: deps,
	}
}

func roundMs(d time.Duration) float64 {
	return float64(d.Microseconds()) / 1000.0
}
