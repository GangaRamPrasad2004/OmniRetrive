package db

import (
	"context"
	"database/sql"
	"fmt"
	"net"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	_ "modernc.org/sqlite"
	"omniretrive/gateway/models"
)

type DB struct {
	*sql.DB
	driverName       string
	connectionString string
}

func InitDB(dataSourceName string) (*DB, error) {
	if dataSourceName == "" {
		dataSourceName = os.Getenv("DATABASE_URL")
	}
	if dataSourceName == "" {
		var err error
		dataSourceName, err = postgresURLFromEnvironment()
		if err != nil {
			return nil, err
		}
	}
	if dataSourceName == "" {
		return nil, fmt.Errorf("DATABASE_URL or PostgreSQL connection settings are required")
	}

	connectionString := dataSourceName
	driverName := "pgx"
	switch {
	case strings.HasPrefix(dataSourceName, "sqlite://"):
		driverName = "sqlite"
		dataSourceName = strings.TrimPrefix(dataSourceName, "sqlite://")
	case strings.HasPrefix(dataSourceName, "postgres://"), strings.HasPrefix(dataSourceName, "postgresql://"):
	default:
		return nil, fmt.Errorf("unsupported database URL scheme; use PostgreSQL")
	}
	if driverName == "pgx" {
		config, err := pgx.ParseConfig(dataSourceName)
		if err != nil {
			return nil, fmt.Errorf("parse PostgreSQL connection URL: %w", err)
		}
		dataSourceName = stdlib.RegisterConnConfig(config)
	}

	conn, err := sql.Open(driverName, dataSourceName)
	if err != nil {
		return nil, fmt.Errorf("failed to open %s database: %w", driverName, err)
	}

	conn.SetMaxOpenConns(25)
	conn.SetMaxIdleConns(5)
	conn.SetConnMaxLifetime(5 * time.Minute)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := conn.PingContext(ctx); err != nil {
		_ = conn.Close()
		return nil, fmt.Errorf("connect to %s database: %w", driverName, err)
	}

	database := &DB{
		DB:               conn,
		driverName:       driverName,
		connectionString: connectionString,
	}
	if driverName == "sqlite" {
		if _, err := conn.Exec("PRAGMA foreign_keys = ON"); err != nil {
			_ = conn.Close()
			return nil, fmt.Errorf("failed to configure SQLite test database: %w", err)
		}
	}
	if err := database.migrate(); err != nil {
		_ = conn.Close()
		return nil, fmt.Errorf("failed to run migrations: %w", err)
	}
	if importPath := os.Getenv("GATEWAY_SQLITE_IMPORT_PATH"); importPath != "" {
		if err := database.ImportSQLiteIdentityData(importPath); err != nil {
			_ = conn.Close()
			return nil, fmt.Errorf("failed to import legacy SQLite identity data: %w", err)
		}
	}

	return database, nil
}

func postgresURLFromEnvironment() (string, error) {
	host := os.Getenv("PGHOST")
	user := os.Getenv("PGUSER")
	password := os.Getenv("PGPASSWORD")
	database := os.Getenv("PGDATABASE")
	if host == "" || user == "" || password == "" || database == "" {
		return "", nil
	}

	port := os.Getenv("PGPORT")
	if port == "" {
		port = "5432"
	}
	sslMode := os.Getenv("PGSSLMODE")
	if sslMode == "" {
		sslMode = "require"
	}

	connectionURL := &url.URL{
		Scheme: "postgres",
		User:   url.UserPassword(user, password),
		Host:   net.JoinHostPort(host, port),
		Path:   database,
	}
	query := connectionURL.Query()
	query.Set("sslmode", sslMode)
	connectionURL.RawQuery = query.Encode()
	return connectionURL.String(), nil
}

// User operations
func (d *DB) CreateUser(u *models.User) error {
	query := `INSERT INTO users (id, email, password_hash, full_name, created_at, updated_at) 
	          VALUES ($1, $2, $3, $4, $5, $6)`
	_, err := d.Exec(query, u.ID, u.Email, u.PasswordHash, u.FullName, u.CreatedAt, u.UpdatedAt)
	return err
}

