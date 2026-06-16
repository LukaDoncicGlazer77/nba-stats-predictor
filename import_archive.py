#!/usr/bin/env python3
import csv
import re
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ARCHIVE_DIR = ROOT / "archive"
DB_PATH = ROOT / "nba.db"


def clean_name(value):
    value = value.strip().lower()
    value = value.replace("%", "percent")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    if not value:
        value = "value"
    if value[0].isdigit():
        value = f"x{value}"
    return value


def unique_names(names):
    seen = {}
    result = []

    for name in names:
        base = clean_name(name)
        count = seen.get(base, 0)
        seen[base] = count + 1
        result.append(base if count == 0 else f"{base}_{count + 1}")

    return result


def table_name(path):
    return f"archive_{clean_name(path.stem)}"


def coerce(value):
    if value is None:
        return None

    value = value.strip()
    if value == "":
        return None

    return value


def import_csv(connection, path):
    table = table_name(path)

    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.reader(file)
        raw_headers = next(reader)
        headers = unique_names(raw_headers)

        connection.execute(f'DROP TABLE IF EXISTS "{table}"')
        columns_sql = ", ".join(f'"{column}"' for column in headers)
        connection.execute(f'CREATE TABLE "{table}" ({columns_sql})')

        placeholders = ", ".join("?" for _ in headers)
        insert_sql = f'INSERT INTO "{table}" ({columns_sql}) VALUES ({placeholders})'

        rows = ([coerce(value) for value in row] for row in reader)
        connection.executemany(insert_sql, rows)

    return table


