"""将职位搜索意图转换为消息卡片，普通文字回答仍使用现有 Runner。"""

from dataclasses import dataclass, replace
import re

from src.career_assistant.job_search_cards import new_job_search_card
from src.career_assistant.job_search_intent import JobSearchIntent, detect_job_search_intent, _explicit_conditions
from src.career_assistant.persistence.records import MessageRecord
from src.career_assistant.job_search_context import resolve_search_scope


@dataclass(frozen=True)
class JobSearchRoute:
    intent: JobSearchIntent
    previous_message: MessageRecord | None = None
    history: tuple[dict, ...] = ()
    previous_card: dict | None = None
    view_batch_id: str | None = None
    cancel_batch_id: str | None = None


def detect_route(repository, active_turn, context, *, classify=None, previous=None) -> JobSearchRoute:
    """只使用最近对话和已确认搜索条件，不解析简历或岗位材料里的指令。"""
    if set(getattr(active_turn.turn, "input_kind_codes", ())) & {"resume_review", "job_search_auto_analysis"}:
        return JobSearchRoute(JobSearchIntent())
    snapshot = getattr(context, "job_search_snapshot", None)
    if snapshot is not None:
        scope = resolve_search_scope(context.redacted_user_text, snapshot)
        catalog = snapshot.get("catalog", [])
        active_id = str(snapshot.get("active_batch_id") or "")
        active_batch = next((item for item in catalog if str(item.get("id") or item.get("batch_id")) == active_id), catalog[-1] if catalog else None)
        card = None
        if active_batch:
            card = {"type": "job_search", "status": "searched", "conditions": active_batch.get("conditions", {}),
                    "result_summary": {"count": active_batch.get("loaded_count", 0), "jobs": [{"title": "已存岗位"}] if active_batch.get("loaded_count") else []}}
        intent = detect_job_search_intent(context.redacted_user_text, previous_card=card, classify=classify)
        text = context.redacted_user_text
        explicit_keyword, explicit_city = _explicit_conditions(text)
        company_question = bool(re.search(r"有(?:哪些|什么)(?:岗位|职位)", text))
        matching = [item for item in catalog if (item.get("conditions") or {}).get("keyword") == explicit_keyword]
        if intent.action == "normal_chat" and company_question and explicit_keyword and not matching:
            intent = JobSearchIntent("open_job_search", explicit_keyword,
                explicit_city or ((card or {}).get("conditions") or {}).get("city"),
                text[:500], auto_search=True, auto_analyze=bool(re.search(r"推荐|适合|匹配", text)))
        elif intent.action in {"open_job_search", "update_job_search"}:
            intent = replace(intent, auto_analyze=bool(re.search(r"推荐|适合我|匹配|比较", text)))
        if intent.action == "cancel_job_search" and active_batch:
            return JobSearchRoute(intent, previous_card=card,
                cancel_batch_id=str(active_batch.get("id") or active_batch.get("batch_id")))
        if intent.action == "normal_chat" and scope["mode"] != "normal":
            intent = replace(intent, analysis_mode=scope["mode"], batch_ids=tuple(scope["batch_ids"]), job_refs=tuple(scope["job_refs"]))
        if scope["mode"] == "view" and not scope["ambiguous"] and scope["batch_ids"]:
            viewed = next((item for item in catalog if str(item.get("id") or item.get("batch_id")) == scope["batch_ids"][0]), {})
            card = {"type": "job_search", "conditions": viewed.get("conditions", {})}
            return JobSearchRoute(replace(intent, action="clarify"), previous_card=card, view_batch_id=scope["batch_ids"][0])
        return JobSearchRoute(intent, previous_card=card)
    if previous is None:
        messages = repository.list_messages(active_turn.conversation.actor_id, active_turn.conversation.id, limit=30)
        # 排除当前以及排在当前之后的用户输入，避免排队消息串入本轮判断。
        prior = []
        found_current = False
        for message in messages:
            if message.turn_id == active_turn.turn.id:
                found_current = True
                break
            prior.append(message)
        if not found_current:
            # 当前消息不在有限窗口时不能把之后排队的输入当作历史。
            prior = []
        previous_message = next((item for item in reversed(prior)
                                 if (getattr(item, "ui_payload", None) or {}).get("type") == "job_search"), None)
        card = previous_message.ui_payload if previous_message else None
        if card is None and getattr(context, "context_binding_version", None) is not None:
            # 只读取服务端固定头部，不能从 JD 正文推测用户搜索意愿。
            header = getattr(context, "target_role_context", "").split("\n\n", 1)[0]
            role = re.search(r"(?m)^岗位：([^\n]{1,100})$", header)
            if role:
                card = new_job_search_card(role.group(1).strip(), None, auto_search=False)
                card["status"] = "selected"
        history = tuple({"role": item.role.value, "content": item.content_text[:800]}
                        for item in prior[-6:])
        previous = JobSearchRoute(JobSearchIntent(), previous_message, history, card)
    intent = detect_job_search_intent(context.redacted_user_text, previous_card=previous.previous_card,
                                      history=list(previous.history), classify=classify)
    return replace(previous, intent=intent)


