#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
南華大學法規全文庫 — 更新流程

每學期法規委員會彙整完、雲端資料夾更新之後，跑這一支就好。
它會依序做完：備份 → 重新解析 → 與上一版比對 → 稽核 → 表格檢查 → 產生網頁，
最後吐一份「這次到底變了什麼」的報告。

    python 更新.py --zips ./法規

比對是這支腳本存在的理由。重新解析誰都會做，難的是回答
「這學期哪幾部法規動了、動了什麼」——沒有這個，你沒辦法跟各單位交代，
也沒辦法判斷網頁該不該重新上傳。

輸出
----
    regs.db            最新資料庫
    regs_prev.db       上一版（自動備份，比對用）
    法規查詢.html      查詢頁，改名成 index.html 上傳
    更新報告.md        這次的變更摘要（人看的）
    變更清單.csv       逐筆變更（分送各單位用）
    品質報告.csv / 法規稽核.csv / 表格檢查.csv / 重複檔案.csv / 名稱調整.csv
"""

import argparse
import csv
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable


def run(script: str, *args: str) -> None:
    cmd = [PY, str(HERE / script), *args]
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0:
        raise SystemExit(f"！{script} 執行失敗（{' '.join(args)}），流程中止")


def snapshot(db: Path) -> dict:
    """把一版資料庫壓成可比對的字典。"""
    if not db.exists():
        return {}
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    out = {}
    for r in con.execute(
            "SELECT reg_id, reg_code, reg_name, unit, approved_date, meeting, "
            "status, article_count, file_hash FROM regulations"):
        # 以秘書室編號為主鍵；沒有編號才退回「單位＋名稱」。
        # 用編號的好處是法規改名時仍能認得出是同一部，不會誤判成一新一廢。
        key = r["reg_code"] or f'{r["unit"]}／{r["reg_name"]}'
        out[key] = dict(r)
    con.close()
    return out


def diff(prev: dict, curr: dict) -> dict:
    added = [curr[k] for k in curr.keys() - prev.keys()]
    removed = [prev[k] for k in prev.keys() - curr.keys()]
    amended, silent, renamed = [], [], []
    for k in prev.keys() & curr.keys():
        a, b = prev[k], curr[k]
        if a["reg_name"] != b["reg_name"]:
            renamed.append((a, b))
        if a["approved_date"] != b["approved_date"]:
            amended.append((a, b))
        elif a["file_hash"] != b["file_hash"] or a["article_count"] != b["article_count"]:
            # 檔案內容變了、通過日期卻沒動。這是最需要人看的一類：
            # 可能是承辦換了檔案忘了更新日期，也可能只是重新掃描同一份。
            silent.append((a, b))
    key = lambda r: (r["unit"], r["reg_name"])
    return {
        "added": sorted(added, key=key),
        "removed": sorted(removed, key=key),
        "amended": sorted(amended, key=lambda p: p[1]["approved_date"] or "", reverse=True),
        "silent": sorted(silent, key=lambda p: key(p[1])),
        "renamed": sorted(renamed, key=lambda p: key(p[1])),
    }


def count_csv(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, encoding="utf-8-sig") as f:
        return max(0, sum(1 for _ in f) - 1)


def write_csv(path: Path, d: dict) -> None:
    rows = []
    for r in d["added"]:
        rows.append({"變更": "新增", "單位": r["unit"], "編號": r["reg_code"] or "",
                     "法規": r["reg_name"], "原值": "", "新值": r["approved_date"] or "",
                     "條文數": r["article_count"]})
    for r in d["removed"]:
        rows.append({"變更": "已移除", "單位": r["unit"], "編號": r["reg_code"] or "",
                     "法規": r["reg_name"], "原值": r["approved_date"] or "", "新值": "",
                     "條文數": r["article_count"]})
    for a, b in d["amended"]:
        rows.append({"變更": "修正", "單位": b["unit"], "編號": b["reg_code"] or "",
                     "法規": b["reg_name"], "原值": a["approved_date"] or "無日期",
                     "新值": b["approved_date"] or "無日期",
                     "條文數": f'{a["article_count"]}→{b["article_count"]}'})
    for a, b in d["silent"]:
        rows.append({"變更": "內容變動但日期未變", "單位": b["unit"],
                     "編號": b["reg_code"] or "", "法規": b["reg_name"],
                     "原值": f'{a["article_count"]} 條', "新值": f'{b["article_count"]} 條',
                     "條文數": ""})
    for a, b in d["renamed"]:
        rows.append({"變更": "更名", "單位": b["unit"], "編號": b["reg_code"] or "",
                     "法規": b["reg_name"], "原值": a["reg_name"], "新值": b["reg_name"],
                     "條文數": ""})
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["變更", "單位", "編號", "法規", "原值", "新值", "條文數"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def write_report(path: Path, d: dict, stats: dict, first_run: bool) -> None:
    L = []
    A = L.append
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    A(f"# 法規全文庫更新報告　{stamp}")
    A("")
    A(f"本次收錄 **{stats['regs']} 部法規／{stats['arts']} 條條文**，涵蓋 {stats['units']} 個單位。")
    A("")

    if first_run:
        A("這是第一次建置，沒有前一版可以比對。下次執行時就會列出變更。")
    else:
        n = (len(d["added"]) + len(d["removed"]) + len(d["amended"])
             + len(d["silent"]) + len(d["renamed"]))
        if n == 0:
            A("**與上一版相比沒有任何變更。** 網頁不需要重新上傳。")
        else:
            A(f"與上一版相比共 **{n} 項變更**：新增 {len(d['added'])}、"
              f"移除 {len(d['removed'])}、修正 {len(d['amended'])}、"
              f"內容變動但日期未變 {len(d['silent'])}、更名 {len(d['renamed'])}。")
    A("")
    A("---")
    A("")

    def table(title, rows, cols, fmt, note=""):
        if not rows:
            return
        A(f"## {title}（{len(rows)} 部）")
        A("")
        if note:
            A(note)
            A("")
        A("| " + " | ".join(cols) + " |")
        A("|" + "---|" * len(cols))
        for r in rows[:60]:
            A("| " + " | ".join(str(x).replace("|", "／") for x in fmt(r)) + " |")
        if len(rows) > 60:
            A("")
            A(f"（僅列前 60 筆，完整清單見 `變更清單.csv`）")
        A("")

    table("新增法規", d["added"], ["單位", "法規名稱", "通過日期", "條文數"],
          lambda r: (r["unit"], r["reg_name"], r["approved_date"] or "—", r["article_count"]))

    table("已移除法規", d["removed"], ["單位", "法規名稱", "原通過日期"],
          lambda r: (r["unit"], r["reg_name"], r["approved_date"] or "—"),
          "從雲端資料夾消失，可能是廢止、更名，也可能是承辦這次漏交。**移除不會自動視為廢止**，"
          "請先確認是哪一種再處理。")

    table("修正法規", d["amended"], ["單位", "法規名稱", "原通過日期", "新通過日期", "條文數"],
          lambda p: (p[1]["unit"], p[1]["reg_name"], p[0]["approved_date"] or "無日期",
                     p[1]["approved_date"] or "無日期",
                     f'{p[0]["article_count"]}→{p[1]["article_count"]}'))

    table("內容變動但通過日期未變", d["silent"], ["單位", "法規名稱", "原條文數", "新條文數"],
          lambda p: (p[1]["unit"], p[1]["reg_name"], p[0]["article_count"], p[1]["article_count"]),
          "檔案換過了，通過日期卻沒動。兩種可能：承辦換檔忘了更新日期（要追），"
          "或只是重新掃描同一份文件（可忽略）。**這一類最值得花時間看**，"
          "因為它是唯一會讓人拿到新內容卻以為是舊版的情況。")

    table("更名", d["renamed"], ["單位", "原名稱", "新名稱"],
          lambda p: (p[1]["unit"], p[0]["reg_name"], p[1]["reg_name"]))

    A("---")
    A("")
    A("## 品質與稽核")
    A("")
    A(f"- 品質報告：{stats['qc']} 項需人工確認（掃描檔、條號斷號、缺通過日期等）")
    A(f"- 一致性稽核：{stats['audit']} 項（母法較新、條號引用失效、引用之法規未收錄等）")
    if isinstance(stats["tbl"], str):
        A(f"- 表格檢查：{stats['tbl']}（加上 `--skip-tables` 時不執行；需要時單獨跑 `python 表格檢查.py`）")
    else:
        A(f"- 表格檢查：{stats['tbl']} 部原檔與入庫表格數有落差，須確認是否為誤判")
    A(f"- 重複合併：{stats['dup']} 個檔案被合併（副本、同一部法規的不同格式）")
    A(f"- 名稱正規化：{stats['name']} 件（去前綴、補校名、補系所名）")
    A("")
    A("細節分別在 `品質報告.csv`、`法規稽核.csv`、`表格檢查.csv`、`重複檔案.csv`、`名稱調整.csv`。")
    A("")
    A("---")
    A("")
    A("## 接下來")
    A("")
    if not first_run and not any(d.values()):
        A("沒有變更，網頁維持現狀即可。")
    else:
        A("1. 看過上面的「內容變動但通過日期未變」與「已移除法規」，這兩類最容易出事")
        A("2. 把 `法規查詢.html` 改名為 `index.html`，上傳覆蓋 GitHub repo")
        A("3. 開啟網址時在後面加 `?t=` 加任意數字，避開瀏覽器與 CDN 快取")
        A("4. 需要分送各單位時，用 `變更清單.csv` 依「單位」欄篩選")
    A("")
    path.write_text("\n".join(L), encoding="utf-8")


def pack_deploy(site: Path, appdir: Path, stamp: str) -> tuple[Path, float]:
    """把產生好的查詢頁打包成可直接上傳的部署檔。

    做兩件手動很容易漏掉的事：
      1. 改名成 index.html —— GitHub Pages 只認這個檔名當首頁
      2. 把 Service Worker 的快取名稱換成本次建置的時間戳

    第 2 點不做的話，已經把網頁「加入主畫面」的同仁，手機裡會留著上一版的
    快取。目前策略是 network-first，有網路時仍會拿到新版，但快取名稱不換，
    舊資料就永遠不會被清掉，離線時看到的還是舊法規。
    """
    appdir.mkdir(parents=True, exist_ok=True)
    dest = appdir / "index.html"
    shutil.copy2(site, dest)

    sw = appdir / "sw.js"
    if sw.exists():
        txt = sw.read_text(encoding="utf-8")
        txt = re.sub(r"const CACHE = '[^']*';",
                     f"const CACHE = 'nhu-law-{stamp}';", txt)
        sw.write_text(txt, encoding="utf-8")

    return dest, dest.stat().st_size / 1024 / 1024


def append_build_log(d: dict, stats: dict, n: int, first_run: bool) -> None:
    """把這次建置追加到建置紀錄。
    這是資料面的流水帳（每次跑都有一筆），跟人工維護的 變更紀錄.md 分開：
    一個記「資料變了什麼」，一個記「程式改了什麼」，混在一起兩邊都難查。"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    csv_path = HERE / "建置紀錄.csv"
    new = not csv_path.exists()
    row = {
        "建置時間": ts, "法規數": stats["regs"], "條文數": stats["arts"],
        "單位數": stats["units"], "變更總數": n,
        "新增": len(d["added"]), "移除": len(d["removed"]), "修正": len(d["amended"]),
        "內容變動但日期未變": len(d["silent"]), "更名": len(d["renamed"]),
        "品質待確認": stats["qc"], "稽核項目": stats["audit"],
    }
    with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)

    md = HERE / "建置紀錄.md"
    if not md.exists():
        md.write_text("# 建置紀錄\n\n每次執行 `更新.py` 自動追加。"
                      "功能與修正的歷程請看 `變更紀錄.md`。\n\n", encoding="utf-8")
    if first_run:
        detail = "初次建置"
    elif n == 0:
        detail = "無變更"
    else:
        parts = [f"{k} {v}" for k, v in
                 [("新增", len(d["added"])), ("移除", len(d["removed"])),
                  ("修正", len(d["amended"])), ("內容變動但日期未變", len(d["silent"])),
                  ("更名", len(d["renamed"]))] if v]
        detail = "、".join(parts)
    with open(md, "a", encoding="utf-8") as f:
        f.write(f"- **{ts}**　{stats['regs']} 部／{stats['arts']} 條／{stats['units']} 單位"
                f"　—　{detail}"
                f"（品質待確認 {stats['qc']}、稽核 {stats['audit']}）\n")


