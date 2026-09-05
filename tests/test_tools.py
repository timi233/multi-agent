"""文件工具集单测：功能 + 安全边界（路径逃逸拒绝）。"""
import pytest

from app.runtime.tools import run_tool


def test_write_and_read(tmp_path):
    r = run_tool("write_file", {"path": "a/b.txt", "content": "hello\nworld"}, tmp_path)
    assert r["ok"] and r["data"]["bytes"] > 0
    r = run_tool("read_file", {"path": "a/b.txt"}, tmp_path)
    assert r["ok"] and "hello" in r["data"]["content"]


def test_read_offset_limit(tmp_path):
    (tmp_path / "f.txt").write_text("\n".join(f"line{i}" for i in range(10)))
    r = run_tool("read_file", {"path": "f.txt", "offset": 2, "limit": 3}, tmp_path)
    assert r["ok"] and r["data"]["lines"] == 2
    assert "line2" in r["data"]["content"] and "line4" in r["data"]["content"]


def test_edit_file(tmp_path):
    (tmp_path / "f.txt").write_text("aaa bbb aaa")
    r = run_tool("edit_file", {"path": "f.txt", "old_text": "aaa", "new_text": "X", "count": 0}, tmp_path)
    assert r["ok"] and (tmp_path / "f.txt").read_text() == "X bbb X"


def test_edit_missing_old_text(tmp_path):
    (tmp_path / "f.txt").write_text("hello")
    r = run_tool("edit_file", {"path": "f.txt", "old_text": "nope", "new_text": "X"}, tmp_path)
    assert not r["ok"] and "not found" in r["error"]


def test_path_escape_rejected(tmp_path):
    (tmp_path.parent / "outside.txt").write_text("secret")
    for name, args in [
        ("write_file", {"path": "../outside.txt", "content": "x"}),
        ("read_file", {"path": "sub/../../outside.txt"}),
        ("list_dir", {"path": ".."}),
    ]:
        r = run_tool(name, args, tmp_path)
        assert not r["ok"], f"{name} should be rejected"
        assert "escapes workspace root" in r["error"]


def test_unknown_tool():
    r = run_tool("nonexistent", {}, tmp_path := __import__("pathlib").Path("."))
    assert not r["ok"] and "unknown tool" in r["error"]


def test_bad_args_json():
    r = run_tool("write_file", "not-json", tmp_path := __import__("pathlib").Path("."))
    assert not r["ok"]


def test_run_command(tmp_path):
    r = run_tool("run_command", {"command": "python3 -c 'print(6*7)'"}, tmp_path)
    assert r["ok"] and r["data"]["exit_code"] == 0 and "42" in r["data"]["stdout"]


def test_run_command_nonzero(tmp_path):
    r = run_tool("run_command", {"command": "python3 -c 'import sys; sys.exit(3)'"}, tmp_path)
    assert r["ok"] and r["data"]["exit_code"] == 3


def test_grep_and_find(tmp_path):
    (tmp_path / "app.py").write_text("def main():\n    pass\n")
    (tmp_path / "notes.txt").write_text("nothing here\n")
    r = run_tool("grep", {"path": ".", "pattern": "def main"}, tmp_path)
    assert r["ok"] and any("app.py" in m["file"] for m in r["data"]["matches"])
    r = run_tool("find_files", {"path": ".", "pattern": r"\.py$"}, tmp_path)
    assert r["ok"] and "app.py" in r["data"]["matches"]


def test_finish_tool():
    r = run_tool("finish", {"summary": "done!"}, tmp_path := __import__("pathlib").Path("."))
    assert r["ok"] and r["data"]["summary"] == "done!"