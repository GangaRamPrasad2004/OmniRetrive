package db

import (
	"context"
	"embed"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"time"

	pgxv4 "github.com/jackc/pgx/v4"
	"github.com/jackc/tern/migrate"
)

//go:embed migrations/*.sql
var migrationFiles embed.FS

type embeddedMigrationFS struct {
	files fs.FS
}

func (f embeddedMigrationFS) ReadDir(name string) ([]os.FileInfo, error) {
	entries, err := fs.ReadDir(f.files, filepath.ToSlash(name))
	if err != nil {
		return nil, err
	}
	infos := make([]os.FileInfo, 0, len(entries))
	for _, entry := range entries {
		info, err := entry.Info()
		if err != nil {
			return nil, err
		}
		infos = append(infos, info)
	}
	return infos, nil
}

func (f embeddedMigrationFS) ReadFile(name string) ([]byte, error) {
	return fs.ReadFile(f.files, filepath.ToSlash(name))
}

func (f embeddedMigrationFS) Glob(pattern string) ([]string, error) {
	return fs.Glob(f.files, filepath.ToSlash(pattern))
}

func (d *DB) migratePostgreSQL() error {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	conn, err := pgxv4.Connect(ctx, d.connectionString)
	if err != nil {
		return fmt.Errorf("connect for Tern migrations: %w", err)
	}
	defer conn.Close(ctx)

	migrator, err := migrate.NewMigratorEx(
		ctx,
		conn,
		"public.schema_version",
		&migrate.MigratorOptions{
			MigratorFS: embeddedMigrationFS{files: migrationFiles},
		},
	)
	if err != nil {
		return fmt.Errorf("initialize Tern migrator: %w", err)
	}
	if err := migrator.LoadMigrations("migrations"); err != nil {
		return fmt.Errorf("load Tern migrations: %w", err)
	}
	if err := migrator.Migrate(ctx); err != nil {
		return fmt.Errorf("apply Tern migrations: %w", err)
	}
	return nil
}
