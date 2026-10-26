package handlers

import (
	"encoding/json"
	"net/http"
	"net/mail"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
	"golang.org/x/crypto/bcrypt"

	"omniretrive/gateway/auth"
	"omniretrive/gateway/db"
	"omniretrive/gateway/middleware"
	"omniretrive/gateway/models"
)

type AuthHandler struct {
	DB *db.DB
}

func NewAuthHandler(database *db.DB) *AuthHandler {
	return &AuthHandler{DB: database}
}

func (h *AuthHandler) Register(w http.ResponseWriter, r *http.Request) {
	var req models.RegisterRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		jsonError(w, "Invalid JSON request payload", http.StatusBadRequest)
		return
	}

	req.Email = strings.ToLower(strings.TrimSpace(req.Email))
	req.FullName = strings.TrimSpace(req.FullName)

	// Email validation
	if _, err := mail.ParseAddress(req.Email); err != nil {
		jsonError(w, "Invalid email address format", http.StatusBadRequest)
		return
	}

	// Password strength validation
	if len(req.Password) < 8 || len(req.Password) > 72 {
		jsonError(w, "Password must be between 8 and 72 bytes", http.StatusBadRequest)
		return
	}

	if req.FullName == "" {
		req.FullName = strings.Split(req.Email, "@")[0]
	}
	if utf8.RuneCountInString(req.FullName) > 120 {
		jsonError(w, "Full name cannot exceed 120 characters", http.StatusBadRequest)
		return
	}

	// Check if email already registered
	existing, _ := h.DB.GetUserByEmail(req.Email)
	if existing != nil {
		jsonError(w, "An account with this email address already exists", http.StatusConflict)
		return
	}

	// Secure bcrypt password hash (work factor 12)
	hashedPassword, err := bcrypt.GenerateFromPassword([]byte(req.Password), 12)
	if err != nil {
		jsonError(w, "Failed to hash password", http.StatusInternalServerError)
		return
	}

	now := time.Now().UTC()
	user := &models.User{
		ID:           uuid.New().String(),
		Email:        req.Email,
		PasswordHash: string(hashedPassword),
		FullName:     req.FullName,
		CreatedAt:    now,
		UpdatedAt:    now,
	}

	ws := &models.Workspace{
		ID:        uuid.New().String(),
		Name:      user.FullName + "'s Workspace",
		OwnerID:   user.ID,
		CreatedAt: now,
		UpdatedAt: now,
	}
	memberID := uuid.New().String()

	// Issue JWT
	token, err := auth.GenerateToken(user.ID, user.Email, 24*time.Hour)
	if err != nil {
		jsonError(w, "Failed to generate authorization token", http.StatusInternalServerError)
		return
	}
	if err := h.DB.CreateUserWithWorkspace(user, ws, memberID); err != nil {
		jsonError(w, "Failed to create user account and default workspace", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(models.AuthResponse{
		Token:     token,
		ExpiresIn: 86400,
		User:      user,
		Workspace: ws,
	})
}

func (h *AuthHandler) Login(w http.ResponseWriter, r *http.Request) {
	var req models.LoginRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		jsonError(w, "Invalid JSON request payload", http.StatusBadRequest)
		return
	}

	req.Email = strings.ToLower(strings.TrimSpace(req.Email))

	user, err := h.DB.GetUserByEmail(req.Email)
	if err != nil {
		jsonError(w, "Invalid email or password", http.StatusUnauthorized)
		return
	}

	// Constant-time bcrypt comparison
	if err := bcrypt.CompareHashAndPassword([]byte(user.PasswordHash), []byte(req.Password)); err != nil {
		jsonError(w, "Invalid email or password", http.StatusUnauthorized)
		return
	}

	// Fetch primary workspace
	workspaces, err := h.DB.ListWorkspacesForUser(user.ID)
	if err != nil {
		jsonError(w, "Failed to load user workspaces", http.StatusInternalServerError)
		return
	}
	var primaryWs *models.Workspace
	if len(workspaces) > 0 {
		primaryWs = &workspaces[0]
	}

	// Issue JWT
	token, err := auth.GenerateToken(user.ID, user.Email, 24*time.Hour)
	if err != nil {
		jsonError(w, "Failed to generate authorization token", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(models.AuthResponse{
		Token:     token,
		ExpiresIn: 86400,
		User:      user,
		Workspace: primaryWs,
	})
}

func (h *AuthHandler) Me(w http.ResponseWriter, r *http.Request) {
	user := middleware.GetUserFromContext(r.Context())
	if user == nil {
		jsonError(w, "Unauthorized", http.StatusUnauthorized)
		return
	}

	workspaces, err := h.DB.ListWorkspacesForUser(user.ID)
	if err != nil {
		jsonError(w, "Failed to load user workspaces", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"user":       user,
		"workspaces": workspaces,
	})
}

func jsonError(w http.ResponseWriter, message string, status int) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": message})
}
