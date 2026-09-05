# 상시 수집 머신 — 웹 서버가 아니라 백그라운드 워커다(열어 둔 포트 없음).
FROM python:3.11-slim

# git: 결과를 레포에 push 하려면 필요
# ca-certificates: HTTPS 스크래핑
# tzdata: 로그 시각을 KST 로 읽고 싶을 때
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY deploy/supervisor.py /app/supervisor.py

# 코드는 볼륨의 release checkout, 데이터는 /data/proodd.sqlite3 에 둔다.
# git push 자체는 실행 중 checkout을 갱신하지 않는다. 배포 시 코드와 supervisor
# 이미지 반영을 따로 검증한다. DB는 git과 독립적으로 백업해야 한다.
ENV REPO_DIR=/data/repo \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

CMD ["python", "-u", "/app/supervisor.py"]
