"""初始化种子数据：演示账号、资讯、插件市场、PPT 模板。"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

from . import auth, db, llm
from .ppt_design import DESIGNS

INDUSTRIES = [
    ("ind_electronics", "电子", "🔌"),
    ("ind_chip", "芯片", "💠"),
    ("ind_ai", "AI", "🤖"),
    ("ind_medical", "医疗", "🩺"),
    ("ind_edu", "教育", "🎓"),
    ("ind_energy", "能源", "⚡"),
    ("ind_auto", "汽车", "🚗"),
    ("ind_finance", "金融", "💹"),
    ("ind_manufacture", "制造", "🏭"),
    ("ind_consumer", "消费", "🛍️"),
]

SOURCES = ["36氪", "机器之心", "财联社", "界面新闻", "第一财经", "InfoQ", "虎嗅", "钛媒体"]

HEADLINES = {
    "ind_electronics": [
        ("折叠屏渗透率突破 12%，供应链进入新一轮扩产周期", "Display Supply Chain 最新报告显示，折叠屏面板出货量同比增长 41%，铰链与 UTG 玻璃环节受益明显，国内两家模组厂已启动扩产。"),
        ("端侧 AI 带动存储升级，LPDDR6 样品开始送测", "多家终端厂商确认下一代旗舰将采用 LPDDR6，带宽提升约 30%，同时带动封装与测试设备需求。"),
        ("智能眼镜出货同比翻倍，光学模组成为瓶颈", "轻量化 AR 眼镜进入消费级价格带，光波导良率仍是制约产能的关键因素。"),
        ("国产 CMOS 传感器进入车规前装量产", "800 万像素车规传感器通过 AEC-Q100 认证，已获得两家主机厂定点。"),
        ("PCB 厂商加速布局高阶 HDI，AI 服务器需求外溢", "高阶 HDI 产能利用率维持高位，部分订单交期延长至 12 周。"),
        ("可穿戴设备健康监测精度提升，医疗认证成新门槛", "心率、血氧之外，血压趋势监测成为新品竞争点，注册审批周期普遍在 9 个月以上。"),
        ("面板价格连续三个月回升，行业库存回到健康水平", "TV 面板报价环比上涨 3.2%，厂商稼动率提升至 85%。"),
        ("Mini LED 背光成本下降，中端机型开始普及", "灯板与驱动 IC 成本较去年下降 22%，推动 Mini LED 向 3000 元价位段渗透。"),
    ],
    "ind_chip": [
        ("先进封装产能持续紧张，CoWoS 交期延长至 40 周", "受 AI 加速卡需求拉动，先进封装成为新瓶颈，封测厂追加资本开支。"),
        ("国产 GPU 完成新一代架构流片，主打推理场景", "新架构在 INT8 推理能效上提升 2.1 倍，配套软件栈同步开源。"),
        ("存储价格上涨周期延续，合约价环比再涨 8%", "HBM 与 DDR5 供给偏紧，模组厂开始与客户重签长协。"),
        ("12 英寸成熟制程稼动率回升，价格战趋于缓和", "消费电子补库存带动 55/40nm 需求回暖，代工厂报价止跌。"),
        ("EDA 国产化率提升，仿真验证环节突破明显", "三家国产 EDA 厂商在时序分析与形式验证上通过头部客户验证。"),
        ("车规 MCU 缺货缓解，交付周期缩短至 16 周", "此前紧缺的 32 位车规 MCU 供给改善，价格回落约 15%。"),
        ("Chiplet 标准落地，接口 IP 需求快速增长", "UCIe 生态扩大，接口 IP 授权收入同比增长 60%。"),
        ("第三代半导体进入价格竞争阶段", "SiC 衬底价格一年下降 35%，器件厂毛利承压。"),
    ],
    "ind_ai": [
        ("国产大模型推理成本再降 18%，应用层迎来窗口期", "多家云厂商公布新一代推理集群，单位 token 成本持续下探，长文本与多模态场景加速落地。"),
        ("智能体从演示走向生产，企业开始按效果付费", "制造、金融行业的智能体项目进入年度预算，验收标准从「能演示」转为「可量化」。"),
        ("多模态模型支持小时级视频理解，内容审核率先落地", "新模型在长视频检索与风险识别上表现提升，审核环节人效提升约 3 倍。"),
        ("开源模型生态扩容，微调门槛进一步降低", "主流开源模型迭代到新版本，单卡即可完成领域微调。"),
        ("AI 编程助手渗透率过半，代码评审成为新战场", "调研显示 54% 的开发者日常使用 AI 编程工具，评审与测试环节成为下一阶段重点。"),
        ("端侧小模型进入系统层，离线摘要成标配", "手机与 PC 厂商在系统层集成本地模型，兼顾隐私与响应速度。"),
        ("模型路由成为降本关键，混合调用比例上升", "头部应用通过路由层把简单请求交给小模型，整体成本下降约 40%。"),
        ("AI 内容标识规范落地，平台加强溯源能力", "生成内容需携带隐式水印，平台侧开始提供来源查询接口。"),
    ],
    "ind_medical": [
        ("医疗大模型进入临床辅助试点，影像与病理先行", "多家三甲医院开展辅助诊断试点，重点评估漏诊率与医生采纳率。"),
        ("创新医疗器械审批提速，AI 辅助软件获批数量创新高", "年内已有 28 款 AI 辅助诊断软件获批三类证。"),
        ("基层医疗信息化升级，慢病管理成为抓手", "县域医共体建设推动慢病随访系统采购，接口标准化需求上升。"),
        ("手术机器人装机量增长，耗材与服务收入占比提升", "腔镜手术机器人装机同比增长 47%，培训体系成为竞争点。"),
        ("互联网医院处方流转打通，医保在线结算扩围", "多地实现线上复诊与药品配送闭环。"),
        ("生物标志物检测灵敏度提升，早筛场景扩展", "多癌种早筛产品进入万人级队列验证。"),
        ("医疗数据要素流通试点启动，脱敏与确权是前提", "试点区域建立数据授权运营机制。"),
        ("可穿戴心电监测获认可，院外管理闭环形成", "单导联心电设备进入家庭健康管理服务包。"),
    ],
    "ind_edu": [
        ("AI 助教进入课堂常态化应用，教师工作量下降明显", "试点学校反馈备课与批改时间平均缩短 35%。"),
        ("职业教育与产业需求对接，微专业受追捧", "短期技能课程报名量同比增长 62%。"),
        ("教育硬件竞争转向内容与服务", "学习机的差异化从硬件参数转向课程体系与学情分析。"),
        ("个性化学习路径落地，学情数据成为核心资产", "区域教育平台开始沉淀过程性数据。"),
        ("高校推进 AI 通识课，师资培训同步展开", "多所高校将 AI 素养纳入本科必修或选修模块。"),
        ("在线教育回归理性，合规与效果成关键", "家长更关注可验证的学习效果与退费机制。"),
        ("语言学习产品接入口语陪练，发音评测精度提升", "端到端评测模型错误率下降 40%。"),
        ("教育出海加速，东南亚市场成为重点", "本地化课程与支付渠道是落地难点。"),
    ],
    "ind_energy": [
        ("储能招标规模创新高，价格竞争转向质量竞争", "新一轮集采对循环寿命与安全指标提出更高要求。"),
        ("光伏组件价格企稳，行业开工率回升", "组件报价环比持平，头部厂商稼动率提升至 70%。"),
        ("虚拟电厂试点扩围，需求侧响应机制逐步完善", "多地明确容量补偿标准。"),
        ("海上风电深远海项目推进，漂浮式技术受关注", "首个商业化漂浮式项目完成并网测试。"),
        ("氢能示范城市群推进，绿氢成本仍是关键", "电解槽价格下降带动项目经济性改善。"),
        ("电力现货市场覆盖扩大，交易策略需求上升", "售电公司与工商业用户开始采购策略服务。"),
        ("充电基础设施下沉，县域覆盖率提升", "县域公共充电桩数量同比增长 58%。"),
        ("核电项目建设提速，设备国产化率提高", "主泵与控制系统国产化取得进展。"),
    ],
    "ind_auto": [
        ("城市 NOA 加速开城，数据闭环决定迭代速度", "多家车企将城市辅助驾驶下放到 20 万元价位段。"),
        ("固态电池装车验证推进，量产仍需时间", "半固态方案先行，全固态预计 2027 年后规模化。"),
        ("整车出口保持增长，本地化生产成新选择", "东南亚与欧洲建厂计划陆续落地。"),
        ("智能座舱进入大模型时代，语音交互体验升级", "端云协同方案成为主流。"),
        ("补能网络竞争加剧，超充桩占比提升", "800V 平台车型推动超充需求。"),
        ("商用车电动化提速，换电模式受青睐", "干线物流换电站利用率提升。"),
        ("汽车芯片国产替代加速，域控制器集成度提高", "舱驾一体方案开始量产。"),
        ("二手车流通效率提升，检测标准化推进", "第三方检测报告覆盖率上升。"),
    ],
    "ind_finance": [
        ("银行智能风控升级，大模型用于反欺诈文本分析", "多家银行在贷后管理环节引入大模型辅助。"),
        ("券商投研接入 AI 助手，研报生产效率提升", "信息抽取与图表生成成为高频功能。"),
        ("保险理赔自动化率提升，车险小额案件最快分钟级", "图像定损准确率提升至 92%。"),
        ("支付机构加强商户风险管理，实时拦截能力增强", "风险交易识别时延下降至 200ms。"),
        ("财富管理转向买方投顾，服务分层更清晰", "投顾组合与陪伴服务成为差异化点。"),
        ("跨境结算便利化推进，企业资金效率提升", "本外币一体化资金池试点扩围。"),
        ("数据资产入表实践推进，估值方法待统一", "首批企业完成数据资源入表。"),
        ("金融大模型合规要求细化，可解释性受重视", "模型输出需保留决策依据。"),
    ],
    "ind_manufacture": [
        ("智能工厂改造进入深水区，数据治理成前提", "设备联网率提升后，数据质量问题凸显。"),
        ("工业机器人密度提升，协作机器人增速领先", "中小制造企业成为新增需求主力。"),
        ("预测性维护落地案例增多，停机时间下降 30%", "振动与温度多模态融合方案效果显著。"),
        ("柔性产线需求上升，小批量多品种成常态", "快速换型能力成为产线竞争力。"),
        ("供应链协同平台普及，交期可视化改善", "核心企业带动上下游接入。"),
        ("工业软件国产化推进，MES 替换需求增长", "本土厂商在流程行业取得突破。"),
        ("质量检测引入视觉大模型，泛化能力提升", "少样本场景部署周期缩短一半。"),
        ("绿色制造评价体系完善，能耗管理精细化", "单位产值能耗纳入考核。"),
    ],
    "ind_consumer": [
        ("即时零售竞争升温，履约时效成为核心指标", "30 分钟达覆盖城市继续扩大。"),
        ("国货美妆份额提升，成分与功效成购买理由", "功效护肤品类增速领先。"),
        ("折扣零售扩张，供应链效率决定毛利", "硬折扣门店数量快速增长。"),
        ("宠物经济持续增长，医疗与食品双轮驱动", "宠物医疗连锁化率提升。"),
        ("户外运动热度延续，细分品类机会增多", "轻量化装备需求上升。"),
        ("会员制商超加速开店，自有品牌占比提升", "自有品牌销售占比超过 30%。"),
        ("内容电商转向货架，商家运营更精细化", "搜索与推荐流量占比提升。"),
        ("银发消费兴起，适老化产品供给不足", "健康监测与助行设备需求增长。"),
    ],
}

CONTENT_TEMPLATE = """{summary}

