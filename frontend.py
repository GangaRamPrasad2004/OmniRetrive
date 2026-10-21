import os
import time
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="OmniRetrive | Document Intelligence",
    page_icon="📄",
    layout="wide",
)

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
INNGEST_EVENT_KEY = os.getenv("INNGEST_EVENT_KEY", "").strip()
INNGEST_SIGNING_KEY = os.getenv("INNGEST_SIGNING_KEY", "").strip()
INNGEST_API_BASE = os.getenv("INNGEST_API_BASE", "http://127.0.0.1:8288/v1").strip()


# ── Custom Styling ─────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    .metric-card {
        background-color: #f8f9fa;
        border-radius: 8px;
        padding: 14px 18px;
        border: 1px solid #e9ecef;
        margin-bottom: 10px;
    }
    .badge {
        display: inline-block;
        padding: 3px 8px;
        border-radius: 12px;
        font-size: 0.8rem;
        font-weight: 600;
    }
    .badge-completed { background-color: #d1e7dd; color: #0f5132; }
    .badge-processing { background-color: #cff4fc; color: #055160; }
    .badge-queued { background-color: #fff3cd; color: #664d03; }
    .badge-retrying { background-color: #ffe5d0; color: #8a3b00; }
    .badge-failed { background-color: #f8d7da; color: #842029; }
    .badge-ready { background-color: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
    .doc-item {
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 16px;
        margin-bottom: 12px;
        background-color: #ffffff;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ── Backend Client Helpers ─────────────────────────────────────────────────────
def upload_pdf_to_backend(file) -> dict:
    resp = requests.post(
        f"{BACKEND_URL}/upload",
        files={"file": (file.name, file.getbuffer(), "application/pdf")},
        data={"workspace_id": "default_workspace", "owner_id": "default_user"},
        timeout=60,
    )
    if resp.status_code not in (200, 201):
        try:
            err = resp.json().get("detail", resp.text)
        except Exception:
            err = resp.text
        raise RuntimeError(err)
    return resp.json()


def fetch_documents(search=None, status_filter=None, sort_by="created_at", sort_order="desc") -> list:
    params = {"sort_by": sort_by, "sort_order": sort_order}
    if search:
        params["search"] = search
    if status_filter and status_filter != "All":
        params["status"] = status_filter
    try:
        resp = requests.get(f"{BACKEND_URL}/documents", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json().get("documents", [])
    except Exception as e:
        st.error(f"Failed to fetch documents from backend: {e}")
        return []


def fetch_doc_details(doc_id: str) -> dict:
    resp = requests.get(f"{BACKEND_URL}/documents/{doc_id}", timeout=10)
    resp.raise_for_status()
    return resp.json()


def retry_backend_job(doc_id: str) -> dict:
    resp = requests.post(f"{BACKEND_URL}/documents/{doc_id}/retry", timeout=15)
    if resp.status_code not in (200, 201):
        try:
            err = resp.json().get("detail", resp.text)
        except Exception:
            err = resp.text
        raise RuntimeError(err)
    return resp.json()


def delete_backend_doc(doc_id: str) -> dict:
    resp = requests.delete(f"{BACKEND_URL}/documents/{doc_id}", timeout=15)
    resp.raise_for_status()
    return resp.json()


def format_size(bytes_len: int) -> str:
    if not bytes_len:
        return "0 B"
    if bytes_len < 1024:
        return f"{bytes_len} B"
    elif bytes_len < 1024 * 1024:
        return f"{bytes_len / 1024:.1f} KB"
    else:
        return f"{bytes_len / (1024 * 1024):.2f} MB"


def get_status_badge_html(status_val: str, is_ready: bool) -> str:
    badges = {
        "completed": '<span class="badge badge-completed">Completed</span>',
        "processing": '<span class="badge badge-processing">Processing</span>',
        "queued": '<span class="badge badge-queued">Queued</span>',
        "retrying": '<span class="badge badge-retrying">Retrying</span>',
        "failed": '<span class="badge badge-failed">Failed</span>',
    }
    badge = badges.get(status_val, f'<span class="badge">{status_val}</span>')
    if is_ready:
        badge += ' <span class="badge badge-ready">Ready for Q&A</span>'
    return badge


# ── Inngest Query Helpers ──────────────────────────────────────────────────────
def send_rag_query_event(question: str, top_k: int) -> str:
    if INNGEST_EVENT_KEY:
        url = f"https://inn.gs/e/{INNGEST_EVENT_KEY}"
    else:
        url = f"{INNGEST_API_BASE}/e/rag_app"

    resp = requests.post(
        url,
        json={
            "name": "rag/query_pdf_ai",
            "data": {
                "question": question,
                "top_k": top_k,
            },
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    ids = data.get("ids", [])
    return ids[0] if ids else data.get("id", "")


def fetch_runs(event_id: str) -> list:
    url = f"{INNGEST_API_BASE}/events/{event_id}/runs"
    headers = {}
    if INNGEST_SIGNING_KEY:
        headers["Authorization"] = f"Bearer {INNGEST_SIGNING_KEY}"
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json().get("data", [])


def wait_for_run_output(event_id: str, timeout_s: float = 120.0, poll_interval_s: float = 1.0) -> dict:
    start = time.time()
    last_status = None
    while True:
        try:
            runs = fetch_runs(event_id)
            if runs:
                run = runs[0]
                status_run = run.get("status")
                last_status = status_run or last_status
                if status_run in ("Completed", "Succeeded", "Success", "Finished"):
                    return run.get("output") or {}
                if status_run in ("Failed", "Cancelled"):
                    raise RuntimeError(f"Workflow execution ended with status: {status_run}")
        except Exception as e:
            if "ended with status" in str(e):
                raise
        if time.time() - start > timeout_s:
            raise TimeoutError(f"Timed out waiting for run output (last status: {last_status})")
        time.sleep(poll_interval_s)


# ── UI Header & Navigation ─────────────────────────────────────────────────────
st.title("OmniRetrive")
st.caption("AI-Powered Document Intelligence & Verification Platform")

tab_docs, tab_qa = st.tabs(["📚 Document Management", "💬 Question Answering"])

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1: DOCUMENT MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────
with tab_docs:
    st.subheader("Document Ingestion & Lifecycle")

    # Upload Section
    with st.expander("⬆️ Upload New PDF Document", expanded=True):
        uploaded_file = st.file_uploader(
            "Choose a PDF file to ingest",
            type=["pdf"],
            accept_multiple_files=False,
            help="Supported formats: PDF. Maximum size: 50 MB. Integrity and readability are validated automatically.",
        )

        col_up1, col_up2 = st.columns([1, 4])
        with col_up1:
            upload_btn = st.button("Upload & Ingest", type="primary", disabled=uploaded_file is None)

        if upload_btn and uploaded_file is not None:
            with st.spinner("Validating and uploading document..."):
                try:
                    res = upload_pdf_to_backend(uploaded_file)
                    st_status = res.get("status")
                    if st_status == "already_exists":
                        st.info(f"ℹ️ {res.get('message')}")
                    elif st_status == "already_processing":
                        st.warning(f"⏳ {res.get('message')}")
                    elif st_status == "retrying":
                        st.success(f"🔄 {res.get('message')}")
                    else:
                        st.success(f"✅ Document '{res.get('filename')}' queued for ingestion!")
                    time.sleep(0.5)
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Upload failed: {str(e)}")

    st.divider()

    # Search, Filters, and Sorting Bar
    f_col1, f_col2, f_col3, f_col4, f_col5 = st.columns([3, 2, 2, 1, 1])
    with f_col1:
        search_query = st.text_input("🔍 Search documents by filename", "")
    with f_col2:
        status_choice = st.selectbox(
            "Status",
            ["All", "completed", "processing", "queued", "retrying", "failed"],
        )
    with f_col3:
        sort_choice = st.selectbox(
            "Sort By",
            ["created_at", "filename", "file_size", "status"],
            format_func=lambda x: {
                "created_at": "Upload Date",
                "filename": "Filename",
                "file_size": "File Size",
                "status": "Status",
            }.get(x, x),
        )
    with f_col4:
        sort_dir = st.selectbox("Order", ["desc", "asc"])
    with f_col5:
        st.write("")
        st.write("")
        if st.button("🔄 Refresh"):
            st.rerun()

    # Fetch Documents
    docs = fetch_documents(
        search=search_query.strip() or None,
        status_filter=status_choice,
        sort_by=sort_choice,
        sort_order=sort_dir,
    )

    # Metric summary
    total_docs = len(docs)
    ready_docs = sum(1 for d in docs if d.get("is_ready_for_qa"))
    pending_docs = sum(1 for d in docs if d.get("status") in ("queued", "processing", "retrying"))
    failed_docs = sum(1 for d in docs if d.get("status") == "failed")

    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    m_col1.metric("Total Documents", total_docs)
    m_col2.metric("Ready for Q&A", ready_docs)
    m_col3.metric("In Progress / Queued", pending_docs)
    m_col4.metric("Failed", failed_docs)

    st.write("")

    if not docs:
        st.info("No documents found matching your filter criteria. Upload a PDF above to get started!")
    else:
        for doc in docs:
            doc_id = doc["id"]
            filename = doc["filename"]
            status_val = doc.get("status", "unknown")
            is_ready = doc.get("is_ready_for_qa", False)
            chunks = doc.get("chunk_count", 0)
            pages = doc.get("page_count", 0)
            size_str = format_size(doc.get("file_size", 0))
            created_at = doc.get("created_at", "")[:19].replace("T", " ")
            err_msg = doc.get("error_message")

            with st.container():
                st.markdown(
                    f"""
                    <div class="doc-item">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div>
                                <strong style="font-size: 1.1rem;">📄 {filename}</strong>
                                <span style="margin-left: 10px;">{get_status_badge_html(status_val, is_ready)}</span>
                            </div>
                            <span style="color: #6c757d; font-size: 0.85rem;">Uploaded: {created_at} UTC</span>
                        </div>
                        <div style="margin-top: 8px; color: #495057; font-size: 0.9rem;">
                            <span>📦 <strong>Size:</strong> {size_str}</span> &nbsp;|&nbsp;
                            <span>📑 <strong>Pages:</strong> {pages}</span> &nbsp;|&nbsp;
                            <span>🧩 <strong>Indexed Chunks:</strong> {chunks}</span> &nbsp;|&nbsp;
                            <span>🏷️ <strong>ID:</strong> <code>{doc_id[:8]}...</code></span>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                # Show failure notice if failed
                if status_val == "failed" and err_msg:
                    st.error(f"⚠️ Processing Error: {err_msg}")

                # Action buttons row
                act_col1, act_col2, act_col3 = st.columns([2, 2, 6])

                if status_val in ("failed", "queued"):
                    with act_col1:
                        if st.button("🔄 Retry Ingestion", key=f"retry_{doc_id}"):
                            with st.spinner("Retrying job..."):
                                try:
                                    retry_res = retry_backend_job(doc_id)
                                    st.success(retry_res.get("message", "Job retried."))
                                    time.sleep(0.5)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Failed to retry: {e}")

                with act_col2:
                    if st.button("🗑️ Delete", key=f"del_{doc_id}", help="Delete document, vectors, and files"):
                        with st.spinner("Deleting document and vector records..."):
                            try:
                                delete_backend_doc(doc_id)
                                st.success("Document and derived vector data deleted.")
                                time.sleep(0.5)
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed to delete: {e}")

                with st.expander(f"Inspect Details for '{filename}'"):
                    try:
                        detail = fetch_doc_details(doc_id)
                        st.json({
                            "document_id": detail.get("id"),
                            "filename": detail.get("filename"),
                            "sha256_hash": detail.get("file_hash"),
                            "file_size_bytes": detail.get("file_size"),
                            "storage_path": detail.get("storage_path"),
                            "status": detail.get("status"),
                            "chunk_count": detail.get("chunk_count"),
                            "page_count": detail.get("page_count"),
                            "is_ready_for_qa": detail.get("is_ready_for_qa"),
                            "created_at": detail.get("created_at"),
                            "updated_at": detail.get("updated_at"),
                            "jobs_history": detail.get("jobs", []),
                        })
                    except Exception as e:
                        st.warning(f"Could not load details: {e}")

                st.write("")


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2: QUESTION ANSWERING (RAG)
# ─────────────────────────────────────────────────────────────────────────────
with tab_qa:
    st.subheader("Ask Questions About Your Ingested Documents")

    # Check for ready documents
    all_docs = fetch_documents()
    ready_count = sum(1 for d in all_docs if d.get("is_ready_for_qa"))

    if ready_count == 0:
        st.warning(
            "⚠️ No documents are currently ready for Q&A. Please upload and ingest a PDF in the 'Document Management' tab first."
        )
    else:
        st.info(f"💡 You have {ready_count} indexed document(s) ready for querying.")

    with st.form("rag_query_form"):
        question = st.text_input("Your question", placeholder="e.g. What are the key findings discussed in the paper?")
        top_k = st.number_input(
            "Context Chunks to Retrieve",
            min_value=1,
            max_value=20,
            value=5,
            step=1,
            help="Higher values retrieve more surrounding text from Qdrant.",
        )
        submitted = st.form_submit_button("Ask Question", type="primary")

        if submitted and question.strip():
            with st.spinner("Searching vector database and generating answer with LLM..."):
                try:
                    event_id = send_rag_query_event(question.strip(), int(top_k))
                    output = wait_for_run_output(event_id)
                    answer = output.get("answer", "")
                    sources = output.get("sources", [])
                    num_ctx = output.get("num_contexts", 0)

                    st.markdown("### 💬 Answer")
                    st.write(answer or "(No answer generated)")

                    if sources:
                        st.markdown("#### 📚 Sources & Citations")
                        for s in sources:
                            st.write(f"- `{s}`")
                        st.caption(f"Retrieved from {num_ctx} context chunks in Qdrant.")
                except Exception as e:
                    st.error(f"❌ Error during query processing: {str(e)}")
