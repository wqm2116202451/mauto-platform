"""模型接入层。

默认使用内置的演示模型引擎（无需外部 Key，可完整演示流式输出/推理/引用/插件），
若配置 MAUTO_LLM_API_KEY + MAUTO_LLM_BASE_URL，则自动切换为真实 OpenAI 兼容接口。
"""
from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.request
from collections.abc import Iterator

MODELS = [
    {"id": "MAuto-Air", "name": "MAuto-Air", "desc": "轻量快速，适合日常问答"},
    {"id": "MAuto-Pro", "name": "MAuto-Pro", "desc": "深度推理，适合长文与复杂任务"},
    {"id": "MAuto-Mini", "name": "MAuto-Mini", "desc": "极速响应，适合短问答"},
]

API_KEY = os.environ.get("MAUTO_LLM_API_KEY", "")
BASE_URL = os.environ.get("MAUTO_LLM_BASE_URL", "https://api.openai.com/v1")


def _clean_prompt(prompt: str) -> str:
    return re.sub(r"\s+", " ", prompt).strip()


def _pick(prompt: str) -> str:
    p = prompt.lower()
    if any(k in p for k in ("单词", "english", "vocabulary", "英语")):
        return "words"
    if any(k in p for k in ("资讯", "早报", "新闻", "行业动态", "晚报")):
        return "news"
    if any(k in p for k in ("ppt", "幻灯片", "演示文稿", "汇报")):
        return "ppt"
    if any(k in p for k in ("代码", "python", "编程", "接口", "sql", "算法", "bug", "报错")):
        return "code"
    if any(k in p for k in ("总结", "润色", "改写", "方案", "周报", "复盘")):
        return "writing"
    if any(k in p for k in ("项目", "资料", "文档", "知识库")):
        return "project"
    return "general"


def _title_of(prompt: str) -> str:
    text = _clean_prompt(prompt).strip("？?。.!！")
    return text[:28] if text else "本次提问"


def _general(prompt: str) -> str:
    topic = _title_of(prompt)
    return f"""关于「{topic}」，可以从目标、路径和落地三个层面来理解。

## 一、先明确目标
把问题拆成「要解决什么」和「判断成功的标准是什么」。多数时候，模糊的目标会让后续所有动作都在返工。建议用一句话写清目标，例如：

> 在两周内，把每日手工整理行业资讯的时间从 60 分钟压缩到 10 分钟以内。

## 二、可执行的三步路径

1. **收敛范围**：只保留与目标强相关的信息源与任务，其余全部延后处理；
2. **建立最小闭环**：先跑通「输入 → 处理 → 输出」的最短链路，再考虑优化；
3. **固定节奏复盘**：以周为单位检查效果，用数据而不是体感做调整。

| 阶段 | 关键动作 | 产出 |
| --- | --- | --- |
| 第 1 周 | 明确目标与边界 | 一页纸目标和指标 |
| 第 2 周 | 跑通最小闭环 | 可复用的流程 |
| 第 3 周起 | 优化与自动化 | 稳定的日常机制 |

## 三、常见误区

- 一开始就追求完备的方案，导致迟迟无法启动；
- 只收集信息、不做收敛，输入越多决策越慢；
- 没有量化标准，无法判断改进是否有效。

## 四、可以马上做的三件事

- 用 10 分钟写下本次要达成的**一个**结果；
- 列出当前卡点，并按「影响 × 成本」排序；
- 把最耗时的一步交给定时任务或插件自动执行。

如果需要，我可以基于你的具体场景，直接给出一份可执行的清单或方案草稿。"""


def _code(prompt: str) -> str:
    topic = _title_of(prompt)
    return f"""针对「{topic}」，下面给出一份可直接运行的 Python 实现与说明。

## 实现思路
1. 先定义输入输出的数据结构，避免后续反复改参数；
2. 把「获取数据」和「处理数据」分离，便于单独测试；
3. 用异常兜底 + 日志记录失败原因，而不是让程序静默退出。

## 示例代码

```python
from dataclasses import dataclass, field
from typing import Iterable

@dataclass
class Item:
    title: str
    score: float
    tags: list[str] = field(default_factory=list)

def rank(items: Iterable[Item], top_k: int = 5) -> list[Item]:
    \"\"\"按分数倒序取前 K 条，分数相同按标题排序保证结果稳定。\"\"\"
    return sorted(items, key=lambda it: (-it.score, it.title))[:top_k]

if __name__ == "__main__":
    data = [Item("A 方案", 92.5, ["效率"]), Item("B 方案", 88.0), Item("C 方案", 95.2)]
    for idx, it in enumerate(rank(data, 2), 1):
        print(idx, it.title, it.score)
```

## 关键点说明

| 关注点 | 做法 | 原因 |
| --- | --- | --- |
| 结果稳定性 | 二级排序键 | 避免同分时顺序抖动 |
| 可扩展性 | dataclass + 纯函数 | 便于替换数据源 |
| 可观测性 | 失败落日志 | 便于定位线上问题 |

## 下一步建议
- 把 `rank` 抽成独立模块，补充 3~5 条单元测试；
- 若数据量超过 10 万条，改用堆排序 `heapq.nlargest` 降低开销；
- 对外暴露为接口时，加上分页参数 `page` / `page_size`。

需要我把它改造成 FastAPI 接口，或补充测试用例吗？"""


def _words(prompt: str) -> str:
    return """今天的 5 个高频英文单词如下，建议配合例句记忆。

## 今日单词

| 单词 | 音标 | 词性 | 释义 |
| --- | --- | --- | --- |
| leverage | /ˈlevərɪdʒ/ | v./n. | 利用；杠杆作用 |
| resilient | /rɪˈzɪliənt/ | adj. | 有韧性的，能快速恢复的 |
| streamline | /ˈstriːmlaɪn/ | v. | 使简化、提高效率 |
| consensus | /kənˈsensəs/ | n. | 共识，一致意见 |
| iteration | /ˌɪtəˈreɪʃn/ | n. | 迭代；反复修改 |

## 例句与用法

1. **leverage** — We can leverage existing data to speed up the launch.
   *我们可以利用现有数据加速上线。*
2. **resilient** — A resilient team recovers quickly from setbacks.
   *有韧性的团队能快速从挫折中恢复。*
3. **streamline** — The new workflow streamlines approval from 5 steps to 2.
   *新流程把审批从 5 步简化到 2 步。*

## 记忆建议

- **词根法**：`resilient` = re（回）+ sil（跳）+ ient → 弹回来 → 有韧性；
- **场景法**：把 5 个词各写一句与你工作相关的句子；
- **复习节奏**：今天记，明天用，第 4 天和第 7 天各回顾一次。

需要我把这些单词做成每日 8:00 自动推送的任务吗？在「任务」里选择「每日英文单词」模板即可。"""


