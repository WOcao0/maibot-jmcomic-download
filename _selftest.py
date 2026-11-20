"""离线自检：不启动 MaiBot，只验证导入、组件注册与纯函数逻辑。"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from maibot_jmcomic_download import create_plugin  # noqa: E402
from maibot_jmcomic_download.jm_client import (  # noqa: E402
    JMComicService,
    format_album_detail,
    parse_album_id,
    parse_chapter_spec,
    parse_target,
    sanitize_filename,
)

failures = []


def check(name, actual, expected):
    if actual != expected:
        failures.append(f"{name}: 期望 {expected!r}，实际 {actual!r}")
        print(f"  FAIL {name}: 期望 {expected!r}，实际 {actual!r}")
    else:
        print(f"  ok   {name}")


print("\n== 1. 导入与实例化 ==")
plugin = create_plugin()
print(f"  ok   插件实例: {type(plugin).__name__}")
check("config_model", plugin.config_model.__name__, "JMConfig")
check(
    "配置默认值",
    (plugin.config_model().download.max_album_chapters, plugin.config_model().download.max_file_mb),
    (5, 45),
)

print("\n== 2. 组件注册 ==")
components = []
for attr_name, attr in vars(type(plugin)).items():
    info = getattr(attr, "__maibot_component_info__", None)
    if info is not None:
        components.append((type(info).__name__, getattr(info, "name", attr_name), attr_name))
for kind, name, attr_name in components:
    print(f"  ok   {kind:<22} name={name:<22} 方法={attr_name}")
tool_names = {n for k, n, _ in components if "Tool" in k}
command_names = {n for k, n, _ in components if "Command" in k}
check("Tool 数量", len(tool_names), 3)
check("Command 数量", len(command_names), 3)
check("Tool 名称", sorted(tool_names), ["jm_detail", "jm_download", "jm_search"])
check("Tool/Command 不重名", bool(tool_names & command_names), False)

print("\n== 3. 目标解析 ==")
check("纯数字", parse_target("350234"), ("album", "350234"))
check("本子链接", parse_target("https://18comic.vip/album/350234/foo"), ("album", "350234"))
check("章节链接", parse_target("https://18comic.vip/photo/123456"), ("photo", "123456"))
check("a 前缀", parse_target("a350234"), ("album", "350234"))
check("p 前缀", parse_target("p123456"), ("photo", "123456"))
check("query id", parse_target("https://x.com/x?id=9988"), ("album", "9988"))
check("夹字", parse_target("350谁还没看过234"), ("album", "350234"))
check("空输入", parse_target("   "), None)
check("无数字", parse_target("abc"), None)
check("详情只认本子", parse_album_id("p123456"), None)

print("\n== 4. 章节表达式 ==")
check("空=全部", parse_chapter_spec(""), None)
check("all=全部", parse_chapter_spec("ALL"), None)
check("单话", parse_chapter_spec("3"), [3])
check("区间", parse_chapter_spec("1-5"), [1, 2, 3, 4, 5])
check("倒序区间", parse_chapter_spec("5-1"), [1, 2, 3, 4, 5])
check("混合", parse_chapter_spec("1,3,5-7"), [1, 3, 5, 6, 7])
check("中文逗号", parse_chapter_spec("1，3"), [1, 3])
try:
    parse_chapter_spec("abc")
    failures.append("非法表达式未报错")
    print("  FAIL 非法表达式未报错")
except Exception as exc:
    print(f"  ok   非法表达式被拒: {type(exc).__name__}")

print("\n== 5. 文件名清洗 ==")
check("非法字符", sanitize_filename('a/b:c*?"<>|d'), "a_b_c______d")
check("空名", sanitize_filename("   "), "comic")
check("超长截断", len(sanitize_filename("x" * 100)), 60)

print("\n== 6. 真实接口（搜索 + 详情）==")
try:
    svc = JMComicService()
    items = svc.search("test", page=1, limit=3)
    print(f"  ok   搜索返回 {len(items)} 条, 首条字段: {sorted(items[0].keys()) if items else '空'}")
    detail = svc.album_detail("350234")
    text = format_album_detail(detail, chapter_limit=5)
    print(f"  ok   详情格式化 {len(text)} 字符, 章节数 {len(JMComicService.chapter_entries(detail))}")
    pids, notice = svc.select_photos(detail, "", 5)
    print(f"  ok   章节选择 -> {pids} | 提示: {notice or '无'}")
except Exception as exc:
    failures.append(f"真实接口失败: {exc}")
    print(f"  FAIL 真实接口: {type(exc).__name__}: {exc}")

print("\n== 结果 ==")
if failures:
    print(f"{len(failures)} 项失败:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("全部通过")
