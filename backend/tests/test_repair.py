from pathlib import Path

from app.cache import scan_layer, write_tile
from app.db import init_db
from app.store import create_layer, repair_issues, repair_scan_issues, run_scan
from app.tiles import tms_y


def _layer(db: Path, slug: str = "demo", max_z: int = 2) -> None:
    create_layer(
        db,
        {
            "slug": slug,
            "name": slug,
            "provider": "procedural",
            "default_scheme": "xyz",
            "min_z": 0,
            "max_z": max_z,
        },
    )


def test_repair_issues_writes_aligned_and_rescan_clean(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    _layer(db)
    # z=1：一个缺口、一个错层
    write_tile(tmp_path, "demo", "xyz", 1, 0, 0, 1, 0, 0)
    write_tile(tmp_path, "demo", "xyz", 1, 0, 1, 1, 0, 0)  # 烘焙错 y
    # 1,1,0 缺失；1,1,1 对齐
    write_tile(tmp_path, "demo", "xyz", 1, 1, 1, 1, 1, 1)

    before = scan_layer(tmp_path, "demo", "xyz", 1)
    coords = [(i["z"], i["x"], i["y"]) for i in before]
    assert ("y_flip", 0, 1) in {(i["kind"], i["x"], i["y"]) for i in before}

    summary = repair_issues(db, tmp_path, "demo", "xyz", coords)
    assert summary["requested"] == 3
    assert summary["succeeded"] == 3
    assert summary["failed"] == 0

    after = scan_layer(tmp_path, "demo", "xyz", 1)
    assert after == [] or all(i["z"] == 0 and i["kind"] == "missing" for i in after)


def test_repair_issues_continues_past_failure(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    _layer(db)
    write_tile(tmp_path, "demo", "xyz", 1, 0, 0, 1, 0, 0)

    summary = repair_issues(
        db,
        tmp_path,
        "demo",
        "xyz",
        [(1, 0, 0), (9, 0, 0), (1, 0, 0)],  # 合法 / 越界 / 重复
    )
    assert summary["requested"] == 3
    assert summary["unique"] == 2
    assert summary["succeeded"] == 1
    assert summary["failed"] == 1
    assert summary["failures"][0]["z"] == 9


def test_repair_scan_issues_full_and_subset(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    _layer(db, max_z=1)
    write_tile(tmp_path, "demo", "xyz", 0, 0, 0, 0, 0, 0)
    # z=1 四块全缺
    run = run_scan(db, tmp_path, "demo", "xyz", 1)
    assert run["total"] == 4

    first = run["issues"][0]
    partial = repair_scan_issues(db, tmp_path, run["id"], [first["id"]])
    assert partial["succeeded"] == 1
    assert partial["failed"] == 0

    run2 = run_scan(db, tmp_path, "demo", "xyz", 1)
    assert run2["total"] == 3

    full = repair_scan_issues(db, tmp_path, run2["id"])
    assert full["succeeded"] == 3

    run3 = run_scan(db, tmp_path, "demo", "xyz", 1)
    assert run3["total"] == 0
    assert run3["missing"] == 0
    assert run3["y_flip"] == 0


def test_demo_grid_style_repair_loop_drops_counts(tmp_path: Path):
    """复刻 seed：错层+缺口 → 扫描 → 全量写回 → 再扫，计数归零。"""
    db = tmp_path / "t.db"
    init_db(db)
    _layer(db, slug="demo-grid", max_z=3)

    for z in range(4):
        n = 1 << z
        for x in range(n):
            for y in range(n):
                write_tile(tmp_path, "demo-grid", "xyz", z, x, y, z, x, y)
    for z, x, y in [(2, 1, 0), (2, 2, 1), (3, 4, 2), (3, 1, 6)]:
        wrong_y = tms_y(z, y)
        from app.cache import meta_path, png_path

        png_path(tmp_path, "demo-grid", "xyz", z, x, y).unlink()
        meta_path(tmp_path, "demo-grid", "xyz", z, x, y).unlink()
        write_tile(tmp_path, "demo-grid", "xyz", z, x, wrong_y, z, x, y)
    for z, x, y in [(1, 0, 1), (3, 7, 0), (3, 2, 5)]:
        from app.cache import meta_path, png_path

        png_path(tmp_path, "demo-grid", "xyz", z, x, y).unlink()
        meta_path(tmp_path, "demo-grid", "xyz", z, x, y).unlink()

    run = run_scan(db, tmp_path, "demo-grid", "xyz", 3)
    # 每个错层手法同时制造 1 个原位缺口 + 1 个翻转位错层：4 + 3 个额外缺口
    assert run["missing"] == 7
    assert run["y_flip"] == 4
    assert run["total"] == 11

    summary = repair_scan_issues(db, tmp_path, run["id"])
    assert summary["succeeded"] == 11
    assert summary["failed"] == 0

    again = run_scan(db, tmp_path, "demo-grid", "xyz", 3)
    assert again["total"] == 0
    assert again["missing"] == 0
    assert again["y_flip"] == 0
