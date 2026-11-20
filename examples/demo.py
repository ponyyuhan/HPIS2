"""Run the terminal SDK and both Linux-compatible services on three example notes."""
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from bench.cluster import running
from hpis.sdk import Device


async def search(folder, url):
    notes = Path(__file__).with_name("notes.jsonl")
    async with Device(folder, url) as device:
        await device.put(json.loads(row) for row in notes.read_text(encoding="utf-8").splitlines())
        return {
            "字段与关键词": await device.terms([["field", "城市", "柏林"], ["word", "出差"]]),
            "模糊词": await device.terms([["fuzzy", "火車票"]]),
            "向量精确 Top-2": await device.vector([1, 0, 0], 2, refine=True),
        }


def main():
    with TemporaryDirectory(prefix="hpis-demo-") as tmp, running(Path(tmp)) as endpoints:
        result = asyncio.run(search(*endpoints))
    for name, reply in result.items():
        print(f"{name}，读取 {reply['stats']['body_rows']} 条正文")
        for rid, content in reply["records"]:
            print(f"  {rid}  {content}")


if __name__ == "__main__":
    main()
