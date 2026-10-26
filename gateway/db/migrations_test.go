package db

import (
	"database/sql"
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"omniretrive/gateway/models"
)

func TestTernMigrationDefinesIdentityAndWorkspaceTables(t *testing.T) {
	migration, err := migrationFiles.ReadFile("migrations/001_create_identity_schema.sql")
	if err != nil {
		t.Fatalf("read embedded identity migration: %v", err)
	}

	upSQL, downSQL, found := strings.Cut(string(migration), "---- create above / drop below ----")
	if !found {
		t.Fatal("Tern migration is missing its up/down separator")
	}
	for _, table := range []string{"users", "workspaces", "workspace_members"} {
		if !strings.Contains(upSQL, "CREATE TABLE IF NOT EXISTS "+table) {
			t.Errorf("up migration does not create %s", table)
		}
		if !strings.Contains(downSQL, "DROP TABLE IF EXISTS "+table) {
			t.Errorf("down migration does not drop %s", table)
		}
	}
}

func TestMigrationsAreVersionedAndIdempotent(t *testing.T) {
	database, err := InitDB("sqlite://" + filepath.Join(t.TempDir(), "gateway.db"))
	if err != nil {
		t.Fatalf("initialize database: %v", err)
	}
	defer database.Close()

	now := time.Now().UTC()
	user := &models.User{
		ID:           "user-1",
		Email:        "user@example.com",
		PasswordHash: "hashed-password",
		FullName:     "Test User",
		CreatedAt:    now,
		UpdatedAt:    now,
	}
	if err := database.CreateUser(user); err != nil {
		t.Fatalf("create user: %v", err)
	}
	if err := database.migrate(); err != nil {
		t.Fatalf("re-run migrations: %v", err)
	}

	got, err := database.GetUserByID(user.ID)
	if err != nil {
		t.Fatalf("read user after migration rerun: %v", err)
	}
	if got.Email != user.Email {
		t.Errorf("user data changed after migration rerun: got %q, want %q", got.Email, user.Email)
	}

	var migrationCount int
	if err := database.QueryRow("SELECT COUNT(*) FROM schema_migrations").Scan(&migrationCount); err != nil {
		t.Fatalf("count schema migrations: %v", err)
	}
	if migrationCount != len(migrations) {
		t.Errorf("got %d migration records, want %d", migrationCount, len(migrations))
	}
}

func TestMigrationPreservesExistingGatewayData(t *testing.T) {
	path := filepath.Join(t.TempDir(), "legacy-gateway.db")
	legacy, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatalf("open legacy database: %v", err)
	}

	_, err = legacy.Exec(`
CREATE TABLE users (
	id TEXT PRIMARY KEY,
	email TEXT UNIQUE NOT NULL,
	password_hash TEXT NOT NULL,
	full_name TEXT NOT NULL,
	created_at TIMESTAMP NOT NULL,
	updated_at TIMESTAMP NOT NULL
);
CREATE TABLE workspaces (
	id TEXT PRIMARY KEY,
	name TEXT NOT NULL,
	owner_id TEXT NOT NULL,
	created_at TIMESTAMP NOT NULL,
	updated_at TIMESTAMP NOT NULL,
	FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE workspace_members (
	id TEXT PRIMARY KEY,
	workspace_id TEXT NOT NULL,
	user_id TEXT NOT NULL,
	role TEXT NOT NULL,
	created_at TIMESTAMP NOT NULL,
	UNIQUE(workspace_id, user_id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
	FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
`)
	if err != nil {
		t.Fatalf("create legacy gateway tables: %v", err)
	}
	now := time.Now().UTC()
	if _, err := legacy.Exec(
		"INSERT INTO users (id, email, password_hash, full_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
		"user-legacy", "legacy@example.com", "legacy-hash", "Legacy User", now, now,
	); err != nil {
		t.Fatalf("insert legacy user: %v", err)
	}
	if _, err := legacy.Exec(
		"INSERT INTO workspaces (id, name, owner_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
		"workspace-legacy", "Legacy Workspace", "user-legacy", now, now,
	); err != nil {
		t.Fatalf("insert legacy workspace: %v", err)
	}
	if _, err := legacy.Exec(
		"INSERT INTO workspace_members (id, workspace_id, user_id, role, created_at) VALUES (?, ?, ?, ?, ?)",
		"membership-legacy", "workspace-legacy", "user-legacy", "owner", now,
	); err != nil {
		t.Fatalf("insert legacy membership: %v", err)
	}
	if err := legacy.Close(); err != nil {
		t.Fatalf("close legacy database: %v", err)
	}

	database, err := InitDB("sqlite://" + path)
	if err != nil {
		t.Fatalf("migrate legacy database: %v", err)
	}
	defer database.Close()

	user, err := database.GetUserByID("user-legacy")
	if err != nil {
		t.Fatalf("read preserved legacy user: %v", err)
	}
	if user.Email != "legacy@example.com" {
		t.Errorf("legacy user changed: got %q", user.Email)
	}
	workspaces, err := database.ListWorkspacesForUser(user.ID)
	if err != nil {
		t.Fatalf("read preserved legacy workspaces: %v", err)
	}
	if len(workspaces) != 1 || workspaces[0].ID != "workspace-legacy" {
		t.Fatalf("legacy workspace not preserved: %#v", workspaces)
	}
}

