"""宫廷艺术展陈叙事签发的本地命令入口。

子命令：
  validate <json>   兼容基线：登记一条基础记录并回显
  story             端到端演示媒体开放日→年代订正→提前撤展→待生效稿到点
"""
import json
import sys
from pathlib import Path

from . import demo
from .service import Service


def _summarize_snapshot(snapshot: dict) -> dict:
    units = []
    for unit in snapshot["units"]:
        channels = {}
        for key, value in unit["channels"].items():
            if value is None:
                channels[key] = None
            else:
                channels[key] = {
                    "文本": value["body"],
                    "勘误": [c["note"] for c in value["corrigenda"]],
                }
        units.append({
            "单元": unit["title"],
            "当时器物": [f'{e["title"]}（{e["slot"]}）' for e in unit["exhibits"]],
            "采用解释": [i["dating"] for i in unit["interpretations"]["adopted"]],
            "反对解释": [
                f'{i["dating"]}：{i["objections"][0]["reason"]}'
                for i in unit["interpretations"]["opposed"]
            ],
            "各渠道": channels,
        })
    return {"时点": snapshot["as_of"], "单元": units}


def _story() -> int:
    stages = demo.run()
    output = []
    for stage in stages:
        item = {"阶段": stage["stage"], **_summarize_snapshot(stage["snapshot"])}
        if "receipt" in stage:
            item["订正回执"] = {
                "release_id": stage["receipt"]["release_id"],
                "replayed": stage["receipt"]["replayed"],
                "变更": stage["receipt"].get("changed"),
            }
        if "recall" in stage:
            item["撤展"] = stage["recall"]
        if "open_reviews" in stage:
            item["待复核"] = [r["note"] for r in stage["open_reviews"]]
        if "applied" in stage:
            item["到期执行"] = stage["applied"]
        output.append(item)
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=False))
    return 0


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "validate":
        payload = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        print(json.dumps(Service().register(payload), ensure_ascii=False, sort_keys=True))
        return 0
    if len(sys.argv) == 2 and sys.argv[1] == "story":
        return _story()
    print(json.dumps(Service().health(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
