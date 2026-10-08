from __future__ import annotations

from dataclasses import asdict, dataclass


BID_COLLUSION_DETAIL_MODEL_ID = "bid_collusion_detail_drilldown"


@dataclass(frozen=True, slots=True)
class BidCollusionDimensionSpec:
    key: str
    ydlx: str
    scene_field: str
    single_model_id: str
    single_model_name: str
    detail_model_id: str
    detail_sql_code: str
    detail_source_table: str
    evidence_columns: tuple[str, ...] = ()
    report_hint: str = ""


DIMENSIONS: tuple[BidCollusionDimensionSpec, ...] = (
    BidCollusionDimensionSpec(
        key="phone_email_same",
        ydlx="电话邮箱相同",
        scene_field="不同投标人电话邮箱相同",
        single_model_id="model_5",
        single_model_name="不同投标人电话、邮箱相同",
        detail_model_id=BID_COLLUSION_DETAIL_MODEL_ID,
        detail_sql_code="phone_email_same",
        detail_source_table="rst_sj_dzzb_tbrdhyxxt",
        evidence_columns=("代表人电话1", "代表人电话2", "代表人邮箱1", "代表人邮箱2"),
        report_hint="重点说明不同投标人电话、邮箱复用情况。",
    ),
    BidCollusionDimensionSpec(
        key="same_bidders",
        ydlx="投标人完全一致",
        scene_field="不同标段投标人完全一致",
        single_model_id="model_1",
        single_model_name="不同标段或不同次招标，投标人完全一致",
        detail_model_id=BID_COLLUSION_DETAIL_MODEL_ID,
        detail_sql_code="same_bidders",
        detail_source_table="rst_sj_dzzb_tbrwqyz",
        evidence_columns=("项目名称", "标段名称", "投标人1", "投标人2"),
        report_hint="重点说明不同标段或不同次招标中投标人组合完全一致的情况。",
    ),
    BidCollusionDimensionSpec(
        key="hardware_ip_same",
        ydlx="硬件或IP重复",
        scene_field="不同投标人硬件及ip地址一致",
        single_model_id="model_7",
        single_model_name="不同投标人硬件及ip地址一致",
        detail_model_id=BID_COLLUSION_DETAIL_MODEL_ID,
        detail_sql_code="hardware_ip_same",
        detail_source_table="rst_sj_dzzb_yjjipyz",
        evidence_columns=("IP地址", "硬件特征码", "机器码", "MAC地址"),
        report_hint="重点说明不同投标人硬件或 IP 地址重复情况。",
    ),
    BidCollusionDimensionSpec(
        key="deposit_same_account",
        ydlx="保证金同一账户转出",
        scene_field="投标保证金从同一账户转出",
        single_model_id="model_8",
        single_model_name="投标保证金从同一账户转出",
        detail_model_id=BID_COLLUSION_DETAIL_MODEL_ID,
        detail_sql_code="deposit_same_account",
        detail_source_table="rst_sj_dzzb_tbbzjctyzhzc",
        evidence_columns=("付款账号", "付款户名", "开户银行", "保证金金额"),
        report_hint="重点说明投标保证金从同一账户转出的资金线索。",
    ),
    BidCollusionDimensionSpec(
        key="person_repeat",
        ydlx="人员重复",
        scene_field="人员关联次数",
        single_model_id="model_6",
        single_model_name="不同投标人的投标联系人、授权代表人、项目负责人相同/有关系的投标人组",
        detail_model_id=BID_COLLUSION_DETAIL_MODEL_ID,
        detail_sql_code="person_repeat",
        detail_source_table="rst_sj_dzzb_tblxrxt",
        evidence_columns=("人员姓名", "人员类型", "身份证号", "联系电话"),
        report_hint="重点说明联系人、授权代表人或项目负责人重复及关联情况。",
    ),
)


def get_dimension_by_ydlx(ydlx: str) -> BidCollusionDimensionSpec | None:
    normalized = str(ydlx or "").strip()
    return next((item for item in DIMENSIONS if item.ydlx == normalized), None)


def get_dimension_by_single_model_id(model_id: str) -> BidCollusionDimensionSpec | None:
    normalized = str(model_id or "").strip()
    return next((item for item in DIMENSIONS if item.single_model_id == normalized), None)


def get_dimension_by_model_name(model_name: str) -> BidCollusionDimensionSpec | None:
    normalized = str(model_name or "").strip()
    return next((item for item in DIMENSIONS if item.single_model_name == normalized), None)


def get_dimension_by_scene_field(field: str) -> BidCollusionDimensionSpec | None:
    normalized = str(field or "").strip()
    return next((item for item in DIMENSIONS if item.scene_field == normalized), None)


def scene_dimension_fields() -> tuple[str, ...]:
    return tuple(item.scene_field for item in DIMENSIONS)


def allowed_ydlx_values() -> tuple[str, ...]:
    return tuple(item.ydlx for item in DIMENSIONS)


def dimension_to_dict(spec: BidCollusionDimensionSpec) -> dict[str, object]:
    return asdict(spec)
