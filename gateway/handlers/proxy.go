package handlers

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"

	"omniretrive/gateway/middleware"
	"omniretrive/gateway/models"
)

type ProxyHandler struct {
	AIServiceURL string
	client       *http.Client
}

func NewProxyHandler() *ProxyHandler {
	aiURL := os.Getenv("AI_SERVICE_URL")
	if aiURL == "" {
		aiURL = "http://localhost:8000"
	}
	return &ProxyHandler{
		AIServiceURL: strings.TrimRight(aiURL, "/"),
		client:       &http.Client{Timeout: 90 * time.Second},
	}
}

// UploadDocument proxies PDF upload to Python AI backend with verified server-side tenancy parameters
func (h *ProxyHandler) UploadDocument(w http.ResponseWriter, r *http.Request) {
	user := middleware.GetUserFromContext(r.Context())
	membership := middleware.GetMembershipFromContext(r.Context())
	workspaceID := chi.URLParam(r, "workspaceId")

	// Parse multipart form from incoming client request (limit: 55MB)
	if err := r.ParseMultipartForm(55 * 1024 * 1024); err != nil {
		jsonError(w, "Failed to parse multipart form: "+err.Error(), http.StatusBadRequest)
		return
	}

	file, header, err := r.FormFile("file")
	if err != nil {
		jsonError(w, "File upload field 'file' is required", http.StatusBadRequest)
		return
	}
	defer file.Close()

	// Prepare outbound multipart request to Python backend
	body := &bytes.Buffer{}
	writer := multipart.NewWriter(body)

	part, err := writer.CreateFormFile("file", header.Filename)
	if err != nil {
		jsonError(w, "Failed to create form file: "+err.Error(), http.StatusInternalServerError)
		return
	}
	if _, err := io.Copy(part, file); err != nil {
		jsonError(w, "Failed to stream file: "+err.Error(), http.StatusInternalServerError)
		return
	}

	// Inject SERVER-VERIFIED workspace_id and owner_id
	_ = writer.WriteField("workspace_id", workspaceID)
	_ = writer.WriteField("owner_id", user.ID)
	_ = writer.WriteField("member_role", string(membership.Role))

	if err := writer.Close(); err != nil {
		jsonError(w, "Failed to finalize multipart form: "+err.Error(), http.StatusInternalServerError)
		return
	}

	targetURL := fmt.Sprintf("%s/upload", h.AIServiceURL)
	outReq, err := http.NewRequestWithContext(r.Context(), http.MethodPost, targetURL, body)
	if err != nil {
		jsonError(w, "Failed to construct AI service request", http.StatusInternalServerError)
		return
	}
	outReq.Header.Set("Content-Type", writer.FormDataContentType())

	resp, err := h.client.Do(outReq)
	if err != nil {
		jsonError(w, "AI backend service unreachable: "+err.Error(), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	w.Header().Set("Content-Type", resp.Header.Get("Content-Type"))
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, resp.Body)
}

// ListDocuments lists documents scoped strictly to the authenticated workspace
func (h *ProxyHandler) ListDocuments(w http.ResponseWriter, r *http.Request) {
	workspaceID := chi.URLParam(r, "workspaceId")

	query := r.URL.Query()
	// Override/force workspace_id query parameter
	query.Set("workspace_id", workspaceID)

	targetURL := fmt.Sprintf("%s/documents?%s", h.AIServiceURL, query.Encode())
	outReq, err := http.NewRequestWithContext(r.Context(), http.MethodGet, targetURL, nil)
	if err != nil {
		jsonError(w, "Failed to construct request", http.StatusInternalServerError)
		return
	}

	resp, err := h.client.Do(outReq)
	if err != nil {
		jsonError(w, "AI backend service unreachable: "+err.Error(), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	w.Header().Set("Content-Type", resp.Header.Get("Content-Type"))
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, resp.Body)
}

// GetDocument retrieves document details, verifying it belongs to the workspace
func (h *ProxyHandler) GetDocument(w http.ResponseWriter, r *http.Request) {
	workspaceID := chi.URLParam(r, "workspaceId")
	docID := chi.URLParam(r, "docId")

	targetURL := fmt.Sprintf("%s/documents/%s", h.AIServiceURL, url.PathEscape(docID))
	outReq, err := http.NewRequestWithContext(r.Context(), http.MethodGet, targetURL, nil)
	if err != nil {
		jsonError(w, "Failed to construct request", http.StatusInternalServerError)
		return
	}

	resp, err := h.client.Do(outReq)
	if err != nil {
		jsonError(w, "AI backend service unreachable: "+err.Error(), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		w.WriteHeader(resp.StatusCode)
		_, _ = io.Copy(w, resp.Body)
		return
	}

	var docData map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&docData); err != nil {
		jsonError(w, "Invalid response from AI backend", http.StatusInternalServerError)
		return
	}

	// Server-side Tenant Security Check: ensure document belongs to this workspace
	docWsID, _ := docData["workspace_id"].(string)
	if docWsID != workspaceID {
		jsonError(w, "Document not found in this workspace", http.StatusNotFound)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(docData)
}

// RetryDocument retries a failed document ingestion after tenant validation
func (h *ProxyHandler) RetryDocument(w http.ResponseWriter, r *http.Request) {
	workspaceID := chi.URLParam(r, "workspaceId")
	docID := chi.URLParam(r, "docId")

	// First verify document belongs to this workspace
	getURL := fmt.Sprintf("%s/documents/%s", h.AIServiceURL, url.PathEscape(docID))
	getReq, _ := http.NewRequestWithContext(r.Context(), http.MethodGet, getURL, nil)
	getResp, err := h.client.Do(getReq)
	if err != nil || getResp.StatusCode != http.StatusOK {
		jsonError(w, "Document not found in this workspace", http.StatusNotFound)
		return
	}
	defer getResp.Body.Close()

	var docData map[string]interface{}
	_ = json.NewDecoder(getResp.Body).Decode(&docData)
	if docData["workspace_id"] != workspaceID {
		jsonError(w, "Document not found in this workspace", http.StatusNotFound)
		return
	}

	targetURL := fmt.Sprintf("%s/documents/%s/retry", h.AIServiceURL, url.PathEscape(docID))
	outReq, _ := http.NewRequestWithContext(r.Context(), http.MethodPost, targetURL, nil)
	resp, err := h.client.Do(outReq)
	if err != nil {
		jsonError(w, "AI backend service unreachable: "+err.Error(), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	w.Header().Set("Content-Type", resp.Header.Get("Content-Type"))
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, resp.Body)
}

// DeleteDocument deletes document if user has owner or admin role
func (h *ProxyHandler) DeleteDocument(w http.ResponseWriter, r *http.Request) {
	membership := middleware.GetMembershipFromContext(r.Context())
	workspaceID := chi.URLParam(r, "workspaceId")
	docID := chi.URLParam(r, "docId")

	if !membership.Role.CanDeleteDocuments() {
		jsonError(w, "Forbidden: only workspace owners and admins can delete documents", http.StatusForbidden)
		return
	}

	// Verify document belongs to this workspace
	getURL := fmt.Sprintf("%s/documents/%s", h.AIServiceURL, url.PathEscape(docID))
	getReq, _ := http.NewRequestWithContext(r.Context(), http.MethodGet, getURL, nil)
	getResp, err := h.client.Do(getReq)
	if err != nil || getResp.StatusCode != http.StatusOK {
		jsonError(w, "Document not found in this workspace", http.StatusNotFound)
		return
	}
	defer getResp.Body.Close()

	var docData map[string]interface{}
	_ = json.NewDecoder(getResp.Body).Decode(&docData)
	if docData["workspace_id"] != workspaceID {
		jsonError(w, "Document not found in this workspace", http.StatusNotFound)
		return
	}

	targetURL := fmt.Sprintf("%s/documents/%s", h.AIServiceURL, url.PathEscape(docID))
	outReq, _ := http.NewRequestWithContext(r.Context(), http.MethodDelete, targetURL, nil)
	resp, err := h.client.Do(outReq)
	if err != nil {
		jsonError(w, "AI backend service unreachable: "+err.Error(), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	w.Header().Set("Content-Type", resp.Header.Get("Content-Type"))
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, resp.Body)
}

// CancelDocument cancels an in-flight or queued ingestion job
func (h *ProxyHandler) CancelDocument(w http.ResponseWriter, r *http.Request) {
	workspaceID := chi.URLParam(r, "workspaceId")
	docID := chi.URLParam(r, "docId")

	// Verify document belongs to this workspace
	getURL := fmt.Sprintf("%s/documents/%s", h.AIServiceURL, url.PathEscape(docID))
	getReq, _ := http.NewRequestWithContext(r.Context(), http.MethodGet, getURL, nil)
	getResp, err := h.client.Do(getReq)
	if err != nil || getResp.StatusCode != http.StatusOK {
		jsonError(w, "Document not found in this workspace", http.StatusNotFound)
		return
	}
	defer getResp.Body.Close()

	var docData map[string]interface{}
	_ = json.NewDecoder(getResp.Body).Decode(&docData)
	if docData["workspace_id"] != workspaceID {
		jsonError(w, "Document not found in this workspace", http.StatusNotFound)
		return
	}

	targetURL := fmt.Sprintf("%s/documents/%s/cancel", h.AIServiceURL, url.PathEscape(docID))
	outReq, _ := http.NewRequestWithContext(r.Context(), http.MethodPost, targetURL, nil)
	resp, err := h.client.Do(outReq)
	if err != nil {
		jsonError(w, "AI backend service unreachable: "+err.Error(), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	w.Header().Set("Content-Type", resp.Header.Get("Content-Type"))
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, resp.Body)
}

// QueryDocuments executes a question-answering query strictly filtered by workspace_id
func (h *ProxyHandler) QueryDocuments(w http.ResponseWriter, r *http.Request) {
	workspaceID := chi.URLParam(r, "workspaceId")

	var req models.QueryRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		jsonError(w, "Invalid query payload", http.StatusBadRequest)
		return
	}

	req.Question = strings.TrimSpace(req.Question)
	if req.Question == "" {
		jsonError(w, "Question cannot be empty", http.StatusBadRequest)
		return
	}

	if req.TopK <= 0 {
		req.TopK = 5
	}

	// Forward query to Python AI service, with mandatory verified workspace_id
	payload := map[string]interface{}{
		"question":     req.Question,
		"top_k":        req.TopK,
		"workspace_id": workspaceID,
	}

	bodyBytes, _ := json.Marshal(payload)
	targetURL := fmt.Sprintf("%s/query", h.AIServiceURL)
	outReq, err := http.NewRequestWithContext(r.Context(), http.MethodPost, targetURL, bytes.NewBuffer(bodyBytes))
	if err != nil {
		jsonError(w, "Failed to construct AI query request", http.StatusInternalServerError)
		return
	}
	outReq.Header.Set("Content-Type", "application/json")

	resp, err := h.client.Do(outReq)
	if err != nil {
		jsonError(w, "AI backend service unreachable: "+err.Error(), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	w.Header().Set("Content-Type", resp.Header.Get("Content-Type"))
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, resp.Body)
}
