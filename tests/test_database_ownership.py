from database import Base, Document


def test_identity_and_workspace_tables_are_owned_by_go():
    table_names = set(Base.metadata.tables)

    assert "users" not in table_names
    assert "workspaces" not in table_names
    assert "workspace_members" not in table_names
    assert {"documents", "document_jobs", "eval_runs", "eval_item_results"} <= table_names


def test_document_metadata_keeps_go_workspace_reference_for_rag():
    document_columns = set(Document.__table__.columns.keys())

    assert "owner_id" in document_columns
    assert "workspace_id" in document_columns
