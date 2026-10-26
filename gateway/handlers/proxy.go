package handlers

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"os"
	"strconv"
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

func uploadLimitBytes() int64 {
	limitMiB := int64(50)
	if configured, err := strconv.ParseInt(os.Getenv("MAX_UPLOAD_SIZE_MB"), 10, 32); err == nil && configured > 0 {
		limitMiB = configured
	}
	return limitMiB * 1024 * 1024
}

func (h *ProxyHandler) verifyDocumentWorkspace(
	w http.ResponseWriter,
	r *http.Request,
	workspaceID string,
	docID string,
) bool {
	targetURL := fmt.Sprintf("%s/documents/%s", h.AIServiceURL, url.PathEscape(docID))
	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, targetURL, nil)
	if err != nil {
		jsonError(w, "Failed to construct AI document verification request", http.StatusInternalServerError)
		return false
	}

	resp, err := h.client.Do(req)
	if err != nil {
		jsonError(w, "AI backend service unreachable: "+err.Error(), http.StatusBadGateway)
		return false
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		jsonError(w, "Document not found in this workspace", http.StatusNotFound)
		return false
	}
	if resp.StatusCode != http.StatusOK {
		jsonError(w, "AI backend failed to verify document access", http.StatusBadGateway)
		return false
	}

	var docData struct {
		WorkspaceID string `json:"workspace_id"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&docData); err != nil {
		jsonError(w, "Invalid response from AI backend while verifying document access", http.StatusBadGateway)
		return false
	}
	if docData.WorkspaceID != workspaceID {
		jsonError(w, "Document not found in this workspace", http.StatusNotFound)
		return false
	}
	return true
}

// UploadDocument proxies PDF upload to Python AI backend with verified server-side tenancy parameters
func (h *ProxyHandler) UploadDocument(w http.ResponseWriter, r *http.Request) {
	user := middleware.GetUserFromContext(r.Context())
	membership := middleware.GetMembershipFromContext(r.Context())
	workspaceID := chi.URLParam(r, "workspaceId")

	maxUpload := uploadLimitBytes()
	r.Body = http.MaxBytesReader(w, r.Body, maxUpload+(1<<20))
	if err := r.ParseMultipartForm(8 * 1024 * 1024); err != nil {
		var maxBytesErr *http.MaxBytesError
		if errors.As(err, &maxBytesErr) {
			jsonError(w, "Upload exceeds the configured file-size limit", http.StatusRequestEntityTooLarge)
			return
		}
		jsonError(w, "Failed to parse multipart form", http.StatusBadRequest)
		return
	}
	if r.MultipartForm != nil {
		defer r.MultipartForm.RemoveAll()
	}

	file, header, err := r.FormFile("file")
	if err != nil {
		jsonError(w, "File upload field 'file' is required", http.StatusBadRequest)
		return
	}
	defer file.Close()

	bodyReader, bodyWriter := io.Pipe()
	writer := multipart.NewWriter(bodyWriter)
	writeDone := make(chan error, 1)
	go func() {
		err := func() error {
			part, err := writer.CreateFormFile("file", header.Filename)
			if err != nil {
				return err
			}
			if _, err := io.Copy(part, file); err != nil {
				return err
			}
			if err := writer.WriteField("workspace_id", workspaceID); err != nil {
				return err
			}
			if err := writer.WriteField("owner_id", user.ID); err != nil {
				return err
			}
			if err := writer.WriteField("member_role", string(membership.Role)); err != nil {
				return err
			}
			return writer.Close()
		}()
		if err != nil {
			_ = bodyWriter.CloseWithError(err)
		} else {
			_ = bodyWriter.Close()
		}
		writeDone <- err
	}()

	targetURL := fmt.Sprintf("%s/upload", h.AIServiceURL)
	outReq, err := http.NewRequestWithContext(r.Context(), http.MethodPost, targetURL, bodyReader)
	if err != nil {
		_ = bodyReader.CloseWithError(err)
		<-writeDone
		jsonError(w, "Failed to construct AI service request", http.StatusInternalServerError)
		return
	}
	outReq.Header.Set("Content-Type", writer.FormDataContentType())

	resp, err := h.client.Do(outReq)
	if err != nil {
		_ = bodyReader.CloseWithError(err)
		<-writeDone
		jsonError(w, "AI backend service unreachable: "+err.Error(), http.StatusBadGateway)
		return
	}
	if writeErr := <-writeDone; writeErr != nil {
		resp.Body.Close()
		jsonError(w, "Could not stream upload to the AI backend", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	w.Header().Set("Content-Type", resp.Header.Get("Content-Type"))
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, resp.Body)
}

// DownloadDocument streams an authorized document from the private AI storage adapter.
func (h *ProxyHandler) DownloadDocument(w http.ResponseWriter, r *http.Request) {
	workspaceID := chi.URLParam(r, "workspaceId")
	docID := chi.URLParam(r, "docId")
	if !h.verifyDocumentWorkspace(w, r, workspaceID, docID) {
		return
	}

	targetURL := fmt.Sprintf("%s/documents/%s/download", h.AIServiceURL, url.PathEscape(docID))
	outReq, err := http.NewRequestWithContext(r.Context(), http.MethodGet, targetURL, nil)
	if err != nil {
		jsonError(w, "Failed to construct AI document download request", http.StatusInternalServerError)
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
	w.Header().Set("Content-Type", "application/pdf")
	if disposition := resp.Header.Get("Content-Disposition"); disposition != "" {
		w.Header().Set("Content-Disposition", disposition)
	}
	if contentLength := resp.Header.Get("Content-Length"); contentLength != "" {
		w.Header().Set("Content-Length", contentLength)
	}
	w.WriteHeader(http.StatusOK)
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

	if !h.verifyDocumentWorkspace(w, r, workspaceID, docID) {
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

	if !h.verifyDocumentWorkspace(w, r, workspaceID, docID) {
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

	if !h.verifyDocumentWorkspace(w, r, workspaceID, docID) {
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
	if len(req.Question) > 2000 {
		jsonError(w, "Question exceeds maximum allowed length of 2,000 characters", http.StatusBadRequest)
		return
	}

	if req.TopK <= 0 {
		req.TopK = 5
	} else if req.TopK > 20 {
		req.TopK = 20
	}

	// Forward query to Python AI service, with mandatory verified workspace_id
	payload := map[string]interface{}{
		"question":             req.Question,
		"top_k":                req.TopK,
		"workspace_id":         workspaceID,
		"similarity_threshold": req.SimilarityThreshold,
	}
	if len(req.DocumentIDs) > 0 {
		payload["document_ids"] = req.DocumentIDs
	}
	if req.EnableHybrid != nil {
		payload["enable_hybrid"] = *req.EnableHybrid
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
