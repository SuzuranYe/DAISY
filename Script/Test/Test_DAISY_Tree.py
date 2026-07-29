r"""Test_DAISY_Tree：DAISY 合成测试树生成器。

职责：
- write：按相对路径造文件（自动建父目录，可指定 mtime）。
- build_snapshot：对树跑真实管线（枚举→哈希→复扫→封存）生成封存快照。
  元数据阶段以 skipped 快进——黄金测试聚焦 Diff 语义，媒体解析由专门测试覆盖；
  pre_enum/post_enum/pre_finalize 钩子用于构造时序类场景（T11b/T16/T17）。
- SCENARIOS＋CLI：把简单场景物化为「旧树 → 变换 → 新树」目录对供人工检视；
  时序/权限/链条类场景（T11/T11b/T13/T14/T16/T17）由黄金测试直接编排。

用法：
  python .\Script\Test\Test_DAISY_Tree.py --list
  python .\Script\Test\Test_DAISY_Tree.py --scenario T05 --dest .\TreePair
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPT_DIR = os.path.dirname(_TEST_DIR)
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_LIB_DIR = os.path.join(_SCRIPT_DIR, "Lib")
sys.path[:0] = [_TEST_DIR, _SCRIPT_DIR, _LIB_DIR]
import Script_DAISY_Lib_01_Core as core
import Script_DAISY_Lib_03_Hash as dbh


def write(tree: str, rel: str, data: bytes, mtime_ns: int | None = None) -> str:
    """在树内写文件（覆盖），自动创建父目录。"""
    p = os.path.join(tree, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    if mtime_ns is not None:
        os.utime(p, ns=(mtime_ns, mtime_ns))
    return p


def build_snapshot(tree_dir: str, out_dir: str, name: str, label: str = "T",
                   hash_mode: str = "full", previous_path: str | None = None,
                   pre_enum=None, post_enum=None, pre_finalize=None) -> str:
    """真实管线快照：枚举（可重跑对账）→哈希→复扫→封存。返回封存路径。

    钩子签名：pre_enum(con, tree_dir)、post_enum(con, tree_dir)、pre_finalize(con)。
    """
    partial = os.path.join(out_dir, f"Scan_{name}.partial.sqlite")
    con = core.create_partial_snapshot(partial, [(label, tree_dir)],
                                       config={"phase": "test-tree"})
    if pre_enum:
        pre_enum(con, tree_dir)
    core.enumerate_and_reconcile(con)
    if post_enum:
        post_enum(con, tree_dir)
        con.commit()
    if hash_mode == "none":
        con.execute("UPDATE entries SET hash_status='skipped'"
                    " WHERE hash_status='pending'")
    else:
        prev = dbh.load_previous(previous_path) if previous_path else None
        dbh.process_hash_stage(con, hash_mode, previous=prev)
    con.execute("UPDATE entries SET meta_status='skipped'"
                " WHERE meta_status IN ('pending','processing')")
    con.commit()
    core.rescan_check(con)
    if pre_finalize:
        pre_finalize(con)
        con.commit()
    return core.finalize_snapshot(con, partial, hash_mode)


# === 简单场景注册表（旧树构建 + 新树变换；新树由旧树 copy2 克隆而来） ===
def _t05_build(t):
    write(t, os.path.join("d1", "f old.bin"), b"move-me")


def _t05_transform(t):
    os.makedirs(os.path.join(t, "d2"), exist_ok=True)
    os.rename(os.path.join(t, "d1", "f old.bin"),
              os.path.join(t, "d2", "g new.bin"))


def _t18_transform(t):
    os.remove(os.path.join(t, "x"))
    write(t, os.path.join("x", "y.txt"), b"inside")


SCENARIOS = {
    "T01": {"build": lambda t: write(t, "a.bin", b"alpha-data"),
            "transform": None, "note": "未变"},
    "T02": {"build": lambda t: write(t, "a.bin", b"short"),
            "transform": lambda t: write(t, "a.bin", b"longer-content"),
            "note": "内容改、size 变"},
    "T03": {"build": lambda t: write(t, "a.bin", b"AAAA"),
            "transform": lambda t: write(t, "a.bin", b"BBBB"),
            "note": "内容改、size 不变"},
    "T04": {"build": lambda t: write(t, "a.bin", b"same-content"),
            "transform": lambda t: os.utime(os.path.join(t, "a.bin")),
            "note": "touch 改 mtime"},
    "T05": {"build": _t05_build, "transform": _t05_transform,
            "note": "同卷移动＋改名"},
    "T06": {"build": lambda t: (write(t, "a.bin", b"dup-content"),
                                write(t, "b.bin", b"dup-content")),
            "transform": lambda t: write(t, "c.bin", b"dup-content"),
            "note": "复制出新副本 2→3"},
    "T07": {"build": lambda t: (write(t, "keep.bin", b"keep"),
                                write(t, "gone.bin", b"gone-data")),
            "transform": lambda t: os.remove(os.path.join(t, "gone.bin")),
            "note": "删除"},
    "T08": {"build": lambda t: write(t, "keep.bin", b"keep"),
            "transform": lambda t: write(t, "fresh.bin", b"fresh-unique"),
            "note": "新增"},
    "T09": {"build": lambda t: write(t, "Name.TXT", b"case-data"),
            "transform": lambda t: os.rename(os.path.join(t, "Name.TXT"),
                                             os.path.join(t, "name.txt")),
            "note": "仅大小写改名"},
    "T10": {"build": lambda t: write(t, "café.bin", b"unicode-data"),
            "transform": lambda t: os.rename(os.path.join(t, "café.bin"),
                                             os.path.join(t, "café.bin")),
            "note": "NFD → NFC 改名（path_key 归一）"},
    "T12": {"build": lambda t: (write(t, "same.bin", b"12345"),
                                write(t, "diff.bin", b"aaaa")),
            "transform": lambda t: write(t, "diff.bin", b"bbbbbb"),
            "note": "无哈希快照对比（两侧 --hash none 扫描）"},
    "T15": {"build": lambda t: write(t, "a.bin", b"hardlink-content"),
            "transform": lambda t: os.link(os.path.join(t, "a.bin"),
                                           os.path.join(t, "b.bin")),
            "note": "硬链接组（同 file_id 两路径）"},
    "T18": {"build": lambda t: (write(t, "x", b"was-a-file"),
                                write(t, "s.bin", b"sibling")),
            "transform": _t18_transform, "note": "file→dir 同名替换"},
    "T19": {"build": lambda t: (write(t, "café.bin", b"one"),
                                write(t, "café.bin", b"two")),
            "transform": None, "note": "NFC/NFD 同名并存（path_key 碰撞组）"},
    "T20": {"build": lambda t: (write(t, "f1.bin", b"content-1"),
                                write(t, os.path.join("sub", "f2.bin"),
                                      b"content-2")),
            "transform": None,
            "note": "备份核对（新树目录名不同、mtime 保留、创建时间自变）"},
}

# 时序/权限/链条类场景需运行时编排，注册表仅记录说明（黄金测试直接构造）
SPECIAL_NOTES = {
    "T11": "模拟目录 PermissionError，构造 access_denied",
    "T11b": "pre_enum 钩子删除 root 构造 root 级枚举失败",
    "T13": "A 全量 → B 增量自 A → C 增量自 B，Diff A vs C",
    "T14": "双侧独立全量（T01 同树即覆盖）",
    "T16": "post_enum 钩子同尺寸改写 → 复扫标 unstable",
    "T16b": "扫描期间新导入（单侧存在且 unstable）",
    "T17": "post_enum 钩子置 is_placeholder=1（哈希/元数据跳过）",
}


def materialize(name: str, dest: str) -> tuple[str, str]:
    """把场景物化为 dest/old 与 dest/new 目录对。"""
    sc = SCENARIOS[name]
    old_dir = os.path.join(dest, "old")
    new_dir = os.path.join(dest, "new")
    os.makedirs(old_dir)
    sc["build"](old_dir)
    shutil.copytree(old_dir, new_dir)      # copy2：保 mtime，创建时间自变
    if sc["transform"]:
        sc["transform"](new_dir)
    return old_dir, new_dir


def main() -> int:
    core.force_utf8_io()
    ap = argparse.ArgumentParser(description="DAISY Diff 合成测试树生成器")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--scenario")
    ap.add_argument("--dest")
    args = ap.parse_args()
    if args.list or not args.scenario:
        for k, v in SCENARIOS.items():
            print(f"{k}: {v['note']}")
        for k, v in SPECIAL_NOTES.items():
            print(f"{k}: {v}（测试编排场景，不可物化）")
        return 0
    if args.scenario not in SCENARIOS:
        print(f"未知或不可物化的场景：{args.scenario}", file=sys.stderr)
        return 2
    if not args.dest:
        print("需要 --dest", file=sys.stderr)
        return 2
    old_dir, new_dir = materialize(args.scenario, args.dest)
    print(f"旧树：{old_dir}\n新树：{new_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