def materialize_route(repository, active_turn, route: JobSearchRoute, redactor):
    """返回正文、可选新卡片与已更新消息；查询动作仍留在浏览器端执行。"""
    intent = route.intent
    if route.cancel_batch_id:
        repository.cancel_job_search_batch(active_turn.conversation.actor_id,
            active_turn.conversation.id, route.cancel_batch_id)
        return "好的，已停止这批岗位的搜索与自动分析。", None, ()
    if route.view_batch_id:
        return "已展开这批保存的岗位，可以继续查看或与其他批次比较。", {
            "type": "job_search", "version": 2, "status": "searched", "auto_search": False,
            "view_batch_id": route.view_batch_id, "batch_ids": [route.view_batch_id],
            "conditions": (route.previous_card or {}).get("conditions", {}),
        }, ()
    if intent.action == "normal_chat":
        return "", None, ()
    if intent.action == "clarify":
        return "你想先搜索哪个城市、哪个方向的岗位？确定后我会在这里展示职位检索。", None, ()
    previous = route.previous_message
    if intent.action == "cancel_job_search":
        updates = ()
        if previous and previous.ui_payload.get("status") != "dismissed":
            updates = (repository.update_job_search_card(active_turn.conversation.actor_id,
                       active_turn.conversation.id, previous.id, {"status": "dismissed"}),)
        return "好的，已收起找工作表单。", None, updates
    keyword = redactor.redact(intent.keyword) if intent.keyword else None
    city = redactor.redact(intent.city) if intent.city else None
    payload = new_job_search_card(keyword, city, auto_search=intent.auto_search)
    # 新建卡片只保存界面投影；岗位事实进入独立批次。
    payload.update(version=2, auto_analyze=intent.auto_analyze)
    old_conditions = (route.previous_card or {}).get("conditions", {})
    if city and city not in intent.evidence and city == old_conditions.get("city"):
        payload["conditions"]["city_source"] = old_conditions.get("city_source") or "profile"
    if intent.action == "update_job_search" and previous:
        updated = repository.update_job_search_card(active_turn.conversation.actor_id,
                  active_turn.conversation.id, previous.id,
                  {"conditions": payload["conditions"], "auto_search": intent.auto_search})
        return "已更新搜索条件，可以在表单里继续搜索岗位。", None, (updated,)
    text = "已为你准备找工作表单，你可以修改条件后搜索。"
    if intent.auto_search:
        text = (
            "已填好搜索条件，正在通过职位库助手获取真实岗位。" if city
            else "已填好岗位方向，获取当前位置或选择工作城市后，会自动搜索一次。"
        )
    return text, payload, ()