func TestImportSQLiteIdentityDataIsIdempotent(t *testing.T) {
	legacyPath := filepath.Join(t.TempDir(), "legacy-gateway.db")
	legacy, err := sql.Open("sqlite", legacyPath)
	if err != nil {
		t.Fatalf("open legacy SQLite database: %v", err)
	}
	if _, err := legacy.Exec(`
CREATE TABLE users (
	id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
	full_name TEXT NOT NULL, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
);
CREATE TABLE workspaces (
	id TEXT PRIMARY KEY, name TEXT NOT NULL, owner_id TEXT NOT NULL,
	created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
	FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE workspace_members (
	id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, user_id TEXT NOT NULL,
	role TEXT NOT NULL, created_at TIMESTAMP NOT NULL,
	UNIQUE(workspace_id, user_id),
	FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
	FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);`); err != nil {
		t.Fatalf("create legacy identity schema: %v", err)
	}
	now := time.Now().UTC()
	if _, err := legacy.Exec(
		"INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
		"user-import", "import@example.com", "password-hash", "Import User", now, now,
	); err != nil {
		t.Fatalf("insert legacy user: %v", err)
	}
	if _, err := legacy.Exec(
		"INSERT INTO workspaces VALUES (?, ?, ?, ?, ?)",
		"workspace-import", "Imported workspace", "user-import", now, now,
	); err != nil {
		t.Fatalf("insert legacy workspace: %v", err)
	}
	if _, err := legacy.Exec(
		"INSERT INTO workspace_members VALUES (?, ?, ?, ?, ?)",
		"membership-import", "workspace-import", "user-import", "owner", now,
	); err != nil {
		t.Fatalf("insert legacy workspace membership: %v", err)
	}
	if err := legacy.Close(); err != nil {
		t.Fatalf("close legacy SQLite database: %v", err)
	}

	database, err := InitDB("sqlite://" + filepath.Join(t.TempDir(), "identity.db"))
	if err != nil {
		t.Fatalf("initialize destination database: %v", err)
	}
	defer database.Close()

	for range 2 {
		if err := database.ImportSQLiteIdentityData(legacyPath); err != nil {
			t.Fatalf("import legacy identity data: %v", err)
		}
	}

	user, err := database.GetUserByID("user-import")
	if err != nil {
		t.Fatalf("read imported user: %v", err)
	}
	if user.Email != "import@example.com" || user.PasswordHash != "password-hash" {
		t.Fatalf("imported user data mismatch: %#v", user)
	}
	workspaces, err := database.ListWorkspacesForUser(user.ID)
	if err != nil {
		t.Fatalf("read imported workspaces: %v", err)
	}
	if len(workspaces) != 1 || workspaces[0].ID != "workspace-import" {
		t.Fatalf("imported workspace data mismatch: %#v", workspaces)
	}
}

func TestImportSQLiteIdentityDataHandlesMissingAndIncompleteSources(t *testing.T) {
	database, err := InitDB("sqlite://" + filepath.Join(t.TempDir(), "identity.db"))
	if err != nil {
		t.Fatalf("initialize destination database: %v", err)
	}
	defer database.Close()

	if err := database.ImportSQLiteIdentityData(filepath.Join(t.TempDir(), "missing.db")); err != nil {
		t.Fatalf("missing legacy database should be ignored: %v", err)
	}

	legacyPath := filepath.Join(t.TempDir(), "incomplete.db")
	legacy, err := sql.Open("sqlite", legacyPath)
	if err != nil {
		t.Fatalf("open incomplete legacy database: %v", err)
	}
	if _, err := legacy.Exec(`
CREATE TABLE users (
	id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
	full_name TEXT NOT NULL, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
);
INSERT INTO users VALUES ('user-incomplete', 'incomplete@example.com', 'hash', 'Incomplete User', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
`); err != nil {
		_ = legacy.Close()
		t.Fatalf("create incomplete legacy schema: %v", err)
	}
	if err := legacy.Close(); err != nil {
		t.Fatalf("close incomplete legacy database: %v", err)
	}

	if err := database.ImportSQLiteIdentityData(legacyPath); err == nil {
		t.Fatal("expected incomplete legacy schema to fail explicitly")
	}
	if _, err := database.GetUserByID("user-incomplete"); err == nil {
		t.Fatal("failed legacy import should roll back all imported records")
	} else if !errors.Is(err, sql.ErrNoRows) {
		t.Fatalf("check rollback: got %v, want sql.ErrNoRows", err)
	}
}

func TestWorkspaceTablesEnforceRelationshipsAndRoles(t *testing.T) {
	database, err := InitDB("sqlite://" + filepath.Join(t.TempDir(), "gateway.db"))
	if err != nil {
		t.Fatalf("initialize database: %v", err)
	}
	defer database.Close()

	if _, err := database.Exec(
		"INSERT INTO workspace_members (id, workspace_id, user_id, role, created_at) VALUES (?, ?, ?, ?, ?)",
		"member-1", "missing-workspace", "missing-user", "owner", time.Now().UTC(),
	); err == nil {
		t.Fatal("expected workspace membership to reject missing foreign keys")
	}

	now := time.Now().UTC()
	user := &models.User{
		ID:           "user-1",
		Email:        "owner@example.com",
		PasswordHash: "hashed-password",
		FullName:     "Workspace Owner",
		CreatedAt:    now,
		UpdatedAt:    now,
	}
	if err := database.CreateUser(user); err != nil {
		t.Fatalf("create workspace owner: %v", err)
	}
	workspace := &models.Workspace{
		ID:        "workspace-1",
		Name:      "Test Workspace",
		OwnerID:   user.ID,
		CreatedAt: now,
		UpdatedAt: now,
	}
	if err := database.CreateWorkspaceWithMember(workspace, "owner-membership"); err != nil {
		t.Fatalf("create workspace and owner membership: %v", err)
	}
}
