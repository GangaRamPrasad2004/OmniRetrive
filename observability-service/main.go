package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/go-chi/chi/v5"
	chimiddleware "github.com/go-chi/chi/v5/middleware"
	"github.com/go-chi/cors"

	"omniretrive/observability-service/handlers"
	"omniretrive/observability-service/metrics"
	obsMiddleware "omniretrive/observability-service/middleware"
	"omniretrive/observability-service/probes"
)

func SetupRouter(collector *metrics.MetricsCollector, probeCfg probes.ProbeConfig) *chi.Mux {
	r := chi.NewRouter()

	// Tracing and error recovery middlewares
	r.Use(obsMiddleware.RequestTracingMiddleware(collector))
	r.Use(obsMiddleware.RecovererMiddleware(collector))
	r.Use(chimiddleware.RealIP)
	r.Use(chimiddleware.Timeout(30 * time.Second))

	// CORS configuration
	r.Use(cors.Handler(cors.Options{
		AllowedOrigins:   []string{"*"},
		AllowedMethods:   []string{"GET", "POST", "PUT", "DELETE", "OPTIONS"},
		AllowedHeaders:   []string{"Accept", "Authorization", "Content-Type", "X-Request-ID", "X-Correlation-ID", "X-Workspace-ID"},
		ExposedHeaders:   []string{"X-Request-ID", "X-Correlation-ID", "X-Response-Time-Ms"},
		AllowCredentials: true,
		MaxAge:           300,
	}))

	h := handlers.NewObservabilityHandler(collector, probeCfg)

	// Liveness Probes
	r.Get("/health", h.Liveness)
	r.Get("/healthz", h.Liveness)

	// Readiness Probes
	r.Get("/ready", h.Readiness)
	r.Get("/readyz", h.Readiness)

	// Operational Metrics
	r.Get("/metrics", h.PrometheusMetrics)
	r.Get("/metrics/json", h.JSONMetrics)

	// Centralized Telemetry
	r.Route("/telemetry", func(r chi.Router) {
		r.Post("/logs", h.IngestLog)
		r.Post("/metrics", h.RecordMetric)
		r.Get("/summary", h.Summary)
	})

	return r
}

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = os.Getenv("OBSERVABILITY_PORT")
	}
	if port == "" {
		port = "8082"
	}

	dbPath := os.Getenv("DATABASE_PATH")
	if dbPath == "" {
		dbPath = "omniretrive.db"
	}

	qdrantURL := os.Getenv("QDRANT_URL")
	if qdrantURL == "" {
		qdrantURL = "http://localhost:6333"
	}

	backendURL := os.Getenv("BACKEND_URL")
	if backendURL == "" {
		backendURL = "http://localhost:8000"
	}

	gatewayURL := os.Getenv("GATEWAY_URL")
	if gatewayURL == "" {
		gatewayURL = "http://localhost:8080"
	}

	uploadsDir := os.Getenv("UPLOADS_DIR")
	if uploadsDir == "" {
		uploadsDir = "uploads"
	}

	probeCfg := probes.ProbeConfig{
		DatabasePath: dbPath,
		QdrantURL:    qdrantURL,
		BackendURL:   backendURL,
		GatewayURL:   gatewayURL,
		UploadsDir:   uploadsDir,
	}

	collector := metrics.NewMetricsCollector()
	router := SetupRouter(collector, probeCfg)

	srv := &http.Server{
		Addr:         ":" + port,
		Handler:      router,
		ReadTimeout:  15 * time.Second,
		WriteTimeout: 30 * time.Second,
		IdleTimeout:  60 * time.Second,
	}

	go func() {
		log.Printf("OmniRetrive Go Observability Microservice listening on port %s", port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("Server listen failed: %v", err)
		}
	}()

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit

	log.Println("Shutting down observability service...")
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := srv.Shutdown(ctx); err != nil {
		log.Fatalf("Observability server forced to shutdown: %v", err)
	}
	log.Println("Observability microservice gracefully stopped")
}