def _news(prompt: str) -> str:
    return """以下是今日值得关注的行业要点（演示数据，按重要性排序）。

## 一、今日三条主线

1. **算力供给继续向头部集中**：多家云厂商公布新一代推理集群，单位 token 成本较上季度下降约 18%。
2. **端侧模型加速落地**：手机与 PC 厂商在系统层集成小参数模型，离线摘要、通话转写成为标配能力。
3. **行业应用从试点转向规模采购**：制造、医疗行业的智能体项目开始进入年度预算。

## 二、关键数据

| 指标 | 本期 | 环比 |
| --- | --- | --- |
| 推理调用量 | 128 亿次/日 | +12.4% |
| 平均首字延迟 | 0.72s | -9.1% |
| 企业智能体渗透率 | 27% | +4.6pp |

## 三、对我们的启示

- 优先把**高频、规则明确**的流程自动化（如日报汇总、资料归档）；
- 对准确率要求高的场景保留人工复核环节；
- 关注单位任务成本，而不只是单次调用价格。

> 以上内容为演示环境生成，可在「咨询」模块查看带来源与 AI 摘要的完整资讯流。

需要我把这份早报设为每天 8:30 自动推送到站内消息吗？"""


def _ppt(prompt: str) -> str:
    topic = _title_of(prompt)
    return f"""已按「{topic}」整理了一份演示文稿结构。

## 建议大纲（12 页）

1. 封面：{topic}
2. 背景与目标：为什么现在要做
3. 现状与痛点：三个关键问题
4. 方案总览：一张图讲清整体思路
5. 核心能力一：统一入口
6. 核心能力二：自动化任务
7. 核心能力三：知识与项目空间
8. 实施路径：三个阶段与里程碑
9. 资源与分工
10. 风险与应对
11. 收益测算
12. 下一步行动

## 排版建议

| 页面类型 | 版式 | 要点 |
| --- | --- | --- |
| 封面 | 大标题 + 副标题 | 一屏只讲一件事 |
| 内容页 | 左文右图 | 每页不超过 4 条要点 |
| 数据页 | 大数字 + 注释 | 突出同比/环比 |
| 结束页 | 结论 + 行动项 | 给出明确下一步 |

打开「PPT」模块，选择模板并输入主题即可一键生成可编辑的 .pptx 文件。"""


def _writing(prompt: str) -> str:
    topic = _title_of(prompt)
    return f"""下面是「{topic}」的结构化整理稿，可直接作为周报或方案初稿使用。

## 一、本期结论

- 核心进展：完成主流程搭建，关键路径平均耗时下降 35%；
- 主要问题：跨模块数据口径不统一，导致对比分析返工；
- 下一步：统一指标定义，并把重复性整理交给定时任务。

## 二、进展明细

| 事项 | 状态 | 说明 |
| --- | --- | --- |
| 需求梳理 | 已完成 | 输出 1 份范围说明与验收清单 |
| 主流程开发 | 进行中 | 核心链路已联调通过 |
| 数据看板 | 未开始 | 依赖指标口径确认 |

## 三、风险与应对

1. **口径不一致** → 建立指标字典，变更需评审；
2. **排期紧张** → 先交付 P0，P1 功能并行推进；
3. **依赖外部服务** → 增加降级方案与超时重试。

## 四、下期计划

- 完成数据看板并接入告警；
- 输出 3 个自动化任务，覆盖高频重复工作；
- 复盘一次，形成可复用模板。

如需更正式的版本，我可以调整为邮件体、汇报体或对外通稿体。"""


def _project(prompt: str) -> str:
    return f"""基于当前项目知识库，对「{_title_of(prompt)}」的回答如下。

## 与资料相关的结论

1. 项目资料中与问题直接相关的内容集中在前两章的目标与范围定义；
2. 现有文档对「验收标准」的描述较粗，建议补充可量化的指标；
3. 历史会话里提到的两个风险点，在最新文档中仍未闭环。

## 建议动作

- 补齐验收指标：每条需求对应一条可验证的结果；
- 将重复出现的问答沉淀到项目指令中，减少重复解释；
- 对关键文档标注版本与责任人。

> 以上结论引用自项目知识库文件，具体出处见回答末尾的引用来源。"""


GENERATORS = {
    "general": _general,
    "code": _code,
    "words": _words,
    "news": _news,
    "ppt": _ppt,
    "writing": _writing,
    "project": _project,
}


def build_answer(prompt: str, *, web_search: bool = False, deep_think: bool = False,
                 model: str = "MAuto-Air", attachments: list[str] | None = None,
                 plugin_results: list[dict] | None = None) -> str:
    kind = _pick(prompt)
    content = GENERATORS[kind](prompt)
    if attachments:
        names = "、".join(attachments[:5])
        content = (f"> 已读取 {len(attachments)} 个附件：{names}。以下回答结合了附件内容。\n\n" + content)
    if plugin_results:
        blocks = ["\n## 插件调用结果\n"]
        for r in plugin_results:
            blocks.append(f"**{r['name']}** · 耗时 {r['duration_ms']}ms\n\n{r['output']}\n")
        content += "\n" + "\n".join(blocks)
    if model == "MAuto-Pro":
        content = content.replace("## ", "## ", 2)
    return content


_SENT_RE = re.compile(r"(?<=[。！？!?；;])\s*|\n+")
_WORD_RE = re.compile(r"[\u4e00-\u9fa5]{2,}|[A-Za-z][A-Za-z0-9.+#_-]{1,}")
_STOP = {"什么", "怎么", "如何", "为什", "哪些", "可以", "一个", "这个", "那个", "我们", "你们",
         "他们", "以及", "还有", "就是", "需要", "应该", "现在", "最新", "介绍", "告诉"}


def _keywords(text: str) -> list[str]:
    words = [w.lower() for w in _WORD_RE.findall(text or "")]
    out: list[str] = []
    for w in words:
        if w in _STOP or len(w) < 2:
            continue
        if w not in out:
            out.append(w)
    return out[:12]


