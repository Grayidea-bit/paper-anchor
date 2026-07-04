"""下載驗收測試論文（PDF 不入版控，見 README.md 的版權說明）。

用法：python docs/fixtures/download.py
"""

import urllib.request
from pathlib import Path

ARXIV_IDS = ["2410.11591v1", "2602.23013v3", "2606.16119v1"]
DEST = Path(__file__).parent


def main() -> None:
    for arxiv_id in ARXIV_IDS:
        dest = DEST / f"{arxiv_id}.pdf"
        if dest.exists():
            print(f"skip (exists): {dest.name}")
            continue
        url = f"https://arxiv.org/pdf/{arxiv_id}"
        print(f"downloading {url} ...")
        urllib.request.urlretrieve(url, dest)  # noqa: S310 - fixed https host
        print(f"  -> {dest.name} ({dest.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
