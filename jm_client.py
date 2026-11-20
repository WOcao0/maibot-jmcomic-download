"""JMComic 内核封装：搜索、详情、按章节下载并转 PDF。

所有函数都是同步阻塞实现，由插件层通过 ``asyncio.to_thread`` 调用，
避免阻塞 MaiBot 的事件循环。

移植自 AstrBot 插件 ``astrbot_plugin_comic_download``（作者 Aurora-xaa）。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Iterable, Sequence

try:  # 延迟到运行时再报错，避免 import 失败拖垮整个插件加载
    import jmcomic
except Exception as _import_error:  # noqa: BLE001
    jmcomic = None  # type: ignore[assignment]
    _IMPORT_ERROR: Exception | None = _import_error
else:
    _IMPORT_ERROR = None

_URL_ID_RE = re.compile(r"/(album|photo)/(\d+)", re.IGNORECASE)
_QUERY_ID_RE = re.compile(r"[?&]id=(\d+)", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d+")

# 允许作为「第几话」的别名
_PHOTO_ALIASES = {"photo", "chapter", "章节", "单章", "章"}
_ALBUM_ALIASES = {"album", "本子", "整本", "全部"}

_PDF_MIME = "application/pdf"


class JMError(Exception):
    """可直接回给用户的可读错误。"""


# --------------------------------------------------------------------------
# 依赖与镜像
# --------------------------------------------------------------------------


def ensure_available() -> None:
    """确认 jmcomic 可用，否则抛出带安装提示的错误。"""
    if jmcomic is None:
        raise JMError(
            "缺少依赖 jmcomic，请在 MaiBot 的 Python 环境执行："
            "pip install jmcomic img2pdf Pillow"
            f"（原始错误：{_IMPORT_ERROR}）"
        )


# --------------------------------------------------------------------------
# 输入解析
# --------------------------------------------------------------------------


def parse_target(raw: str) -> tuple[str, str] | None:
    """把用户输入解析成 ``(album|photo, id)``。

    支持：纯数字、``/album/123``、``/photo/123``、``a123``、``p123``、
    ``?id=123``，以及「350谁还没看过234」这种夹字的输入。
    """
    text = (raw or "").strip()
    if not text:
        return None

    # p<id> / a<id> 前缀
    if len(text) > 1 and text[0].lower() in ("p", "a") and text[1:].isdigit():
        return ("photo" if text[0].lower() == "p" else "album"), text[1:]

    url_match = _URL_ID_RE.search(text)
    if url_match:
        mode = "photo" if url_match.group(1).lower() == "photo" else "album"
        return mode, url_match.group(2)

    query_match = _QUERY_ID_RE.search(text)
    if query_match:
        return "album", query_match.group(1)

    numbers = _NUMBER_RE.findall(text)
    if numbers:
        # 与原项目 jmv 一致：多个数字片段直接拼成一个 ID
        return "album", "".join(numbers)
    return None


def parse_album_id(raw: str) -> str | None:
    """只接受本子 ID；传入单章链接时返回 None（详情接口需要本子 ID）。"""
    parsed = parse_target(raw)
    if parsed is None or parsed[0] != "album":
        return None
    return parsed[1]


def parse_chapter_spec(spec: str) -> list[int] | None:
    """解析章节选择表达式。

    返回 None 表示「全部章节」；否则返回升序的「第几话」列表。
    支持 ``3``、``1-5``、``1,3,5``、``1-3,7``。
    """
    text = (spec or "").strip().lower()
    if not text or text in {"all", "全部", "*"}:
        return None

    indexes: set[int] = set()
    for piece in re.split(r"[,，、\s]+", text):
        if not piece:
            continue
        range_match = re.fullmatch(r"(\d+)\s*[-~]\s*(\d+)", piece)
        if range_match:
            start, end = int(range_match.group(1)), int(range_match.group(2))
            if start > end:
                start, end = end, start
            if end - start > 200:
                raise JMError("章节范围过大，一次最多选择 200 话。")
            indexes.update(range(start, end + 1))
            continue
        if piece.isdigit():
            indexes.add(int(piece))
            continue
        raise JMError(f"无法识别的章节表达：{piece}")
    return sorted(indexes) if indexes else None


# --------------------------------------------------------------------------
# 服务
# --------------------------------------------------------------------------


class JMComicService:
    """按配置构造 jmcomic 客户端，提供同步查询/下载能力。"""

    def __init__(
        self,
        proxy: str = "",
        image_threads: int = 8,
        photo_threads: int = 4,
    ) -> None:
        self.proxy = (proxy or "").strip()
        self.image_threads = max(1, min(int(image_threads or 8), 32))
        self.photo_threads = max(1, min(int(photo_threads or 4), 8))

    # ------------------------------------------------------------------ 基础

    def _client_option(self) -> Any:
        ensure_available()
        option_dict: dict[str, Any] = {}
        if self.proxy:
            option_dict["client"] = {"postman": {"meta_data": {"proxies": self.proxy}}}
        return jmcomic.JmOption.construct(option_dict)

    def _download_option(self, workdir: Path) -> Any:
        ensure_available()
        option_dict: dict[str, Any] = {
            "dir_rule": {
                "base_dir": str(workdir / "images"),
                "rule": "Bd_Pname",
            },
            "download": {
                "cache": False,
                "threading": {
                    "image": self.image_threads,
                    "photo": self.photo_threads,
                },
            },
            "plugins": {
                "after_photo": [
                    {
                        "plugin": "img2pdf",
                        "kwargs": {
                            "pdf_dir": str(workdir / "pdf"),
                            "filename_rule": "Pid",
                            "delete_original_file": True,
                        },
                    }
                ]
            },
        }
        if self.proxy:
            option_dict["client"] = {"postman": {"meta_data": {"proxies": self.proxy}}}
        return jmcomic.JmOption.construct(option_dict)

    # ------------------------------------------------------------------ 查询

    def search(self, keyword: str, page: int = 1, limit: int = 10) -> list[dict[str, Any]]:
        """按关键词搜索本子，返回精简后的条目列表。

        jmcomic 单页固定返回 80 条，这里按 ``limit`` 截断。
        """
        option = self._client_option()
        client = option.new_jm_client()
        try:
            result = client.search_site(keyword, page=max(1, int(page)))
        except Exception as exc:  # noqa: BLE001
            raise JMError(f"搜索失败：{exc}") from exc

        items: list[dict[str, Any]] = []
        for entry in getattr(result, "content", []) or []:
            if not isinstance(entry, (tuple, list)) or len(entry) < 2:
                continue
            album_id, info = entry[0], entry[1]
            if not isinstance(info, dict):
                continue
            items.append(
                {
                    "id": str(album_id),
                    "name": str(info.get("name") or "").strip(),
                    "author": str(info.get("author") or "").strip(),
                    "tags": [str(t) for t in (info.get("tags") or [])][:8],
                    "category": str(info.get("category_sub") or info.get("category") or ""),
                }
            )
            if len(items) >= limit:
                break
        return items

    def album_detail(self, album_id: str) -> Any:
        option = self._client_option()
        client = option.new_jm_client()
        try:
            return client.get_album_detail(str(album_id))
        except Exception as exc:  # noqa: BLE001
            raise JMError(f"查询本子 {album_id} 失败：{exc}") from exc

    # ------------------------------------------------------------------ 章节

    @staticmethod
    def chapter_entries(album: Any) -> list[tuple[str, str, str]]:
        """返回 ``[(photo_id, 话数, 标题)]``，按话数升序。"""
        entries: list[tuple[str, str, str]] = []
        for item in getattr(album, "episode_list", []) or []:
            try:
                pid, index, name = item[0], item[1], item[2]
            except (TypeError, IndexError):
                continue
            entries.append((str(pid), str(index).strip(), str(name or "").strip()))

        def sort_key(entry: tuple[str, str, str]) -> tuple[int, str]:
            try:
                return (0, f"{int(entry[1]):09d}")
            except ValueError:
                return (1, entry[1])

        entries.sort(key=sort_key)
        return entries

    def select_photos(
        self,
        album: Any,
        chapters: str = "",
        max_chapters: int = 5,
    ) -> tuple[list[str], str]:
        """根据选择表达式挑出要下载的 photo id，并返回提示文案。"""
        entries = self.chapter_entries(album)
        if not entries:
            return [], "该本子没有解析到任何章节。"

        wanted = parse_chapter_spec(chapters)
        if wanted is None:
            indexed = [(pid, index, name) for pid, index, name in entries]
        else:
            index_map: dict[int, str] = {}
            for pid, index, _name in entries:
                try:
                    index_map[int(index)] = pid
                except ValueError:
                    continue
            missing = [i for i in wanted if i not in index_map]
            if missing:
                raise JMError(
                    f"本子共 {len(entries)} 话，找不到第 "
                    + "、".join(str(i) for i in missing[:5])
                    + " 话。"
                )
            indexed = [(index_map[i], str(i), "") for i in wanted]

        notice = ""
        if len(indexed) > max_chapters:
            notice = (
                f"该本子共 {len(indexed)} 个待下载章节，单次最多 {max_chapters} 话，"
                f"已自动截取前 {max_chapters} 话。"
            )
            indexed = indexed[:max_chapters]

        return [pid for pid, _, _ in indexed], notice

    # ------------------------------------------------------------------ 下载

    def download(
        self,
        photo_ids: Sequence[str],
        workdir: Path,
    ) -> tuple[list[tuple[str, Path]], str]:
        """批量下载章节并按章节转 PDF，返回 ``([(文件名, 路径)], 警告)``。"""
        ensure_available()
        if not photo_ids:
            return [], ""

        option = self._download_option(workdir)
        try:
            batch = jmcomic.download_photo(list(photo_ids), option, check_exception=False)
        except Exception as exc:  # noqa: BLE001
            raise JMError(f"下载失败：{exc}") from exc

        pdfs: list[tuple[str, Path]] = []
        notes: list[str] = []
        for item in batch:
            try:
                raw_paths = [
                    Path(p)
                    for p in item.manifest.get_export_filepath_list("pdf")
                    if p and Path(p).is_file()
                ]
            except Exception:  # noqa: BLE001 - 单个章节失败不影响其它章节
                raw_paths = []
            pdfs.extend(rename_pdfs(item, raw_paths))

            downloader = getattr(item, "downloader", None)
            if downloader is not None and getattr(downloader, "has_download_failures", False):
                failed_photos = len(getattr(downloader, "download_failed_photo", []) or [])
                failed_images = len(getattr(downloader, "download_failed_image", []) or [])
                notes.append(f"{failed_photos} 个章节、{failed_images} 张图片下载失败")

        failed_batch = getattr(batch, "failed", None) or []
        if failed_batch:
            notes.append(f"{len(failed_batch)} 个章节请求失败")

        warning = "；".join(notes)
        return pdfs, warning


# --------------------------------------------------------------------------
# 文本格式化
# --------------------------------------------------------------------------


def _truncate_items(items: Iterable[str], limit: int = 10) -> str:
    values = [str(i) for i in items]
    if len(values) <= limit:
        return ", ".join(values)
    return ", ".join(values[:limit]) + f" …等{len(values)}个"


def _truncate_text(text: str, limit: int = 320) -> str:
    flat = re.sub(r"\s+", " ", str(text or "")).strip()
    return flat if len(flat) <= limit else flat[:limit] + "…"


def format_album_detail(album: Any, chapter_limit: int = 60) -> str:
    """沿用原项目 ``jmv`` 的输出样式，并对超长字段做截断。"""
    sep = "─" * 42
    lines = [
        sep,
        f"  📖 标题:  {getattr(album, 'name', '') or '未知'}",
        f"  🆔 ID:    JM{getattr(album, 'album_id', '')}",
        f"  🔗 链接:  {jmcomic.JmcomicText.format_album_url(album.album_id)}",
        f"  ✍️ 作者:  {_truncate_items(getattr(album, 'authors', []) or ['未知'])}",
        sep,
        f"  📅 发布:  {getattr(album, 'pub_date', '') or '未知'}",
        f"  📅 更新:  {getattr(album, 'update_date', '') or '未知'}",
        f"  📄 页数:  {getattr(album, 'page_count', 0)}",
        f"  👀 观看:  {getattr(album, 'views', 0)}",
        f"  ❤️ 点赞:  {getattr(album, 'likes', 0)}",
        sep,
    ]

    tags = getattr(album, "tags", None)
    if tags:
        lines.append(f"  🏷️ 标签:  {_truncate_items(tags)}")
    actors = getattr(album, "actors", None)
    if actors:
        lines.append(f"  🎭 人物:  {_truncate_items(actors)}")
    works = getattr(album, "works", None)
    if works:
        lines.append(f"  📚 作品:  {_truncate_items(works)}")
    description = getattr(album, "description", "")
    if description:
        lines.append(f"  📝 简介:  {_truncate_text(description)}")

    entries = JMComicService.chapter_entries(album)
    lines.append(sep)
    lines.append(f"  📑 章节 ({len(entries)}):")
    for _pid, index, name in entries[:chapter_limit]:
        lines.append(f"     第{index}话  {name}".rstrip())
    if len(entries) > chapter_limit:
        lines.append(f"     …还有 {len(entries) - chapter_limit} 话未显示")
    lines.append(sep)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# PDF 重命名与清理
# --------------------------------------------------------------------------


def sanitize_filename(name: str, max_length: int = 60) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(name or "")).strip(" .")
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" .")
    return cleaned or "comic"


def rename_pdfs(result: Any, pdf_paths: list[Path]) -> list[tuple[str, Path]]:
    """把以章节 ID 命名的 PDF 改成可读章节名，并按章节顺序排列。"""
    album = getattr(result, "album", None)
    photo_map: dict[str, Any] = {}

    photo = getattr(result, "photo", None)
    if photo is not None:
        photo_map[str(getattr(photo, "photo_id", ""))] = photo
    if album is not None:
        try:
            for item in album:
                photo_map[str(getattr(item, "photo_id", ""))] = item
        except Exception:  # noqa: BLE001 - 遍历失败时仍可发送已生成的 PDF
            pass

    entries: list[tuple[int, str, Path]] = []
    for pdf_path in pdf_paths:
        photo_obj = photo_map.get(pdf_path.stem)
        if photo_obj is not None:
            order = getattr(photo_obj, "album_index", 0) or 0
            title = _photo_display_name(photo_obj, album)
        else:
            order = len(entries)
            title = pdf_path.stem
        entries.append((order, title, pdf_path))

    entries.sort(key=lambda item: item[0])

    renamed: list[tuple[str, Path]] = []
    used: set[str] = set()
    for _order, title, pdf_path in entries:
        base = sanitize_filename(title)
        final_name = f"{base}.pdf"
        index = 2
        while final_name in used:
            final_name = f"{base} ({index}).pdf"
            index += 1
        used.add(final_name)

        final_path = pdf_path.with_name(final_name)
        if final_path != pdf_path:
            try:
                pdf_path.replace(final_path)
            except OSError:
                final_path, final_name = pdf_path, pdf_path.name
        renamed.append((final_name, final_path))
    return renamed


def _photo_display_name(photo: Any, album: Any) -> str:
    title = (
        getattr(photo, "indextitle", "")
        or getattr(photo, "name", "")
        or getattr(photo, "photo_id", "chapter")
    )
    album_title = ""
    if album is not None:
        album_title = getattr(album, "oname", "") or getattr(album, "title", "")
    title = str(title)
    if album_title and str(album_title) not in title:
        return f"{album_title} - {title}"
    return title


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