def _best_sentences(text: str, keywords: list[str], limit: int = 3,
                    prefer_version: bool = False, prefer_zh: bool = False) -> list[str]:
    sentences = [s.strip() for s in _SENT_RE.split(text or "") if s and len(s.strip()) >= 14]
    scored: list[tuple[float, int, str]] = []
    for idx, sent in enumerate(sentences[:240]):
        if len(sent) > 180:
            continue
        if re.search(r"[│┃|]|http://127\.0\.0\.1|INFO:|限时|广告|扫码|公众号|注册即|入门指南|本文来自|作者[：:]|(电|讯)\s*题[：:]", sent):
            continue
        cjk = len(re.findall(r"[\u4e00-\u9fa5]", sent))
        if prefer_zh and cjk < 8:
            continue
        words = re.findall(r"[\u4e00-\u9fa5]{2,}", sent)
        if words and max(words.count(w) for w in set(words)) >= 3:
            continue  # 导航 / 目录式罗列
        low = sent.lower()
        score = sum(1.0 for kw in keywords if kw in low)
        if re.search(r"\d", sent):
            score += 0.4
        if prefer_version and re.search(r"\d+\.\d+", sent):
            score += 1.2
        if 25 <= len(sent) <= 160:
            score += 0.3
        scored.append((score, -idx, sent))
    scored.sort(reverse=True)
    picked: list[str] = []
    seen: set[str] = set()
    for _, _, sent in scored:
        key = re.sub(r"\s+", "", sent)[:18]
        if key in seen:
            continue
        seen.add(key)
        picked.append(re.sub(r"\s+", " ", sent).strip())
        if len(picked) >= limit:
            break
    return picked


_CMP_RE = re.compile(r"(?:和|与|跟|vs\.?|VS|对比|比较|还是|哪个更好|哪个好)")
_CONFLICT_POS = ("支持", "能够", "提升", "有效", "领先", "增长", "适合", "优势", "推荐", "值得",
                 "将会", "必然", "取代", "成功")
_CONFLICT_NEG = ("不支持", "不能", "无法", "并非", "不建议", "难以", "尚未", "下降", "下滑",
                 "缩减", "风险", "争议", "不足", "限制", "质疑", "不会", "不再", "不必", "淘汰")
_FACT_RE = re.compile(r"\d{4}年|\d{1,2}\s*月|\d+(?:\.\d+)+|\d+(?:\.\d+)?\s*(?:%|％|亿|万|元|美元)")
_TIME_RE = re.compile(r"\d{4}\s*年|\d{1,2}\s*月|\d{1,2}\s*日|今天|昨天|本周|上周|今年|明年")
_INFO_RE = re.compile(r"适合|推荐|优势|劣势|区别|不同|成本|价格|免费|相比|更强|更好|意味着|趋势|"
                      r"认为|建议|风险|机会|取代|效率")
_PRICE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:元|美元|人民币|万|亿)")
_INTENT_HINTS = (
    (("什么时候", "何时", "时间", "日期", "几月"), ("发布", "发售", "上市", "推出", "开售", "月")),
    (("价格", "多少钱", "售价"), ("元", "价格", "售价", "美元", "起售")),
    (("为什么", "原因", "为何"), ("因为", "原因", "导致", "由于")),
    (("如何", "怎么", "怎样"), ("方法", "步骤", "建议", "可以")),
)


def _intent_keywords(query: str) -> list[str]:
    extra: list[str] = []
    for triggers, hints in _INTENT_HINTS:
        if any(t in query for t in triggers):
            extra.extend(hints)
    return extra


def _bigrams(text: str) -> set[str]:
    clean = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", text or "").lower()
    if len(clean) < 4:
        return {clean} if clean else set()
    return {clean[i:i + 2] for i in range(len(clean) - 1)}


