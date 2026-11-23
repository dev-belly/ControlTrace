"""Build the offline browser snapshot from the repository's saved evidence."""

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[1]


def source(label, path):
    normalized = (ROOT / path).read_text(encoding="utf-8").encode("utf-8")
    return {
        "label": label,
        "url": f"{REPOSITORY}/blob/main/{path}",
        "sha256_lf": hashlib.sha256(normalized).hexdigest(),
    }


def fact(label, value, kind=None):
    return {"label": label, "value": value, "format": kind}


def column(key, title, kind=None):
    return {"key": key, "title": title, "format": kind}


def order(key, title, descending=True):
    return {"key": key, "title": title, "id": key, "descending": descending}


def metric(label, value, note):
    return {"label": label, "value": value, "note": note}


def table(title, columns, rows, *formats):
    return {
        "title": title,
        "table": {"columns": columns, "rows": rows, "formats": formats},
    }


def theme(accent, muted, base, glow):
    return {
        "accent": accent,
        "accent-soft": muted,
        "hero-base": base,
        "hero-glow": glow,
    }


REPOSITORY = "https://github.com/dev-belly/ControlTrace"


def build():
    sys.path.insert(0, str(ROOT / "src"))
    from controltrace.data import DEMO_CUTOFF, generate_demo_data
    from controltrace.reviews import finding_fingerprint
    from controltrace.rules import RULES, evaluate

    data = generate_demo_data()
    findings = evaluate(data)
    risks = {"high": "高", "medium": "中", "low": "低"}
    kinds = {"exception": "规则异常", "manual_review": "待人工判断"}
    severity = {"high": 3, "medium": 2, "low": 1}
    coverage = Counter(finding["classification"] for finding in findings)
    records = []
    for finding in findings:
        identifier = finding["finding_id"]
        time_rows = [
            [item["at"], item["event"], item["source_system"], item["evidence_id"]]
            for item in finding["timeline"]
        ]
        rule = finding["rule"]
        sections = [
            {"title": "命中依据", "text": finding["rationale"]},
            table(
                "证据时间线（按 UTC 排序）", ["原始时间", "事件", "来源系统", "证据 ID"], time_rows
            ),
            {
                "title": "控制规则",
                "items": [rule["control_goal"], rule["logic"], rule["exceptions"]],
                "collapsed": True,
            },
        ]
        for item in finding["related_records"]:
            evidence = item["record"]
            rows = [
                [key, value if value is not None else "原值为空"] for key, value in evidence.items()
            ]
            sections.append(
                {
                    **table(f"{item['table']} / {item['id']}", ["字段", "原始值"], rows),
                    "collapsed": True,
                }
            )
        records.append(
            {
                "id": identifier,
                "title": finding["title"],
                "subtitle": f"{identifier} · {finding['system']} · {finding['control_id']}",
                "search": " ".join(finding["evidence_ids"]),
                "values": {
                    "control": finding["control_id"],
                    "risk": finding["risk"],
                    "classification": finding["classification"],
                    "system": finding["system"],
                    "date": finding["occurred_at"][:10],
                    "severity": severity[finding["risk"]],
                },
                "facts": [
                    fact("发现 ID", identifier),
                    fact("控制", f"{finding['control_id']} · {rule['name']}"),
                    fact("系统", finding["system"]),
                    fact("主体", f"{finding['entity_type']} / {finding['entity_id']}"),
                    fact("规则结果", kinds[finding["classification"]]),
                    fact("证据 SHA-256", finding_fingerprint(finding)),
                ],
                "sections": sections,
                "related_source_records": finding["related_records"],
            }
        )
    return {
        "repository": REPOSITORY,
        "slug": "controltrace",
        "project": "ControlTrace",
        "mark": "CT",
        "theme": theme("#11667d", "#e9f6fb", "#122b39", "#28728d"),
        "workspace": "IT 审计与内控",
        "title": "内控证据工作台",
        "description": "串起人事、权限与发布记录。筛选自动发现，逐项核对审计证据和原始事件时间。",
        "dataKind": "合成企业记录 · 五项控制规则",
        "hero": metric("已重放观察", f"{len(records)} 条", "使用仓库固定种子与正式规则"),
        "metrics": [
            metric("规则异常", str(coverage["exception"]), "按样例规则自动命中"),
            metric("待人工判断", str(coverage["manual_review"]), "证据缺口或需人工验证"),
            metric("审计截止日", DEMO_CUTOFF[:10], "23:59:59 UTC"),
        ],
        "recordHeading": "自动观察",
        "recordLabel": "发现",
        "detailHeading": "关联证据与时间线",
        "searchPlaceholder": "发现、证据 ID 或系统",
        "initialRecord": records[0]["id"],
        "filters": [
            {
                "key": "control",
                "title": "控制",
                "labels": {key: f"{key} · {item['name']}" for key, item in RULES.items()},
            },
            {"key": "risk", "title": "风险等级", "labels": risks},
            {"key": "classification", "title": "判断类型", "labels": kinds},
            {"key": "system", "title": "来源系统"},
        ],
        "columns": [
            column("control", "控制"),
            column("risk", "风险"),
            column("classification", "判断"),
            column("system", "系统"),
            column("date", "事件日期"),
        ],
        "orders": [order("severity", "风险等级从高到低"), order("date", "事件日期从近到远")],
        "views": [
            {
                "id": "risk",
                "label": "风险等级",
                "title": "观察风险分布",
                "mode": "group",
                "group": "risk",
                "format": "integer",
                "caption": "点击风险等级筛选观察。明细保留规则异常与人工核实标记。",
            },
            {
                "id": "control",
                "label": "控制覆盖",
                "title": "控制命中分布",
                "mode": "group",
                "group": "control",
                "format": "integer",
                "caption": "点击控制查看对应的发现、原始记录和判定口径。",
            },
        ],
        "sourceNote": "使用演示数据生成器及项目自带规则重放。实际录入复核和导出底稿 ZIP 在本机 Streamlit 应用完成。",
        "sources": [
            source("合成数据生成器", "src/controltrace/data.py"),
            source("原生控制规则", "src/controltrace/rules.py"),
            source("可核对的底稿案例", "docs/EXAMPLE_WORKPAPER.md"),
        ],
        "records": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the generated snapshot")
    args = parser.parse_args()
    document = build()
    records = document.pop("records")
    lines = ",\n".join(
        "    " + json.dumps(record, ensure_ascii=False, allow_nan=False) for record in records
    )
    header = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2)
    result = (
        "// Generated by build_data.py from repository evidence.\n"
        "window.workbenchData = " + header[:-2] + ',\n  "records": [\n' + lines + "\n  ]\n};\n"
    )
    path = DIRECTORY / "data.js"
    if args.check:
        if not path.exists() or path.read_text(encoding="utf-8") != result:
            parser.exit(1, "Snapshot differs. Run build_data.py without --check.\n")
        print(f"Verified {len(records)} records")
    else:
        path.write_text(result, encoding="utf-8", newline="\n")
        print(f"Wrote {len(records)} records")


if __name__ == "__main__":
    main()
