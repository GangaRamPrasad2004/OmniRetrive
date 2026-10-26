package handlers

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"omniretrive/gateway/db"
	"omniretrive/gateway/middleware"
	"omniretrive/gateway/models"
)

type WorkspaceHandler struct {
	DB      *db.DB
	AIServe string
}

func NewWorkspaceHandler(database *db.DB) *WorkspaceHandler {
	aiURL := os.Getenv("AI_SERVICE_URL")
	if aiURL == "" {
		aiURL = "http://localhost:8000"
	}
	return &WorkspaceHandler{
		DB:      database,
		AIServe: strings.TrimRight(aiURL, "/"),
	}
}

func (h *WorkspaceHandler) CreateWorkspace(w http.ResponseWriter, r *http.Request) {
	user := middleware.GetUserFromContext(r.Context())
	if user == nil {
		jsonError(w, "Unauthorized", http.StatusUnauthorized)
		return
	}

	var req models.CreateWorkspaceRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		jsonError(w, "Invalid request payload", http.StatusBadRequest)
		return
	}

	req.Name = strings.TrimSpace(req.Name)
	if req.Name == "" {
		jsonError(w, "Workspace name cannot be empty", http.StatusBadRequest)
		return
	}

	now := time.Now().UTC()
	ws := &models.Workspace{
		ID:        uuid.New().String(),
		Name:      req.Name,
		OwnerID:   user.ID,
		CreatedAt: now,
		UpdatedAt: now,
	}

	memberID := uuid.New().String()
	if err := h.DB.CreateWorkspaceWithMember(ws, memberID); err != nil {
		jsonError(w, "Failed to create workspace: "+err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(ws)
}

func (h *WorkspaceHandler) ListWorkspaces(w http.ResponseWriter, r *http.Request) {
	user := middleware.GetUserFromContext(r.Context())
	if user == nil {
		jsonError(w, "Unauthorized", http.StatusUnauthorized)
		return
	}

	workspaces, err := h.DB.ListWorkspacesForUser(user.ID)
	if err != nil {
		jsonError(w, "Failed to list workspaces: "+err.Error(), http.StatusInternalServerError)
		return
	}

	if workspaces == nil {
		workspaces = []models.Workspace{}
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(workspaces)
}

func (h *WorkspaceHandler) GetWorkspace(w http.ResponseWriter, r *http.Request) {
	workspaceID := chi.URLParam(r, "workspaceId")
	ws, err := h.DB.GetWorkspaceByID(workspaceID)
	if err != nil {
		jsonError(w, "Workspace not found", http.StatusNotFound)
		return
	}

	members, err := h.DB.ListWorkspaceMembers(workspaceID)
	if err != nil {
		members = []models.WorkspaceMember{}
	}

	membership := middleware.GetMembershipFromContext(r.Context())

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"workspace":    ws,
		"members":      members,
		"current_role": membership.Role,
	})
}

