package middleware

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/go-chi/chi/v5"
	"golang.org/x/time/rate"

	"omniretrive/gateway/auth"
	"omniretrive/gateway/db"
	"omniretrive/gateway/models"
)

type contextKey string

const (
	UserKey            contextKey = "user"
	WorkspaceMemberKey contextKey = "workspace_member"
	WorkspaceKey       contextKey = "workspace"
)

func GetUserFromContext(ctx context.Context) *models.User {
	if val, ok := ctx.Value(UserKey).(*models.User); ok {
		return val
	}
	return nil
}

func GetMembershipFromContext(ctx context.Context) *models.WorkspaceMember {
	if val, ok := ctx.Value(WorkspaceMemberKey).(*models.WorkspaceMember); ok {
		return val
	}
	return nil
}

func jsonError(w http.ResponseWriter, message string, status int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": message})
}

// AuthMiddleware validates JWT bearer token and sets user in context
func AuthMiddleware(database *db.DB) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			authHeader := r.Header.Get("Authorization")
			if authHeader == "" {
				jsonError(w, "Authorization header required", http.StatusUnauthorized)
				return
			}

			parts := strings.Split(authHeader, " ")
			if len(parts) != 2 || strings.ToLower(parts[0]) != "bearer" {
				jsonError(w, "Invalid authorization format. Format: Bearer <token>", http.StatusUnauthorized)
				return
			}

			claims, err := auth.ValidateToken(parts[1])
			if err != nil {
				jsonError(w, "Invalid or expired token", http.StatusUnauthorized)
				return
			}

			user, err := database.GetUserByID(claims.UserID)
			if err != nil {
				jsonError(w, "User not found or deactivated", http.StatusUnauthorized)
				return
			}

			ctx := context.WithValue(r.Context(), UserKey, user)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

// WorkspaceRBAC ensures the authenticated user is an active member of the workspace
// and possesses the required role privilege.
func WorkspaceRBAC(database *db.DB, minRole models.Role) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			user := GetUserFromContext(r.Context())
			if user == nil {
				jsonError(w, "Unauthorized: user authentication required", http.StatusUnauthorized)
				return
			}

			workspaceID := chi.URLParam(r, "workspaceId")
			if workspaceID == "" {
				jsonError(w, "Workspace ID is required in URL path", http.StatusBadRequest)
				return
			}

			// Validate workspace existence
			workspace, err := database.GetWorkspaceByID(workspaceID)
			if err != nil {
				jsonError(w, "Workspace not found", http.StatusNotFound)
				return
			}

			// Validate user membership
			membership, err := database.GetWorkspaceMembership(workspaceID, user.ID)
			if err != nil {
				jsonError(w, "Access forbidden: you are not a member of this workspace", http.StatusForbidden)
				return
			}

			// Enforce role hierarchy: owner > admin > member
			if minRole != "" {
				hasPermission := false
				switch minRole {
				case models.RoleMember:
					hasPermission = true // Any member has at least member role
				case models.RoleAdmin:
					hasPermission = (membership.Role == models.RoleAdmin || membership.Role == models.RoleOwner)
				case models.RoleOwner:
					hasPermission = (membership.Role == models.RoleOwner)
				}

				if !hasPermission {
					jsonError(w, "Access forbidden: insufficient role permissions in workspace", http.StatusForbidden)
					return
				}
			}

			ctx := context.WithValue(r.Context(), WorkspaceMemberKey, membership)
			ctx = context.WithValue(ctx, WorkspaceKey, workspace)
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

// RateLimiter implements an IP-based token bucket rate limiter
type IPRateLimiter struct {
	ips map[string]*clientLimiter
	mu  sync.RWMutex
	r   rate.Limit
	b   int
}

type clientLimiter struct {
	limiter  *rate.Limiter
	lastSeen time.Time
}

func NewIPRateLimiter(r rate.Limit, b int) *IPRateLimiter {
	i := &IPRateLimiter{
		ips: make(map[string]*clientLimiter),
		r:   r,
		b:   b,
	}

	// Periodic cleanup of stale IPs every 5 minutes
	go func() {
		for {
			time.Sleep(5 * time.Minute)
			i.mu.Lock()
			for ip, client := range i.ips {
				if time.Since(client.lastSeen) > 10*time.Minute {
					delete(i.ips, ip)
				}
			}
			i.mu.Unlock()
		}
	}()

	return i
}

func (i *IPRateLimiter) GetLimiter(ip string) *rate.Limiter {
	i.mu.Lock()
	defer i.mu.Unlock()

	client, exists := i.ips[ip]
	if !exists {
		limiter := rate.NewLimiter(i.r, i.b)
		i.ips[ip] = &clientLimiter{limiter: limiter, lastSeen: time.Now()}
		return limiter
	}

	client.lastSeen = time.Now()
	return client.limiter
}

func RateLimitMiddleware(limiter *IPRateLimiter) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// Extract client IP (handling X-Forwarded-For if behind proxy)
			ip := r.Header.Get("X-Forwarded-For")
			if ip == "" {
				ip = strings.Split(r.RemoteAddr, ":")[0]
			} else {
				ip = strings.Split(ip, ",")[0]
			}

			l := limiter.GetLimiter(ip)
			if !l.Allow() {
				jsonError(w, "Rate limit exceeded. Please slow down your requests.", http.StatusTooManyRequests)
				return
			}

			next.ServeHTTP(w, r)
		})
	}
}
