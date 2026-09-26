"""postgres 배포 잡(Jenkinsfile)의 "버전 목록" 단계에서 쓴다.

MAJORS(공백 구분, 예: "15 16 17 18 19")의 각 메이저에 대해 Docker Hub 에서 X.Y 태그를 찾아
versions.txt 에 최신순으로 쓴다. 현재 메이저는 PER_MAJOR_CURRENT 개, 다른 메이저는 PER_MAJOR_OTHER 개까지.
-alpine, -bookworm 같은 변형과 17 같은 움직이는 태그는 뺀다. 아직 나오지 않은 메이저는 조용히 건너뛴다.
"""

import json
import os
import re
import urllib.request

PER_MAJOR_CURRENT = 10
PER_MAJOR_OTHER = 3

current = open("current.txt").read().strip()
current_major = current.split(".")[0]
majors = sorted(set(os.environ["MAJORS"].split()) | {current_major}, key=int)


def minors(major: str) -> list[str]:
    pattern = re.compile(rf"^{re.escape(major)}\.(\d+)$")
    url = f"{os.environ['HUB_TAGS_URL']}?page_size=100&name={major}."
    found = set()
    for _ in range(5):  # 버전마다 변형 태그가 여럿이라 몇 페이지만 본다
        if not url:
            break
        with urllib.request.urlopen(url, timeout=20) as r:
            page = json.load(r)
        for t in page["results"]:
            if pattern.match(t["name"]):
                found.add(t["name"])
        url = page.get("next")
    return sorted(found, key=lambda v: int(v.split(".")[1]), reverse=True)


versions = []
for major in sorted(majors, key=int, reverse=True):
    limit = PER_MAJOR_CURRENT if major == current_major else PER_MAJOR_OTHER
    found = minors(major)[:limit]
    print(f"  {major}: {' '.join(found) if found else '(없음)'}")
    versions += found

if not versions:
    raise SystemExit("버전 태그를 하나도 찾지 못했다")

with open("versions.txt", "w") as f:
    f.write("\n".join(versions) + "\n")
print(f"현재 {current} / 선택지 {len(versions)}개")
