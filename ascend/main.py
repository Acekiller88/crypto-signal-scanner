"""ASCEND-30 CLI.

Local:   python -m ascend.main
CI:      python -m ascend.main            (full top-30 scan, writes JSON)
         python -m ascend.main --symbols BTCUSDT ETHUSDT   (test)
         python -m ascend.main --dry-run
         python -m ascend.main --validate-only
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import Config
from . import persist


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ASCEND-30 signal scanner")
    parser.add_argument("--config", default=None)
    parser.add_argument("--symbols", nargs="*", help="override universe (local testing)")
    parser.add_argument("--max-symbols", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--account-equity", type=float, default=100_000_000.0)
    parser.add_argument("--json-output", action="store_true", help="print status JSON")
    parser.add_argument("--calibrate", metavar="PATH", default=None,
                        help="Refit scoring.logistic {a,b} from a JSON of resolved "
                             "(score, profit>=1R) pairs")
    parser.add_argument("--calibrate-l2", type=float, default=0.01)
    args = parser.parse_args(argv)

    cfg = Config.load(args.config)
    errs = cfg.validate()
    if errs:
        for e in errs:
            print(f"[CONFIG ERROR] {e}", file=sys.stderr)
        return 1
    if args.calibrate:
        from .calibrate import apply_fit, fit_logistic
        samples = _load_samples(args.calibrate)
        if not samples:
            print("[CALIBRATE] no resolved samples found -- nothing fitted", file=sys.stderr)
            return 1
        a, b = fit_logistic(samples, l2=args.calibrate_l2)
        apply_fit(cfg, a, b)
        print(f"[CALIBRATE] fitted scoring.logistic = {{'a': {a}, 'b': {b}}} "
              f"from {len(samples)} resolved samples")
        return 0
    if args.validate_only:
        from .validate_data import main as validate_main
        return validate_main()

    persist.seed_empty_files()
    from .engine import ScanLog, scan_once
    log = ScanLog()
    code = scan_once(cfg, log, symbols=args.symbols, max_symbols=args.max_symbols,
                     dry_run=args.dry_run, account_equity=args.account_equity)
    if args.json_output:
        status = persist.load_json("ascend-system-status.json", {})
        print(json.dumps(status, default=str))
    return code


def _load_samples(path: str) -> list[tuple[float, int]]:
    """Load (score, won) pairs from a JSON list of objects.""" 
    import json
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    samples: list[tuple[float, int]] = []
    if isinstance(data, dict):
        data = data.get("samples", [])
    for rec in data:
        if isinstance(rec, dict):
            score = rec.get("score")
            won = rec.get("won")
        elif isinstance(rec, (list, tuple)) and len(rec) >= 2:
            score, won = rec[0], rec[1]
        else:
            continue
        try:
            samples.append((float(score), _to_binary(won)))
        except (TypeError, ValueError):
            continue
    return samples


def _to_binary(value) -> int:
    """Coerce a resolved-outcome scalar to 0/1, accepting strings like 'false'."""
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("1", "true", "yes", "won", "win"):
            return 1
        if s in ("0", "false", "no", "lost", "loss", ""):
            return 0
        try:
            return 1 if float(s) else 0
        except ValueError:
            raise ValueError(f"unrecognised outcome: {value!r}")
    return 1 if (value and value != 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
