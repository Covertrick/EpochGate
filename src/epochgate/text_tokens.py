"""冻结的客户端可观察文本词元计数。不是供应商计费口径。"""

from __future__ import annotations

TOKENIZER_NAME = "tiktoken"
TOKENIZER_ENCODING = "cl100k_base"
TOKENIZER_VERSION = "tiktoken>=0.7"


def count_observed_text_tokens(text: str) -> int:
    """用冻结的 tiktoken 编码统计客户端已收到文本的词元数。

    输入：客户端拼出的纯文本。
    输出：词元个数。空串为 0。
    副作用：首次调用会加载编码表。
    失败：tiktoken 不可用时抛出 ``ImportError``。
    """
    if not text:
        return 0
    import tiktoken

    encoding = tiktoken.get_encoding(TOKENIZER_ENCODING)
    return len(encoding.encode(text))