## 事件概述
{summary}据{source}报道，相关变化已在产业链上下游同步体现，短期内以存量替换与效率提升为主，中期将影响供给格局。

## 关键数据
- 同比变化：{pct}%
- 行业关注度：{heat_text}
- 预计影响周期：{months} 个月

## 行业影响
1. **供给侧**：产能与良率是决定节奏的核心变量，头部厂商的资本开支计划更值得跟踪；
2. **需求侧**：客户从「试用」转向「按效果付费」，验收标准趋于量化；
3. **竞争格局**：新进入者集中在细分环节，短期难以形成全面替代。

## 观点引用
> 「这一轮变化更像是结构性调整，而不是简单的周期反弹。能否把能力沉淀为可复用的流程，决定了谁能穿越周期。」——某行业研究机构分析师

## 后续关注
- 下一季度财报中的相关业务收入与毛利率变化；
- 上游关键材料的报价与交期；
- 监管与标准的最新进展。

（本文为演示环境内容，用于展示资讯模块能力，不构成投资建议。）"""


def _mk_news(idx: int, industry_id: str, title: str, summary: str, rnd: random.Random) -> dict:
    source = rnd.choice(SOURCES)
    hours = rnd.randint(1, 24 * 7)
    publish = datetime.now(timezone.utc) - timedelta(hours=hours)
    heat = rnd.randint(1200, 98000)
    pct = round(rnd.uniform(3, 48), 1)
    content = CONTENT_TEMPLATE.format(
        summary=summary, source=source, pct=pct,
        heat_text=f"{heat / 1000:.1f}k", months=rnd.randint(3, 12),
    )
    item = {
        "id": f"news_{idx:04d}",
        "title": title,
        "summary": summary,
        "content": content,
        "source": source,
        "industry_id": industry_id,
        "publish_at": db.iso(publish),
        "heat": heat,
        "status": "online",
        "ai_points_json": "[]",
        "url": "",
        "cover": "",
        "read_count": rnd.randint(100, 12000),
    }
    ai = llm.news_ai_summary({**item, "industry_name": dict((i[0], i[1]) for i in INDUSTRIES)[industry_id]})
    item["ai_points_json"] = json.dumps(ai, ensure_ascii=False)
    return item


def seed_news() -> None:
    """仅初始化行业分类；资讯条目由 newssource 从真实 RSS 源抓取。"""
    for idx, (industry_id, name, icon) in enumerate(INDUSTRIES, 1):
        if not db.q1("SELECT id FROM news_category WHERE id = ?", (industry_id,)):
            db.insert("news_category", {"id": industry_id, "name": name, "sort": idx, "icon": icon})


PLUGINS = [
    ("plugin_web_search", "联网搜索", "🔍", "办公效率",
     "实时检索全网信息并返回带来源的摘要，适合资讯追踪与事实核查。",
     "覆盖主流站点与行业垂类站点，返回标题、站点、时间与链接，支持按时间范围过滤。",
     152000, 4.9, 3200, ["联网访问", "读取公开网页"], "search",
     [("query", "string", "检索关键词", True), ("time_range", "string", "时间范围：day/week/month", False)]),
    ("plugin_weather", "天气助手", "🌤️", "生活服务",
     "查询城市实时天气与未来 7 天预报，支持出行建议。",
     "接入公共气象数据，返回温度、天气现象、空气质量与出行建议。",
     88000, 4.8, 1200, ["获取城市位置"], "weather",
     [("city", "string", "城市名称", True)]),
    ("plugin_translate", "多语翻译", "🌐", "办公效率",
     "支持 30+ 语种互译，可指定术语库与语气风格。",
     "面向商务与学术场景优化，支持保留原文格式、术语一致性校验。",
     76000, 4.7, 980, ["文本内容"], "translate",
     [("text", "string", "待翻译文本", True), ("target", "string", "目标语言", True)]),
    ("plugin_excel", "表格分析", "📊", "数据分析",
     "上传或粘贴表格数据，自动完成清洗、透视与图表建议。",
     "支持 CSV / XLSX，输出统计摘要、异常值提示与推荐图表。",
     64000, 4.8, 1500, ["读取上传文件"], "excel",
     [("file", "file", "表格文件", True), ("question", "string", "分析问题", False)]),
    ("plugin_sql", "SQL 生成器", "🗄️", "开发工具",
     "把自然语言问题转换为 SQL，并解释执行计划与索引建议。",
     "支持 MySQL / PostgreSQL / SQLite 方言，输出可执行 SQL 与优化建议。",
     52000, 4.6, 760, ["读取表结构"], "sql",
     [("schema", "string", "表结构 DDL", True), ("question", "string", "查询需求", True)]),
    ("plugin_doc", "文档速读", "📄", "办公效率",
     "对长文档进行摘要、要点提炼与问答，支持 PDF / Word。",
     "解析后按章节返回摘要，支持定位到原文页码。",
     93000, 4.9, 2100, ["读取上传文件"], "doc",
     [("file", "file", "文档文件", True), ("question", "string", "问题", False)]),
    ("plugin_image", "图像理解", "🖼️", "内容创作",
     "识别图片内容、提取文字并生成配图文案。",
     "支持 OCR、场景描述与营销文案生成。",
     71000, 4.7, 890, ["读取图片"], "image",
     [("image", "file", "图片文件", True)]),
    ("plugin_calendar", "日程协同", "📅", "办公效率",
     "读取与创建日程，自动识别会议时间与参会人。",
     "与平台日历打通，支持冲突检测与会议室推荐。",
     45000, 4.5, 560, ["读取日历", "写入日历"], "calendar",
     [("action", "string", "create/list", True), ("summary", "string", "日程标题", False)]),
    ("plugin_crawler", "网页摘要", "🕸️", "内容创作",
     "抓取指定网页并生成结构化摘要与要点。",
     "支持正文抽取、作者与发布时间识别。",
     38000, 4.4, 410, ["联网访问"], "crawler",
     [("url", "string", "网页地址", True)]),
    ("plugin_formula", "公式计算", "🧮", "开发工具",
     "解析数学公式并给出计算过程与结果。",
     "支持符号计算、单位换算与步骤展示。",
     29000, 4.6, 330, ["无"], "formula",
     [("expression", "string", "表达式", True)]),
    ("plugin_ppt", "PPT 大纲助手", "📽️", "内容创作",
     "根据主题生成演示大纲与逐页要点。",
     "输出结构化大纲，可一键导入 PPT 模块。",
     57000, 4.8, 720, ["无"], "ppt",
     [("topic", "string", "主题", True), ("pages", "number", "页数", False)]),
    ("plugin_news", "行业快讯", "📰", "生活服务",
     "按行业订阅每日快讯，支持要点提炼。",
     "每日聚合多来源资讯，输出 3~5 条要点。",
     41000, 4.5, 480, ["联网访问"], "news",
     [("industry", "string", "行业", True)]),
    ("plugin_meeting", "会议纪要", "🎙️", "办公效率",
     "将会议录音或文字整理为结构化纪要。",
     "输出结论、待办与责任人，可同步到任务模块。",
     68000, 4.7, 950, ["读取音频", "读取文本"], "meeting",
     [("transcript", "string", "会议文本", True)]),
    ("plugin_data_viz", "图表生成", "📈", "数据分析",
     "把数据快速转换为可分享的图表规格。",
     "支持折线、柱状、饼图与堆叠图，输出图表配置 JSON。",
     35000, 4.4, 390, ["无"], "chart",
     [("data", "string", "数据", True), ("chart_type", "string", "图表类型", True)]),
]


def seed_plugins() -> None:
    if db.q1("SELECT id FROM plugin LIMIT 1"):
        return
    now = db.now_utc()
    for i, (pid, name, icon, cat, desc, detail, installs, rating, rcount, perms, kind, params) in enumerate(PLUGINS):
        db.insert("plugin", {
            "id": pid,
            "name": name,
            "icon": icon,
            "description": desc,
            "detail": detail,
            "category": cat,
            "type": "public",
            "author_id": None,
            "status": "approved",
            "install_count": installs,
            "rating": rating,
            "rating_count": rcount,
            "version": f"{1 + i % 2}.{i % 5}.0",
            "changelog": "1.2.0 增加批量处理与结构化输出；1.1.0 修复长文本截断问题。",
            "params_json": json.dumps([
                {"name": p[0], "type": p[1], "desc": p[2], "required": p[3]} for p in params
            ], ensure_ascii=False),
            "examples_json": json.dumps([
                {"input": "示例输入", "output": "示例输出"}], ensure_ascii=False),
            "permissions_json": json.dumps(perms, ensure_ascii=False),
            "auth_type": "none",
            "api_url": "",
            "reject_reason": "",
            "created_at": now,
            "updated_at": now,
        })


PPT_TEMPLATES = [
    (d.id, d.name, d.category, d.style, d.ratio, d.page_count, d.use_count,
     d.color_a, d.color_b, d.id) for d in DESIGNS.values()
]


def seed_ppt_templates() -> None:
    """模板既可初始化也可随版本升级：保留使用次数，更新版式与配色。"""
    for t in PPT_TEMPLATES:
        row = db.q1("SELECT id, use_count FROM ppt_template WHERE id = ?", (t[0],))
        payload = {
            "name": t[1], "category": t[2], "style": t[3], "ratio": t[4],
            "page_count": t[5], "color_a": t[7], "color_b": t[8], "layout": t[9],
        }
        if row:
            db.update("ppt_template", t[0], payload)
        else:
            db.insert("ppt_template", {**payload, "id": t[0], "use_count": t[6]})


DEMO_CONVERSATIONS = [
    ("如何用定时任务替代每天的手工资讯整理？", "general",
     [("user", "如何用定时任务替代每天的手工资讯整理？"),
      ("assistant", None)]),
    ("帮我写一份本周工作周报", "writing",
     [("user", "帮我写一份本周工作周报，重点体现效率提升"), ("assistant", None)]),
    ("Python 里如何稳定地对列表做多字段排序？", "code",
     [("user", "Python 里如何稳定地对列表做多字段排序？"), ("assistant", None)]),
]


def seed_demo_user() -> None:
    if db.q1("SELECT id FROM user WHERE email = 'demo@mauto.ai'"):
        return
    user = auth.create_user("demo@mauto.ai", "demo1234", "演示用户")
    base = datetime.now(timezone.utc)
    for i, (title, kind, msgs) in enumerate(DEMO_CONVERSATIONS):
        conv_id = db.uid("c_")
        created = db.iso(base - timedelta(days=i, hours=3))
        db.insert("conversation", {
            "id": conv_id, "user_id": user["id"], "title": title[:20],
            "project_id": None, "model": "MAuto-Air",
            "is_pinned": 1 if i == 0 else 0, "is_archived": 0,
            "created_at": created, "updated_at": created,
        })
        for role, content in msgs:
            text = content if content is not None else llm.build_answer(title)
            db.insert("message", {
                "id": db.uid("m_"), "conversation_id": conv_id, "role": role,
                "content": text, "tokens": llm.estimate_tokens(text), "status": "done",
                "model": "MAuto-Air", "feedback": 0, "citations_json": "[]",
                "plugin_calls_json": "[]", "reasoning": "", "attachments_json": "[]",
                "created_at": created,
            })
    db.insert("notification", {
        "id": db.uid("n_"), "user_id": user["id"], "type": "system",
        "title": "欢迎使用 MAuto 智能助手平台",
        "body": "左侧可选择新聊天、任务、咨询、插件、PPT、项目六个模块。演示账号已预置 3 条历史会话。",
        "ref_type": "", "ref_id": "", "is_read": 0, "created_at": db.now_utc(),
    })


def seed_all() -> None:
    db.init_db()
    seed_news()
    seed_plugins()
    seed_ppt_templates()
    seed_demo_user()
