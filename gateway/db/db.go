package db

import (
	"database/sql"
	"fmt"
	"log"
	"os"
	"time"

	_ "modernc.org/sqlite"
	"omniretrive/gateway/models"
)

type DB struct {
	*sql.DB
}

func InitDB(dataSourceName string) (*DB, error) {
	if dataSourceName == "" {
		dataSourceName = os.Getenv("GATEWAY_DB_PATH")
		if dataSourceName == "" {
			dataSourceName = "gateway.db"
		}
	}

	conn, err := sql.Open("sqlite", dataSourceName)
	if err != nil {
		return nil, fmt.Errorf("failed to open sqlite database: %w", err)
	}

	// Enable WAL mode & foreign keys for SQLite
	if _, err := conn.Exec("PRAGMA journal_mode=WAL; PRAGMA foreign_keys = ON;"); err != nil {
		log.Printf("Warning setting pragmas: %v", err)
	}

	conn.SetMaxOpenConns(25)
	conn.SetMaxIdleConns(5)
	conn.SetConnMaxLifetime(5 * time.Minute)

	database := &DB{conn}
	if err := database.migrate(); err != nil {
		return nil, fmt.Errorf("failed to run migrations: %w", err)
	}

	return database, nil
}

func (d *DB) migrate() error {
	schema := `
	CREATE TABLE IF NOT EXISTS users (
		id TEXT PRIMARY KEY,
		email TEXT UNIQUE NOT NULL,
		password_hash TEXT NOT NULL,
		full_name TEXT NOT NULL,
		created_at TIMESTAMP NOT NULL,
		updated_at TIMESTAMP NOT NULL
	);

	CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

	CREATE TABLE IF NOT EXISTS workspaces (
		id TEXT PRIMARY KEY,
		name TEXT NOT NULL,
		owner_id TEXT NOT NULL,
		created_at TIMESTAMP NOT NULL,
		updated_at TIMESTAMP NOT NULL,
		FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE
	);

	CREATE INDEX IF NOT EXISTS idx_workspaces_owner ON workspaces(owner_id);

	CREATE TABLE IF NOT EXISTS workspace_members (
		id TEXT PRIMARY KEY,
		workspace_id TEXT NOT NULL,
		user_id TEXT NOT NULL,
		role TEXT NOT NULL,
		created_at TIMESTAMP NOT NULL,
		UNIQUE(workspace_id, user_id),
		FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
		FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
	);

	CREATE INDEX IF NOT EXISTS idx_workspace_members_user ON workspace_members(user_id);
	CREATE INDEX IF NOT EXISTS idx_workspace_members_ws ON workspace_members(workspace_id);
	`
	_, err := d.Exec(schema)
	return err
}

// User operations
func (d *DB) CreateUser(u *models.User) error {
	query := `INSERT INTO users (id, email, password_hash, full_name, created_at, updated_at) 
	          VALUES (?, ?, ?, ?, ?, ?)`
	_, err := d.Exec(query, u.ID, u.Email, u.PasswordHash, u.FullName, u.CreatedAt, u.UpdatedAt)
	return err
}

func (d *DB) GetUserByEmail(email string) (*models.User, error) {
	query := `SELECT id, email, password_hash, full_name, created_at, updated_at FROM users WHERE email = ?`
	row := d.QueryRow(query, email)

	var u models.User
	err := row.Scan(&u.ID, &u.Email, &u.PasswordHash, &u.FullName, &u.CreatedAt, &u.UpdatedAt)
	if err != nil {
		return nil, err
	}
	return &u, nil
}

func (d *DB) GetUserByID(id string) (*models.User, error) {
	query := `SELECT id, email, password_hash, full_name, created_at, updated_at FROM users WHERE id = ?`
	row := d.QueryRow(query, id)

	var u models.User
	err := row.Scan(&u.ID, &u.Email, &u.PasswordHash, &u.FullName, &u.CreatedAt, &u.UpdatedAt)
	if err != nil {
		return nil, err
	}
	return &u, nil
}

