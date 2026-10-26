package db

import (
	"fmt"
	"strings"
)

type migration struct {
	version    int
	name       string
	statements []string
}

var migrations = []migration{
	{
		version: 1,
		name:    "create_users_workspaces_and_memberships",
		statements: []string{
			`CREATE TABLE IF NOT EXISTS users (
				id TEXT PRIMARY KEY,
				email TEXT UNIQUE NOT NULL,
				password_hash TEXT NOT NULL,
				full_name TEXT NOT NULL,
				created_at TIMESTAMPTZ NOT NULL,
				updated_at TIMESTAMPTZ NOT NULL
			)`,
			`CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)`,
			`CREATE TABLE IF NOT EXISTS workspaces (
				id TEXT PRIMARY KEY,
				name TEXT NOT NULL,
				owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
				created_at TIMESTAMPTZ NOT NULL,
				updated_at TIMESTAMPTZ NOT NULL
			)`,
			`CREATE INDEX IF NOT EXISTS idx_workspaces_owner ON workspaces(owner_id)`,
			`CREATE TABLE IF NOT EXISTS workspace_members (
				id TEXT PRIMARY KEY,
				workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
				user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
				role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
				created_at TIMESTAMPTZ NOT NULL,
				UNIQUE(workspace_id, user_id)
			)`,
			`CREATE INDEX IF NOT EXISTS idx_workspace_members_user ON workspace_members(user_id)`,
			`CREATE INDEX IF NOT EXISTS idx_workspace_members_ws ON workspace_members(workspace_id)`,
			`CREATE TABLE IF NOT EXISTS legacy_sqlite_imports (
				source_path TEXT PRIMARY KEY,
				imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
			)`,
		},
	},
}

func (d *DB) migrate() error {
	if d.driverName == "pgx" {
		return d.migratePostgreSQL()
	}
	return d.migrateSQLite()
}

func (d *DB) migrateSQLite() error {
	tx, err := d.Begin()
	if err != nil {
		return fmt.Errorf("begin migration transaction: %w", err)
	}
	defer tx.Rollback()

	migrationTable := `
		CREATE TABLE IF NOT EXISTS schema_migrations (
			version INTEGER PRIMARY KEY,
			name TEXT NOT NULL,
			applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
		)`
	if d.driverName == "sqlite" {
		migrationTable = strings.ReplaceAll(migrationTable, "TIMESTAMPTZ", "TIMESTAMP")
	}
	if _, err := tx.Exec(migrationTable); err != nil {
		return fmt.Errorf("create migration table: %w", err)
	}

	for _, m := range migrations {
		var applied bool
		err := tx.QueryRow(
			"SELECT EXISTS (SELECT 1 FROM schema_migrations WHERE version = $1)",
			m.version,
		).Scan(&applied)
		if err != nil {
			return fmt.Errorf("check migration %d: %w", m.version, err)
		}
		if applied {
			continue
		}

		for _, statement := range m.statements {
			if d.driverName == "sqlite" {
				statement = strings.ReplaceAll(statement, "TIMESTAMPTZ", "TIMESTAMP")
			}
			if _, err := tx.Exec(statement); err != nil {
				return fmt.Errorf("apply migration %d (%s): %w", m.version, m.name, err)
			}
		}
		if _, err := tx.Exec(
			"INSERT INTO schema_migrations (version, name) VALUES ($1, $2)",
			m.version, m.name,
		); err != nil {
			return fmt.Errorf("record migration %d (%s): %w", m.version, m.name, err)
		}
	}

	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit migrations: %w", err)
	}
	return nil
}
