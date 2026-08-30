"""Plugin Manifest 白盒测试：严格 schema 校验（正常 / 非法输入 / 边界）。"""
import json

import pytest

from src.plugins.manifest import PluginManifest, PluginManifestError


def _base(**over):
    data = {
        "id": "example_plugin", "name": "Example Plugin", "version": "1.0.0",
        "author": "author", "description": "Example", "runtime": "python",
        "entry": "plugin.py", "api_version": "1", "permissions": ["send_message"],
        "config": {},
    }
    data.update(over)
    return data


# ---------- 正常 ----------
def test_valid_python_manifest():
    m = PluginManifest.from_dict(_base())
    assert m.id == "example_plugin"
    assert m.runtime == "python"
    assert m.entry == "plugin.py"
    assert m.permissions == ["send_message"]


def test_valid_node_manifest():
    m = PluginManifest.from_dict(_base(runtime="node", entry="index.js"))
    assert m.runtime == "node"
    assert m.entry == "index.js"


def test_valid_declarative_json_manifest():
    m = PluginManifest.from_dict(_base(runtime="json", entry="", permissions=["read_message"], declarations=[
        {"event": "message", "match": {"text_prefix": "hello"},
         "actions": [{"type": "send_message", "payload": {"group_id": "${group_id}"}}]}]))
    assert m.runtime == "json"
    assert len(m.declarations) == 1


def test_roundtrip_to_dict_json():
    m = PluginManifest.from_dict(_base())
    out = m.to_dict()
    assert out["id"] == "example_plugin"
    m2 = PluginManifest.from_dict(json.loads(m.to_json()))
    assert m2.id == m.id and m2.permissions == m.permissions


# ---------- 非法输入（要求全部被拒） ----------
@pytest.mark.parametrize("over,needle", [
    ({"id": "BadID!"}, "小写"),                # 大写/非法字符
    ({"id": ""}, "小写"),
    ({"id": "9abc"}, "小写"),                  # 数字开头
    ({"id": "a" * 33}, "32"),
    ({"name": ""}, "name"),
    ({"version": "1.0"}, "x.y.z"),
    ({"version": "1.0.0-alpha"}, "x.y.z"),
    ({"runtime": "shell"}, "runtime"),
    ({"entry": "../evil.py"}, "非法路径"),
    ({"entry": "/etc/passwd"}, "非法路径"),
    ({"entry": "a\\b.py"}, "非法路径"),
    ({"api_version": "2"}, "api_version"),
    ({"permissions": "send_message"}, "数组"),
    ({"permissions": ["nope"]}, "不在允许列表"),
    ({"config": []}, "JSON 对象"),
    ({"unknown_field": 1}, "未知字段"),
])
def test_invalid_manifest_rejected(over, needle):
    with pytest.raises(PluginManifestError, match=needle):
        PluginManifest.from_dict(_base(**over))


def test_missing_required_fields_rejected():
    for key in ("id", "name", "version", "runtime", "entry", "api_version", "permissions"):
        data = _base()
        del data[key]
        with pytest.raises(PluginManifestError, match="必填字段"):
            PluginManifest.from_dict(data)


def test_declarations_rejected_for_python():
    with pytest.raises(PluginManifestError, match="仅 runtime=json"):
        PluginManifest.from_dict(_base(declarations=[]))


def test_declarations_rule_validation():
    with pytest.raises(PluginManifestError, match="event 非法"):
        PluginManifest.from_dict(_base(runtime="json", entry="", declarations=[
            {"event": "hack", "match": {}, "actions": [{"type": "send_message"}]}]))
    with pytest.raises(PluginManifestError, match="actions"):
        PluginManifest.from_dict(_base(runtime="json", entry="", declarations=[
            {"event": "message", "match": {}, "actions": []}]))
    with pytest.raises(PluginManifestError, match="match"):
        PluginManifest.from_dict(_base(runtime="json", entry="", declarations=[
            {"event": "message", "match": {"evil": 1}, "actions": [{"type": "test"}]}]))


def test_permission_dedupe_and_cap():
    m = PluginManifest.from_dict(_base(permissions=["send_message", "send_message"]))
    assert m.permissions == ["send_message"]
    too_many = ["send_message"] * 25
    with pytest.raises(PluginManifestError, match="上限"):
        PluginManifest.from_dict(_base(permissions=too_many))


def test_load_from_disk(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(_base()), encoding="utf-8")
    m = PluginManifest.load(str(p))
    assert m.id == "example_plugin"


def test_load_missing_file(tmp_path):
    with pytest.raises(PluginManifestError, match="不存在"):
        PluginManifest.load(str(tmp_path / "nope.json"))


def test_load_oversized_manifest(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(_base(description="x" * 70000)), encoding="utf-8")
    with pytest.raises(PluginManifestError, match="大小上限"):
        PluginManifest.load(str(p))
