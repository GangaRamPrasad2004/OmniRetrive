import streamlit as st
from dotenv import load_dotenv
import os
import requests
import time

load_dotenv()

st.set_page_config(page_title="OmniRetrive", layout="centered")

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")


def upload_pdf_to_backend(file) -> dict:
    """POST the PDF to the backend's /upload endpoint."""
    resp = requests.post(
        f"{BACKEND_URL}/upload",
        files={"file": (file.name, file.getbuffer(), "application/pdf")},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


st.title("Upload a PDF to Ingest")
uploaded = st.file_uploader("Choose a PDF", type=["pdf"], accept_multiple_files=False)

if uploaded is not None:
    with st.spinner("Uploading and triggering ingestion..."):
        result = upload_pdf_to_backend(uploaded)
        time.sleep(0.3)
    st.success(f"Triggered ingestion for: {result['filename']}")
    st.caption("You can upload another PDF if you like.")

st.divider()
st.title("Ask a question about your PDFs")


def _inngest_api_base() -> str:
    # In production this should point to the Inngest Cloud REST API.
    # Locally it points to the Inngest Dev Server.
    return os.getenv("INNGEST_API_BASE", "http://127.0.0.1:8288/v1")


def send_rag_query_event(question: str, top_k: int) -> str:
    """Send the query event via the Inngest REST API and return the event ID."""
    url = f"{_inngest_api_base()}/e/rag_app"
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
    # Inngest REST API returns {"ids": ["..."]} or {"status": 200, ...}
    ids = data.get("ids", [])
    return ids[0] if ids else data.get("id", "")


def fetch_runs(event_id: str) -> list[dict]:
    url = f"{_inngest_api_base()}/events/{event_id}/runs"
    resp = requests.get(url)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", [])


def wait_for_run_output(event_id: str, timeout_s: float = 120.0, poll_interval_s: float = 0.5) -> dict:
    start = time.time()
    last_status = None
    while True:
        runs = fetch_runs(event_id)
        if runs:
            run = runs[0]
            status = run.get("status")
            last_status = status or last_status
            if status in ("Completed", "Succeeded", "Success", "Finished"):
                return run.get("output") or {}
            if status in ("Failed", "Cancelled"):
                raise RuntimeError(f"Function run {status}")
        if time.time() - start > timeout_s:
            raise TimeoutError(f"Timed out waiting for run output (last status: {last_status})")
        time.sleep(poll_interval_s)


with st.form("rag_query_form"):
    question = st.text_input("Your question")
    top_k = st.number_input("How many chunks to retrieve", min_value=1, max_value=20, value=5, step=1)
    submitted = st.form_submit_button("Ask")

    if submitted and question.strip():
        with st.spinner("Sending event and generating answer..."):
            event_id = send_rag_query_event(question.strip(), int(top_k))
            output = wait_for_run_output(event_id)
            answer = output.get("answer", "")
            sources = output.get("sources", [])

        st.subheader("Answer")
        st.write(answer or "(No answer)")
        if sources:
            st.caption("Sources")
            for s in sources:
                st.write(f"- {s}")
