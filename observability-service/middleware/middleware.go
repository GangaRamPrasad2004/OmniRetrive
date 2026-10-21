package middleware

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"time"

	"github.com/google/uuid"
	"omniretrive/observability-service/metrics"
	"omniretrive/observability-service/redactor"
)

type contextKey string

const (
	RequestIDKey     contextKey = "request_id"
	CorrelationIDKey contextKey = "correlation_id"
	WorkspaceIDKey   contextKey = "workspace_id"
)

func GetRequestID(ctx context.Context) string {
	if val, ok := ctx.Value(RequestIDKey).(string); ok {
		return val
	}
	return ""
}

func GetCorrelationID(ctx context.Context) string {
	if val, ok := ctx.Value(CorrelationIDKey).(string); ok {
		return val
	}
	return ""
}

// RequestTracingMiddleware inspects and assigns correlation IDs and tracks request duration.
func RequestTracingMiddleware(collector *metrics.MetricsCollector) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			reqID := r.Header.Get("X-Request-ID")
			if reqID == "" {
				reqID = r.Header.Get("X-Correlation-ID")
			}
			if reqID == "" {
				reqID = uuid.New().String()
			}

			corrID := r.Header.Get("X-Correlation-ID")
			if corrID == "" {
				corrID = reqID
			}

			wsID := r.Header.Get("X-Workspace-ID")
			if wsID == "" {
				wsID = r.URL.Query().Get("workspace_id")
			}

			ctx := context.WithValue(r.Context(), RequestIDKey, reqID)
			ctx = context.WithValue(ctx, CorrelationIDKey, corrID)
			if wsID != "" {
				ctx = context.WithValue(ctx, WorkspaceIDKey, wsID)
			}

			r = r.WithContext(ctx)

			start := time.Now()
			wrappedWriter := &responseWriterInterceptor{ResponseWriter: w, statusCode: http.StatusOK}

			next.ServeHTTP(wrappedWriter, r)

			duration := time.Since(start)
			durMs := float64(duration.Microseconds()) / 1000.0

			wrappedWriter.Header().Set("X-Request-ID", reqID)
			wrappedWriter.Header().Set("X-Correlation-ID", corrID)
			wrappedWriter.Header().Set("X-Response-Time-Ms", fmt.Sprintf("%.2f", durMs))

			if collector != nil {
				collector.RecordHTTPRequest(r.Method, r.URL.Path, wrappedWriter.statusCode, durMs)
			}

			// Structured log entry
			logEntry := map[string]interface{}{
				"timestamp":      time.Now().UTC().Format(time.RFC3339),
				"level":          "INFO",
				"service":        "observability-service",
				"request_id":     reqID,
				"correlation_id": corrID,
				"method":         r.Method,
				"path":           redactor.RedactSecrets(r.URL.Path),
				"status_code":    wrappedWriter.statusCode,
				"duration_ms":    durMs,
			}
			if wsID != "" {
				logEntry["workspace_id"] = wsID
			}
			jsonLog, _ := json.Marshal(logEntry)
			log.Println(string(jsonLog))
		})
	}
}

// RecovererMiddleware traps panics and logs structured JSON without crashing the microservice.
func RecovererMiddleware(collector *metrics.MetricsCollector) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			defer func() {
				if rvr := recover(); rvr != nil {
					reqID := GetRequestID(r.Context())
					logEntry := map[string]interface{}{
						"timestamp":  time.Now().UTC().Format(time.RFC3339),
						"level":      "ERROR",
						"service":    "observability-service",
						"request_id": reqID,
						"error":      redactor.RedactSecrets(fmt.Sprintf("%v", rvr)),
					}
					jsonLog, _ := json.Marshal(logEntry)
					log.Println(string(jsonLog))

					if collector != nil {
						collector.RecordHTTPRequest(r.Method, r.URL.Path, http.StatusInternalServerError, 0)
					}

					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(http.StatusInternalServerError)
					_ = json.NewEncoder(w).Encode(map[string]string{
						"detail":     "Internal server error.",
						"request_id": reqID,
					})
				}
			}()
			next.ServeHTTP(w, r)
		})
	}
}

type responseWriterInterceptor struct {
	http.ResponseWriter
	statusCode int
}

func (w *responseWriterInterceptor) WriteHeader(code int) {
	w.statusCode = code
	w.ResponseWriter.WriteHeader(code)
}
