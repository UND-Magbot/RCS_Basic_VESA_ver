# 로컬 DB(127.0.0.1) 사용 — 격리된 개발용
# 내 노트북 MariaDB만 바라봄. 공장 서버(192.168.0.21)와 완전히 분리됨.
# 여기서 뭘 수정해도 공장 DB에는 영향 없음.
$env:DB_HOST="127.0.0.1"
$env:DB_USER="root"
$env:DB_PASSWORD="1234"
$env:DB_NAME="rcs_vesa_db"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8002
