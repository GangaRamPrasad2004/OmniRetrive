package models

import (
	"time"
)

type Role string

const (
	RoleOwner  Role = "owner"
	RoleAdmin  Role = "admin"
	RoleMember Role = "member"
)

func (r Role) CanManageMembers() bool {
	return r == RoleOwner || r == RoleAdmin
}

func (r Role) CanDeleteWorkspace() bool {
	return r == RoleOwner
}

func (r Role) CanDeleteDocuments() bool {
	return r == RoleOwner || r == RoleAdmin
}

func (r Role) IsValid() bool {
	return r == RoleOwner || r == RoleAdmin || r == RoleMember
}

type User struct {
	ID           string    `json:"id"`
	Email        string    `json:"email"`
	PasswordHash string    `json:"-"`
	FullName     string    `json:"full_name"`
	CreatedAt    time.Time `json:"created_at"`
	UpdatedAt    time.Time `json:"updated_at"`
}

type Workspace struct {
	ID        string    `json:"id"`
	Name      string    `json:"name"`
	OwnerID   string    `json:"owner_id"`
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}

type WorkspaceMember struct {
	ID          string    `json:"id"`
	WorkspaceID string    `json:"workspace_id"`
	UserID      string    `json:"user_id"`
	Role        Role      `json:"role"`
	Email       string    `json:"email,omitempty"`
	FullName    string    `json:"full_name,omitempty"`
	CreatedAt   time.Time `json:"created_at"`
}

type RegisterRequest struct {
	Email    string `json:"email"`
	Password string `json:"password"`
	FullName string `json:"full_name"`
}

type LoginRequest struct {
	Email    string `json:"email"`
	Password string `json:"password"`
}

type AuthResponse struct {
	Token     string     `json:"token"`
	ExpiresIn int64      `json:"expires_in"`
	User      *User      `json:"user"`
	Workspace *Workspace `json:"default_workspace,omitempty"`
}

type CreateWorkspaceRequest struct {
	Name string `json:"name"`
}

type AddMemberRequest struct {
	Email string `json:"email"`
	Role  Role   `json:"role"`
}

type QueryRequest struct {
	Question            string   `json:"question"`
	TopK                int      `json:"top_k"`
	DocumentIDs         []string `json:"document_ids,omitempty"`
	SimilarityThreshold float64  `json:"similarity_threshold,omitempty"`
	EnableHybrid        *bool    `json:"enable_hybrid,omitempty"`
}
