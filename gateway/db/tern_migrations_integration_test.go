package db

import (
	"os"
	"testing"
)

func TestPostgresTernMigrationsCreateIdentitySchema(t *testing.T) {
	connectionString := os.Getenv("GATEWAY_TEST_POSTGRES_URL")
	if connectionString == "" {
		t.Skip("GATEWAY_TEST_POSTGRES_URL is not set")
	}

	database, err := InitDB(connectionString)
	if err != nil {
		t.Fatalf("initialize PostgreSQL with Tern migrations: %v", err)
	}
	defer database.Close()

	for _, table := range []string{"users", "workspaces", "workspace_members"} {
		var exists bool
		if err := database.QueryRow(
			"SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = $1)",
			table,
		).Scan(&exists); err != nil {
			t.Fatalf("check %s table: %v", table, err)
		}
		if !exists {
			t.Errorf("Tern did not create the %s table", table)
		}
	}

	if err := database.migrate(); err != nil {
		t.Fatalf("rerun PostgreSQL Tern migrations: %v", err)
	}

	var currentVersion int
	if err := database.QueryRow("SELECT version FROM public.schema_version").Scan(&currentVersion); err != nil {
		t.Fatalf("read Tern migration version: %v", err)
	}
	if currentVersion != 1 {
		t.Errorf("got Tern migration version %d, want 1", currentVersion)
	}
}
