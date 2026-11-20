# JM 漫画下载（MaiBot 插件）

把 JM 本子按章节下载、转成 PDF 发到聊天，并把「搜索 / 详情 / 下载」三件事暴露成
LLM 可调用的工具，麦麦能自己判断该搜什么、该下哪一话。

移植自 AstrBot 插件 [`astrbot_plugin_comic_download`](https://github.com/Aurora-xaa/astrbot_plugin_comic_download)
（作者 Aurora-xaa），下载与解析内核仍是 [jmcomic](https://github.com/hect0x7/JMComic-Crawler-Python)。

## 相对原版的改动

| 项 | 原版（AstrBot） | 本版（MaiBot） |
| --- | --- | --- |
| 触发方式 | 只能敲 `/jm`、`/jmv` | **新增 3 个 LLM 工具**，模型可自主调用 |
| 搜索 | 无 | `jm_search` + `/jm搜` |
| 章节选择 | 整本（前 5 话）或单话 | 支持 `3`、`1-5`、`1,3,5` |
| 文件发送 | AstrBot `File` 消息段 | 走 MaiBot `ctx.files`，逐个上传发送 |
| 体积保护 | 无 | 单 PDF 超限跳过，不会撑爆宿主配额 |
| 临时文件 | 数据目录 + 定时清理 | 运行时目录隔离 + 启动清理残留 |

## 安装

**方式一：WebUI 导入 ZIP（推荐）**

插件页 → 从 ZIP 安装 → 选 `maibot_jmcomic_download.zip` → 重启麦麦。
ZIP 里是唯一一层插件目录，符合宿主 `zip_install` 的要求，可直接导入。

> 注意：如果同一个 `plugin id` 已经装过，导入会被 409 拒绝（提示「已安装，请先卸载」），
> 要先在插件页卸载再导入。

**方式二：手动放目录**

把 `maibot_jmcomic_download` 目录整体放进 MaiBot 的 `plugins/`：

```
plugins/
└── maibot_jmcomic_download/
    ├── _manifest.json
    ├── __init__.py
    ├── plugin.py
    ├── jm_client.py
    └── requirements.txt
```

目录名随意（以 `_manifest.json` 的 `id` 为准），但如果走 ZIP 安装，宿主会把它
重建成 `com_aurora_jmcomic-download`。

依赖已在 `_manifest.json` 的 `dependencies` 里声明（`jmcomic` / `img2pdf` / `Pillow`），
MaiBot 加载时会自动装；装不上就手动补到 **MaiBot 自己的那个 Python 环境**：

```bash
pip install jmcomic img2pdf Pillow
```

然后重启 MaiBot，或在 WebUI 里重载插件。

> **必须开启「能力 → 文件功能」**（`bot_config.toml` 里 `capabilities.files = true`）。
> 不开的话下载会正常完成，但 PDF 发不出去，插件会把文件存到
> `data/plugins/<插件>/exports/<时间戳>/` 并把路径回给你。

## 配置

WebUI 插件配置页编辑，或直接改插件目录下的 `config.toml`：

```toml
[plugin]
enabled = true
config_version = "1.0.0"

[download]
proxy = ""                 # http://127.0.0.1:7890，留空直连
max_album_chapters = 5     # 单次最多下载几话
max_file_mb = 45           # 单个 PDF 体积上限（宿主默认单文件 50 MiB）
image_threads = 8          # 图片并发
photo_threads = 4          # 章节并发
download_timeout_s = 900   # 单次下载总超时
cleanup_seconds = 1800     # 临时文件保留时间
detail_chapter_limit = 60  # 详情里最多列几话
enable_commands = true     # 关掉后只剩 LLM 工具
```

## 给 LLM 的工具

| 工具 | 作用 | 参数 |
| --- | --- | --- |
| `jm_search` | 按关键词搜本子，返回带 id 的列表 | `keyword`、`page` |
| `jm_detail` | 查标题/作者/标签/简介/章节表 | `target`（id 或本子链接） |
| `jm_download` | 下载并逐话发 PDF | `target`、`chapters`（可空） |

工具描述里写了「什么时候该调、什么时候不该调」，比如 `jm_download` 明确要求
**只在用户说要下载时才调用**，避免模型自作主张下东西。

典型对话：

```
用户：帮我找找有没有 xxx 的本子
麦麦：（调用 jm_search）找到这些，id=123456 ... 要哪本？

用户：第二本，看看有几话
麦麦：（调用 jm_detail）这本共 12 话 ...

用户：把前 3 话发我
麦麦：（调用 jm_download，chapters="1-3"）已发送 3 个 PDF
```

## 指令

| 指令 | 作用 |
| --- | --- |
| `/jm <ID或链接>` | 下载整本（受单次章节上限限制） |
| `/jm <ID或链接> p<话数>` | 下载指定话 |
| `/jmv <ID或链接>` | 查详情 |
| `/jm搜 <关键词>` | 搜索 |

ID 支持纯数字、`a123`、`p123`、`/album/123`、`?id=123`，也容忍
「350谁还没看过234」这种夹字输入（会自动拼成 350234）。

## 已知限制

1. **单次下载比较慢**。实测一话 25 页约 110 秒（视图源与网络），
   所以整本 5 话可能要几分钟到十几分钟。想要更快就调 `image_threads`，
   或者干脆一次只下一两话。
2. **下载必须在工具调用内完成**。MaiBot 的文件能力（`ctx.files`）作用域绑定在
   一次宿主派发的调用上，所以没法「后台下载完再推送」。宿主默认 60 秒就掐断工具调用，
   本插件已显式声明 `timeout_ms`（下载 15 分钟、搜索/详情 3 分钟）来避开这个默认值，
   插件自身的 `download_timeout_s` 默认 840 秒，会抢在 RPC 超时前收手并说明情况。
3. **文件发送依赖平台**。QQ 走 OneBot 适配器要能发文件；发不出去时插件会把 PDF
   挪到 `data/plugins/<插件>/exports/<时间戳>/` 并回一条本地路径。
4. 单话 PDF 可能超过宿主 50 MiB 上限，`max_file_mb` 就是为此存在的——
   超限的章节会被跳过而不是发送失败卡住整个任务。
5. 只下载你有权访问的内容，并遵守所在地法律法规与平台规则。

## 排错

- **提示缺少 jmcomic**：MaiBot 用的是自己 `.onekey/environments/<hash>/python.exe`，
  在别的 Python 里装没用，要装到那个环境里。
- **搜索/详情超时**：JM 的 API 域名会变，jmcomic 启动时会自动拉最新域名；
  国内网络建议配 `proxy`。
- **PDF 发不出去**：先确认「能力 → 文件功能」开着，再看 manifest 里
  `files.upload` / `files.send` 是否被宿主接受（插件详情页会显示能力校验结果）。

## 致谢与许可

- 原始 AstrBot 插件：[Aurora-xaa/astrbot_plugin_comic_download](https://github.com/Aurora-xaa/astrbot_plugin_comic_download)
- 下载内核：[hect0x7/JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python)（MIT）
- 图片转 PDF：[josch/img2pdf](https://github.com/josch/img2pdf)（LGPL）

**许可证：AGPL-3.0**。上游 `astrbot_plugin_comic_download` 采用 AGPL-3.0，
本移植版是其衍生作品，沿用同一许可证（见 `LICENSE`）。分发或部署为网络服务时，
请同样遵守 AGPL-3.0 的源码开放要求。
