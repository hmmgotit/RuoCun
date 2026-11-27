"""实现前台 /memory 的审计和管理命令。"""

import asyncio
import shlex
from pathlib import Path

from ..tools.types import ToolRejected
from .audit import formatAudit, gatherAudit
from .project_store import ProjectMemory
from .store import MemoryStore, isMemoryName

_DELETE = {"delete", "rm", "forget"}
_USAGE = "用法:/memory delete <名> [名…](名见 /memory 审核报告)"


def _targets(text: str) -> list[str] | None:
    parts = text.split()
    if len(parts) >= 2 and parts[0] == "/memory" and parts[1] in _DELETE:
        return parts[2:]
    return None


def isMutatingMemoryCommand(text: str) -> bool:
    parts = text.split()
    if parts[:2] == ["/memory", "project"] and len(parts) >= 3:
        return parts[2] not in {"status", "list", "show"}
    targets = _targets(text)
    return bool(targets and all(isMemoryName(target) for target in targets))


def _project_command(raw: str, project: ProjectMemory) -> str:
    try:
        args = [word[1:-1] if word[:1] in {'"', "'"} and word[-1:] == word[:1] else word
                for word in shlex.split(raw, posix=False)][2:]
        if args == ["enable"]:
            project.enable()
            return "已开启当前工作区的工程记忆。"
        if not project.enabled:
            return "当前工作区尚未开启工程记忆。"
        if args == ["list"]:
            return "\n".join(f"{item.id} v{item.version} [{item.kind}] {item.text}"
                             for item in project.visible()) or "暂无工程记忆。"
        match args:
            case [] | ["status"]:
                return project.status()
            case ["quota", quantity]:
                project.set_quota(int(quantity))
                return "已设置项目工程记忆内容配额。\n" + project.status()
            case ["show", identity]:
                entry = project.get(identity)
                if entry is None:
                    return "未找到有效工程记忆。"
                project.touch(entry.id, entry.version)
                return (f"{entry.id} v{entry.version} [{entry.kind}]\n来源：{entry.workspace} · "
                        f"{entry.source or entry.source_id} · {entry.source_status()}\n"
                        f"适用条件：{entry.condition or '按当前任务核对'}\n{entry.text}")
            case ["link", path]:
                project.link(Path(path))
                return f"已关联：{project.cwd} 读取 {path}。"
            case ["unlink", path]:
                return "已解除关联。" if project.unlink(Path(path)) else "未找到关联。"
            case ["publish", identity, path]:
                return (f"已发布 {identity}：{project.cwd} → {path}。" if project.publish(identity, Path(path))
                        else "未发布：仅能发布本项目经核实且来源有效的结论或规则。")
            case ["forget", identity]:
                entry = project.get(identity)
                if entry is None:
                    return "未找到有效工程记忆。"
                if project.forget(identity, expected_version=entry.version):
                    return f"已撤回 {identity} v{entry.version}，本项目和引用方的新检索均失效。"
                return "受保护或版本已变。"
            case ["correct", identity, *words] if words:
                entry = project.get(identity)
                if entry is None:
                    return "未找到有效工程记忆。"
                if project.correct(identity, " ".join(words), expected_version=entry.version):
                    return "已更正工程记忆。"
                return "未更正：条目受保护、版本已变、内容或配额不符合要求。"
        return ("用法：/memory project enable|status|list|quota <字节>|link <目录>|unlink <目录>|"
                "publish <ID> <目录>|correct <ID> <新正文>|forget <ID>")
    except ValueError as error:
        return f"工程记忆操作未完成：{error}"
    except OSError:
        return "工程记忆操作结果未确认，请按条目身份检查当前状态。"


async def memoryCommand(text: str, store: MemoryStore, project: ProjectMemory | None = None) -> str | None:
    parts = text.split()
    if not parts or parts[0] != "/memory":
        return None
    if len(parts) >= 2 and parts[1] == "project" and project is not None:
        return await asyncio.to_thread(_project_command, text, project)
    targets = _targets(text)
    if targets is None:
        today = store.today()
        report = formatAudit(await gatherAudit(store, today), today)
        if parts == ["/memory"] and project is not None and project.enabled:
            return report + "\n" + await asyncio.to_thread(project.status) + "\n/memory project status 查看索引状态。"
        return report
    if not targets or not all(isMemoryName(target) for target in targets):
        return _USAGE
    done, missing, readonly, locked = [], [], [], []
    refusals = []
    for target in targets:
        try:
            result = await store.delete(target, exactName=True)
        except ToolRejected as exc:
            refusals.append(f"未删除:{target}。{exc}")
            continue
        if result.removed:
            done.append(target)
        elif result.readOnly:
            readonly.append(target)
        elif result.locked:
            locked.append(target)
        else:
            missing.append(target)
    groups = (("已删除", done), ("未找到", missing), ("只读兼容来源已保留", readonly), ("锁定记忆已保留", locked))
    lines = [f"{label}:{', '.join(names)}" for label, names in groups if names]
    return "\n".join(lines + refusals) or "无操作"
