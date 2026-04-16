from __future__ import annotations

from ops_distiller_forge.ontology.models import EpisodeRecord, KnowledgePoint


def knowledge_point_to_episode(
    kp: KnowledgePoint,
    *,
    client_id: str = "demo_client",
) -> EpisodeRecord:
    """
    将母版知识点投影为一条 Graphiti Episode 正文（短文）。
    不直接调用 Graphiti；产出给 ops-agent graphiti-ingest 或本仓批处理。
    """
    parts: list[str] = [
        f"【{kp.title}】",
        "",
        "## 底层逻辑",
        kp.theory_logic.strip() or "（待补充）",
        "",
        "## 执行步骤",
    ]
    for i, step in enumerate(kp.sop_steps, 1):
        parts.append(f"{i}. {step}")
    if kp.key_metrics:
        parts.extend(["", "## 关键指标", *[f"- {m}" for m in kp.key_metrics]])
    if kp.anti_patterns:
        parts.extend(["", "## 避坑", *[f"- {a}" for a in kp.anti_patterns]])
    if kp.case_reference:
        parts.extend(["", "## 案例参考", *[f"- {c}" for c in kp.case_reference]])
    parts.extend(
        [
            "",
            "---",
            f"来源: {kp.metadata.source_relpath} | handbook={kp.metadata.handbook_version} | kp_id={kp.id}",
        ]
    )
    body = "\n".join(parts)
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in kp.title[:48]) or "kp"
    return EpisodeRecord(
        name=f"{safe_name}_{kp.id[:8]}",
        body=body,
        source_description=f"forge:kp:{kp.id}",
        source="text",
        client_id=client_id,
        knowledge_point_id=kp.id,
    )