func (d *DB) CreateUserWithWorkspace(user *models.User, workspace *models.Workspace, memberID string) error {
	tx, err := d.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	if _, err := tx.Exec(
		`INSERT INTO users (id, email, password_hash, full_name, created_at, updated_at)
		 VALUES ($1, $2, $3, $4, $5, $6)`,
		user.ID, user.Email, user.PasswordHash, user.FullName, user.CreatedAt, user.UpdatedAt,
	); err != nil {
		return err
	}
	if _, err := tx.Exec(
		`INSERT INTO workspaces (id, name, owner_id, created_at, updated_at)
		 VALUES ($1, $2, $3, $4, $5)`,
		workspace.ID, workspace.Name, workspace.OwnerID, workspace.CreatedAt, workspace.UpdatedAt,
	); err != nil {
		return err
	}
	if _, err := tx.Exec(
		`INSERT INTO workspace_members (id, workspace_id, user_id, role, created_at)
		 VALUES ($1, $2, $3, $4, $5)`,
		memberID, workspace.ID, user.ID, string(models.RoleOwner), workspace.CreatedAt,
	); err != nil {
		return err
	}
	return tx.Commit()
}

func (d *DB) GetUserByEmail(email string) (*models.User, error) {
	query := `SELECT id, email, password_hash, full_name, created_at, updated_at FROM users WHERE email = $1`
	row := d.QueryRow(query, email)

	var u models.User
	err := row.Scan(&u.ID, &u.Email, &u.PasswordHash, &u.FullName, &u.CreatedAt, &u.UpdatedAt)
	if err != nil {
		return nil, err
	}
	return &u, nil
}

func (d *DB) GetUserByID(id string) (*models.User, error) {
	query := `SELECT id, email, password_hash, full_name, created_at, updated_at FROM users WHERE id = $1`
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

	queryWs := `INSERT INTO workspaces (id, name, owner_id, created_at, updated_at) VALUES ($1, $2, $3, $4, $5)`
	if _, err := tx.Exec(queryWs, ws.ID, ws.Name, ws.OwnerID, ws.CreatedAt, ws.UpdatedAt); err != nil {
		return err
	}

	queryMember := `INSERT INTO workspace_members (id, workspace_id, user_id, role, created_at) VALUES ($1, $2, $3, $4, $5)`
	if _, err := tx.Exec(queryMember, memberID, ws.ID, ws.OwnerID, string(models.RoleOwner), ws.CreatedAt); err != nil {
		return err
	}

	return tx.Commit()
}

func (d *DB) GetWorkspaceByID(id string) (*models.Workspace, error) {
	query := `SELECT id, name, owner_id, created_at, updated_at FROM workspaces WHERE id = $1`
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
		WHERE wm.user_id = $1
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
	query := `DELETE FROM workspaces WHERE id = $1`
	_, err := d.Exec(query, workspaceID)
	return err
}

// Membership & RBAC operations
func (d *DB) GetWorkspaceMembership(workspaceID, userID string) (*models.WorkspaceMember, error) {
	query := `
		SELECT wm.id, wm.workspace_id, wm.user_id, wm.role, wm.created_at, u.email, u.full_name
		FROM workspace_members wm
		JOIN users u ON wm.user_id = u.id
		WHERE wm.workspace_id = $1 AND wm.user_id = $2`

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
		WHERE wm.workspace_id = $1
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
	query := `INSERT INTO workspace_members (id, workspace_id, user_id, role, created_at) VALUES ($1, $2, $3, $4, $5)`
	_, err := d.Exec(query, m.ID, m.WorkspaceID, m.UserID, string(m.Role), m.CreatedAt)
	return err
}

func (d *DB) RemoveWorkspaceMember(workspaceID, userID string) error {
	query := `DELETE FROM workspace_members WHERE workspace_id = $1 AND user_id = $2`
	_, err := d.Exec(query, workspaceID, userID)
	return err
}
