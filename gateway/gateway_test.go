package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"omniretrive/gateway/db"
	"omniretrive/gateway/models"
)

func setupTestDB(t *testing.T) (*db.DB, func()) {
	t.Helper()
	tempDir, err := os.MkdirTemp("", "gateway_test_*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}

	dbPath := filepath.Join(tempDir, "test_gateway.db")
	database, err := db.InitDB(dbPath)
	if err != nil {
		t.Fatalf("failed to init test db: %v", err)
	}

	cleanup := func() {
		database.Close()
		_ = os.RemoveAll(tempDir)
	}

	return database, cleanup
}

func registerUser(t *testing.T, router http.Handler, email, password, name string) (string, *models.User, *models.Workspace) {
	t.Helper()
	payload := models.RegisterRequest{
		Email:    email,
		Password: password,
		FullName: name,
	}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/auth/register", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusCreated {
		t.Fatalf("expected register status 201, got %d: %s", w.Code, w.Body.String())
	}

	var res models.AuthResponse
	if err := json.NewDecoder(w.Body).Decode(&res); err != nil {
		t.Fatalf("failed to decode auth response: %v", err)
	}
	return res.Token, res.User, res.Workspace
}

func createWorkspace(t *testing.T, router http.Handler, token, name string) *models.Workspace {
	t.Helper()
	payload := models.CreateWorkspaceRequest{Name: name}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/workspaces", bytes.NewBuffer(body))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusCreated {
		t.Fatalf("expected create workspace status 201, got %d: %s", w.Code, w.Body.String())
	}

	var ws models.Workspace
	_ = json.NewDecoder(w.Body).Decode(&ws)
	return &ws
}