func (h *WorkspaceHandler) AddMember(w http.ResponseWriter, r *http.Request) {
	workspaceID := chi.URLParam(r, "workspaceId")
	currentUser := middleware.GetUserFromContext(r.Context())
	currentMember := middleware.GetMembershipFromContext(r.Context())

	if !currentMember.Role.CanManageMembers() {
		jsonError(w, "Forbidden: only workspace owners and admins can invite members", http.StatusForbidden)
		return
	}

	var req models.AddMemberRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		jsonError(w, "Invalid request payload", http.StatusBadRequest)
		return
	}

	req.Email = strings.ToLower(strings.TrimSpace(req.Email))
	if req.Email == "" {
		jsonError(w, "Member email is required", http.StatusBadRequest)
		return
	}

	if !req.Role.IsValid() {
		req.Role = models.RoleMember
	}

	// Only owner can assign admin role
	if req.Role == models.RoleAdmin && currentMember.Role != models.RoleOwner {
		jsonError(w, "Forbidden: only workspace owner can assign admin role", http.StatusForbidden)
		return
	}

	// Cannot assign owner role via add member
	if req.Role == models.RoleOwner {
		jsonError(w, "Cannot assign owner role directly", http.StatusBadRequest)
		return
	}

	targetUser, err := h.DB.GetUserByEmail(req.Email)
	if err != nil {
		jsonError(w, "User with email '"+req.Email+"' not found. They must register first.", http.StatusNotFound)
		return
	}

	// Prevent adding oneself
	if targetUser.ID == currentUser.ID {
		jsonError(w, "You are already a member of this workspace", http.StatusBadRequest)
		return
	}

	// Check existing membership
	existingMember, _ := h.DB.GetWorkspaceMembership(workspaceID, targetUser.ID)
	if existingMember != nil {
		jsonError(w, "User is already a member of this workspace", http.StatusConflict)
		return
	}

	member := &models.WorkspaceMember{
		ID:          uuid.New().String(),
		WorkspaceID: workspaceID,
		UserID:      targetUser.ID,
		Role:        req.Role,
		Email:       targetUser.Email,
		FullName:    targetUser.FullName,
		CreatedAt:   time.Now().UTC(),
	}

	if err := h.DB.AddWorkspaceMember(member); err != nil {
		jsonError(w, "Failed to add member: "+err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(member)
}

func (h *WorkspaceHandler) RemoveMember(w http.ResponseWriter, r *http.Request) {
	workspaceID := chi.URLParam(r, "workspaceId")
	targetUserID := chi.URLParam(r, "userId")
	currentMember := middleware.GetMembershipFromContext(r.Context())

	if !currentMember.Role.CanManageMembers() {
		jsonError(w, "Forbidden: only workspace owners and admins can remove members", http.StatusForbidden)
		return
	}

	targetMembership, err := h.DB.GetWorkspaceMembership(workspaceID, targetUserID)
	if err != nil {
		jsonError(w, "Member not found in workspace", http.StatusNotFound)
		return
	}

	if targetMembership.Role == models.RoleOwner {
		jsonError(w, "Forbidden: cannot remove the workspace owner", http.StatusForbidden)
		return
	}

	// Admin cannot remove another admin (only owner can)
	if targetMembership.Role == models.RoleAdmin && currentMember.Role != models.RoleOwner {
		jsonError(w, "Forbidden: only the owner can remove an admin", http.StatusForbidden)
		return
	}

	if err := h.DB.RemoveWorkspaceMember(workspaceID, targetUserID); err != nil {
		jsonError(w, "Failed to remove member: "+err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]string{
		"status":  "success",
		"message": "Member removed from workspace",
	})
}

func (h *WorkspaceHandler) DeleteWorkspace(w http.ResponseWriter, r *http.Request) {
	workspaceID := chi.URLParam(r, "workspaceId")
	currentMember := middleware.GetMembershipFromContext(r.Context())

	if !currentMember.Role.CanDeleteWorkspace() {
		jsonError(w, "Forbidden: only the workspace owner can delete the workspace", http.StatusForbidden)
		return
	}

	// Cascade cleanup of derived vector data and documents in Python AI service
	cleanupURL := fmt.Sprintf("%s/workspaces/%s/cleanup", h.AIServe, url.PathEscape(workspaceID))
	reqCleanup, err := http.NewRequestWithContext(r.Context(), http.MethodDelete, cleanupURL, nil)
	if err != nil {
		jsonError(w, "Failed to construct workspace cleanup request", http.StatusInternalServerError)
		return
	}
	client := &http.Client{Timeout: 15 * time.Second}
	cleanupResponse, err := client.Do(reqCleanup)
	if err != nil {
		jsonError(w, "Workspace data cleanup service is unavailable; workspace was not deleted", http.StatusBadGateway)
		return
	}
	defer cleanupResponse.Body.Close()
	if cleanupResponse.StatusCode < http.StatusOK || cleanupResponse.StatusCode >= http.StatusMultipleChoices {
		jsonError(w, "Workspace data cleanup failed; workspace was not deleted", http.StatusBadGateway)
		return
	}

	// Delete from Gateway DB (cascades memberships)
	if err := h.DB.DeleteWorkspace(workspaceID); err != nil {
		jsonError(w, "Failed to delete workspace: "+err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]string{
		"status":  "deleted",
		"message": "Workspace and all associated tenant records deleted successfully",
	})
}