// Workspace operations
func (d *DB) CreateWorkspaceWithMember(ws *models.Workspace, memberID string) error {
	tx, err := d.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	queryWs := `INSERT INTO workspaces (id, name, owner_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)`
	if _, err := tx.Exec(queryWs, ws.ID, ws.Name, ws.OwnerID, ws.CreatedAt, ws.UpdatedAt); err != nil {
		return err
	}

	queryMember := `INSERT INTO workspace_members (id, workspace_id, user_id, role, created_at) VALUES (?, ?, ?, ?, ?)`
	if _, err := tx.Exec(queryMember, memberID, ws.ID, ws.OwnerID, string(models.RoleOwner), ws.CreatedAt); err != nil {
		return err
	}

	return tx.Commit()
}

func (d *DB) GetWorkspaceByID(id string) (*models.Workspace, error) {
	query := `SELECT id, name, owner_id, created_at, updated_at FROM workspaces WHERE id = ?`
	row := d.QueryRow(query, id)

	var ws models.Workspace
	err := row.Scan(&ws.ID, &ws.Name, &ws.OwnerID, &ws.CreatedAt, &ws.UpdatedAt)
	if err != nil {
		return nil, err
	}
	return &ws, nil
}

func (d *DB) ListWorkspacesForUser(userID string) ([]models.Workspace, error) {
	query := `
		SELECT w.id, w.name, w.owner_id, w.created_at, w.updated_at 
		FROM workspaces w
		JOIN workspace_members wm ON w.id = wm.workspace_id
		WHERE wm.user_id = ?
		ORDER BY w.created_at DESC`

	rows, err := d.Query(query, userID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var workspaces []models.Workspace
	for rows.Next() {
		var ws models.Workspace
		if err := rows.Scan(&ws.ID, &ws.Name, &ws.OwnerID, &ws.CreatedAt, &ws.UpdatedAt); err != nil {
			return nil, err
		}
		workspaces = append(workspaces, ws)
	}
	return workspaces, nil
}

func (d *DB) DeleteWorkspace(workspaceID string) error {
	query := `DELETE FROM workspaces WHERE id = ?`
	_, err := d.Exec(query, workspaceID)
	return err
}

// Membership & RBAC operations
func (d *DB) GetWorkspaceMembership(workspaceID, userID string) (*models.WorkspaceMember, error) {
	query := `
		SELECT wm.id, wm.workspace_id, wm.user_id, wm.role, wm.created_at, u.email, u.full_name
		FROM workspace_members wm
		JOIN users u ON wm.user_id = u.id
		WHERE wm.workspace_id = ? AND wm.user_id = ?`

	row := d.QueryRow(query, workspaceID, userID)
	var m models.WorkspaceMember
	var roleStr string
	err := row.Scan(&m.ID, &m.WorkspaceID, &m.UserID, &roleStr, &m.CreatedAt, &m.Email, &m.FullName)
	if err != nil {
		return nil, err
	}
	m.Role = models.Role(roleStr)
	return &m, nil
}

func (d *DB) ListWorkspaceMembers(workspaceID string) ([]models.WorkspaceMember, error) {
	query := `
		SELECT wm.id, wm.workspace_id, wm.user_id, wm.role, wm.created_at, u.email, u.full_name
		FROM workspace_members wm
		JOIN users u ON wm.user_id = u.id
		WHERE wm.workspace_id = ?
		ORDER BY wm.created_at ASC`

	rows, err := d.Query(query, workspaceID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var members []models.WorkspaceMember
	for rows.Next() {
		var m models.WorkspaceMember
		var roleStr string
		if err := rows.Scan(&m.ID, &m.WorkspaceID, &m.UserID, &roleStr, &m.CreatedAt, &m.Email, &m.FullName); err != nil {
			return nil, err
		}
		m.Role = models.Role(roleStr)
		members = append(members, m)
	}
	return members, nil
}

func (d *DB) AddWorkspaceMember(m *models.WorkspaceMember) error {
	query := `INSERT INTO workspace_members (id, workspace_id, user_id, role, created_at) VALUES (?, ?, ?, ?, ?)`
	_, err := d.Exec(query, m.ID, m.WorkspaceID, m.UserID, string(m.Role), m.CreatedAt)
	return err
}

func (d *DB) RemoveWorkspaceMember(workspaceID, userID string) error {
	query := `DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ?`
	_, err := d.Exec(query, workspaceID, userID)
	return err
}