def main():
    ap = argparse.ArgumentParser(description="法規全文庫更新流程")
    ap.add_argument("--zips", default="./法規", help="含各單位 zip 的資料夾")
    ap.add_argument("--work", default="./work")
    ap.add_argument("--db", default="regs.db")
    ap.add_argument("--template", default="template.html")
    ap.add_argument("--out", default="法規查詢.html")
    ap.add_argument("--kinds", default="法規")
    ap.add_argument("--appdir", default="./app",
                    help="部署資料夾：產生 index.html 並更新 Service Worker 快取版號")
    ap.add_argument("--inbox", default="./收件匣",
                    help="收件匣資料夾：各單位陸續送來的單檔法規")
    ap.add_argument("--skip-tables", action="store_true",
                    help="跳過表格檢查（要逐份重開原檔，較慢）")
    args = ap.parse_args()

    db = HERE / args.db
    prev_db = HERE / "regs_prev.db"

    print("〔1/6〕備份上一版資料庫")
    first_run = not db.exists()
    if not first_run:
        shutil.copy2(db, prev_db)
        print(f"      → {prev_db.name}")
    else:
        print("      （沒有前一版，本次視為初次建置）")
    prev = snapshot(prev_db)

    print("〔2/6〕解析與入庫")
    run("nhu_regs.py", "ingest", "--zips", args.zips, "--work", args.work,
        "--db", args.db, "--kinds", args.kinds, "--inbox", args.inbox)

    print("〔3/6〕一致性稽核")
    run("法規稽核.py", args.db)

    if args.skip_tables:
        print("〔4/6〕表格檢查（略過）")
    else:
        print("〔4/6〕表格檢查")
        run("表格檢查.py", args.db)

    print("〔5/6〕產生查詢頁")
    run("nhu_regs.py", "build-site", "--db", args.db,
        "--template", args.template, "--out", args.out)

    print("〔6/6〕與上一版比對")
    curr = snapshot(db)
    d = diff(prev, curr)

    con = sqlite3.connect(db)
    stats = {
        "regs": con.execute("SELECT COUNT(*) FROM regulations").fetchone()[0],
        "arts": con.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
        "units": con.execute("SELECT COUNT(DISTINCT unit) FROM regulations").fetchone()[0],
        "qc": count_csv(HERE / "品質報告.csv"),
        "audit": count_csv(HERE / "法規稽核.csv"),
        "tbl": "（本次略過）" if args.skip_tables else count_csv(HERE / "表格檢查.csv"),
        "dup": count_csv(HERE / "重複檔案.csv"),
        "name": count_csv(HERE / "名稱調整.csv"),
    }
    con.close()

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    dest, size = pack_deploy(HERE / args.out, HERE / args.appdir, stamp)

    n = write_csv(HERE / "變更清單.csv", d)
    write_report(HERE / "更新報告.md", d, stats, first_run)
    append_build_log(d, stats, n, first_run)

    print()
    print("─" * 46)
    print(f"完成：{stats['regs']} 部法規／{stats['arts']} 條條文／{stats['units']} 個單位")
    if first_run:
        print("初次建置，無可比對之前版。")
    elif n == 0:
        print("與上一版無任何變更 —— 網頁不需要重新上傳。")
    else:
        print(f"變更 {n} 項："
              f"新增 {len(d['added'])}、移除 {len(d['removed'])}、修正 {len(d['amended'])}、"
              f"內容變動但日期未變 {len(d['silent'])}、更名 {len(d['renamed'])}")
        if d["silent"]:
            print("  ⚠ 有法規換了檔案卻沒更新通過日期，請先看 更新報告.md 的該節")
    print(f"報告：更新報告.md　清單：變更清單.csv")
    print(f"待上傳：{dest}（{size:.1f} MB，Service Worker 快取版號已更新為 {stamp}）")
    if not first_run and n == 0:
        print("　（本次資料無變更，若介面也沒改就不必重新上傳）")
    print("─" * 46)


if __name__ == "__main__":
    main()