def add_indexes(connection, table):
    columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}

    for column in ("player_id", "player", "season", "team", "tm", "team_abbreviation"):
        if column in columns:
            index_name = f"idx_{table}_{column}"
            connection.execute(f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{table}" ("{column}")')

    if {"player_id", "season"}.issubset(columns):
        connection.execute(
            f'CREATE INDEX IF NOT EXISTS "idx_{table}_player_id_season" ON "{table}" ("player_id", "season")'
        )


def create_views(connection):
    connection.execute("DROP VIEW IF EXISTS archive_player_dashboard")
    connection.execute("DROP VIEW IF EXISTS archive_latest_player_dashboard")
    connection.execute("DROP VIEW IF EXISTS archive_player_per_game_combined")

    extra_tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'archive_x%_nba_player_stats_regular'"
        )
    ]

    per_game_source = 'archive_player_per_game'
    if extra_tables:
        union_parts = []
        for table in extra_tables:
            season_key = table[len('archive_'):].rsplit('_nba_player_stats_regular', 1)[0]
            if season_key.startswith('x'):
                season_key = season_key[1:]
            parts = season_key.split('_')
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                season = f"{parts[0]}-{parts[1][-2:]}"
            else:
                season = season_key.replace('_', '-')
            union_parts.append(
                f"""
                SELECT
                  '{season}' AS season,
                  'NBA' AS lg,
                  player AS player,
                  NULL AS player_id,
                  CASE WHEN trim(age) = '' THEN NULL ELSE CAST(age AS REAL) END AS age,
                  tm AS team,
                  pos AS pos,
                  CASE WHEN trim(g) = '' THEN NULL ELSE CAST(g AS INTEGER) END AS g,
                  CASE WHEN trim(gs) = '' THEN NULL ELSE CAST(gs AS INTEGER) END AS gs,
                  CASE WHEN trim(mp) = '' THEN NULL ELSE CAST(mp AS REAL) END AS mp_per_game,
                  CASE WHEN trim(fg) = '' THEN NULL ELSE CAST(fg AS REAL) END AS fg_per_game,
                  CASE WHEN trim(fga) = '' THEN NULL ELSE CAST(fga AS REAL) END AS fga_per_game,
                  CASE WHEN trim(fgpercent) = '' THEN NULL ELSE CAST(fgpercent AS REAL) END AS fg_percent,
                  CASE WHEN trim(x3p) = '' THEN NULL ELSE CAST(x3p AS REAL) END AS x3p_per_game,
                  CASE WHEN trim(x3pa) = '' THEN NULL ELSE CAST(x3pa AS REAL) END AS x3pa_per_game,
                  CASE WHEN trim(x3ppercent) = '' THEN NULL ELSE CAST(x3ppercent AS REAL) END AS x3p_percent,
                  CASE WHEN trim(x2p) = '' THEN NULL ELSE CAST(x2p AS REAL) END AS x2p_per_game,
                  CASE WHEN trim(x2pa) = '' THEN NULL ELSE CAST(x2pa AS REAL) END AS x2pa_per_game,
                  CASE WHEN trim(x2ppercent) = '' THEN NULL ELSE CAST(x2ppercent AS REAL) END AS x2p_percent,
                  CASE WHEN trim(efgpercent) = '' THEN NULL ELSE CAST(efgpercent AS REAL) END AS e_fg_percent,
                  CASE WHEN trim(ft) = '' THEN NULL ELSE CAST(ft AS REAL) END AS ft_per_game,
                  CASE WHEN trim(fta) = '' THEN NULL ELSE CAST(fta AS REAL) END AS fta_per_game,
                  CASE WHEN trim(ftpercent) = '' THEN NULL ELSE CAST(ftpercent AS REAL) END AS ft_percent,
                  CASE WHEN trim(orb) = '' THEN NULL ELSE CAST(orb AS REAL) END AS orb_per_game,
                  CASE WHEN trim(drb) = '' THEN NULL ELSE CAST(drb AS REAL) END AS drb_per_game,
                  CASE WHEN trim(trb) = '' THEN NULL ELSE CAST(trb AS REAL) END AS trb_per_game,
                  CASE WHEN trim(ast) = '' THEN NULL ELSE CAST(ast AS REAL) END AS ast_per_game,
                  CASE WHEN trim(stl) = '' THEN NULL ELSE CAST(stl AS REAL) END AS stl_per_game,
                  CASE WHEN trim(blk) = '' THEN NULL ELSE CAST(blk AS REAL) END AS blk_per_game,
                  CASE WHEN trim(tov) = '' THEN NULL ELSE CAST(tov AS REAL) END AS tov_per_game,
                  CASE WHEN trim(pf) = '' THEN NULL ELSE CAST(pf AS REAL) END AS pf_per_game,
                  CASE WHEN trim(pts) = '' THEN NULL ELSE CAST(pts AS REAL) END AS pts_per_game
                FROM "{table}"
                WHERE player IS NOT NULL AND player != ''
                """
            )
        union_sql = "\nUNION ALL\n".join(union_parts)
        connection.execute(
            f"""
            CREATE VIEW archive_player_per_game_combined AS
            {union_sql}
            """
        )
        per_game_source = 'archive_player_per_game_combined'

    connection.executescript(
        f"""
        CREATE VIEW archive_player_dashboard AS
        SELECT
          per_game.season,
          per_game.lg,
          per_game.player,
          per_game.player_id,
          per_game.age,
          per_game.team,
          per_game.pos,
          per_game.g,
          per_game.gs,
          per_game.mp_per_game,
          per_game.fg_per_game,
          per_game.fga_per_game,
          per_game.fg_percent,
          per_game.x3p_per_game,
          per_game.x3pa_per_game,
          per_game.x3p_percent,
          per_game.ft_per_game,
          per_game.fta_per_game,
          per_game.ft_percent,
          per_game.orb_per_game,
          per_game.drb_per_game,
          per_game.trb_per_game,
          per_game.ast_per_game,
          per_game.stl_per_game,
          per_game.blk_per_game,
          per_game.tov_per_game,
          per_game.pf_per_game,
          per_game.pts_per_game,
          advanced.per,
          advanced.ts_percent,
          advanced.usg_percent,
          advanced.ows,
          advanced.dws,
          advanced.ws,
          advanced.obpm,
          advanced.dbpm,
          advanced.bpm,
          advanced.vorp
        FROM {per_game_source} per_game
        LEFT JOIN archive_advanced advanced
          ON advanced.player_id = per_game.player_id
         AND advanced.season = per_game.season
         AND advanced.team = per_game.team;

        CREATE VIEW archive_latest_player_dashboard AS
        SELECT dashboard.*
        FROM archive_player_dashboard dashboard
        JOIN (
          SELECT player_id, MAX(CAST(season AS INTEGER)) AS latest_season
          FROM archive_player_dashboard
          GROUP BY player_id
        ) latest
          ON latest.player_id = dashboard.player_id
         AND latest.latest_season = CAST(dashboard.season AS INTEGER);
        """
    )


def main():
    if not ARCHIVE_DIR.exists():
        raise SystemExit(f"Missing archive folder: {ARCHIVE_DIR}")

    # Only import NBA Player Stats - Regular.csv files and the optional Advanced metrics file.
    csv_paths = sorted(
        path
        for path in ARCHIVE_DIR.glob("*.csv")
        if "nba player stats" in path.name.lower() or path.name.lower() == "advanced.csv"
    )
    if not csv_paths:
        raise SystemExit(f"No NBA Player Stats or Advanced CSV files found in {ARCHIVE_DIR}")

    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")

        imported = []
        for path in csv_paths:
            table = import_csv(connection, path)
            add_indexes(connection, table)
            imported.append((table, path.name))

        create_views(connection)

    for table, filename in imported:
        print(f"{filename} -> {table}")


if __name__ == "__main__":
    main()
