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

# 실제 코드·데이터는 볼륨(/data/repo)의 git 클론에서 돌린다.
# 이미지에 굽지 않는 이유: 코드를 고치면 git push 만으로 반영되고,
# 데이터는 재시작에도 살아남아야 하기 때문.
ENV REPO_DIR=/data/repo \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

CMD ["python", "-u", "/app/supervisor.py"]
