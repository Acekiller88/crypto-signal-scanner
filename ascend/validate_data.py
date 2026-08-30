"""ASCEND JSON validation CLI (used by CI).

    python -m ascend.validate_data

Exits non-zero if any /data/ascend-*.json file is unparseable or a signal row
fails basic integrity (score bounds, probability bounds, level ordering).
"""
from __future__ import annotations

import json
import sys

from .config import Config
from . import persist

VALID_LABELS = {"Strong Buy", "Buy", "Neutral", "Sell", "Strong Sell"}


def validate_signal_row(row: dict) -> list[str]:
    errs: list[str] = []
    if not isinstance(row.get("score"), (int, float)) or not (0 <= row["score"] <= 100):
        errs.append("score out of 0..100")
    if not isinstance(row.get("prob"), (int, float)) or not (0 <= row["prob"] <= 1):
        errs.append("prob out of 0..1")
    if row.get("label") not in VALID_LABELS:
        errs.append(f"unknown label {row.get('label')}")
    if row.get("direction") not in ("LONG", "SHORT"):
        errs.append("direction invalid")
    if row.get("entrySource") not in (None, "fvg", "order_block", "retracement", "value_area"):
        errs.append(f"unknown entrySource {row.get('entrySource')}")
    ep = row.get("exitPlan")
    if ep:
        entry, t1 = row.get("entry"), row.get("target1")
        for key in ("breakeven", "partial"):
            if ep[key]["atR"] <= 0:
                errs.append(f"exitPlan.{key}.atR must be > 0")
        be_p, pa_p = ep["breakeven"]["price"], ep["partial"]["price"]
        # breakeven/partial must land inside the entry..target1 corridor
        if row.get("direction") == "LONG":
            if not (entry < be_p <= t1 and entry < pa_p <= t1):
                errs.append("exitPlan breakeven/partial outside entry..target1")
        else:
            if not (t1 <= be_p < entry and t1 <= pa_p < entry):
                errs.append("exitPlan breakeven/partial outside entry..target1")
        if not (0 < ep["partial"]["fraction"] <= 1):
            errs.append("exitPlan partial fraction out of (0,1]")
    # level ordering
    if row.get("direction") == "LONG":
        if not (row["stop"] < row["entry"] < row["target1"]):
            errs.append("LONG ordering invalid")
    elif row.get("direction") == "SHORT":
        if not (row["target1"] < row["entry"] < row["stop"]):
            errs.append("SHORT ordering invalid")
    return errs


def main() -> int:
    cfg = Config.load()
    errors: list[str] = []
    files = ["ascend-signals.json", "ascend-screener.json",
             "ascend-system-status.json", "ascend-performance.json"]
    data = persist.data_dir()
    payloads: dict = {}
    for name in files:
        path = data / name
        if not path.exists():
            errors.append(f"{name}: missing")
            continue
        try:
            payloads[name] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{name}: invalid JSON ({exc})")

    if "ascend-signals.json" in payloads:
        signals = payloads["ascend-signals.json"].get("signals", [])
        for i, row in enumerate(signals):
            for e in validate_signal_row(row):
                errors.append(f"signals[{i}] ({row.get('symbol')}): {e}")
        print(f"ascend-signals.json: {len(signals)} signals")

    if "ascend-screener.json" in payloads:
        rows = payloads["ascend-screener.json"].get("rows", [])
        print(f"ascend-screener.json: {len(rows)} screener rows")

    if "ascend-system-status.json" in payloads:
        st = payloads["ascend-system-status.json"]
        if not isinstance(st.get("health"), str):
            errors.append("ascend-system-status.json: health missing")

    if errors:
        for e in errors:
            print(f"[VALIDATION ERROR] {e}", file=sys.stderr)
        return 1
    print("ASCEND data validation OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
