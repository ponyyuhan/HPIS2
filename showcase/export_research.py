"""Prepare leadership-facing summaries from the measured Linux result files."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).with_name("static") / "data"


def source(name, path):
    data = json.loads((ROOT / path).read_text())
    folder = OUT / "records"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return data, "/data/records/" + name


def ms(us):
    return f"{us/1000:.3f} ms"


def pct(value):
    return f"{value:+.2f}%"


def fastest(summary):
    result = summary["results"]
    name = min(("mapped_plain", "native_plain"), key=lambda mode: result[mode]["mean_us"])
    comparison = result["versus_" + name]
    return (result[name]["mean_us"], result["mapped_encrypted"]["mean_us"], comparison["mean_overhead_pct"],
            comparison["mean_ci95_block_pairs_16"])


def build():
    arm, arm_url = source("fast-arm.json", "results/vector_attribution/summary.json")
    x86, x86_url = source("fast-x86.json", "results/vector_attribution_x86/summary.json")
    _, history_url = source("historical-attribution.json", "results/vector_attribution/historical_decomposition.json")
    global_split, split_url = source("split-global.json", "results/strict_refinement_x86/after/summary.json")
    filtered, filter_url = source("split-filtered.json", "results/strict_refinement_x86/compact/summary.json")
    pools = []
    release = "results/release_exact_x86"
    final = (ROOT / release / "fp32_primary/summary.json").exists() and (ROOT / release / "fp32_holdout/summary.json").exists()
    directory = release if final else "results/mapped_exact_x86"
    for label, ending in (("首批 512 个查询", "primary"), ("独立后 512 个查询", "holdout")):
        target = ("fp32_" if final else "pipeline_") + ending
        value, path = source("mapped-" + ending + ".json", f"{directory}/{target}/summary.json")
        pools.append((label, value, path))
    fast_rows = []
    for label, data in (("Linux ARM64", arm), ("Linux x86", x86)):
        arms = data["e2e"]["arms"]
        c = data["e2e"]["comparisons"]["ppe_fast_vs_plain_fast"]
        fast_rows.append([label, ms(arms["plain_fast"]["us"]["mean"]), ms(arms["ppe_fast"]["us"]["mean"]),
                          pct(c["overhead_pct"]["mean"]),f"{100*arms['ppe_fast']['recall']:.2f}%"])
    mapped_rows = []
    for label, report, _ in pools:
        plain, encrypted, delta, ci = fastest(report)
        mapped_rows.append([label, ms(plain), ms(encrypted), pct(delta), f"{ci[0]:.2f}% 至 {ci[1]:.2f}%"])
    f32_lower = fastest(pools[-1][1])[2]
    split_rows = []
    for label, results in (("无过滤，全库选 10", global_split["results"]["global"]),
                           ("筛选后恰有 10 条，精确排序", filtered["results"]["filtered10"])):
        p, e = results["plain"], results["encrypted"]
        split_rows.append([label, ms(p["e2e_us"]["mean"]), ms(e["e2e_us"]["mean"]),
                           pct(e["overhead_pct"]["mean"]), f"{100*e['exact_order']:.0f}%"])
    sections = [
        {"title":"快速向量检索，同一评分实现",
         "summary":"两边使用同一套点积和 Top-10 选择内核。每台 Linux 各执行 9,216 次真实 TLS 请求，包含快排与精确参考三组。均值包含 SDK、传输、授权和返回正文。",
         "columns":["平台","明文快排","密文快排","平均开销","密文 Recall@10"], "rows":fast_rows,
         "note":"ARM64 平均开销的配对 95% 区间为 +3.76% 至 +5.24%，x86 为 +6.07% 至 +7.22%。",
         "sources":[["ARM64 原始统计",arm_url],["x86 原始统计",x86_url]]},
        {"title":"为什么曾测出 −1.14%",
         "summary":"早期明文做 FP32 粗筛和 FP64 近邻复核，密文快速路径只做近似排序。那一轮密文在 HPIS 检索和取正文阶段少用 88.13 µs，解密及其余链路多用 62.86 µs，端到端净差为 −25.27 µs。",
         "note":"复测时，只切换明文排序路径，x86 的 HPIS 索引阶段就节省了 72.92 µs。相同型号的另一轮早期实验测得 +1.03%。",
         "sources":[["原始分段计时",history_url]]},
        {"title":"严格 Top-10 · HPIC 预置密文索引",
         "summary":"HPIC 在一次请求内并行处理本地精排和 HPIS 授权。20,000 条 100 维向量，每批 512 个不同查询、4 轮。表中分母取原生与同部署明文两组中均值更快的一组。",
         "columns":["x86 全库","较快明文","严格密文","平均开销","配对 95% 区间"],"rows":mapped_rows,
         "note":("每批 2,048 次密文查询均与全库真值逐项同序。HPIC 预置约 16.72 MB 向量索引，获得向量密钥后具备打开全库向量的能力，正文仍只取最终 10 条。"+
                 ("" if final else "此组 x86 阶段数据先于最终查询绑定加固。")),
         "sources":[[label, url] for label, _, url in pools]},
        {"title":"严格 Top-10 · 独立双服务",
         "summary":"不在 HPIC 预置全库向量时，通用精排需要从 HPIS 传送候选向量。筛选后恰有 10 条的请求可在一次往返内按真实余弦排好这 10 条。",
         "columns":["x86 查询条件","明文","严格密文","平均开销","次序一致"],"rows":split_rows,
         "note":"现场邮件问答的整库上传对比与以上同库向量基准有不同的计时分母。",
         "sources":[["全库精排",split_url],["受限候选精排",filter_url]]},
    ]
    records={"highlights":[{"label":"同核快速查询 · x86","value":fast_rows[1][3],"note":"平均开销 · 相对明文快排"},
                           {"label":"全库严格精排 · x86","value":pct(f32_lower),"note":"独立留出集 · HPIC 预置密文索引"},
                           {"label":"严格 Top-10 一致率","value":"100%","note":"完整核对记录及顺序"}],"sections":sections}
    (OUT/"research.json").write_text(json.dumps(records,ensure_ascii=False,indent=2)+"\n")


if __name__=="__main__":
    build()
