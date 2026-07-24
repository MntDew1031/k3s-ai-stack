"""Durable local conversation checkpoints stored outside memory vectors."""
from __future__ import annotations

import os
from typing import Any

import psycopg
from psycopg.rows import dict_row


def _connect():
    return psycopg.connect(
        host=os.environ["MEM0_PG_HOST"],
        port=int(os.environ.get("MEM0_PG_PORT", "5432")),
        dbname=os.environ.get("MEM0_PG_DB", "mem0"),
        user=os.environ["MEM0_PG_USER"],
        password=os.environ["MEM0_PG_PASSWORD"],
        row_factory=dict_row,
    )


def _ensure_table(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS context_checkpoints (
            checkpoint_key CHAR(64) PRIMARY KEY,
            model TEXT NOT NULL,
            summary TEXT NOT NULL,
            token_count INTEGER NOT NULL CHECK (token_count >= 0),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def put_context_checkpoint(
    checkpoint_key: str, model: str, summary: str, token_count: int
) -> dict[str, Any]:
    with _connect() as connection:
        with connection.cursor() as cursor:
            _ensure_table(cursor)
            cursor.execute(
                """
                INSERT INTO context_checkpoints
                    (checkpoint_key, model, summary, token_count, updated_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (checkpoint_key) DO UPDATE SET
                    model = EXCLUDED.model,
                    summary = EXCLUDED.summary,
                    token_count = EXCLUDED.token_count,
                    updated_at = NOW()
                RETURNING checkpoint_key, model, token_count, updated_at
                """,
                (checkpoint_key, model, summary, token_count),
            )
            return dict(cursor.fetchone())


def get_context_checkpoint(checkpoint_key: str) -> dict[str, Any] | None:
    with _connect() as connection:
        with connection.cursor() as cursor:
            _ensure_table(cursor)
            cursor.execute(
                """
                SELECT checkpoint_key, model, summary, token_count, updated_at
                FROM context_checkpoints
                WHERE checkpoint_key = %s
                """,
                (checkpoint_key,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
