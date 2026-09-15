from app.investigations.retrieval import chunk_snapshot, rank_chunks


def test_chunk_snapshot_preserves_offsets_and_overlap() -> None:
    text = ("产品能力和价格说明。" * 80) + "\n\n" + ("企业安全与权限说明。" * 80)
    chunks = chunk_snapshot(text, max_chars=300, overlap_chars=30)
    assert len(chunks) > 2
    assert all(text[item.char_start : item.char_end] == item.content for item in chunks)
    assert [item.ordinal for item in chunks] == list(range(len(chunks)))


def test_rank_chunks_prefers_query_specific_chinese_text() -> None:
    chunks = [
        {"id": "features", "content": "支持工作流、权限和企业登录"},
        {"id": "pricing", "content": "专业版每月人民币 199 元"},
    ]
    ranked = rank_chunks("专业版价格 每月 人民币", chunks)
    assert ranked[0]["id"] == "pricing"
