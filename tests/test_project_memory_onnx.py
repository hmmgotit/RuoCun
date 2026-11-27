"""用小型 E5 形状图测试本机 ONNX 权重的加载、池化及正常任务召回。"""
import asyncio

import pytest

from ruocun.config.compat import DEFAULT_COMPATIBILITY
from ruocun.memory.inject import StartupMemorySnapshot
from ruocun.memory.project_search import rebuildProjectIndex
from ruocun.memory.project_store import ProjectMemory
from ruocun.memory.project_vectors import configureVectors
from ruocun.memory.retrieval import buildMemoryChecks
from ruocun.memory.store import MemoryStore


def test_onnx_passage_and_query_use_the_local_cpu_runtime(tmp_path):
    onnx = pytest.importorskip("onnx")
    tokenizers = pytest.importorskip("tokenizers")
    pytest.importorskip("onnxruntime")
    model = tmp_path / "model"
    model.mkdir()
    vocab = {"[UNK]": 0, "query": 1, "passage": 2, ":": 3, "checkpoint": 4, "断点": 5, "palette": 6}
    tokenizer = tokenizers.Tokenizer(tokenizers.models.WordLevel(vocab, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tokenizer.save(str(model / "tokenizer.json"))
    tensor = onnx.TensorProto
    standard = onnx.helper
    table = standard.make_tensor("weights", tensor.FLOAT, [7, 2], [0, 0]*4 + [1, 0, 1, 0, 0, 1])
    ids = standard.make_tensor_value_info("input_ids", tensor.INT64, [1, "sequence"])
    tokens = standard.make_tensor_value_info("last_hidden_state", tensor.FLOAT, [1, "sequence", 2])
    graph = standard.make_graph([standard.make_node("Gather", ["weights", "input_ids"], ["last_hidden_state"])],
                                "synthetic-e5", [ids], [tokens], [table])
    tiny = standard.make_model(graph, opset_imports=[standard.make_opsetid("", 17)])
    tiny.ir_version = 8
    onnx.save(tiny, str(model / "model.onnx"))
    cwd = tmp_path / "target"
    cwd.mkdir()
    project = ProjectMemory(tmp_path / "home", cwd)
    project.enable()
    saved = project.capture("checkpoint", kind="observation", source_id="s1")
    project.capture("palette", kind="observation", source_id="s2")
    store = MemoryStore(cwd, project.user_root, DEFAULT_COMPATIBILITY)
    configureVectors(project, "local", model=str(model))

    async def run():
        assert "已就绪" in await rebuildProjectIndex(store, project)
        result = await buildMemoryChecks(store, "断点", startup=StartupMemorySnapshot(), project=project)
        assert result.ids == (saved,)
        assert "checkpoint" in result.text

    asyncio.run(run())
