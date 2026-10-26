package db

import (
	"database/sql"
	"errors"
	"fmt"
	"os"
	"time"
)

// ImportSQLiteIdentityData imports users, workspaces, and memberships from a
// previous gateway SQLite database. Document and vector metadata remain in AI.
func (d *DB) ImportSQLiteIdentityData(path string) error {
	if _, err := os.Stat(path); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		return fmt.Errorf("inspect legacy database: %w", err)
	}

	source, err := sql.Open("sqlite", path)
	if err != nil {
		return fmt.Errorf("open legacy SQLite database: %w", err)
	}
	defer source.Close()

	var hasUsers bool
	err = source.QueryRow(
		"SELECT EXISTS (SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users')",
	).Scan(&hasUsers)
	if err != nil {
		return fmt.Errorf("inspect legacy identity schema: %w", err)
	}
	if !hasUsers {
		return nil
	}

	tx, err := d.Begin()
	if err != nil {
		return fmt.Errorf("begin legacy import transaction: %w", err)
	}
	defer tx.Rollback()

	var imported bool
	err = tx.QueryRow(
		"SELECT EXISTS (SELECT 1 FROM legacy_sqlite_imports WHERE source_path = $1)",
		path,
	).Scan(&imported)
	if err != nil {
		return fmt.Errorf("check prior legacy import: %w", err)
	}
	if imported {
		return nil
	}

	if err := importUsers(tx, source); err != nil {
		return err
	}
	if err := importWorkspaces(tx, source); err != nil {
		return err
	}
	if err := importMemberships(tx, source); err != nil {
		return err
	}
	if _, err := tx.Exec(
		"INSERT INTO legacy_sqlite_imports (source_path) VALUES ($1)",
		path,
	); err != nil {
		return fmt.Errorf("record legacy import: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("commit legacy import: %w", err)
	}
	return nil
}

func importUsers(tx *sql.Tx, source *sql.DB) error {
	rows, err := source.Query(
		"SELECT id, email, password_hash, full_name, created_at, updated_at FROM users",
	)
	if err != nil {
		return fmt.Errorf("read legacy users: %w", err)
	}
	defer rows.Close()

	for rows.Next() {
		var id, email, passwordHash, fullName string
		var createdAt, updatedAt any
		if err := rows.Scan(&id, &email, &passwordHash, &fullName, &createdAt, &updatedAt); err != nil {
			return fmt.Errorf("scan legacy user: %w", err)
		}
		if _, err := tx.Exec(
			`INSERT INTO users (id, email, password_hash, full_name, created_at, updated_at)
			 VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (id) DO NOTHING`,
			id, email, passwordHash, fullName, normalizeLegacyValue(createdAt), normalizeLegacyValue(updatedAt),
		); err != nil {
			return fmt.Errorf("import legacy user %q: %w", id, err)
		}
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("iterate legacy users: %w", err)
	}
	return nil
}

func importWorkspaces(tx *sql.Tx, source *sql.DB) error {
	rows, err := source.Query("SELECT id, name, owner_id, created_at, updated_at FROM workspaces")
	if err != nil {
		return fmt.Errorf("read legacy workspaces: %w", err)
	}
	defer rows.Close()

	for rows.Next() {
		var id, name, ownerID string
		var createdAt, updatedAt any
		if err := rows.Scan(&id, &name, &ownerID, &createdAt, &updatedAt); err != nil {
			return fmt.Errorf("scan legacy workspace: %w", err)
		}
		if _, err := tx.Exec(
			`INSERT INTO workspaces (id, name, owner_id, created_at, updated_at)
			 VALUES ($1, $2, $3, $4, $5) ON CONFLICT (id) DO NOTHING`,
			id, name, ownerID, normalizeLegacyValue(createdAt), normalizeLegacyValue(updatedAt),
		); err != nil {
			return fmt.Errorf("import legacy workspace %q: %w", id, err)
		}
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("iterate legacy workspaces: %w", err)
	}
	return nil
}

func importMemberships(tx *sql.Tx, source *sql.DB) error {
	rows, err := source.Query(
		"SELECT id, workspace_id, user_id, role, created_at FROM workspace_members",
	)
	if err != nil {
		return fmt.Errorf("read legacy workspace memberships: %w", err)
	}
	defer rows.Close()

	for rows.Next() {
		var id, workspaceID, userID, role string
		var createdAt any
		if err := rows.Scan(&id, &workspaceID, &userID, &role, &createdAt); err != nil {
			return fmt.Errorf("scan legacy workspace membership: %w", err)
		}
		if _, err := tx.Exec(
			`INSERT INTO workspace_members (id, workspace_id, user_id, role, created_at)
			 VALUES ($1, $2, $3, $4, $5) ON CONFLICT (id) DO NOTHING`,
			id, workspaceID, userID, role, normalizeLegacyValue(createdAt),
		); err != nil {
			return fmt.Errorf("import legacy membership %q: %w", id, err)
		}
	}
	if err := rows.Err(); err != nil {
		return fmt.Errorf("iterate legacy workspace memberships: %w", err)
	}
	return nil
}

func normalizeLegacyValue(value any) any {
	if timestamp, ok := value.(time.Time); ok {
		return timestamp.UTC()
	}
	if bytes, ok := value.([]byte); ok {
		return string(bytes)
	}
	return value
}
