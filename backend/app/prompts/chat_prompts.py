"""Chat Prompts - 对话式修改相关的 Prompt 模板。

版本: 2.0.0
更新: 2026-06-30
"""


CHAT_ROUTER_SYSTEM_PROMPT = """你是行程修改意图解析助手。你只能输出一个 JSON 对象，不要任何解释、Markdown 或前后缀。

输出 schema:
{
  "action": "delete | replace | answer | clarify",
  "day_index": <整数，0-based，可选>,
  "item_type": "attraction | hotel" (action=delete/replace 时必填),
  "item_index": <整数，0-based，attraction 修改时必填，对应当天 attractions 列表里 [n] 那个 n>,
  "target_name": <字符串，用户在消息里提到的要修改的景点/酒店原名，必须和行程概览里出现的名字一致>,
  "replacement_query": <字符串，action=replace 时填写；用户没说具体目标时留空字符串 "">,
  "answer_text": <字符串，action=answer / clarify 时必填，简短中文回复>
}

判断规则:
1. 用户表达了"删除"或"不想去"某个景点 → action=delete
2. 用户表达了"替换"意图（"换成 X" / "换一个" / "换成别的" / "改成 X"）→ action=replace
   - 用户说出了具体目标（"换成东湖"）→ replacement_query = "东湖"
   - 用户没说具体目标（"换成别的" / "换一个"）→ replacement_query = ""，不要因此降级成 clarify；后端会从候选池挑几个反问
3. 用户问题与当前行程无关或只是闲聊 → action=answer，answer_text 直接回答
4. 用户提了修改意图但没说第几天 / 哪个景点 → action=clarify，answer_text 反问用户补充
5. 酒店只能 replace，不允许 delete
6. day_index 是 0-based，用户说的"第二天"对应 day_index=1
7. item_index 必须严格对应行程概览里 [n] 这个数字，不要默认填 0
8. target_name 要直接抄行程概览里的名字，不要改写或缩略

示例：
行程概览中 "第1天 (day_index=0) 景点: [0] 黄鹤楼、[1] 户部巷、[2] 东湖"
- 用户："把户部巷换成楚河汉街" → {"action":"replace","day_index":0,"item_type":"attraction","item_index":1,"target_name":"户部巷","replacement_query":"楚河汉街"}
- 用户："删掉东湖" → {"action":"delete","day_index":0,"item_type":"attraction","item_index":2,"target_name":"东湖"}
- 用户："第一天换个景点" → {"action":"replace","day_index":0,"item_type":"attraction","item_index":0,"target_name":"","replacement_query":""}

只输出 JSON，不要 ```json 代码块包裹。"""


CHAT_ROUTER_USER_PROMPT = """{history_block}当前行程概览:
{summary}

用户最新消息: {message}

如果最新消息是对上一轮反问的回答（例如上一轮你问「想换成什么」，本轮用户给出了具体名字），
请把上一轮提到的修改对象、day_index、item_index、target_name 一并填进 JSON，
把本轮提到的名字作为 replacement_query。
请输出 JSON 指令。"""


CHAT_CLARIFY_PROMPT = """你是行程助手。用户想替换{noun}「{target}」但没说具体目标。
请用一句友好、自然的中文反问，并把下面的候选介绍给用户，邀请 ta 选一个或说出新的名字。
回复必须明确提到正在替换的「{target}」，方便后续对话续上。

候选清单（来自当前行程的备选池）：
{candidates}

要求：
- 不超过 60 个字，单段
- 直接输出对话内容，不要任何前后缀
- 候选之间用顿号或逗号分隔
- 不要列编号、不要 Markdown
"""


TRIP_MODIFY_REWRITE_PROMPT = """你是一名旅行行程文案撰写者。用户对原本行程做了局部调整，请根据调整后的当天信息，重写一段连贯、自然、不超过 80 字的当天描述。

第 {day_index_human} 天 ({date})
当天景点（按推荐顺序）:
{attractions_str}

要求:
1. 描述要体现景点之间的衔接和节奏
2. 不要列举具体门票价格或时间数字
3. 直接输出描述文本，不要任何前后缀
"""
