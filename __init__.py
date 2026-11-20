"""JM 漫画下载（MaiBot 插件）。

把 JM 本子按章节下载并转成 PDF 发送，同时向 LLM 暴露搜索/详情/下载工具。

移植自 AstrBot 插件 `astrbot_plugin_comic_download`
（作者 Aurora-xaa，https://github.com/Aurora-xaa/astrbot_plugin_comic_download），
下载内核仍是 jmcomic（JMComic-Crawler-Python）。

模块划分：
    plugin.py     入口、配置模型、生命周期、Tool/Command 组件、文件发送
    jm_client.py  jmcomic 同步内核：搜索、详情、章节选择、下载转 PDF、重命名
"""

from .plugin import JMComicPlugin, create_plugin

__all__ = ["JMComicPlugin", "create_plugin"]
