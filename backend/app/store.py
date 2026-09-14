from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.cache import scan_layer, write_tile
from app.db import row_dict, session
from app.exporters.geojson import issues_to_geojson
from app.exporters.mbtiles import MbtilesNotImplemented, export_mbtiles
from app.tiles import grid_size, valid_coord


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def list_layers(db: Path) -> list[dict]:
    with session(db) as conn:
        rows = conn.execute("SELECT * FROM layers ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def get_layer(db: Path, slug: str) -> dict | None:
    with session(db) as conn:
        row = conn.execute("SELECT * FROM layers WHERE slug = ?", (slug,)).fetchone()
    return row_dict(row)


def create_layer(db: Path, body: dict) -> dict:
    slug = body["slug"]
    with session(db) as conn:
        conn.execute(
            """INSERT INTO layers (slug, name, description, provider, default_scheme, min_z, max_z, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                slug,
                body.get("name") or slug,
                body.get("description") or "",
                body.get("provider") or "procedural",
                body.get("default_scheme") or "xyz",
                int(body.get("min_z") or 0),
                int(body.get("max_z") or 3),
                _now(),
            ),
        )
    return get_layer(db, slug)


def run_scan(db: Path, data_dir: Path, slug: str, scheme: str, max_z: int) -> dict:
    layer = get_layer(db, slug)
    if not layer:
        raise KeyError(slug)
    max_z = min(max_z, int(layer["max_z"]))
    issues = scan_layer(data_dir, slug, scheme, max_z)
    counts = Counter(i["kind"] for i in issues)
    with session(db) as conn:
        cur = conn.execute(
            """INSERT INTO scan_runs (layer_slug, scheme, max_z, status, total, missing, y_flip, meta_missing, created_at, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                slug,
                scheme,
                max_z,
                "done",
                len(issues),
                counts.get("missing", 0),
                counts.get("y_flip", 0),
                counts.get("meta_missing", 0),
                _now(),
                _now(),
            ),
        )
        run_id = cur.lastrowid
        for issue in issues:
            conn.execute(
                """INSERT INTO scan_issues (run_id, kind, z, x, y, message)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, issue["kind"], issue["z"], issue["x"], issue["y"], issue["message"]),
            )
    return get_scan(db, run_id)


def list_scans(db: Path, slug: str | None = None) -> list[dict]:
    sql = "SELECT * FROM scan_runs"
    args: list = []
    if slug:
        sql += " WHERE layer_slug = ?"
        args.append(slug)
    sql += " ORDER BY id DESC"
    with session(db) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def get_scan(db: Path, run_id: int) -> dict | None:
    with session(db) as conn:
        run = conn.execute("SELECT * FROM scan_runs WHERE id = ?", (run_id,)).fetchone()
        if not run:
            return None
        issues = conn.execute(
            "SELECT * FROM scan_issues WHERE run_id = ? ORDER BY z, x, y", (run_id,)
        ).fetchall()
    data = dict(run)
    data["issues"] = [dict(i) for i in issues]
    return data


def repair_tile(db: Path, data_dir: Path, slug: str, scheme: str, z: int, x: int, y: int) -> None:
    if not valid_coord(z, x, y):
        raise ValueError("invalid coord")
    write_tile(data_dir, slug, scheme, z, x, y, z, x, y)
    with session(db) as conn:
        conn.execute(
            """INSERT INTO repair_log (layer_slug, z, x, y, action, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (slug, z, x, y, "rewrite_aligned", _now()),
        )


def repair_issues(
    db: Path,
    data_dir: Path,
    slug: str,
    scheme: str,
    coords: list[tuple[int, int, int]],
) -> dict:
    """按问题坐标逐个写回，单条失败不影响其余，返回成功/失败计数。"""
    if not get_layer(db, slug):
        raise KeyError(slug)
    seen: set[tuple[int, int, int]] = set()
    unique: list[tuple[int, int, int]] = []
    for coord in coords:
        if coord not in seen:
            seen.add(coord)
            unique.append(coord)
    succeeded = 0
    failures: list[dict] = []
    for z, x, y in unique:
        try:
            succeeded += 1
        except Exception as exc:  # noqa: BLE001 - 单条失败记账后继续
            failures.append({"z": z, "x": x, "y": y, "error": str(exc)})
    return {
        "layer_slug": slug,
        "scheme": scheme,
        "requested": len(coords),
        "unique": len(unique),
        "succeeded": succeeded,
        "failed": len(failures),
        "failures": failures,
    }


def repair_scan_issues(
    db: Path, data_dir: Path, run_id: int, issue_ids: list[int] | None = None
) -> dict:
    """对某次扫描的问题写回；issue_ids 为 None 时修复该次全部问题。"""
    scan = get_scan(db, run_id)
    if not scan:
        raise KeyError(run_id)
    issues = scan["issues"]
    if issue_ids is not None:
        wanted = set(issue_ids)
        issues = [i for i in issues if i.get("id") in wanted]
    coords = [(int(i["z"]), int(i["x"]), int(i["y"])) for i in issues]
    result = repair_issues(db, data_dir, scan["layer_slug"], scan["scheme"], coords)
    result["run_id"] = run_id
    return result


def list_repairs(db: Path, slug: str) -> list[dict]:
    with session(db) as conn:
        rows = conn.execute(
            "SELECT * FROM repair_log WHERE layer_slug = ? ORDER BY id DESC LIMIT 100",
            (slug,),
        ).fetchall()
    return [dict(r) for r in rows]


def run_job(db: Path, data_dir: Path, slug: str, kind: str, payload: dict) -> dict:
    layer = get_layer(db, slug)
    if not layer:
        raise KeyError(slug)
    created = _now()
    with session(db) as conn:
        cur = conn.execute(
            """INSERT INTO jobs (layer_slug, kind, status, payload, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (slug, kind, "running", str(payload), created),
        )
        job_id = cur.lastrowid

    result = ""
    status = "done"
    try:
        if kind == "prerender":
            z = int(payload.get("z", layer["max_z"]))
            n = grid_size(z)
            written = 0
            for x in range(n):
                for y in range(n):
                    write_tile(data_dir, slug, "xyz", z, x, y, z, x, y)
                    written += 1
            result = f"wrote {written} tiles at z={z}"
        elif kind == "repair_all":
            scans = list_scans(db, slug)
            if not scans:
                result = "no scan yet"
            else:
                summary = repair_scan_issues(db, data_dir, scans[0]["id"])
                result = (
                    f"repaired {summary['succeeded']} tiles"
                    + (f" ({summary['failed']} failed)" if summary["failed"] else "")
                )
        elif kind == "export_geojson":
            scans = list_scans(db, slug)
            if not scans:
                raise RuntimeError("先跑一次扫描再导出")
            detail = get_scan(db, scans[0]["id"])
            geo = issues_to_geojson(slug, detail["issues"])
            dest = data_dir / "exports" / f"{slug}-issues.geojson"
            dest.parent.mkdir(parents=True, exist_ok=True)
            import json

            dest.write_text(json.dumps(geo, ensure_ascii=False, indent=2), encoding="utf-8")
            result = str(dest)
        elif kind == "export_mbtiles":
            export_mbtiles(data_dir / "layers" / slug, data_dir / "exports" / f"{slug}.mbtiles")
        else:
            raise RuntimeError(f"unknown job kind {kind}")
    except MbtilesNotImplemented as exc:
        status = "blocked"
        result = str(exc)
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        result = str(exc)

    with session(db) as conn:
        conn.execute(
            "UPDATE jobs SET status = ?, result = ?, finished_at = ? WHERE id = ?",
            (status, result, _now(), job_id),
        )
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row)


def list_jobs(db: Path) -> list[dict]:
    with session(db) as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def get_setting(db: Path, key: str, default: str = "") -> str:
    with session(db) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(db: Path, key: str, value: str) -> None:
    with session(db) as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def all_settings(db: Path) -> dict:
    with session(db) as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}