// ─────────────────────────────────────────────────────────────────────────────
// 1. AUTHENTICATION & TOKEN MANAGEMENT TESTS
// ─────────────────────────────────────────────────────────────────────────────
func TestAuth_RegisterAndLogin(t *testing.T) {
	database, cleanup := setupTestDB(t)
	defer cleanup()
	router := SetupRouter(database)

	// 1. Successful Registration
	token, user, ws := registerUser(t, router, "alice@example.com", "Password123!", "Alice Smith")
	if token == "" || user.ID == "" || ws.ID == "" {
		t.Errorf("expected token, user ID, and default workspace to be populated")
	}

	// 2. Duplicate Registration Rejection
	payload := models.RegisterRequest{Email: "alice@example.com", Password: "Password123!", FullName: "Alice Clone"}
	body, _ := json.Marshal(payload)
	req := httptest.NewRequest(http.MethodPost, "/api/auth/register", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusConflict {
		t.Errorf("expected status 409 Conflict for duplicate registration, got %d", w.Code)
	}

	// 3. Login with Wrong Password
	loginPayload := models.LoginRequest{Email: "alice@example.com", Password: "WrongPassword"}
	body, _ = json.Marshal(loginPayload)
	req = httptest.NewRequest(http.MethodPost, "/api/auth/login", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	w = httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusUnauthorized {
		t.Errorf("expected status 401 for bad password, got %d", w.Code)
	}

	// 4. Successful Login
	loginPayload = models.LoginRequest{Email: "alice@example.com", Password: "Password123!"}
	body, _ = json.Marshal(loginPayload)
	req = httptest.NewRequest(http.MethodPost, "/api/auth/login", bytes.NewBuffer(body))
	req.Header.Set("Content-Type", "application/json")
	w = httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected status 200 for valid login, got %d", w.Code)
	}

	// 5. Protected /me Endpoint
	req = httptest.NewRequest(http.MethodGet, "/api/auth/me", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	w = httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected status 200 for /me, got %d", w.Code)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. WORKSPACE CREATION, MEMBERSHIP & RBAC TESTS
// ─────────────────────────────────────────────────────────────────────────────
func TestWorkspaces_RBAC_Roles(t *testing.T) {
	database, cleanup := setupTestDB(t)
	defer cleanup()
	router := SetupRouter(database)

	aliceToken, _, _ := registerUser(t, router, "alice@acme.com", "Password123!", "Alice (Owner)")
	bobToken, _, _ := registerUser(t, router, "bob@acme.com", "Password123!", "Bob (Admin)")
	charlieToken, _, _ := registerUser(t, router, "charlie@acme.com", "Password123!", "Charlie (Member)")

	// Alice creates "Acme Engineering"
	ws := createWorkspace(t, router, aliceToken, "Acme Engineering")

	// 1. Owner invites Bob as Admin
	addMemberReq := models.AddMemberRequest{Email: "bob@acme.com", Role: models.RoleAdmin}
	body, _ := json.Marshal(addMemberReq)
	req := httptest.NewRequest(http.MethodPost, fmt.Sprintf("/api/workspaces/%s/members", ws.ID), bytes.NewBuffer(body))
	req.Header.Set("Authorization", "Bearer "+aliceToken)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusCreated {
		t.Fatalf("failed to add Bob as admin, got %d: %s", w.Code, w.Body.String())
	}

	// 2. Admin (Bob) invites Charlie as Member
	addMemberReq = models.AddMemberRequest{Email: "charlie@acme.com", Role: models.RoleMember}
	body, _ = json.Marshal(addMemberReq)
	req = httptest.NewRequest(http.MethodPost, fmt.Sprintf("/api/workspaces/%s/members", ws.ID), bytes.NewBuffer(body))
	req.Header.Set("Authorization", "Bearer "+bobToken)
	req.Header.Set("Content-Type", "application/json")
	w = httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusCreated {
		t.Fatalf("failed to add Charlie as member, got %d: %s", w.Code, w.Body.String())
	}

	// 3. Member (Charlie) attempts to invite someone -> Forbidden (403)
	registerUser(t, router, "dave@acme.com", "Password123!", "Dave")
	addMemberReq = models.AddMemberRequest{Email: "dave@acme.com", Role: models.RoleMember}
	body, _ = json.Marshal(addMemberReq)
	req = httptest.NewRequest(http.MethodPost, fmt.Sprintf("/api/workspaces/%s/members", ws.ID), bytes.NewBuffer(body))
	req.Header.Set("Authorization", "Bearer "+charlieToken)
	req.Header.Set("Content-Type", "application/json")
	w = httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusForbidden {
		t.Errorf("expected member invite to be forbidden 403, got %d", w.Code)
	}

	// 4. Admin (Bob) attempts to delete workspace -> Forbidden (403, only Owner can delete workspace)
	req = httptest.NewRequest(http.MethodDelete, fmt.Sprintf("/api/workspaces/%s", ws.ID), nil)
	req.Header.Set("Authorization", "Bearer "+bobToken)
	w = httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusForbidden {
		t.Errorf("expected admin workspace deletion to be forbidden 403, got %d", w.Code)
	}

	// 5. Owner (Alice) deletes workspace -> Success (200)
	req = httptest.NewRequest(http.MethodDelete, fmt.Sprintf("/api/workspaces/%s", ws.ID), nil)
	req.Header.Set("Authorization", "Bearer "+aliceToken)
	w = httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected owner workspace deletion to succeed 200, got %d", w.Code)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. MULTI-TENANT ISOLATION AS A SECURITY BOUNDARY TESTS
// ─────────────────────────────────────────────────────────────────────────────
func TestMultiTenant_CrossWorkspaceIsolation(t *testing.T) {
	database, cleanup := setupTestDB(t)
	defer cleanup()
	router := SetupRouter(database)

	// Tenant A: Acme Corp
	tokenTenantA, _, _ := registerUser(t, router, "alice@acme.com", "Password123!", "Alice")
	wsAcme := createWorkspace(t, router, tokenTenantA, "Acme Confidential")

	// Tenant B: Beta Global
	tokenTenantB, _, _ := registerUser(t, router, "bob@beta.com", "Password123!", "Bob")
	_ = createWorkspace(t, router, tokenTenantB, "Beta Public")

	// Test Case 1: Tenant B tries to inspect Tenant A's workspace
	req := httptest.NewRequest(http.MethodGet, fmt.Sprintf("/api/workspaces/%s", wsAcme.ID), nil)
	req.Header.Set("Authorization", "Bearer "+tokenTenantB)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusForbidden {
		t.Errorf("security breach: Tenant B accessed Tenant A's workspace details, got %d (expected 403)", w.Code)
	}

	// Test Case 2: Tenant B tries to list Tenant A's documents
	req = httptest.NewRequest(http.MethodGet, fmt.Sprintf("/api/workspaces/%s/documents", wsAcme.ID), nil)
	req.Header.Set("Authorization", "Bearer "+tokenTenantB)
	w = httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusForbidden {
		t.Errorf("security breach: Tenant B listed Tenant A's documents, got %d (expected 403)", w.Code)
	}

	// Test Case 3: Tenant B tries to query Tenant A's vectors / RAG
	queryBody, _ := json.Marshal(models.QueryRequest{Question: "What are Acme secrets?", TopK: 5})
	req = httptest.NewRequest(http.MethodPost, fmt.Sprintf("/api/workspaces/%s/query", wsAcme.ID), bytes.NewBuffer(queryBody))
	req.Header.Set("Authorization", "Bearer "+tokenTenantB)
	req.Header.Set("Content-Type", "application/json")
	w = httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusForbidden {
		t.Errorf("security breach: Tenant B queried Tenant A's vectors, got %d (expected 403)", w.Code)
	}

	// Test Case 4: Tenant B tries to delete a document in Tenant A's workspace
	req = httptest.NewRequest(http.MethodDelete, fmt.Sprintf("/api/workspaces/%s/documents/doc-secret-123", wsAcme.ID), nil)
	req.Header.Set("Authorization", "Bearer "+tokenTenantB)
	w = httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusForbidden {
		t.Errorf("security breach: Tenant B deleted document in Tenant A, got %d (expected 403)", w.Code)
	}

	// Test Case 5: Tenant B tries to cancel an ingestion job in Tenant A's workspace
	req = httptest.NewRequest(http.MethodPost, fmt.Sprintf("/api/workspaces/%s/documents/doc-secret-123/cancel", wsAcme.ID), nil)
	req.Header.Set("Authorization", "Bearer "+tokenTenantB)
	w = httptest.NewRecorder()
	router.ServeHTTP(w, req)
	if w.Code != http.StatusForbidden {
		t.Errorf("security breach: Tenant B cancelled document job in Tenant A, got %d (expected 403)", w.Code)
	}
}


// ─────────────────────────────────────────────────────────────────────────────
// 4. RATE LIMITING TESTS
// ─────────────────────────────────────────────────────────────────────────────
func TestRateLimiter_Exceeded(t *testing.T) {
	database, cleanup := setupTestDB(t)
	defer cleanup()
	router := SetupRouter(database)

	hitCount := 0
	rateLimited := false

	// Rapidly spam registration/login endpoint
	for i := 0; i < 25; i++ {
		payload := models.LoginRequest{Email: "test@example.com", Password: "Password123!"}
		body, _ := json.Marshal(payload)
		req := httptest.NewRequest(http.MethodPost, "/api/auth/login", bytes.NewBuffer(body))
		req.Header.Set("Content-Type", "application/json")
		req.RemoteAddr = "192.168.1.100:12345"

		w := httptest.NewRecorder()
		router.ServeHTTP(w, req)

		if w.Code == http.StatusTooManyRequests {
			rateLimited = true
			break
		}
		hitCount++
	}

	if !rateLimited {
		t.Errorf("expected rate limiter to trigger HTTP 429 after rapid requests, completed %d requests without rate limit", hitCount)
	}
}
