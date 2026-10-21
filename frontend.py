import os
import time
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="OmniRetrive | Enterprise Document Intelligence",
    page_icon="🛡️",
    layout="wide",
)

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8080").rstrip("/")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
INNGEST_EVENT_KEY = os.getenv("INNGEST_EVENT_KEY", "").strip()
INNGEST_SIGNING_KEY = os.getenv("INNGEST_SIGNING_KEY", "").strip()
INNGEST_API_BASE = os.getenv("INNGEST_API_BASE", "http://127.0.0.1:8288/v1").strip()


# ── Session State Initialization ───────────────────────────────────────────────
if "auth_token" not in st.session_state:
    st.session_state.auth_token = None
if "current_user" not in st.session_state:
    st.session_state.current_user = None
if "active_workspace" not in st.session_state:
    st.session_state.active_workspace = None
if "workspaces" not in st.session_state:
    st.session_state.workspaces = []


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
    .badge-cancelled { background-color: #e2e3e5; color: #41464b; }
    .badge-ready { background-color: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
    .badge-role-owner { background-color: #cff4fc; color: #055160; font-weight: bold; }
    .badge-role-admin { background-color: #e2d9f3; color: #491217; }
    .badge-role-member { background-color: #e2e3e5; color: #41464b; }
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


# ── API Client Helpers with Tenant Isolation ──────────────────────────────────
def get_auth_headers() -> dict:
    if st.session_state.auth_token:
        return {"Authorization": f"Bearer {st.session_state.auth_token}"}
    return {}


def is_gateway_alive() -> bool:
    try:
        r = requests.get(f"{GATEWAY_URL}/health", timeout=1.5)
        return r.status_code == 200
    except Exception:
        return False


def login_user(email, password) -> dict:
    resp = requests.post(
        f"{GATEWAY_URL}/api/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    if resp.status_code != 200:
        err = resp.json().get("error", "Login failed")
        raise RuntimeError(err)
    return resp.json()


def register_user(email, password, full_name) -> dict:
    resp = requests.post(
        f"{GATEWAY_URL}/api/auth/register",
        json={"email": email, "password": password, "full_name": full_name},
        timeout=10,
    )
    if resp.status_code != 201:
        err = resp.json().get("error", "Registration failed")
        raise RuntimeError(err)
    return resp.json()


def fetch_user_workspaces() -> list:
    if not st.session_state.auth_token:
        return []
    try:
        resp = requests.get(
            f"{GATEWAY_URL}/api/workspaces",
            headers=get_auth_headers(),
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return []


def create_new_workspace(name: str) -> dict:
    resp = requests.post(
        f"{GATEWAY_URL}/api/workspaces",
        headers=get_auth_headers(),
        json={"name": name},
        timeout=10,
    )
    if resp.status_code != 201:
        raise RuntimeError(resp.json().get("error", "Failed to create workspace"))
    return resp.json()


def fetch_workspace_details(workspace_id: str) -> dict:
    resp = requests.get(
        f"{GATEWAY_URL}/api/workspaces/{workspace_id}",
        headers=get_auth_headers(),
        timeout=10,
    )
    if resp.status_code == 200:
        return resp.json()
    return {}


def add_workspace_member(workspace_id: str, email: str, role: str) -> dict:
    resp = requests.post(
        f"{GATEWAY_URL}/api/workspaces/{workspace_id}/members",
        headers=get_auth_headers(),
        json={"email": email, "role": role},
        timeout=10,
    )
    if resp.status_code != 201:
        raise RuntimeError(resp.json().get("error", "Failed to add member"))
    return resp.json()


def delete_workspace_api(workspace_id: str) -> dict:
    resp = requests.delete(
        f"{GATEWAY_URL}/api/workspaces/{workspace_id}",
        headers=get_auth_headers(),
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(resp.json().get("error", "Failed to delete workspace"))
    return resp.json()


def upload_pdf_tenant(file, workspace_id: str) -> dict:
    headers = get_auth_headers()
    if is_gateway_alive() and st.session_state.auth_token:
        url = f"{GATEWAY_URL}/api/workspaces/{workspace_id}/documents/upload"
        data = {}
    else:
        url = f"{BACKEND_URL}/upload"
        data = {"workspace_id": workspace_id, "owner_id": st.session_state.current_user["id"] if st.session_state.current_user else "default_user"}

    resp = requests.post(
        url,
        headers=headers,
        files={"file": (file.name, file.getbuffer(), "application/pdf")},
        data=data,
        timeout=60,
    )
    if resp.status_code not in (200, 201):
        try:
            err = resp.json().get("error") or resp.json().get("detail") or resp.text
        except Exception:
            err = resp.text
        raise RuntimeError(err)
    return resp.json()


def fetch_documents_tenant(workspace_id: str, search=None, status_filter=None, sort_by="created_at", sort_order="desc") -> list:
    params = {"sort_by": sort_by, "sort_order": sort_order}
    if search:
        params["search"] = search
    if status_filter and status_filter != "All":
        params["status"] = status_filter

    headers = get_auth_headers()
    if is_gateway_alive() and st.session_state.auth_token:
        url = f"{GATEWAY_URL}/api/workspaces/{workspace_id}/documents"
    else:
        url = f"{BACKEND_URL}/documents"
        params["workspace_id"] = workspace_id

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json().get("documents", [])
    except Exception as e:
        st.error(f"Failed to fetch documents: {e}")
        return []


def retry_doc_tenant(workspace_id: str, doc_id: str) -> dict:
    headers = get_auth_headers()
    if is_gateway_alive() and st.session_state.auth_token:
        url = f"{GATEWAY_URL}/api/workspaces/{workspace_id}/documents/{doc_id}/retry"
    else:
        url = f"{BACKEND_URL}/documents/{doc_id}/retry"

    resp = requests.post(url, headers=headers, timeout=15)
    if resp.status_code not in (200, 201):
        err = resp.json().get("error") or resp.json().get("detail") or resp.text
        raise RuntimeError(err)
    return resp.json()


def delete_doc_tenant(workspace_id: str, doc_id: str) -> dict:
    headers = get_auth_headers()
    if is_gateway_alive() and st.session_state.auth_token:
        url = f"{GATEWAY_URL}/api/workspaces/{workspace_id}/documents/{doc_id}"
    else:
        url = f"{BACKEND_URL}/documents/{doc_id}"

    resp = requests.delete(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()


def cancel_doc_tenant(workspace_id: str, doc_id: str) -> dict:
    headers = get_auth_headers()
    if is_gateway_alive() and st.session_state.auth_token:
        url = f"{GATEWAY_URL}/api/workspaces/{workspace_id}/documents/{doc_id}/cancel"
    else:
        url = f"{BACKEND_URL}/documents/{doc_id}/cancel"

    resp = requests.post(url, headers=headers, timeout=15)
    if resp.status_code not in (200, 201):
        err = resp.json().get("error") or resp.json().get("detail") or resp.text
        raise RuntimeError(err)
    return resp.json()


def query_rag_tenant(workspace_id: str, question: str, top_k: int, document_ids: list = None) -> dict:
    headers = get_auth_headers()
    if is_gateway_alive() and st.session_state.auth_token:
        url = f"{GATEWAY_URL}/api/workspaces/{workspace_id}/query"
    else:
        url = f"{BACKEND_URL}/query"

    payload = {"question": question, "top_k": top_k, "workspace_id": workspace_id}
    if document_ids:
        payload["document_ids"] = document_ids

    resp = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=45,
    )
    if resp.status_code != 200:
        err = resp.json().get("error") or resp.json().get("detail") or resp.text
        raise RuntimeError(err)
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
        "cancelled": '<span class="badge badge-cancelled">Cancelled</span>',
    }
    badge = badges.get(status_val, f'<span class="badge">{status_val}</span>')
    if is_ready:
        badge += ' <span class="badge badge-ready">Ready for Q&A</span>'
    return badge


# ── SIDEBAR: AUTHENTICATION & WORKSPACE MULTI-TENANCY ──────────────────────────
with st.sidebar:
    st.title("🛡️ Tenant Isolation")
    st.caption("Secure Multi-Tenant Workspace Control")

    gateway_ok = is_gateway_alive()
    if gateway_ok:
        st.success("🟢 Go Gateway Active")
    else:
        st.info("ℹ️ Running in Direct Backend Mode")

    if not st.session_state.auth_token:
        auth_mode = st.radio("Access Control", ["Login", "Register"], horizontal=True)

        if auth_mode == "Login":
            st.subheader("Login")
            l_email = st.text_input("Email", key="l_email")
            l_pass = st.text_input("Password", type="password", key="l_pass")
            if st.button("Log In", type="primary", use_container_width=True):
                if l_email and l_pass:
                    try:
                        res = login_user(l_email, l_pass)
                        st.session_state.auth_token = res["token"]
                        st.session_state.current_user = res["user"]
                        st.session_state.active_workspace = res.get("default_workspace")
                        st.success("Authenticated successfully!")
                        time.sleep(0.4)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Login failed: {e}")
                else:
                    st.warning("Please fill in email and password.")

        else:
            st.subheader("Create Account")
            r_name = st.text_input("Full Name", key="r_name")
            r_email = st.text_input("Email", key="r_email")
            r_pass = st.text_input("Password (min 8 chars)", type="password", key="r_pass")
            if st.button("Register Account", type="primary", use_container_width=True):
                if r_email and r_pass:
                    try:
                        res = register_user(r_email, r_pass, r_name)
                        st.session_state.auth_token = res["token"]
                        st.session_state.current_user = res["user"]
                        st.session_state.active_workspace = res.get("default_workspace")
                        st.success("Account created and workspace initialized!")
                        time.sleep(0.4)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Registration failed: {e}")
                else:
                    st.warning("Please provide email and password.")

    else:
        # User is authenticated
        user = st.session_state.current_user
        st.markdown(f"**User:** `{user.get('full_name')}`")
        st.caption(f"📧 {user.get('email')}")

        if st.button("Logout", use_container_width=True):
            st.session_state.auth_token = None
            st.session_state.current_user = None
            st.session_state.active_workspace = None
            st.session_state.workspaces = []
            st.rerun()

        st.divider()

        # Workspace Selection
        workspaces = fetch_user_workspaces()
        st.session_state.workspaces = workspaces

        if workspaces:
            ws_map = {w["name"]: w for w in workspaces}
            ws_names = list(ws_map.keys())
            current_ws_name = st.session_state.active_workspace["name"] if st.session_state.active_workspace else ws_names[0]
            selected_ws_name = st.selectbox("Active Workspace", ws_names, index=ws_names.index(current_ws_name) if current_ws_name in ws_names else 0)
            st.session_state.active_workspace = ws_map[selected_ws_name]
        else:
            if not st.session_state.active_workspace:
                st.session_state.active_workspace = {"id": "default_workspace", "name": "Default Workspace"}

        active_ws_id = st.session_state.active_workspace["id"]
        st.caption(f"🆔 **Workspace ID:** `{active_ws_id}`")

        # Workspace Details & Management
        if active_ws_id != "default_workspace":
            ws_details = fetch_workspace_details(active_ws_id)
            current_role = ws_details.get("current_role", "member")
            st.markdown(f"**Your Role:** <span class='badge badge-role-{current_role}'>{current_role.upper()}</span>", unsafe_allow_html=True)

            with st.expander("👥 Workspace Members"):
                for m in ws_details.get("members", []):
                    st.write(f"- **{m.get('full_name') or m.get('email')}** (`{m.get('role')}`)")

            if current_role in ("owner", "admin"):
                with st.expander("➕ Invite Member"):
                    inv_email = st.text_input("Member Email", key="inv_email")
                    inv_role = st.selectbox("Role", ["member", "admin"] if current_role == "owner" else ["member"], key="inv_role")
                    if st.button("Add to Workspace"):
                        if inv_email:
                            try:
                                add_workspace_member(active_ws_id, inv_email, inv_role)
                                st.success("Member added!")
                                time.sleep(0.4)
                                st.rerun()
                            except Exception as e:
                                st.error(str(e))

            if current_role == "owner":
                with st.expander("⚠️ Danger Zone"):
                    if st.button("Delete Entire Workspace", type="secondary"):
                        try:
                            delete_workspace_api(active_ws_id)
                            st.success("Workspace deleted.")
                            st.session_state.active_workspace = None
                            time.sleep(0.5)
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))

        with st.expander("🏢 Create Another Workspace"):
            new_ws_name = st.text_input("Workspace Name", key="new_ws_name")
            if st.button("Create"):
                if new_ws_name.strip():
                    try:
                        new_ws = create_new_workspace(new_ws_name.strip())
                        st.session_state.active_workspace = new_ws
                        st.success(f"Workspace '{new_ws['name']}' created!")
                        time.sleep(0.4)
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))


# ── MAIN APPLICATION VIEW ──────────────────────────────────────────────────────
active_ws = st.session_state.active_workspace or {"id": "default_workspace", "name": "Default Workspace"}
active_ws_id = active_ws["id"]

st.title("OmniRetrive")
st.markdown(f"### 🏢 Workspace: **{active_ws['name']}**")
st.caption(f"🔒 Multi-Tenant Boundary: All uploads, embeddings, and vector queries are strictly isolated to `{active_ws_id}`.")

tab_docs, tab_qa = st.tabs(["📚 Workspace Documents", "💬 Tenant-Isolated Q&A"])

# ─────────────────────────────────────────────────────────────────────────────
# TAB 1: TENANT DOCUMENT MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────
with tab_docs:
    st.subheader(f"Documents in '{active_ws['name']}'")

    # Upload Section
    with st.expander("⬆️ Upload PDF to Active Workspace", expanded=True):
        uploaded_file = st.file_uploader(
            "Choose a PDF file to ingest",
            type=["pdf"],
            accept_multiple_files=False,
            help="PDF only. Max size: 50 MB. Integrity and readability are validated automatically.",
        )

        col_up1, _ = st.columns([1, 4])
        with col_up1:
            upload_btn = st.button("Upload & Ingest", type="primary", disabled=uploaded_file is None)

        if upload_btn and uploaded_file is not None:
            with st.spinner("Validating and uploading document to workspace..."):
                try:
                    res = upload_pdf_tenant(uploaded_file, active_ws_id)
                    st_status = res.get("status")
                    if st_status == "already_exists":
                        st.info(f"ℹ️ {res.get('message')}")
                    elif st_status == "already_processing":
                        st.warning(f"⏳ {res.get('message')}")
                    elif st_status == "retrying":
                        st.success(f"🔄 {res.get('message')}")
                    else:
                        st.success(f"✅ Document '{res.get('filename')}' queued for workspace ingestion!")
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

    # Fetch Documents strictly for this tenant
    docs = fetch_documents_tenant(
        workspace_id=active_ws_id,
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
    m_col1.metric("Tenant Documents", total_docs)
    m_col2.metric("Ready for Q&A", ready_docs)
    m_col3.metric("In Progress", pending_docs)
    m_col4.metric("Failed", failed_docs)

    st.write("")

    if not docs:
        st.info(f"No documents found in workspace '{active_ws['name']}'. Upload a PDF above to begin.")
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
                            <span>🏢 <strong>Workspace:</strong> <code>{active_ws_id}</code></span>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                if status_val == "failed" and err_msg:
                    st.error(f"⚠️ Processing Error: {err_msg}")

                act_col1, act_col2, act_col3, _ = st.columns([2, 2, 2, 4])

                if status_val in ("failed", "cancelled"):
                    with act_col1:
                        if st.button("🔄 Retry", key=f"retry_{doc_id}"):
                            with st.spinner("Retrying job..."):
                                try:
                                    retry_res = retry_doc_tenant(active_ws_id, doc_id)
                                    st.success(retry_res.get("message", "Job retried."))
                                    time.sleep(0.5)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Failed to retry: {e}")

                if status_val in ("queued", "processing", "retrying"):
                    with act_col2:
                        if st.button("🛑 Cancel", key=f"cancel_{doc_id}", help="Cancel in-flight job and clean up vectors"):
                            with st.spinner("Cancelling job..."):
                                try:
                                    cancel_res = cancel_doc_tenant(active_ws_id, doc_id)
                                    st.info(cancel_res.get("message", "Job cancelled."))
                                    time.sleep(0.5)
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Failed to cancel: {e}")

                with act_col3:
                    if st.button("🗑️ Delete", key=f"del_{doc_id}", help="Delete document and workspace vectors"):
                        with st.spinner("Deleting document and vector records..."):
                            try:
                                delete_doc_tenant(active_ws_id, doc_id)
                                st.success("Document and derived vector data deleted.")
                                time.sleep(0.5)
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed to delete: {e}")



# ─────────────────────────────────────────────────────────────────────────────
# TAB 2: TENANT-ISOLATED QUESTION ANSWERING (RAG)
# ─────────────────────────────────────────────────────────────────────────────
with tab_qa:
    st.subheader(f"Ask Questions About '{active_ws['name']}' Documents")
    st.caption("🔒 Vector search strictly applies `workspace_id` filtering. Data from other tenants is completely inaccessible.")

    # Check for ready documents in this tenant
    ready_count = sum(1 for d in docs if d.get("is_ready_for_qa"))
    if ready_count == 0:
        st.warning(
            f"⚠️ No documents are currently ready for Q&A in '{active_ws['name']}'. Please upload and ingest a PDF in the 'Workspace Documents' tab first."
        )
    else:
        st.info(f"💡 You have {ready_count} indexed document(s) ready in this workspace.")

    # Ready document selection
    ready_docs_map = {d["id"]: d["filename"] for d in docs if d.get("is_ready_for_qa")}
    selected_doc_ids = []
    if ready_count > 1:
        target_mode = st.radio(
            "Query Target",
            ["All Ready Documents in Workspace", "Specific Document(s)"],
            horizontal=True,
        )
        if target_mode == "Specific Document(s)":
            chosen = st.multiselect(
                "Filter search to specific documents:",
                options=list(ready_docs_map.keys()),
                format_func=lambda x: ready_docs_map.get(x, x),
            )
            selected_doc_ids = chosen

    with st.form("rag_tenant_query_form"):
        question = st.text_input("Your question", placeholder="e.g. What are the key terms in Section 3?")
        q_col1, q_col2 = st.columns([1, 3])
        with q_col1:
            top_k = st.number_input(
                "Top-K Chunks",
                min_value=1,
                max_value=20,
                value=5,
                step=1,
            )
        with q_col2:
            st.caption("Hybrid search fuses dense semantic embeddings with lexical keyword matching for high recall.")
        submitted = st.form_submit_button("Ask Question", type="primary")

        if submitted and question.strip():
            with st.spinner(f"Querying Qdrant vectors strictly for workspace '{active_ws_id}'..."):
                try:
                    res = query_rag_tenant(
                        active_ws_id,
                        question.strip(),
                        int(top_k),
                        document_ids=selected_doc_ids if selected_doc_ids else None,
                    )
                    answer = res.get("answer", "")
                    sources = res.get("sources", [])
                    citations = res.get("citations", [])
                    num_ctx = res.get("num_contexts", 0)
                    is_insufficient = res.get("insufficient_context", False)

                    if is_insufficient:
                        st.warning("⚠️ **Context Insufficient**: The retrieved documents in this workspace do not contain sufficient evidence to answer this question.")

                    st.markdown("### 💬 Answer")
                    st.write(answer or "(No answer generated)")

                    if citations:
                        st.markdown(f"#### 📚 Verified Source Citations ({len(citations)})")
                        for idx, cit in enumerate(citations, start=1):
                            doc_name = cit.get("document_name", "Unknown")
                            page = cit.get("page_number", 1)
                            score = cit.get("score", 0.0)
                            heading = cit.get("heading")
                            snippet = cit.get("relevant_text", "")
                            heading_str = f" • *{heading}*" if heading else ""

                            with st.expander(f"Citation {idx}: 📄 {doc_name} — Page {page}{heading_str} (Score: {score:.2f})"):
                                st.markdown(f"**Document**: `{doc_name}` &nbsp;|&nbsp; **Page**: `{page}` &nbsp;|&nbsp; **Relevance Score**: `{score:.2f}`")
                                st.markdown(f"> {snippet}")
                        st.caption(f"Retrieved from {num_ctx} context chunks inside workspace '{active_ws_id}'. All citations verified against retrieved Qdrant vectors.")
                    elif sources:
                        st.markdown("#### 📚 Verified Workspace Sources")
                        for s in sources:
                            st.write(f"- `{s}`")
                except Exception as e:
                    st.error(f"❌ Query error: {str(e)}")
