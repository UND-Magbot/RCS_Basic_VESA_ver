$env:DB_NAME="rcs_lg_db"
$env:DB_HOST="127.0.0.1"      # 로컬
$env:DB_USER="root"
$env:DB_PASSWORD="unde5466"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8002