def _similarity(a: str, b: str) -> float:
    sa, sb = _bigrams(a), _bigrams(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _refs(srcs) -> str:
    return "".join(f"[{i}]" for i in sorted(set(srcs)))


def _stance(sentence: str) -> str:
    # 「会不会 / 能不能 / 是否」是疑问句，不能当作否定表态
    if re.search(r"会不会|能不能|是否|能否|该不该|要不要|是不是", sentence):
        return "neutral"
    if any(w in sentence for w in _CONFLICT_NEG):
        return "neg"
    if any(w in sentence for w in _CONFLICT_POS):
        return "pos"
    return "neutral"


def _is_noise_sentence(sentence: str) -> bool:
    """过滤网页标题、目录编号、导航式短句等不适合当论点的文本。"""
    if re.match(r"^\s*\d{1,4}\s*[.、)]\s*\S", sentence):
        return True
    if len(re.findall(r"[\u4e00-\u9fa5]\s[\u4e00-\u9fa5]", sentence)) >= 3:
        return True
    if re.search(r"\s(?:vs\.?|VS)\s", sentence):
        return True
    if re.search(r"本文将|本文结合|本文梳理|本文从|一篇看懂|点击这里|image\.png|下载页面|阅读\s*\(|"
                 r"责任编辑|原标题|扫码|关注公众号|广告|注册即|登录后|最常问的问题|到底选哪个|答案其实|"
                 r"面对市场上涌现|始终站在聚光灯下|在深入.{0,10}之前|注意事项|信息时效性|以上内容基于|"
                 r"作者[:：]|来源[:：]\s*\S|编辑[:：]|阅读\s*\(",
                 sentence):
        return True
    return sentence.rstrip().endswith(("：", ":"))


def _claim_pool(query: str, results: list[dict]) -> list[dict]:
    """从各来源提取候选句子，供跨来源聚类比较。"""
    keywords = _keywords(query) + _intent_keywords(query)
    pair = _compare_pair(query)
    if pair:
        # 对比型问题：优先抓评价性、差异性的句子，而不是开篇导语
        keywords += [a.lower() for a in pair]
        keywords += ["适合", "推荐", "优势", "劣势", "区别", "不同", "成本", "价格", "免费",
                     "更强", "更好", "胜在", "缺点"]
    elif _intent_of(query) == "general" and any(w in query for w in ("会", "是否", "能否", "吗")):
        keywords += ["认为", "观点", "趋势", "影响", "机会", "风险", "不会", "能够", "取代"]
    prefer_version = bool(re.search(r"版本|最新|发布|release|version", query, re.I))
    prefer_zh = bool(re.search(r"[\u4e00-\u9fa5]", query))
    pool: list[dict] = []
    for idx, src in enumerate(results, 1):
        text = (src.get("text") or "").strip()
        sents = _best_sentences(text, keywords, 5, prefer_version=prefer_version, prefer_zh=prefer_zh)
        if not sents and src.get("snippet"):
            sents = [s.strip() for s in _SENT_RE.split(src["snippet"]) if len(s.strip()) >= 12][:2]
        for sent in sents:
            sent = re.sub(r"^(?:\d{4}年\d{1,2}月\d{1,2}日|\d+\s*(?:天|小时|分钟)\s*(?:之)?前)"
                          r"\s*[·\-]?\s*", "", sent.strip()).strip()
            sent = re.sub(r"^[#＃][^#＃\s]{1,24}[#＃]\s*", "", sent)
            sent = re.sub(r"^(?:摘要|导语|核心观点|观点)\s*[.。:：,，]\s*", "", sent)
            sent = re.sub(r"^热点解读\s*", "", sent)
            sent = re.sub(r"\.{3,}", "…", sent).strip()
            if prefer_zh and len(re.findall(r"[\u4e00-\u9fa5]", sent)) < 4:
                continue  # 中文问题里过滤纯英文营销文案
            if _is_noise_sentence(sent):
                continue
            pool.append({"src": idx, "sent": sent})
    return pool


def _cluster_claims(pool: list[dict]) -> list[list[dict]]:
    """把不同来源中语义相近的句子归为一组（字符二元组 Jaccard 相似度）。"""
    parent = list(range(len(pool)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(pool)):
        for j in range(i + 1, len(pool)):
            if pool[i]["src"] == pool[j]["src"]:
                continue
            if _similarity(pool[i]["sent"], pool[j]["sent"]) >= 0.32:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
    groups: dict[int, list[dict]] = {}
    for i, item in enumerate(pool):
        groups.setdefault(find(i), []).append(item)
    return list(groups.values())


def _shorten(text: str, limit: int = 110) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    cut = max(text.rfind(p, 0, limit) for p in "。；！？，,;")
    return (text[:cut] if cut >= 40 else text[:limit]).rstrip("。；;，, ") + "…"


def _representative(items: list[dict]) -> str:
    def key(item: dict):
        sent = item["sent"]
        return (1 if _TIME_RE.search(sent) else 0, 1 if re.search(r"\d", sent) else 0,
                -abs(len(sent) - 64))
    return _shorten(max(items, key=key)["sent"])


def _cluster_conflict(items: list[dict]) -> str:
    """检测同一主题下来源之间的方向性/数据性分歧。"""
    by_src: dict[int, str] = {}
    for item in items:
        by_src.setdefault(item["src"], item["sent"])
    stances = {src: _stance(sent) for src, sent in by_src.items()}
    pos = [s for s, v in stances.items() if v == "pos"]
    neg = [s for s, v in stances.items() if v == "neg"]
    if pos and neg:
        return (f"{_refs(pos)} 偏正面（{by_src[pos[0]][:60]}），"
                f"{_refs(neg)} 偏否定或负面（{by_src[neg[0]][:60]}），两者方向相反。")
    facts: dict[int, set[str]] = {}
    for src, sent in by_src.items():
        tokens = {t.replace(" ", "") for t in _FACT_RE.findall(sent)}
        if tokens:
            facts[src] = tokens
    srcs = list(facts)
    for i in range(len(srcs)):
        for j in range(i + 1, len(srcs)):
            a, b = srcs[i], srcs[j]
            fa, fb = facts[a], facts[b]
            if fa != fb and not fa <= fb and not fb <= fa:
                return (f"{_refs([a])} 提到 {'、'.join(sorted(fa)[:2])}，"
                        f"{_refs([b])} 提到 {'、'.join(sorted(fb)[:2])}，具体数字/时间口径不一致。")
    return ""


def _cluster_infos(pool: list[dict]) -> list[dict]:
    infos: list[dict] = []
    for group in _cluster_claims(pool):
        rep = _representative(group)
        infos.append({
            "srcs": sorted({item["src"] for item in group}),
            "rep": rep,
            "conflict": _cluster_conflict(group),
            "has_fact": bool(_FACT_RE.search(rep)),
            "has_info": bool(_INFO_RE.search(rep)),
        })
    infos.sort(key=lambda c: (-len(c["srcs"]), -int(c["has_info"]), -int(c["has_fact"]), -len(c["rep"])))
    return infos


def _excerpt(src: dict) -> str:
    """取来源正文中第一句可读内容，避免表格里出现导航/版权行。"""
    for sent in _SENT_RE.split((src.get("text") or "").strip()):
        sent = re.sub(r"\s+", " ", sent).strip()
        sent = re.sub(r"^\d+\s*(?:天|小时|分钟)\s*(?:之)?前\s*[·\-]?\s*", "", sent)
        if len(sent) >= 20 and not _is_noise_sentence(sent):
            return sent[:52]
    fallback = re.sub(r"\s+", " ", (src.get("text") or "")).strip()[:52]
    return fallback or (src.get("snippet") or "无可用正文")[:52]


def _evidence(src: dict) -> tuple[str, str]:
    text = (src.get("text") or "").strip()
    size = len(text)
    fact = bool(_FACT_RE.search(text))
    if size >= 900:
        return "强", "正文完整" + ("，含可核查的数据/时间" if fact else "，但缺少具体数据")
    if size >= 300:
        return "中", f"正文片段约 {size} 字" + ("，含数据" if fact else "")
    if size:
        return "弱", f"正文仅 {size} 字，信息有限"
    return "弱", "正文未抓取，仅保留标题与摘要"


def _compare_pair(query: str) -> tuple[str, str] | None:
    text = re.sub(r"[？?！!。，,：:]", " ", query)
    text = re.sub(r"^\s*(请|帮我|帮忙|能否|可以)?\s*(对比|比较|分析)\s*", "", text)
    def clean(part: str) -> str:
        part = re.sub(r"^\s*(请问|请|帮我|帮忙|能否|可以)\s*", "", part)
        part = re.split(r"哪个|哪家|谁|怎么样|怎么|如何|更好|好$", part)[0]
        return part.strip()
    parts = [clean(p) for p in _CMP_RE.split(text) if p.strip()]
    parts = [p for p in parts if 2 <= len(p) <= 24]
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def _entity_hit(name: str, text: str) -> bool:
    """实体匹配：允许「GitHub Copilot」被简写为「Copilot」。"""
    low = text.lower()
    target = name.lower()
    if target in low:
        return True
    parts = [p for p in re.split(r"\s+", target) if len(p) >= 3]
    return any(p in low for p in parts)


def _compare_block(pair: tuple[str, str], pool: list[dict], results: list[dict]) -> list[str]:
    a, b = pair

    def claims_of(name: str, other: str) -> tuple[list[int], str]:
        items = [it for it in pool if _entity_hit(name, it["sent"])]
        if not items:
            return [], ""
        items.sort(key=lambda it: (other.lower() in it["sent"].lower(),
                                   0 if len(it["sent"]) >= 40 else 1, -len(it["sent"])))
        return sorted({it["src"] for it in items}), _shorten(items[0]["sent"], 90)

    sa, ca = claims_of(a, b)
    sb, cb = claims_of(b, a)
    if not ca and not cb:
        return []

    def cell(sent: str, srcs: list[int], other_empty: bool = False) -> str:
        if not sent:
            return "未检索到直接表述"
        if other_empty:
            return "同上（来源把两者放在同一段对比中）"
        return f"{sent.replace('|', '/')} {_refs(srcs)}"

    def refs_cell(srcs: list[int]) -> str:
        if not srcs:
            return "—"
        levels = "/".join(_evidence(results[i - 1])[0] for i in srcs)
        return f"{_refs(srcs)}（{levels}）"

    return [
        "### 两个对象的差异",
        "",
        f"| 对比项 | {a} | {b} |",
        "| --- | --- | --- |",
        f"| 提及来源 | {refs_cell(sa)} | {refs_cell(sb)} |",
        f"| 主要说法 | {cell(ca, sa)} | {cell(cb, sb, bool(ca) and ca == cb)} |",
        "",
    ]


def _intent_of(query: str) -> str:
    if any(t in query for t in ("什么时候", "何时", "时间", "日期", "几月", "哪年")):
        return "time"
    if any(t in query for t in ("多少钱", "价格", "售价", "费用")):
        return "price"
    if _compare_pair(query):
        return "compare"
    return "general"


def _coverage(query: str, intent: str, pool: list[dict], pair: tuple[str, str] | None) -> tuple[bool, str]:
    """判断检索内容是否真的覆盖了问题意图，避免用相关页面硬凑答案。"""
    sents = [it["sent"] for it in pool]
    if intent == "time":
        launch = ("发布", "发售", "上市", "推出", "开售", "亮相")
        if any(w in query for w in launch):
            ok = any(_TIME_RE.search(s) and any(w in s for w in launch) for s in sents)
        else:
            ok = any(_TIME_RE.search(s) for s in sents)
        return ok, "没有出现可回答的时间信息"
    if intent == "price":
        return any(_PRICE_RE.search(s) for s in sents), "没有出现具体的价格信息"
    if intent == "compare" and pair:
        a, b = pair
        has_a = any(_entity_hit(a, s) for s in sents)
        has_b = any(_entity_hit(b, s) for s in sents)
        if has_a and has_b:
            return True, ""
        missing = b if has_a else a if has_b else f"{a} 和 {b}"
        return False, f"只覆盖了对比对象中的一方，缺少「{missing}」的资料"
    return True, ""


def _trigrams(text: str) -> set[str]:
    clean = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", text or "").lower()
    return {clean[i:i + 3] for i in range(len(clean) - 2)}


def _aspect_of(sentence: str) -> str:
    low = sentence.lower()
    for name, cues in _ASPECTS:
        if any(c in low for c in cues):
            return name
    return ""


def _stance_conflicts(query: str, pool: list[dict]) -> list[str]:
    """跨来源观点冲突：同一话题下出现方向相反的表态。"""
    qkw = [k for k in _keywords(query) if len(k) >= 2]
    if not qkw:
        return []
    pos, neg = [], []
    for it in pool:
        low = it["sent"].lower()
        if not any(k in low for k in qkw):
            continue
        stance = _stance(it["sent"])
        if stance == "pos":
            pos.append(it)
        elif stance == "neg":
            neg.append(it)
    out: list[str] = []
    seen: set[tuple[int, int]] = set()
    for p in pos[:4]:
        for n in neg[:4]:
            if p["src"] == n["src"]:
                continue
            if not (_trigrams(p["sent"]) & _trigrams(n["sent"])):
                continue  # 必须在同一话题上，才算观点冲突
            if (_similarity(p["sent"], n["sent"]) < 0.3
                    and _aspect_of(p["sent"]) != _aspect_of(n["sent"])):
                continue  # 同一话题但讨论的不是同一方面（如“价格”对“演进路径”）
            key = tuple(sorted((p["src"], n["src"])))
            if key in seen:
                continue
            seen.add(key)
            out.append(f"{_refs([p['src']])} 认为「{_shorten(p['sent'], 70)}」，"
                       f"{_refs([n['src']])} 则认为「{_shorten(n['sent'], 70)}」，观点方向相反。")
            if len(out) >= 3:
                return out
    return out


_ASPECTS = (
    ("价格与成本", ("价格", "费用", "订阅", "收费", "免费", "成本", "性价比")),
    ("功能与能力", ("功能", "补全", "重构", "生成", "上下文", "agent", "插件", "能力")),
    ("适用场景", ("适合", "场景", "团队", "新手", "复杂项目", "大型项目")),
    ("效率与体验", ("效率", "速度", "体验", "流畅", "响应")),
    ("风险与不确定性", ("风险", "安全", "隐私", "错误", "幻觉", "取代", "失业")),
)


def _aspect_groups(pool: list[dict]) -> list[dict]:
    """跨来源共同关注的维度：不同措辞但讨论同一方面，用于补充对比视角。"""
    groups: list[dict] = []
    for name, cues in _ASPECTS:
        hits = [it for it in pool if any(c in it["sent"].lower() for c in cues)]
        srcs = sorted({it["src"] for it in hits})
        if len(srcs) < 2:
            continue
        groups.append({"name": name, "srcs": srcs, "sent": _shorten(_representative(hits), 90)})
    return groups


def build_web_answer(query: str, retrieval: dict) -> str:
    """无模型 Key 时：对真实检索结果做跨来源综合，先结论、再对比、最后观点。

    只做归纳与分歧识别，不补充检索资料之外的事实；证据不足时明确说明。
    """
    results = retrieval.get("results") or []
    fetched = [r for r in results if (r.get("text") or "").strip()]
    snippets = [r for r in results if (r.get("snippet") or "").strip()]
    if not results:
        reason = retrieval.get("error") or "未检索到任何网页结果"
        return (f"## 联网检索未完成\n\n"
                f"我尝试联网检索「{query[:60]}」，但没有拿到可用的真实网页：{reason}。\n\n"
                "**结论**：本次无法回答，也不提供推测性内容，避免编造。\n\n"
                "## 可以怎么处理\n\n"
                "- 换一种更具体的问法（加入产品名、版本号、时间范围）；\n"
                "- 稍后重试，或检查本机网络与代理设置；\n"
                "- 关闭「联网搜索」开关，改用模型已有知识作答。")
    if not fetched and not snippets:
        reason = retrieval.get("error") or "网页正文无法读取"
        return (f"## 联网检索结果不可用\n\n"
                f"检索到了 {len(results)} 条结果，但{reason}，无法据此作答。\n\n"
                "**结论**：没有足够正文支撑，这里不给出推测性结论，也不编造数据。\n\n"
                "## 建议\n\n"
                "- 直接打开下方来源核对原文；\n"
                "- 换一个更易抓取的查询（例如换成新闻页而非脚本渲染页面）。")

    pool = _claim_pool(query, results)
    infos = _cluster_infos(pool) if pool else []
    intent = _intent_of(query)
    pair = _compare_pair(query)
    covered, gap = _coverage(query, intent, pool, pair)
    if infos:
        if intent == "time":
            launch = ("发布", "发售", "上市", "推出", "开售", "亮相")
            infos.sort(key=lambda c: (
                0 if _TIME_RE.search(c["rep"]) and any(w in c["rep"] for w in launch) else 1,
                -len(c["srcs"]), -int(c["has_fact"])))
        elif intent == "price":
            infos.sort(key=lambda c: (0 if _PRICE_RE.search(c["rep"]) else 1,
                                      -len(c["srcs"]), -int(c["has_fact"])))
    agreements = [c for c in infos if len(c["srcs"]) >= 2 and not c["conflict"]]
    conflicts = [c["conflict"] for c in infos if c["conflict"]]
    conflicts += [x for x in _stance_conflicts(query, pool) if x not in conflicts]
    singles = [c for c in infos if len(c["srcs"]) == 1]

    lines: list[str] = [
        f"已检索 {len(results)} 条网页，成功读取 {len(fetched)} 篇正文"
        f"（检索时间 {retrieval.get('fetched_at', '')}）。"
        + ("全部来源仅返回标题与摘要，以下综合基于摘要，证据强度偏弱。"
           if not fetched else "以下为跨来源综合，不是逐条摘录。"),
        "",
    ]

    # ---------------- 结论
    if not covered:
        lead = (f"检索到的 {len(results)} 条网页没有直接回答「{_shorten(query, 40)}」"
                f"：{gap}。下面只客观呈现来源内容，不硬下结论。")
    elif agreements:
        top = agreements[0]
        lead = f"{top['rep']} {_refs(top['srcs'])}，这一点在 {len(top['srcs'])} 个来源中重复出现。"
        if conflicts:
            lead += f"但另有 {len(conflicts)} 处来源说法不一致，见下方分歧点。"
    elif singles:
        top = singles[0]
        lead = f"{top['rep']} {_refs(top['srcs'])}（目前仅单一来源支撑，可信度有限）。"
    else:
        lead = "检索到的正文与问题的关联较弱，暂时无法给出可靠结论。"
    lines += [f"> **结论**：{lead}", "", "## 结论要点", ""]
    if not covered:
        lines += ["> 说明：以下要点是来源实际在讲的内容，并非对问题的回答。", ""]

    bullets: list[str] = []
    for conf in agreements[:3]:
        bullets.append(f"- {conf['rep']} {_refs(conf['srcs'])}"
                       f"（{len(conf['srcs'])} 个来源相互印证）")
    for single in singles[:max(0, 3 - len(bullets))]:
        bullets.append(f"- {single['rep']} {_refs(single['srcs'])}（孤证，需二次核实）")
    if not bullets:
        bullets.append("- 各来源没有形成可归纳的共同表述。")
    lines += bullets

    # ---------------- 跨来源对比
    lines += ["", "## 跨来源对比", "", "### 一致点", ""]
    if not covered and agreements:
        lines.append("> 注意：这些一致点只是网页之间的重复表述，并未回答原问题。")
    if agreements:
        for conf in agreements[:4]:
            lines.append(f"- {conf['rep']} {_refs(conf['srcs'])}")
    else:
        lines.append("- 各来源切入角度不同，未发现可交叉印证的共同表述。")

    aspects = _aspect_groups(pool)
    if aspects:
        lines += ["", "### 各来源共同关注的方面", ""]
        for group in aspects[:4]:
            lines.append(f"- **{group['name']}**：{_refs(group['srcs'])} 都谈到——{group['sent']}")

    lines += ["", "### 分歧点与不确定性", ""]
    if conflicts:
        for conflict in conflicts[:4]:
            lines.append(f"- {conflict}")
    else:
        lines.append("- 未发现直接冲突的说法；但这只说明来源之间没有互相矛盾，"
                     "不代表结论已被独立验证。")
    if pair:
        lines += [""] + _compare_block(pair, pool, results)

    lines += ["", "### 证据强弱", "", "| 来源 | 关键信息 | 证据强度 | 判断依据 |",
              "| --- | --- | --- | --- |"]
    for idx, src in enumerate(results, 1):
        level, why = _evidence(src)
        title = (src.get("title") or src.get("url") or "")[:36].replace("|", "/")
        site = (src.get("site") or "").replace("|", "/")
        key = _excerpt(src)
        key = key.replace("|", "/").replace("\n", " ")
        lines.append(f"| [{idx}] {title} | {site}：{key} | {level} | {why} |")

    # ---------------- 综合总结与观点
    lines += ["", "## 综合总结与建议观点", ""]
    if not covered:
        lines.append(f"**综合判断**：现有资料{gap}，无法回答「{_shorten(query, 40)}」；"
                     "把不相关的页面拼成结论会误导判断。")
    elif agreements:
        merged = "；".join(f"{c['rep']} {_refs(c['srcs'])}" for c in agreements[:2])
        lines.append(f"**综合判断**：{merged}。这些说法互相补充，构成目前证据下较稳的部分。")
    elif singles:
        top = singles[0]
        lines.append(f"**综合判断**：目前只有 {_refs(top['srcs'])} 给出了相对直接的说法"
                     f"（{top['rep']}），其余来源没有可交叉印证的内容，"
                     "所以这是「当前最可信的一条」，而不是已被多源验证的定论。")
    else:
        lines.append("**综合判断**：没有可归纳的有效信息，本次不作判断。")

    strong = [i for i, src in enumerate(results, 1) if _evidence(src)[0] == "强"]
    if not covered:
        if intent == "compare" and pair:
            lines.append(f"**我的观点**：缺少对比对象资料时，任何「哪个更好」的判断都是臆测。"
                         f"建议分别查 {pair[0]} 与 {pair[1]} 的官方文档或独立评测，再按同一维度对比。")
        elif intent == "time":
            lines.append(f"**我的观点**：这次不拿不相关的页面凑答案。"
                         f"建议改用「{_shorten(query, 18)} 发布时间 官方」这类更具体的检索词。")
        else:
            lines.append("**我的观点**：证据不足就不下判断，建议换更具体的检索词或补充一手资料。")
    elif conflicts:
        if strong:
            lines.append(f"**我的观点**：在分歧点上，目前正文最完整的是 {_refs(strong)}，"
                         "可优先参考；但没有一手来源前，我不会把它当作最终结论。")
        else:
            lines.append("**我的观点**：分歧双方都没有提供完整正文或可核查数据，"
                         "我倾向于暂不下结论，先按分歧点逐条核对原始出处。")
    elif agreements:
        lines.append(f"**我的观点**：倾向采信结论要点中的共同说法——它在多个独立来源中重复出现；"
                     f"不过来源集中在 {'、'.join(sorted({r.get('site') or '未知站点' for r in fetched})[:3])}，"
                     "属于二手信息，涉及关键决策时仍应回到一手文档核对。")
    else:
        top = singles[0] if singles else None
        if top:
            extra = (f"；其余 {len(singles) - 1} 条说法无法与之交叉验证" if len(singles) > 1
                     else "；目前缺少其他来源交叉验证")
            lines.append(f"**我的观点**：如果现在必须给答案，我会把 {_refs(top['srcs'])} 的说法作为当前答案"
                         f"（{top['rep']}）{extra}，正式引用前建议核对一手来源。")
        else:
            lines.append("**我的观点**：证据不足以支撑明确观点，建议先补充更具体或更权威的来源。")

    caveats: list[str] = []
    if not covered:
        caveats.append(f"检索结果未覆盖问题意图（{gap}）")
    snippet_only = [i for i, src in enumerate(results, 1) if not (src.get("text") or "").strip()]
    if snippet_only:
        caveats.append(f"{_refs(snippet_only)} 仅拿到标题/摘要，未纳入交叉印证")
    if singles:
        caveats.append(f"{len(singles)} 条说法为单一来源，存在孤证风险")
    if not caveats:
        caveats.append("来源均为公开网页，未提供一手数据或完整口径说明")
    lines.append(f"**不确定性**：{'；'.join(caveats)}。")
    if not covered:
        lines.append("**建议**：换用更具体的检索词（产品名 + 时间/价格/对比维度）后重试，"
                     "或直接打开下方来源核对原文。")
    else:
        lines.append(f"**建议**：拿分歧点或关键数字去核对原文页面；若问题涉及价格、版本、政策等易变信息，"
                     "以最新一手公告为准。")

    # ---------------- 来源
    lines += ["", "## 参考来源", ""]
    for idx, src in enumerate(results, 1):
        title = src.get("title") or src.get("url", "")
        lines.append(f"{idx}. [{title}]({src.get('url', '')}) — {src.get('site') or ''}")
    return "\n".join(lines)


def build_web_answer_remote(query: str, retrieval: dict) -> str:
    """有真实模型 Key 时，把检索正文作为上下文交给模型，按统一结构生成回答。"""
    if not API_KEY:
        return build_web_answer(query, retrieval)
    results = retrieval.get("results") or []
    if not results:
        return build_web_answer(query, retrieval)
    blocks = []
    for idx, src in enumerate(results, 1):
        body = (src.get("text") or "").strip()
        if not body:
            body = f"（正文未能抓取，仅有搜索摘要：{src.get('snippet') or '无'}）"
        blocks.append(f"[{idx}] {src.get('title')} ({src.get('url')}) — {src.get('site') or ''}\n{body[:1200]}")
    prompt = (
        f"你是严谨的检索问答助手。请只依据下面编号的检索资料，回答用户问题：{query}\n\n"
        "必须按以下结构输出中文 Markdown，不要逐条摘抄原文，要归纳、比较并给出判断：\n"
        "1. 开头一行 `> **结论**：`，直接回答问题，句末标注依据编号；\n"
        "2. `## 结论要点`：2-4 条，每条是可独立成立的结论，句末标注 [编号]；\n"
        "3. `## 跨来源对比`，包含三个子节：\n"
        "   - `### 一致点`：多个来源共同支持的说法，标注各自编号；\n"
        "   - `### 分歧点与不确定性`：来源之间的矛盾、口径差异或证据不足，必须具体说明哪个来源说什么；\n"
        "   - `### 证据强弱`：Markdown 表格，列出每个来源的关键信息、证据强度（强/中/弱）与判断依据；\n"
        "4. `## 综合总结与建议观点`：给出你的综合判断、明确观点，以及适用边界或需要核实之处。\n\n"
        "规则：每个事实性表述后必须标注 [编号]，编号只能来自资料编号 1 到 N；"
        "不同来源矛盾时并列呈现并说明证据强弱，不得强行下结论；资料不足时写\"资料不足以判断\"；"
        "不得编造资料中没有的事实、数据或来源；不要输出\"参考来源\"列表（系统会自动附加）。\n\n"
        + "\n\n".join(blocks)
    )
    try:
        text = _remote(prompt)
    except Exception:  # noqa: BLE001 - 远程失败时回退到本地综合
        return build_web_answer(query, retrieval)
    refs = "\n\n## 参考来源\n" + "\n".join(
        f"{i}. [{s.get('title')}]({s.get('url')}) — {s.get('site')}" for i, s in enumerate(results, 1))
    return text.strip() + refs


def build_reasoning(prompt: str) -> str:
    return (f"先判断问题类型：这是一次关于「{_title_of(prompt)}」的开放式提问。\n"
            "1. 拆解目标：用户希望得到可执行的结论，而不是概念罗列；\n"
            "2. 选择结构：先给结论，再给依据，最后给下一步动作；\n"
            "3. 检查风险：避免绝对化表述，对不确定内容标注前提；\n"
            "4. 组织表达：用标题分层，关键数据用表格呈现。")


CITATION_POOL = [
    ("生成式 AI 应用落地观察：从试点到规模化", "36氪", "https://36kr.com"),
    ("2026 年企业智能化白皮书：效率提升的关键路径", "艾瑞咨询", "https://www.iresearch.com.cn"),
    ("大模型推理成本持续下降，应用层迎来窗口期", "机器之心", "https://www.jiqizhixin.com"),
    ("AI 智能体在办公协同场景的实践与反思", "InfoQ", "https://www.infoq.cn"),
    ("从工具到工作台：对话式产品的下一站", "少数派", "https://sspai.com"),
]


def build_citations(prompt: str, count: int = 3) -> list[dict]:
    rnd = random.Random(hash(prompt) & 0xFFFF)
    pool = CITATION_POOL[:]
    rnd.shuffle(pool)
    out = []
    for i, (title, site, url) in enumerate(pool[:count]):
        out.append({
            "title": title,
            "site": site,
            "time": f"2026-11-{15 - i:02d}",
            "url": url,
            "snippet": f"与「{_title_of(prompt)}」相关的行业观察与数据，供参考。",
        })
    return out


def simulate_plugin(plugin: dict, params: dict) -> str:
    """插件沙箱/调用模拟：根据插件类型返回结构化结果，避免真实外网请求（SSRF 风险）。"""
    name = plugin.get("name", "插件")
    cat = plugin.get("category", "")
    if "天气" in name:
        return "北京 · 晴转多云 · 12~20℃ · 空气质量良 · 东南风 2 级（演示数据）"
    if "翻译" in name:
        return f"译文（{params.get('target', '中文')}）：{params.get('text', '示例文本')} —— 已按术语库校准。"
    if "搜索" in name or cat == "内容创作":
        return ("检索到 3 条高相关内容：\n"
                "1. 行业年度报告摘要（可信度 高）\n"
                "2. 头部厂商季度数据（可信度 中）\n"
                "3. 相关政策解读（可信度 高）")
    if cat == "数据分析":
        return ("| 维度 | 本期 | 上期 | 变化 |\n| --- | --- | --- | --- |\n"
                "| 访问量 | 12.4 万 | 10.1 万 | +22.8% |\n"
                "| 转化率 | 3.8% | 3.1% | +0.7pp |")
    return f"{name} 执行成功：已处理输入参数 {json.dumps(params, ensure_ascii=False)[:120]}"


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / 1.6))


