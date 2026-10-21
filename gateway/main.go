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
	"golang.org/x/time/rate"

	"omniretrive/gateway/db"
	"omniretrive/gateway/handlers"
	"omniretrive/gateway/middleware"
	"omniretrive/gateway/models"
)

func SetupRouter(database *db.DB) *chi.Mux {
	r := chi.NewRouter()

	// Global Middlewares
	r.Use(chimiddleware.RequestID)
	r.Use(chimiddleware.RealIP)
	r.Use(chimiddleware.Logger)
	r.Use(chimiddleware.Recoverer)
	r.Use(chimiddleware.Timeout(60 * time.Second))

	// CORS Setup
	r.Use(cors.Handler(cors.Options{
		AllowedOrigins:   []string{"*"},
		AllowedMethods:   []string{"GET", "POST", "PUT", "DELETE", "OPTIONS"},
		AllowedHeaders:   []string{"Accept", "Authorization", "Content-Type", "X-CSRF-Token"},
		ExposedHeaders:   []string{"Link"},
		AllowCredentials: true,
		MaxAge:           300,
	}))

	// Rate Limiters
	authLimiter := middleware.NewIPRateLimiter(rate.Limit(5), 10)  // 5 req/sec, burst 10
	uploadLimiter := middleware.NewIPRateLimiter(rate.Limit(2), 5) // 2 req/sec, burst 5

	authH := handlers.NewAuthHandler(database)
	wsH := handlers.NewWorkspaceHandler(database)
	proxyH := handlers.NewProxyHandler()

	// Public Health Endpoint
	r.Get("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok","service":"OmniRetrive Go Gateway"}`))
	})

	// Public Auth Endpoints (Rate Limited)
	r.Route("/api/auth", func(r chi.Router) {
		r.With(middleware.RateLimitMiddleware(authLimiter)).Post("/register", authH.Register)
		r.With(middleware.RateLimitMiddleware(authLimiter)).Post("/login", authH.Login)

		// Protected Auth Endpoints
		r.Group(func(r chi.Router) {
			r.Use(middleware.AuthMiddleware(database))
			r.Get("/me", authH.Me)
		})
	})

	// Protected Workspace & Multi-Tenant Endpoints
	r.Route("/api/workspaces", func(r chi.Router) {
		r.Use(middleware.AuthMiddleware(database))

		r.Post("/", wsH.CreateWorkspace)
		r.Get("/", wsH.ListWorkspaces)

		// Tenant-Scoped Endpoints: strictly enforced by WorkspaceRBAC
		r.Route("/{workspaceId}", func(r chi.Router) {
			// Workspace Details (Any member)
			r.With(middleware.WorkspaceRBAC(database, models.RoleMember)).Get("/", wsH.GetWorkspace)

			// Delete Workspace (Only Owner)
			r.With(middleware.WorkspaceRBAC(database, models.RoleOwner)).Delete("/", wsH.DeleteWorkspace)

			// Membership Management
			r.Route("/members", func(r chi.Router) {
				// List members (Any member)
				r.With(middleware.WorkspaceRBAC(database, models.RoleMember)).Get("/", wsH.GetWorkspace)
				// Add member (Admin or Owner)
				r.With(middleware.WorkspaceRBAC(database, models.RoleAdmin)).Post("/", wsH.AddMember)
				// Remove member (Admin or Owner)
				r.With(middleware.WorkspaceRBAC(database, models.RoleAdmin)).Delete("/{userId}", wsH.RemoveMember)
			})

			// Tenant-Scoped Document & Query Operations
			r.Route("/documents", func(r chi.Router) {
				// Upload PDF (Any member, rate-limited)
				r.With(middleware.WorkspaceRBAC(database, models.RoleMember), middleware.RateLimitMiddleware(uploadLimiter)).
					Post("/upload", proxyH.UploadDocument)

				// List documents (Any member)
				r.With(middleware.WorkspaceRBAC(database, models.RoleMember)).
					Get("/", proxyH.ListDocuments)

				// Document Details (Any member)
				r.With(middleware.WorkspaceRBAC(database, models.RoleMember)).
					Get("/{docId}", proxyH.GetDocument)

				// Retry Ingestion (Any member)
				r.With(middleware.WorkspaceRBAC(database, models.RoleMember)).
					Post("/{docId}/retry", proxyH.RetryDocument)

				// Delete Document (Only Admin or Owner)
				r.With(middleware.WorkspaceRBAC(database, models.RoleAdmin)).
					Delete("/{docId}", proxyH.DeleteDocument)
			})

			// Question-Answering Query (Strictly isolated by workspace_id)
			r.With(middleware.WorkspaceRBAC(database, models.RoleMember)).
				Post("/query", proxyH.QueryDocuments)
		})
	})

	return r
}

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	database, err := db.InitDB("")
	if err != nil {
		log.Fatalf("Failed to initialize database: %v", err)
	}

	router := SetupRouter(database)

	srv := &http.Server{
		Addr:         ":" + port,
		Handler:      router,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 90 * time.Second,
		IdleTimeout:  120 * time.Second,
	}

	go func() {
		log.Printf("OmniRetrive Go Gateway listening on port %s", port)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("Server listen failed: %v", err)
		}
	}()

	// Graceful shutdown
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit
	log.Println("Shutting down server...")

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	if err := srv.Shutdown(ctx); err != nil {
		log.Fatalf("Server forced to shutdown: %v", err)
	}
	log.Println("Server gracefully stopped")
}
