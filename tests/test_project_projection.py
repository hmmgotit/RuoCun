import asyncio
import json

from ruocun.agent.compact import buildCompaction
from ruocun.memory.project_projection import filter_projection, mark_memory


def test_withdrawal_invalidates_mixed_and_nested_summaries_but_preserves_original_user():
    rule = mark_memory("pm-123", "1", "ORIGINAL-MEMORY")
    literal = {"role": "user", "content": "用户的原文 " + rule}
    messages = [{"role": "system", "content": rule}, literal]
    for label in "abc":
        messages.extend([{"role": "user", "content": label * 800}, {"role": "assistant", "content": label * 800}])

    async def summarize(_):
        return "DERIVED-FACT"

    compacted = asyncio.run(buildCompaction(messages, {"summarize": summarize, "retainTokens": 0})).messages
    assert "DERIVED-FACT" in str(filter_projection(compacted, lambda i, v: True))
    assert "DERIVED-FACT" not in str(filter_projection(compacted, lambda i, v: False))
    assert filter_projection([literal], lambda i, v: False) == [literal]
    nested = [dict(role="system", content="prefix"), *compacted[1:], *messages[-4:]]
    nested = asyncio.run(buildCompaction(nested, {"summarize": summarize, "retainTokens": 0})).messages
    assert "DERIVED-FACT" not in str(filter_projection(nested, lambda i, v: False))


def test_archive_tool_page_with_no_visible_markers_still_depends_on_original_version():
    from ruocun.tools.archive_read import makeArchiveReadTool
    from ruocun.tools.types import ToolContext

    async def page(identity, offset, limit):
        return {"id": identity, "text": "INTERIOR-PAGE", "memory_versions": [["pm-123", "1"]]}

    context = ToolContext("work", archiveRead=page)
    body = asyncio.run(makeArchiveReadTool().handler({"id": "entry", "offset": 100, "limit": 20}, context))
    reply = {"role": "tool", "tool_call_id": "archive", "content": body}
    visible = filter_projection([reply], lambda i, v: True)[0]
    assert json.loads(visible["content"])["text"] == "INTERIOR-PAGE"
    assert "INTERIOR-PAGE" not in filter_projection([reply], lambda i, v: False)[0]["content"]
