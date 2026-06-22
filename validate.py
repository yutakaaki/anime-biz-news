"""ラベル付き20本に対して判定を走らせ、精度を表示する（PoCの核）。

使い方:
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 validate.py

取りこぼし最小化の方針に合わせ、{対象, グレー}=keep / {対象外}=drop で gold と比較する。
特に重視するのは「正例の取りこぼし（gold=対象 を drop）」を0に近づけること。
"""
from __future__ import annotations

import sys

from classifier import MODEL, classify
from examples import LABELED_EXAMPLES
from fetcher import fetch_one


def main() -> int:
    rows = []
    n_correct = n_total = 0
    miss_positive = 0  # 正例の取りこぼし（最重要指標）
    false_keep = 0     # 負例を拾ってしまったノイズ

    print(f"モデル: {MODEL}\n検証対象: {len(LABELED_EXAMPLES)} 件\n")

    for url, gold in LABELED_EXAMPLES:
        art = fetch_one(url)
        if art.error and not (art.text or art.summary):
            rows.append((gold, "取得失敗", "—", art.error, url))
            print(f"[取得失敗] {gold:4} {url}\n    {art.error}")
            continue

        try:
            j = classify(art.for_classification())
        except Exception as e:  # noqa: BLE001
            rows.append((gold, "判定失敗", "—", str(e), url))
            print(f"[判定失敗] {gold:4} {url}\n    {e}")
            continue

        gold_keep = gold == "対象"
        ok = j.keep == gold_keep
        n_total += 1
        n_correct += int(ok)
        if gold_keep and not j.keep:
            miss_positive += 1
        if (not gold_keep) and j.keep:
            false_keep += 1

        mark = "✓" if ok else "✗"
        rows.append((gold, j.label, j.confidence, j.reason, url))
        print(f"[{mark}] gold={gold:4} pred={j.label:4}({j.confidence}) {url}\n    {j.reason}")

    print("\n" + "=" * 60)
    if n_total:
        print(f"正答率(keep/drop一致): {n_correct}/{n_total} = {n_correct / n_total:.0%}")
    print(f"正例の取りこぼし（最重要・0が理想）: {miss_positive}")
    print(f"負例を拾ったノイズ: {false_keep}")
    skipped = len(LABELED_EXAMPLES) - n_total
    if skipped:
        print(f"取得/判定できず除外: {skipped} 件（ペイウォール等）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
