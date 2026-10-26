package handlers

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/go-chi/chi/v5"
)

func requestWithDocumentRouteParams(workspaceID, documentID string) *http.Request {
	request := httptest.NewRequest(http.MethodGet, "/", nil)
	routeContext := chi.NewRouteContext()
	routeContext.URLParams.Add("workspaceId", workspaceID)
	routeContext.URLParams.Add("docId", documentID)
	return request.WithContext(context.WithValue(request.Context(), chi.RouteCtxKey, routeContext))
}

func TestDownloadDocumentStreamsWorkspaceDocument(t *testing.T) {
	const pdf = "%PDF-1.4 private document"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/documents/doc-1":
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]string{"workspace_id": "ws-1"})
		case "/documents/doc-1/download":
			w.Header().Set("Content-Disposition", `attachment; filename="report.pdf"`)
			_, _ = w.Write([]byte(pdf))
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	handler := &ProxyHandler{AIServiceURL: server.URL, client: server.Client()}
	response := httptest.NewRecorder()
	handler.DownloadDocument(response, requestWithDocumentRouteParams("ws-1", "doc-1"))

	if response.Code != http.StatusOK {
		t.Fatalf("expected status 200, got %d: %s", response.Code, response.Body.String())
	}
	if response.Body.String() != pdf {
		t.Fatalf("expected streamed PDF body %q, got %q", pdf, response.Body.String())
	}
	if got := response.Header().Get("Content-Disposition"); got != `attachment; filename="report.pdf"` {
		t.Fatalf("unexpected content disposition: %q", got)
	}
}

func TestDownloadDocumentRejectsCrossWorkspaceDocument(t *testing.T) {
	downloadRequested := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/documents/doc-1" {
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]string{"workspace_id": "ws-other"})
			return
		}
		if r.URL.Path == "/documents/doc-1/download" {
			downloadRequested = true
		}
		http.NotFound(w, r)
	}))
	defer server.Close()

	handler := &ProxyHandler{AIServiceURL: server.URL, client: server.Client()}
	response := httptest.NewRecorder()
	handler.DownloadDocument(response, requestWithDocumentRouteParams("ws-1", "doc-1"))

	if response.Code != http.StatusNotFound {
		t.Fatalf("expected status 404, got %d: %s", response.Code, response.Body.String())
	}
	if downloadRequested {
		t.Fatal("download endpoint must not be called for a document in another workspace")
	}
}
