from __future__ import annotations

from stockml.intraday.worker import intraday_tick


def main() -> None:
    result = intraday_tick()
    print(f"intraday_tick_status: {result.get('status')}")
    print(f"reason: {result.get('reason', '')}")
    print(f"decisions_written: {result.get('decisions_written', 0)}")
    if result.get("tripped"):
        print(f"tripped: {','.join(result['tripped'])}")


if __name__ == "__main__":
    main()