def stream_text(text: str, *, delay: float = 0.012, chunk: int = 4) -> Iterator[str]:
    for i in range(0, len(text), chunk):
        yield text[i:i + chunk]
        time.sleep(delay)


def generate_once(prompt: str, **kwargs) -> str:
    """非流式生成（任务执行、PPT 大纲等场景）。"""
    if API_KEY:
        try:
            return _remote(prompt, **kwargs)
        except Exception:  # noqa: BLE001 - 降级到内置引擎
            pass
    return build_answer(prompt, **kwargs)


def _remote(prompt: str, model: str = "MAuto-Air", **_: object) -> str:
    body = json.dumps({
        "model": os.environ.get("MAUTO_LLM_MODEL", "gpt-4o-mini"),
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"{BASE_URL.rstrip('/')}/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"]


def build_ppt_outline(topic: str, pages: int = 10, extra: str = "") -> list[dict]:
    topic = topic.strip() or "未命名主题"
    middle = [
        ("agenda", "目录", ["年度目标回顾", "关键增长数据", "能力建设进展", "问题与风险", "下一步行动"], {}),
        ("bullets", "背景与目标", ["行业环境变化带来新的效率要求", "本次汇报聚焦的三个问题", "期望达成的量化目标"], {}),
        ("chart", "现状：效率损耗分布", [], {
            "chart": {"categories": ["检索资料", "重复问答", "手工整理", "跨团队对齐"], "unit": "%",
                      "note": "以每周实际工时统计",
                      "series": [{"name": "工时占比", "values": [28, 34, 22, 16]}]},
        }),
        ("metrics", "收益测算", [], {
            "metrics": [
                {"label": "人均每日节省", "value": "40", "unit": "分钟", "note": "重复整理与检索"},
                {"label": "响应提速", "value": "70", "unit": "%", "note": "重复问题自动应答"},
                {"label": "资料复用", "value": "2", "unit": "倍", "note": "项目知识库持续沉淀"},
                {"label": "交付周期", "value": "12", "unit": "天", "note": "较改造前缩短 40%"},
            ],
        }),
        ("section", "方案：从对话到自动化", ["统一入口 · 自动执行 · 知识沉淀"], {"kicker": "PART 01"}),
        ("bullets", "方案总览", ["统一入口：对话即工作台", "自动化：定时任务替代重复提问", "知识化：项目空间沉淀资料"], {}),
        ("compare", "传统方式 vs 智能工作台", [], {
            "compare": {"left_title": "传统方式", "right_title": "智能工作台",
                        "left": ["信息分散在多个工具", "重复提问与手工整理", "经验留在个人手里"],
                        "right": ["对话即统一入口", "定时任务自动执行", "知识在项目内沉淀"]},
        }),
        ("bullets", "核心能力", ["智能对话：多轮上下文理解，联网检索带引用", "自动化任务：按天/周/月执行，结果可追溯",
                                "知识与项目：私有资料问答，成员权限分级"], {}),
        ("quote", "把重复劳动交给系统，把判断留给人。", [], {
            "quote": {"text": "把重复劳动交给系统，把判断留给人。", "author": "MAuto 产品团队"},
        }),
        ("bullets", "实施路径", ["阶段一：跑通主流程（2 周）", "阶段二：扩展能力（4 周）", "阶段三：优化与推广（2 周）"], {}),
        ("bullets", "风险与应对", ["内容质量风险：人工抽检机制", "数据安全风险：权限与审计", "推广风险：试点先行"], {}),
        ("bullets", "下一步行动", ["确认范围与验收标准", "锁定试点团队", "明确里程碑与责任人"], {}),
    ]
    items = [{
        "title": topic,
        "subtitle": "现状、方案与实施路径",
        "bullets": [],
        "notes": "开场：一句话说明汇报目的与结论。",
        "layout": "cover",
    }]
    count = max(3, pages - 2)
    for i in range(count):
        layout, title, bullets, extra_data = middle[i % len(middle)]
        if i >= len(middle):
            title = f"{title}（续）"
        if layout == "agenda":
            bullets = ["01 背景与目标", "02 现状数据", "03 收益测算", "04 方案与路径", "05 下一步行动"]
        slide = {
            "title": title,
            "subtitle": "",
            "bullets": list(bullets),
            "notes": f"讲解要点：围绕「{title}」展开，控制在 90 秒内。",
            "layout": layout,
        }
        slide.update(extra_data)
        if i == 0 and extra:
            slide["notes"] = f"讲解要点：围绕「{title}」展开。补充资料：{extra.strip()[:80]}"
        items.append(slide)
    items.append({
        "title": "谢谢聆听",
        "subtitle": "让每一次汇报都有结论",
        "bullets": [],
        "notes": "收尾：重复核心结论，给出下一步行动。",
        "layout": "end",
    })
    return items


def news_ai_summary(news_item: dict) -> dict:
    """基于真实正文摘录生成摘要与要点，不做推测性补全。"""
    title = (news_item.get("title") or "").strip()
    summary = (news_item.get("summary") or "").strip()
    content = (news_item.get("content") or "").strip()
    text = f"{summary}\n{content}" if content else summary
    if not text.strip():
        return {"summary": "该资讯暂无可读正文，请打开原文查看。", "points": [],
                "model": "MAuto-Air·摘录"}
    sentences = [s.strip() for s in _SENT_RE.split(text) if len(s.strip()) >= 12]
    lead: list[str] = []
    for sent in sentences:
        if len(sent) > 220 or re.search(r"│|\||扫码|公众号|广告|下载客户端|本文来自|作者[：:]|(电|讯)\s*题[：:]", sent):
            continue
        key = re.sub(r"\s+", "", sent)[:18]
        if any(key == re.sub(r"\s+", "", x)[:18] for x in lead):
            continue
        lead.append(re.sub(r"\s+", " ", sent))
        if len(lead) >= 2:
            break
    abstract = " ".join(lead)[:240] or title
    if title and title in abstract:
        abstract = re.sub(r"^[，。、；：\s]+", "", abstract.replace(title, "")).strip()
    abstract = abstract[:240] or title
    lead_keys = {re.sub(r"\s+", "", x)[:18] for x in lead}
    picked = _best_sentences(text, _keywords(title), limit=6, prefer_zh=True)
    points: list[str] = []
    for sent in picked:
        if re.sub(r"\s+", "", sent)[:18] in lead_keys:
            continue
        points.append(sent[:160])
        if len(points) >= 4:
            break
    if not points:
        points = [s[:160] for s in lead[1:]]
    return {"summary": abstract, "points": points, "model": "MAuto-Air·摘录"}
